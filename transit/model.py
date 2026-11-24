"""Historical travel-time model: segment running time + stop dwell, by day type and half hour.

Estimation is hierarchical so every segment always has an answer, and the API
can say how much evidence stands behind it:

  level 0  segment x day type x half hour        (enough observations)
  level 1  segment typical time x area profile   (segment seen, but not at this hour)
  level 2  area (3 km cell) speed x hour         (segment never observed)
  level 3  city-wide speed x hour                (area never observed)

Levels are blended with empirical-Bayes shrinkage, not switched: an estimate
from n observations gets weight n / (n + K) and the rest comes from the level
below. Bins are half-hours smoothed with their neighbours.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .gtfs import Network
from .ingest import to_xy

IST = timezone(timedelta(hours=5, minutes=30))
BINS = 48                      # half hours
DAYTYPES = ('weekday', 'saturday', 'sunday')
K_SHRINK = 6.0                 # pseudo-observations for the prior
K_DWELL = 20.0
AREA = 3000.0                  # metres, area cell size for fallbacks
Z80 = 1.2816                   # z for the 10th/90th percentile

# Public holidays in October 2026 behave like Sundays. Gandhi Jayanti (2 Oct)
# and Dussehra (20 Oct). The ingest report checks this against the GPS speeds.
HOLIDAYS = {date(2026, 10, 2), date(2026, 10, 20)}


def daytype_of(d: date) -> int:
    if d in HOLIDAYS or d.weekday() == 6:
        return 2
    return 1 if d.weekday() == 5 else 0


def bin_of(seconds_of_day) -> np.ndarray:
    return (np.asarray(seconds_of_day) % 86400) // 1800


def local(ts):
    """Unix seconds -> (ist date ordinal, seconds since IST midnight)."""
    t = np.asarray(ts, dtype=np.int64) + 19800
    return t // 86400, t % 86400


def _group_quantiles(key, val, n_keys, qs):
    """Per-group quantiles of val. Returns (count, {q: array})."""
    o = np.lexsort((val, key))
    key, val = key[o], val[o]
    count = np.bincount(key, minlength=n_keys)
    start = np.concatenate([[0], np.cumsum(count)[:-1]])
    out = {}
    for q in qs:
        idx = start + np.floor(q * np.maximum(count - 1, 0)).astype(np.int64)
        v = np.full(n_keys, np.nan)
        has = count > 0
        v[has] = val[np.minimum(idx[has], len(val) - 1)]
        out[q] = v
    return count, out


def _smooth(a, n, axis=-1):
    """Count-weighted circular smoothing over half-hour bins: weights 1-2-1."""
    num = np.nan_to_num(a * n) * 2 + np.roll(np.nan_to_num(a * n), 1, axis) + np.roll(np.nan_to_num(a * n), -1, axis)
    den = n * 2 + np.roll(n, 1, axis) + np.roll(n, -1, axis)
    with np.errstate(invalid='ignore', divide='ignore'):
        return np.where(den > 0, num / den, np.nan), den / 4.0


@dataclass
class Model:
    run_mu: np.ndarray        # [seg, daytype, bin] log seconds (median)
    run_sigma: np.ndarray     # [seg, daytype, bin] log-sd
    run_n: np.ndarray         # [seg, daytype, bin] effective observations
    run_level: np.ndarray     # [seg, daytype, bin] int8 evidence level
    dwell: np.ndarray         # [stop, daytype, bin] mean seconds
    city_speed: np.ndarray    # [daytype, bin] median m/s
    calib: float = 1.0        # multiplicative end-to-end calibration
    rho: float = 0.5          # correlation between segment errors on one trip
    meta: dict = None

    # ---- queries ------------------------------------------------------
    def run_time(self, seg, daytype, sec):
        b = bin_of(sec)
        return np.exp(self.run_mu[seg, daytype, b]) * self.calib

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, run_mu=self.run_mu.astype(np.float32), run_sigma=self.run_sigma.astype(np.float32),
                            run_n=self.run_n.astype(np.float32), run_level=self.run_level,
                            dwell=self.dwell.astype(np.float32), city_speed=self.city_speed.astype(np.float32),
                            calib=self.calib, rho=self.rho, meta=json.dumps(self.meta or {}))

    @staticmethod
    def load(path: Path) -> 'Model':
        z = np.load(path)
        return Model(z['run_mu'], z['run_sigma'], z['run_n'], z['run_level'], z['dwell'], z['city_speed'],
                     float(z['calib']), float(z['rho']), json.loads(str(z['meta'])))


def load_obs(paths):
    seg, t0, run, dws, dwt, dw = [], [], [], [], [], []
    for p in paths:
        z = np.load(p, allow_pickle=True)
        seg.append(z['seg']); t0.append(z['t0']); run.append(z['run'])
        dws.append(z['dw_stop']); dwt.append(z['dw_t']); dw.append(z['dw_s'])
    return (np.concatenate(seg), np.concatenate(t0), np.concatenate(run),
            np.concatenate(dws), np.concatenate(dwt), np.concatenate(dw))


def obs_daytype(t0):
    day, sec = local(t0)
    uniq = np.unique(day)
    lut = {d: daytype_of(date.fromordinal(int(d) + date(1970, 1, 1).toordinal())) for d in uniq}
    dt = np.zeros(len(day), np.int64)
    for d, v in lut.items():
        dt[day == d] = v
    return dt, bin_of(sec)


def fit(net: Network, obs_paths) -> Model:
    seg, t0, run, dws, dwt, dw = load_obs(obs_paths)
    S, D, B = net.n_segs, len(DAYTYPES), BINS
    dt, b = obs_daytype(t0)
    logrun = np.log(run.astype(np.float64))

    # --- level 0: per segment x daytype x bin (median and robust spread) ----
    key = (seg.astype(np.int64) * D + dt) * B + b
    n0, q = _group_quantiles(key, logrun, S * D * B, (0.1, 0.5, 0.9))
    n0 = n0.reshape(S, D, B).astype(float)
    mu0 = q[0.5].reshape(S, D, B)
    sd0 = ((q[0.9] - q[0.1]) / (2 * Z80)).reshape(S, D, B)
    mu0s, n0s = _smooth(mu0, n0)
    sd0s, _ = _smooth(sd0, n0)

    # --- city profile: speed (m/s) by daytype x bin --------------------------
    speed = net.seg_len[seg] / run
    ckey = dt * B + b
    _, cq = _group_quantiles(ckey, speed, D * B, (0.5,))
    city_speed = cq[0.5].reshape(D, B)
    city_speed = np.where(np.isnan(city_speed), np.nanmedian(city_speed), city_speed)

    # --- area profile: log speed by 3 km cell x daytype x bin ---------------
    mx, my = to_xy((net.lat[net.seg_from] + net.lat[net.seg_to]) / 2, (net.lon[net.seg_from] + net.lon[net.seg_to]) / 2)
    cell = (np.floor(mx / AREA).astype(np.int64) * 1000 + np.floor(my / AREA).astype(np.int64))
    ucell, seg_cell = np.unique(cell, return_inverse=True)
    C = len(ucell)
    akey = (seg_cell[seg] * D + dt) * B + b
    an, aq = _group_quantiles(akey, np.log(speed), C * D * B, (0.5,))
    an = an.reshape(C, D, B).astype(float)
    amu = aq[0.5].reshape(C, D, B)
    amu, an = _smooth(amu, an)
    city_log = np.log(city_speed)[None]
    area_log_speed = np.where(an > 0, (an * np.nan_to_num(amu) + 50 * city_log) / (an + 50), city_log)  # [C,D,B]

    # --- level 1 prior: segment's own typical time, shaped by the area profile ---
    seg_all_n = n0.sum(axis=(1, 2))
    seg_typ = np.full(S, np.nan)
    has = seg_all_n > 0
    with np.errstate(invalid='ignore'):
        seg_typ[has] = (np.nansum(mu0 * n0, axis=(1, 2))[has] / seg_all_n[has])
    # area-profile shape relative to its own mean over (d, b), weighted by segment usage
    area_shape = area_log_speed - area_log_speed.mean(axis=(1, 2), keepdims=True)
    seg_shape = area_shape[seg_cell]                     # [S,D,B]  (log speed deviation)
    # typical bin mix of this segment's observations -> correct seg_typ for the shape at those bins
    with np.errstate(invalid='ignore'):
        mix = np.where(seg_all_n[:, None, None] > 0, n0 / np.maximum(seg_all_n, 1)[:, None, None], 0)
    seg_base = seg_typ + (mix * seg_shape).sum(axis=(1, 2))   # log time at average conditions
    prior1 = seg_base[:, None, None] - seg_shape              # slower area speed -> longer time
    prior2 = np.log(np.maximum(net.seg_len, 10.0))[:, None, None] - area_log_speed[seg_cell]
    prior = np.where(has[:, None, None], prior1, prior2)
    prior_n = np.where(has, np.minimum(seg_all_n, 30), 0)[:, None, None] * np.ones((1, D, B))

    w = n0s / (n0s + K_SHRINK)
    run_mu = np.where(n0s > 0, w * np.nan_to_num(mu0s) + (1 - w) * prior, prior)
    global_sd = np.nanmedian(sd0[n0 >= 5]) if np.any(n0 >= 5) else 0.3
    run_sigma = np.where(n0s > 0, w * np.nan_to_num(sd0s, nan=global_sd) + (1 - w) * global_sd, global_sd * 1.3)
    run_sigma = np.clip(run_sigma, 0.05, 1.5)
    level = np.full((S, D, B), 3, np.int8)
    level[np.broadcast_to(~has[:, None, None], (S, D, B)) & (an[seg_cell] > 0)] = 2
    level[prior_n > 0] = 1
    level[n0s >= 3] = 0

    # --- dwell: mean seconds per stop x daytype x bin, shrunk to city mean ----
    ddt, dbin = obs_daytype(dwt)
    N = len(net.stop_ids)
    dkey = (dws.astype(np.int64) * D + ddt) * B + dbin
    dn = np.bincount(dkey, minlength=N * D * B).reshape(N, D, B).astype(float)
    dsum = np.bincount(dkey, weights=dw, minlength=N * D * B).reshape(N, D, B)
    city_dw = dsum.sum(0) / np.maximum(dn.sum(0), 1)
    dmean, dn_s = _smooth(np.where(dn > 0, dsum / np.maximum(dn, 1), np.nan), dn)
    dwell = (dn_s * np.nan_to_num(dmean) + K_DWELL * city_dw[None]) / (dn_s + K_DWELL)

    meta = dict(
        segment_observations=int(len(seg)), dwell_observations=int(len(dw)),
        segments=int(S), segments_observed=int(has.sum()),
        cells_level0=float((level == 0).mean()), cells_level1=float((level == 1).mean()),
        cells_level2=float((level == 2).mean()), cells_level3=float((level == 3).mean()),
        city_speed_kmh={DAYTYPES[d]: [round(float(x) * 3.6, 1) for x in city_speed[d]] for d in range(D)},
        trained_at=datetime.now(IST).isoformat(timespec='seconds'),
    )
    return Model(run_mu, run_sigma, n0s, level, dwell, city_speed, meta=meta)


def schedule_trip_seconds(net: Network, pattern, i, j):
    return int(pattern.sched_offsets[j] - pattern.sched_offsets[i])


if __name__ == '__main__':
    import argparse
    import sys
    ap = argparse.ArgumentParser()
    ap.add_argument('obs', nargs='+', type=Path)
    ap.add_argument('--network', type=Path, default=Path('build/network.pkl'))
    ap.add_argument('--out', type=Path, default=Path('build/model.npz'))
    a = ap.parse_args()
    from transit.model import fit as _fit
    m = _fit(Network.load(a.network), a.obs)
    m.save(a.out)
    print(json.dumps({k: v for k, v in m.meta.items() if k != 'city_speed_kmh'}, indent=1))
