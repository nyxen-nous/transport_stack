"""Live conditions: blend the last 30 minutes of bus movements with the historical model.

For every segment that buses crossed recently we compute the *live ratio*

    r = median( observed running time / historical expected time for that half hour )

and adjust predictions with

    factor = 1 + (r - 1) * w * exp(-horizon / TAU),   w = n / (n + 2)

* w shrinks the ratio when only one or two buses were seen (noisy).
* horizon is how far in the future the bus reaches the segment. Live traffic
  says a lot about the next 15 minutes and little about the next 2 hours, so
  its effect decays and the estimate falls back to history.
* Segments with no recent bus borrow the ratio of their 3 km area (weaker
  weight), and with no live data at all the factor is exactly 1: the model is
  then purely historical, which is what a quiet road at night gets.

Two feeds produce the same observations:
* ReplayFeed: a held-out October day, so the demo and the validation can run
  "live" against real data with no future leakage (only observations that
  ended before the clock are visible).
* GtfsRtFeed: polls a GTFS-Realtime VehiclePositions URL and runs the same
  stop-pass map matching incrementally on a rolling per-vehicle buffer.
"""
from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

import numpy as np

from .gtfs import Network
from .ingest import StopGrid, link_segments, stop_passes, to_xy, visits
from .model import AREA, Model, bin_of, daytype_of

WINDOW = 1800        # seconds of history that count as "live"
TAU = 2400.0         # decay of live influence with forecast horizon
K_LIVE = 2.0
K_AREA = 10.0


