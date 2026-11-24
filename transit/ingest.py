"""Turn raw GPS pings into stop passes, dwell times and segment running times.

Route-agnostic map matching
---------------------------
The October 2026 feed numbers routes differently from the public static GTFS,
so we do not trust ``route_id``. Instead, for every bus:

1. Clean: sort by time, drop duplicate timestamps, drop points outside Delhi,
   drop fixes that imply > 90 km/h from the last kept fix (GPS jumps).
2. Each pair of consecutive fixes (a *step*, <= 120 s apart) is a short line.
   We find stops whose closest approach to that line is <= 35 m. The time of
   that closest point (linear interpolation) is a *stop pass*.
3. Passes at the same stop that follow each other are merged: the first gives
   arrival, the last gives departure -> **dwell time** at that stop.
4. Two passes a -> b form an observation for segment (a, b) when (a, b) is a
   directed consecutive stop pair in GTFS and b follows a within 20 minutes.
   Running time = arrival(b) - departure(a). Opposite-side stops never form a
   valid directed pair, which is how direction is resolved without headings.

Output per day (npz): segment observations (seg, t_start, running_s) and dwell
observations (stop, t_arrive, dwell_s).
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import numpy as np

from .gtfs import Network

R_STOP = 35.0          # metres: max closest-approach distance to count a pass
MAX_STEP_S = 120       # a gap longer than this breaks the trajectory
MAX_SPEED = 25.0       # m/s (90 km/h): faster implied motion is a GPS jump
MAX_LINK_S = 1200      # a -> b must happen within 20 minutes
CELL = 200.0           # grid cell size for stop lookup, metres
MAX_STEP_M = 300.0     # steps longer than this are not searched for passes
BBOX = (28.3, 29.0, 76.7, 77.5)
LAT0 = 28.62
MX = 111320.0 * np.cos(np.radians(LAT0))   # metres per degree longitude
MY = 110540.0                              # metres per degree latitude


def to_xy(lat, lon):
    return (np.asarray(lon) - 77.0) * MX, (np.asarray(lat) - LAT0) * MY


class StopGrid:
    """Each stop is registered in its own cell and the 8 around it, so a lookup
    of one cell returns every stop within CELL metres of any point in it."""

    def __init__(self, net: Network):
        self.x, self.y = to_xy(net.lat, net.lon)
        cx = np.floor(self.x / CELL).astype(np.int64)
        cy = np.floor(self.y / CELL).astype(np.int64)
        keys, owners = [], []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                keys.append(self._key(cx + dx, cy + dy))
                owners.append(np.arange(len(cx)))
        keys, owners = np.concatenate(keys), np.concatenate(owners)
        o = np.argsort(keys, kind='stable')
        self.keys, self.owners = keys[o], owners[o]
        self.ukeys, self.start = np.unique(self.keys, return_index=True)
        self.count = np.diff(np.append(self.start, len(self.keys)))

    @staticmethod
    def _key(cx, cy):
        return (cx + 100000) * 1000003 + (cy + 100000)

    def candidates(self, px, py):
        """For each query point return (query_index, stop_index) pairs."""
        k = self._key(np.floor(px / CELL).astype(np.int64), np.floor(py / CELL).astype(np.int64))
        pos = np.searchsorted(self.ukeys, k)
        pos = np.clip(pos, 0, len(self.ukeys) - 1)
        hit = self.ukeys[pos] == k
        n = np.where(hit, self.count[pos], 0)
        q = np.repeat(np.arange(len(px)), n)
        base = np.repeat(self.start[pos] - np.concatenate([[0], np.cumsum(n)[:-1]]), n)
        return q, self.owners[base + np.arange(n.sum())]


def load_day(db: Path):
    """Read one day's SQLite file into numpy arrays (vehicle ids mapped to ints chunk by chunk)."""
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    cur = con.execute('SELECT vehicle_id, CAST(timestamp AS INTEGER), CAST(lat AS REAL), '
                      'CAST(lng AS REAL) FROM vehicle_feed')
    codes: dict[str, int] = {}
    veh, ts, lat, lon = [], [], [], []
    while True:
        rows = cur.fetchmany(1_000_000)
        if not rows:
            break
        a, b, c, d = zip(*rows)
        del rows
        veh.append(np.fromiter((codes.setdefault(v, len(codes)) for v in a), dtype=np.int32, count=len(a)))
        ts.append(np.array(b, dtype=np.int64))
        lat.append(np.array([v if v is not None else np.nan for v in c], dtype=np.float64))
        lon.append(np.array([v if v is not None else np.nan for v in d], dtype=np.float64))
    con.close()
    names = np.array(sorted(codes, key=codes.get), dtype=object)
    return names, np.concatenate(veh), np.concatenate(ts), np.concatenate(lat), np.concatenate(lon)


