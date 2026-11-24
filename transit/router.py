"""Journey planning: RAPTOR over a timetable whose times come from the GPS model.

Departures from each route's first stop come from the static GTFS. Everything
after that (time between stops, dwell at stops) comes from the travel-time
model for the hour the bus actually reaches each stretch, so a 08:30 bus and
a 23:00 bus on the same route get different running times.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np

from .gtfs import Network
from .ingest import to_xy
from .model import BINS, Model, bin_of, daytype_of

WALK_MS = 1.2          # metres per second (4.3 km/h)
DETOUR = 1.3           # straight-line to street distance
MAX_ACCESS = 1000.0    # metres to first / from last stop
MAX_TRANSFER = 350.0   # metres walking between stops at a change
MIN_CHANGE = 120       # seconds slack when changing buses
ROUNDS = 3             # up to two changes
INF = 10 ** 9


def walk_seconds(metres):
    return metres * DETOUR / WALK_MS


@dataclass
class Timetable:
    """Predicted arrival/departure per pattern for one day type. Arrays are stops x trips."""
    arr: list
    dep: list
    trip_dep: list         # sorted first-stop departures per pattern


def predict_timetable(net: Network, model: Model, daytype: int, factors=None) -> Timetable:
    """Roll each trip forward segment by segment using the half-hour it reaches each segment."""
    arr_all, dep_all, first = [], [], []
    mu = model.run_mu[:, daytype, :]
    dw = model.dwell[:, daytype, :]
    for p in net.patterns:
        t = p.first_dep.astype(np.float64)
        n = len(p.stops)
        arr = np.empty((n, len(t)), np.int32)
        dep = np.empty((n, len(t)), np.int32)
        arr[0] = dep[0] = t
        for k in range(n - 1):
            s = p.segs[k]
            b = bin_of(t).astype(np.int64)
            run = np.exp(mu[s, b]) * model.calib
            if factors is not None:
                run = run * factors(s, t)
            t = t + run
            arr[k + 1] = t
            if k + 1 < n - 1:
                t = t + dw[p.stops[k + 1], bin_of(t).astype(np.int64)]
            dep[k + 1] = t
        # first-in-first-out: a later bus never arrives earlier than an earlier one
        arr = np.maximum.accumulate(arr, axis=1)
        dep = np.maximum.accumulate(dep, axis=1)
        arr_all.append(arr)
        dep_all.append(dep)
        first.append(p.first_dep)
    return Timetable(arr_all, dep_all, first)


class Planner:
    def __init__(self, net: Network, model: Model):
        self.net, self.model = net, model
        self.x, self.y = to_xy(net.lat, net.lon)
        self.tables: dict[int, Timetable] = {}
        self._transfers()
        names = {}
        for i, n in enumerate(net.stop_names):
            names.setdefault(n.casefold(), []).append(i)
        self.by_name = names

    # ---- setup -----------------------------------------------------------
    def table(self, daytype: int) -> Timetable:
        if daytype not in self.tables:
            self.tables[daytype] = predict_timetable(self.net, self.model, daytype)
        return self.tables[daytype]

    def _near(self, x, y, radius):
        d = np.hypot(self.x - x, self.y - y)
        idx = np.nonzero(d <= radius)[0]
        return idx, d[idx]

    def _transfers(self):
        from .ingest import StopGrid
        cell = MAX_TRANSFER
        cx = np.floor(self.x / cell).astype(np.int64)
        cy = np.floor(self.y / cell).astype(np.int64)
        buckets = {}
        for i, k in enumerate(zip(cx.tolist(), cy.tolist())):
            buckets.setdefault(k, []).append(i)
        self.walks = [[] for _ in self.x]
        for i in range(len(self.x)):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j in buckets.get((cx[i] + dx, cy[i] + dy), ()):
                        if j == i:
                            continue
                        d = math.hypot(self.x[i] - self.x[j], self.y[i] - self.y[j])
                        if d <= MAX_TRANSFER:
                            self.walks[i].append((j, int(walk_seconds(d)), d))

    # ---- RAPTOR ------------------------------------------------------------
    def raptor(self, access, egress, t0, daytype, banned=frozenset()):
        """access/egress: {stop: walk_seconds}. Returns per-round labels and parents."""
        net, tt = self.net, self.table(daytype)
        N = len(net.stop_ids)
        best = np.full(N, INF, np.int64)
        labels = [dict()]
        parents = [dict()]
        marked = set()
        for s, w in access.items():
            labels[0][s] = t0 + w
            parents[0][s] = ('access', None, w)
            best[s] = t0 + w
            marked.add(s)
        # walking between stops right at the start (e.g. cross the road)
        for k in range(1, ROUNDS + 1):
            prev = labels[k - 1]          # tau_{k-1}: best arrival using <= k-1 buses
            cur, par = {}, {}
            # collect patterns to scan with earliest marked position
            queue = {}
            for s in marked:
                for pid, pos in net.stop_patterns[s]:
                    if net.patterns[pid].route_id in banned:
                        continue
                    if pos < queue.get(pid, INF):
                        queue[pid] = pos
            for pid, start in queue.items():
                p = net.patterns[pid]
                arr, dep = tt.arr[pid], tt.dep[pid]
                ntrips = arr.shape[1]
                trip, board_pos, board_stop = -1, -1, -1
                for pos in range(start, len(p.stops)):
                    s = int(p.stops[pos])
                    if trip >= 0:
                        a = int(arr[pos, trip])
                        if a < best[s] and a < cur.get(s, INF):
                            cur[s] = a
                            best[s] = a
                            par[s] = ('bus', pid, trip, board_pos, pos, board_stop)
                    ready = prev.get(s)
                    if ready is not None and (trip < 0 or ready + MIN_CHANGE <= dep[pos, trip]):
                        need = ready + (MIN_CHANGE if k > 1 else 0)
                        j = int(np.searchsorted(dep[pos], need))
                        if j < ntrips and (trip < 0 or j < trip):
                            trip, board_pos, board_stop = j, pos, s
            # footpaths from newly improved stops
            for s in list(cur):
                base = cur[s]
                for j, w, _ in self.walks[s]:
                    a = base + w
                    if a < best[j] and a < cur.get(j, INF):
                        cur[j] = a
                        best[j] = a
                        par[j] = ('walk', s, w)
            marked = set(cur)
            merged = dict(prev)
            merged.update(cur)
            labels.append(merged)
            parents.append(par)
            if not marked:
                break
        results = []
        for k in range(1, len(labels)):
            bestd, beste = INF, None
            for s, w in egress.items():
                if s in parents[k] and labels[k][s] + w < bestd:
                    bestd, beste = labels[k][s] + w, s
            if beste is not None:
                results.append((k, bestd, self._trace(labels, parents, k, beste, egress[beste])))
        return results

    def _trace(self, labels, parents, k, stop, egress_walk):
        legs = [('egress', stop, egress_walk)]
        s = stop
        while k >= 0:
            p = parents[k].get(s)
            if p is None:
                k -= 1
                continue
            if p[0] == 'walk':
                legs.append(('transfer_walk', p[1], s, p[2]))
                s = p[1]
                continue
            if p[0] == 'bus':
                _, pid, trip, i, j, board = p
                legs.append(('bus', pid, trip, i, j))
                s = board
                k -= 1
                continue
            if p[0] == 'access':
                legs.append(('access', s, p[2]))
                break
        return legs[::-1]

    # ---- public API ----------------------------------------------------------
    def stops_near(self, lat, lon, radius=MAX_ACCESS):
        x, y = to_xy(lat, lon)
        idx, d = self._near(float(x), float(y), radius)
        if len(idx) == 0:
            idx, d = self._near(float(x), float(y), radius * 2.5)
        return {int(i): int(walk_seconds(di)) for i, di in zip(idx, d)}, (float(x), float(y))

    def plan(self, o_lat, o_lon, d_lat, d_lon, when: datetime, max_options=3, factors=None):
        dtype = daytype_of(when.date())
        t0 = when.hour * 3600 + when.minute * 60 + when.second
        access, _ = self.stops_near(o_lat, o_lon)
        egress, _ = self.stops_near(d_lat, d_lon)
        direct_walk = int(walk_seconds(math.hypot(*(np.subtract(to_xy(o_lat, o_lon), to_xy(d_lat, d_lon))))))
        found, banned = [], set()
        for attempt in range(4):
            res = self.raptor(access, egress, t0, dtype, frozenset(banned))
            if not res:
                break
            for k, arrival, legs in res:
                key = tuple(self.net.patterns[l[1]].route_name for l in legs if l[0] == 'bus')
                if key and all(key != f[2] for f in found):
                    found.append((arrival, k, key, legs))
            # ban the most-used route of the best new option to find alternatives
            best = min(res, key=lambda r: r[1])
            first_bus = next((l for l in best[2] if l[0] == 'bus'), None)
            if first_bus is None:
                break
            banned.add(self.net.patterns[first_bus[1]].route_id)
            if len(found) >= max_options + 2:
                break
        found.sort(key=lambda f: (f[0] + 240 * (f[1] - 1), f[0]))
        return dtype, t0, found[:max_options], direct_walk