class LiveIndex:
    """Recent segment observations -> per-segment and per-area log ratios."""

    def __init__(self, net: Network, model: Model):
        self.net, self.model = net, model
        mx, my = to_xy((net.lat[net.seg_from] + net.lat[net.seg_to]) / 2,
                       (net.lon[net.seg_from] + net.lon[net.seg_to]) / 2)
        cell = np.floor(mx / AREA).astype(np.int64) * 1000 + np.floor(my / AREA).astype(np.int64)
        _, self.seg_cell = np.unique(cell, return_inverse=True)
        self.n_cells = int(self.seg_cell.max()) + 1
        self.clear()

    def clear(self):
        self.seg_ratio = {}
        self.cell_ratio = {}
        self.observations = 0

    def update(self, seg, t_end_local, run, daytype):
        """seg, end time (seconds since local midnight) and running seconds of recent traversals."""
        self.clear()
        if len(seg) == 0:
            return
        start = t_end_local - run
        hist = np.exp(self.model.run_mu[seg, daytype, bin_of(start).astype(np.int64)]) * self.model.calib
        lr = np.log(np.clip(run / hist, 0.2, 5))
        o = np.argsort(seg, kind='stable')
        seg, lr = seg[o], lr[o]
        u, st, cnt = np.unique(seg, return_index=True, return_counts=True)
        for s, a, c in zip(u.tolist(), st.tolist(), cnt.tolist()):
            self.seg_ratio[s] = (float(np.median(lr[a:a + c])), c)
        cells = self.seg_cell[seg]
        o = np.lexsort((lr, cells))
        cs, lv = cells[o], lr[o]
        u, st, cnt = np.unique(cs, return_index=True, return_counts=True)
        med = lv[st + (cnt - 1) // 2]
        self.cell_ratio = {int(c): (float(m), int(n)) for c, m, n in zip(u, med, cnt)}
        self.observations = int(len(seg))

    def factor(self, seg, at_sec, now_sec):
        horizon = max(0.0, at_sec - now_sec)
        decay = math.exp(-horizon / TAU)
        hit = self.seg_ratio.get(seg)
        if hit:
            lr, n = hit
            w = n / (n + K_LIVE)
            return math.exp(lr * w * decay), 1
        hit = self.cell_ratio.get(int(self.seg_cell[seg]))
        if hit:
            lr, n = hit
            w = 0.6 * n / (n + K_AREA)
            return math.exp(lr * w * decay), 0
        return 1.0, 0


class ReplayFeed:
    """Plays back one processed October day as if it were the live feed."""

    def __init__(self, net: Network, model: Model, obs_dir: Path):
        self.net, self.model, self.dir = net, model, Path(obs_dir)
        self.index = LiveIndex(net, model)
        self._day = None
        self.clock = None
        self._last_now = None
        self.reuse = 0
        self.source = 'replay'

    def available_days(self):
        return sorted(p.stem for p in self.dir.glob('2026_10_??.npz'))

    def set_clock(self, when: datetime):
        key = when.strftime('%Y_%m_%d')
        path = self.dir / f'{key}.npz'
        if not path.exists():
            self.index.clear()
            self.clock = None
            return False
        if self._day != key:
            z = np.load(path, allow_pickle=True)
            t_end = z['t0'] + z['run'].astype(np.int64)
            o = np.argsort(t_end)
            self._seg, self._end, self._run = z['seg'][o], t_end[o], z['run'][o].astype(np.float64)
            self._day = key
            self._last_now = None
        now = int(when.timestamp())
        # reuse the index for up to `reuse` seconds: it then only holds older data, never future data
        if self.clock is not None and self._last_now is not None and 0 <= now - self._last_now < self.reuse:
            return True
        self._last_now = now
        a, b = np.searchsorted(self._end, [now - WINDOW, now])
        local_end = (self._end[a:b] + 19800) % 86400
        self.index.update(self._seg[a:b], local_end, self._run[a:b], daytype_of(when.date()))
        self.clock = when
        return True

    def factor(self, seg, at_sec, now_sec):
        return self.index.factor(seg, at_sec, now_sec)

    def status(self):
        return dict(source=self.source, clock=self.clock.isoformat() if self.clock else None,
                    live_observations=self.index.observations, segments_with_live_data=len(self.index.seg_ratio),
                    replay_days=self.available_days())


class GtfsRtFeed(ReplayFeed):
    """Poll a GTFS-Realtime VehiclePositions feed and keep a rolling live index.

    Configure with TRANSIT_RT_URL (and TRANSIT_RT_KEY if the feed needs ?key=).
    """

    def __init__(self, net: Network, model: Model, url: str, poll=10):
        super().__init__(net, model, Path('.'))
        self.url, self.poll, self.source = url, poll, 'gtfs-rt'
        self.grid = StopGrid(net)
        self.buffer = defaultdict(lambda: deque(maxlen=400))   # vehicle -> (ts, lat, lon)
        self.lock = threading.Lock()
        self.last_error = None
        threading.Thread(target=self._loop, daemon=True).start()

    def _fetch(self):
        import requests
        from google.transit import gtfs_realtime_pb2
        msg = gtfs_realtime_pb2.FeedMessage()
        msg.ParseFromString(requests.get(self.url, timeout=20).content)
        rows = []
        for e in msg.entity:
            if e.HasField('vehicle') and e.vehicle.HasField('position'):
                v = e.vehicle
                rows.append((v.vehicle.id or e.id, int(v.timestamp or msg.header.timestamp),
                             v.position.latitude, v.position.longitude))
        return rows

    def _loop(self):
        while True:
            try:
                rows = self._fetch()
                with self.lock:
                    for vid, ts, lat, lon in rows:
                        buf = self.buffer[vid]
                        if not buf or ts > buf[-1][0]:
                            buf.append((ts, lat, lon))
                self._rebuild()
                self.last_error = None
            except Exception as exc:     # keep serving historical estimates
                self.last_error = repr(exc)
            time.sleep(self.poll)

    def _rebuild(self):
        now = int(time.time())
        veh, ts, lat, lon = [], [], [], []
        with self.lock:
            for n, (vid, buf) in enumerate(self.buffer.items()):
                for t, a, b in buf:
                    if t >= now - WINDOW - 600:
                        veh.append(n); ts.append(t); lat.append(a); lon.append(b)
        if not ts:
            return
        veh, ts = np.array(veh, np.int32), np.array(ts, np.int64)
        x, y = to_xy(np.array(lat), np.array(lon))
        o = np.lexsort((ts, veh))
        v, s, t, _ = stop_passes(self.grid, veh[o], ts[o], x[o], y[o])
        vv, vs, va, vd = visits(v, s, t)
        seg, t0, run = link_segments(self.net, vv, vs, va, vd)
        end = t0 + run
        keep = end >= now - WINDOW
        dt = datetime.fromtimestamp(now)
        self.index.update(seg[keep], (end[keep].astype(np.int64) + 19800) % 86400, run[keep], daytype_of(dt.date()))
        self.clock = dt

    def set_clock(self, when):
        return True    # real time: the clock is the wall clock

    def status(self):
        st = super().status()
        st.update(url=self.url, vehicles_buffered=len(self.buffer), last_error=self.last_error)
        return st