def clean(veh, ts, lat, lon):
    """Sort, dedupe, bbox-filter and drop GPS jumps. Returns kept arrays + stats."""
    stats = {'raw': len(ts)}
    ok = (lat > BBOX[0]) & (lat < BBOX[1]) & (lon > BBOX[2]) & (lon < BBOX[3]) & (ts > 0)
    veh, ts, lat, lon = veh[ok], ts[ok], lat[ok], lon[ok]
    stats['outside_bbox'] = int((~ok).sum())
    o = np.lexsort((ts, veh))
    veh, ts, lat, lon = veh[o], ts[o], lat[o], lon[o]
    dup = np.zeros(len(ts), bool)
    dup[1:] = (veh[1:] == veh[:-1]) & (ts[1:] == ts[:-1])
    veh, ts, lat, lon = veh[~dup], ts[~dup], lat[~dup], lon[~dup]
    stats['duplicates'] = int(dup.sum())
    x, y = to_xy(lat, lon)
    # Jump filter: a point is a spike if it is far from both neighbours but the
    # neighbours are close to each other. Two passes catch isolated spikes.
    jumps = 0
    for _ in range(2):
        n = len(ts)
        if n < 3:
            break
        dt_prev = np.diff(ts).astype(float)
        dist_prev = np.hypot(np.diff(x), np.diff(y))
        same = veh[1:] == veh[:-1]
        fast = same & (dist_prev > MAX_SPEED * np.maximum(dt_prev, 1) + 50)
        bad = np.zeros(n, bool)
        # point i is bad if step (i-1 -> i) and (i -> i+1) are both too fast
        bad[1:-1] = fast[:-1] & fast[1:]
        jumps += int(bad.sum())
        keep = ~bad
        veh, ts, x, y = veh[keep], ts[keep], x[keep], y[keep]
    stats['gps_jumps'] = jumps
    stats['clean'] = len(ts)
    return veh, ts, x, y, stats


def stop_passes(grid: StopGrid, veh, ts, x, y):
    """Closest approach of each GPS step to nearby stops -> (veh, stop, t, dist)."""
    dt = np.diff(ts)
    dx, dy = np.diff(x), np.diff(y)
    length = np.hypot(dx, dy)
    step_ok = (veh[1:] == veh[:-1]) & (dt <= MAX_STEP_S) & (length <= MAX_STEP_M) & \
              (length <= MAX_SPEED * np.maximum(dt, 1) + 50)
    idx = np.nonzero(step_ok)[0]
    out = []
    for chunk in np.array_split(idx, max(1, len(idx) // 2_000_000)):
        x0, y0, ddx, ddy, l2 = x[chunk], y[chunk], dx[chunk], dy[chunk], length[chunk] ** 2
        q, s = grid.candidates(x0 + ddx / 2, y0 + ddy / 2)
        sx, sy = grid.x[s] - x0[q], grid.y[s] - y0[q]
        f = np.where(l2[q] > 0, (sx * ddx[q] + sy * ddy[q]) / np.maximum(l2[q], 1e-9), 0.0)
        f = np.clip(f, 0, 1)
        d = np.hypot(sx - f * ddx[q], sy - f * ddy[q])
        near = d <= R_STOP
        q, s, f, d = q[near], s[near], f[near], d[near]
        i = chunk[q]
        t = ts[i] + f * dt[i]
        out.append((veh[i], s, t, d))
    v, s, t, d = (np.concatenate(z) for z in zip(*out)) if out else [np.array([])] * 4
    o = np.lexsort((t, v))
    return v[o], s[o].astype(np.int32), t[o], d[o]


def visits(v, s, t, gap=180):
    """Merge passes at the same stop into one visit: arrive = first pass, depart = last.

    Passes are grouped per (bus, stop) and split where consecutive passes of that
    stop are more than `gap` seconds apart. Grouping per stop (not by adjacency)
    matters: a bus waiting at a stop also "passes" the stop on the other side of
    the road on every ping, which interleaves the two and would hide the dwell.
    """
    if len(v) == 0:
        return v, s, t, t
    o = np.lexsort((t, s, v))
    v2, s2, t2 = v[o], s[o], t[o]
    new = np.ones(len(v2), bool)
    new[1:] = (v2[1:] != v2[:-1]) | (s2[1:] != s2[:-1]) | (t2[1:] - t2[:-1] > gap)
    first = np.nonzero(new)[0]
    last = np.append(first[1:] - 1, len(v2) - 1)
    vv, vs, va, vd = v2[first], s2[first], t2[first], t2[last]
    o = np.lexsort((va, vv))
    return vv[o], vs[o], va[o], vd[o]


def link_segments(net: Network, vv, vs, varr, vdep, lookahead=6):
    """Link each visit a to a later visit b of the same bus when (a,b) is a GTFS segment."""
    seg_lookup = dict(zip(zip(net.seg_from.tolist(), net.seg_to.tolist()), range(net.n_segs)))
    n = len(vv)
    segs, t0, run = [], [], []
    keyed = net.seg_from.astype(np.int64) * 100000 + net.seg_to
    order = np.argsort(keyed)
    keyed_sorted = keyed[order]
    used = np.zeros(n, bool)
    for k in range(1, lookahead + 1):
        a = np.arange(n - k)
        b = a + k
        ok = (vv[a] == vv[b]) & (varr[b] - vdep[a] <= MAX_LINK_S) & (varr[b] - vdep[a] > 0) & ~used[a]
        a, b = a[ok], b[ok]
        key = vs[a].astype(np.int64) * 100000 + vs[b]
        pos = np.clip(np.searchsorted(keyed_sorted, key), 0, len(keyed_sorted) - 1)
        hit = keyed_sorted[pos] == key
        a, b, sid = a[hit], b[hit], order[pos[hit]]
        used[a] = True
        segs.append(sid); t0.append(vdep[a]); run.append(varr[b] - vdep[a])
    del seg_lookup
    seg = np.concatenate(segs).astype(np.int32)
    t0 = np.concatenate(t0)
    run = np.concatenate(run)
    return seg, t0, run


def process_day(net: Network, db: Path, out: Path, grid: StopGrid | None = None):
    grid = grid or StopGrid(net)
    tick = time.time()
    names, veh, ts, lat, lon = load_day(db)
    t_load = time.time() - tick
    veh, ts, x, y, stats = clean(veh, ts, lat, lon)
    del lat, lon
    v, s, t, d = stop_passes(grid, veh, ts, x, y)
    vv, vs, varr, vdep = visits(v, s, t)
    seg, t0, run = link_segments(net, vv, vs, varr, vdep)
    # plausibility: running speed between 2 and 80 km/h
    speed = net.seg_len[seg] / np.maximum(run, 1)
    ok = (speed > 0.55) & (speed < 22.2) & (run >= 5)
    stats['segment_obs_raw'] = int(len(seg))
    stats['segment_obs_implausible'] = int((~ok).sum())
    seg, t0, run = seg[ok], t0[ok], run[ok]
    dwell = vdep - varr
    dw_ok = dwell <= 600
    stats.update(passes=int(len(v)), visits=int(len(vv)), segment_obs=int(len(seg)),
                 segments_covered=int(len(np.unique(seg))), vehicles=int(len(names)),
                 seconds_load=round(t_load, 1), seconds_total=round(time.time() - tick, 1))
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, seg=seg, t0=t0.astype(np.int64), run=run.astype(np.float32),
                        dw_stop=vs[dw_ok], dw_t=varr[dw_ok].astype(np.int64),
                        dw_s=dwell[dw_ok].astype(np.float32), veh=vv[dw_ok].astype(np.int32),
                        stats=np.array([stats], dtype=object))
    # compact per-visit table, used to reconstruct real end-to-end bus trips for validation
    day0 = int(np.min(varr)) if len(varr) else 0
    np.savez_compressed(out.with_name(out.stem + '_visits.npz'), veh=vv.astype(np.int16), stop=vs.astype(np.int16),
                        arr=(varr - day0).astype(np.int32), dwell=np.minimum(vdep - varr, 32000).astype(np.int16),
                        day0=np.int64(day0))
    return stats


if __name__ == '__main__':
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument('dbs', nargs='+', type=Path)
    ap.add_argument('--network', type=Path, default=Path('build/network.pkl'))
    ap.add_argument('--out', type=Path, default=Path('build/obs'))
    a = ap.parse_args()
    net = Network.load(a.network)
    grid = StopGrid(net)
    for db in a.dbs:
        day = db.stem.replace('bus_movements_', '')
        st = process_day(net, db, a.out / f'{day}.npz', grid)
        print(day, json.dumps(st), flush=True)
