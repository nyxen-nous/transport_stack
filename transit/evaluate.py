"""Ground truth from GPS: real end-to-end bus trips, and how well each method predicts them.

An *observed trip* is one bus that departs stop A and later arrives at stop B of
the same GTFS route pattern, having passed most of the pattern's stops in
between (so it really drove that route). Its ride time is
arrival(B) - departure(A), measured from the GPS stop passes.

Methods compared on identical trips:
  timetable   - GTFS scheduled offsets (what most apps show)
  historical  - our model for that day type and half hour
  live        - historical + the last 30 minutes of the same day (replay, no leakage)
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from .gtfs import Network
from .model import Model, Z80, bin_of, daytype_of

PERIODS = [('early 05-08', 5, 8), ('morning peak 08-11', 8, 11), ('midday 11-17', 11, 17),
           ('evening peak 17-20', 17, 20), ('night 20-24', 20, 24)]


def sample_od(net: Network, rng, n, min_km=3.0, max_km=18.0):
    """Random (pattern, i, j) pairs whose stops are min_km..max_km apart along the route."""
    pats = [p for p in net.patterns if len(p.stops) >= 8 and p.cum[-1] >= min_km * 1000]
    out = []
    while len(out) < n:
        p = pats[rng.integers(len(pats))]
        i = int(rng.integers(0, len(p.stops) - 2))
        ok = np.nonzero((p.cum - p.cum[i] >= min_km * 1000) & (p.cum - p.cum[i] <= max_km * 1000))[0]
        if len(ok):
            out.append((p.pid, i, int(ok[rng.integers(len(ok))])))
    return out


def observed_trips(net: Network, visits_path: Path, ods, min_cover=0.6, max_per_od=6, rng=None):
    z = np.load(visits_path)
    veh, stop, arr, dwell, day0 = z['veh'], z['stop'].astype(np.int64), z['arr'].astype(np.int64), \
        z['dwell'].astype(np.int64), int(z['day0'])
    by_stop = {}
    order = np.argsort(stop, kind='stable')
    st_sorted = stop[order]
    u, starts, counts = np.unique(st_sorted, return_index=True, return_counts=True)
    for s, a, c in zip(u.tolist(), starts.tolist(), counts.tolist()):
        by_stop[s] = np.sort(order[a:a + c])
    trips = []
    for pid, i, j in ods:
        p = net.patterns[pid]
        si, sj = int(p.stops[i]), int(p.stops[j])
        P, Q = by_stop.get(si), by_stop.get(sj)
        if P is None or Q is None:
            continue
        pos_in_pattern = {int(s): n for n, s in enumerate(p.stops[i:j + 1])}
        need = len(pos_in_pattern)
        found = []
        last_b = -1
        for b in Q:
            k = np.searchsorted(P, b) - 1          # the bus's LAST visit to A before reaching B
            if k < 0:
                continue
            a = int(P[k])
            b = int(b)
            if a <= last_b or veh[a] != veh[b]:
                continue
            ride = arr[b] - (arr[a] + dwell[a])
            if ride <= 60 or ride > 3 * 3600:
                continue
            seq_t = arr[a:b + 1]
            if np.max(np.diff(seq_t)) > 600:       # parked, lost signal or out of service
                continue
            idx = [pos_in_pattern[x] for x in stop[a:b + 1].tolist() if x in pos_in_pattern]
            if len(set(idx)) / need < min_cover:
                continue
            steps = np.diff(idx)
            if len(steps) and np.mean(steps >= 0) < 0.9:   # must move along the route in order
                continue
            last_b = b
            found.append((a, b, int(ride)))
        if not found:
            continue
        if rng is not None and len(found) > max_per_od:
            found = [found[x] for x in rng.choice(len(found), max_per_od, replace=False)]
        for a, b, ride in found:
            dep = day0 + int(arr[a] + dwell[a])
            trips.append((pid, i, j, dep, ride))
    return trips


def ride_seconds(net: Network, model: Model, pid, i, j, start_local, daytype, live=None, now_local=None):
    p = net.patterns[pid]
    t = float(start_local)
    sds, total = [], 0.0
    for k in range(i, j):
        s = int(p.segs[k])
        b = int(bin_of(t))
        run = math.exp(model.run_mu[s, daytype, b]) * model.calib
        if live is not None:
            run *= live.factor(s, t, now_local)[0]
        sds.append(run * float(model.run_sigma[s, daytype, b]))
        t += run
        total += run
        if k + 1 < j:
            d = float(model.dwell[p.stops[k + 1], daytype, int(bin_of(t))])
            t += d
            total += d
    sds = np.array(sds)
    sd = math.sqrt(model.rho * sds.sum() ** 2 + (1 - model.rho) * (sds ** 2).sum())
    return total, sd, sds


def local_of(unix):
    t = int(unix) + 19800
    d = date.fromordinal(t // 86400 + date(1970, 1, 1).toordinal())
    return d, t % 86400


def metrics(obs, pred):
    obs, pred = np.asarray(obs, float), np.asarray(pred, float)
    err = pred - obs
    ape = np.abs(err) / obs
    return dict(n=int(len(obs)), mae_min=round(float(np.mean(np.abs(err))) / 60, 2),
                median_ae_min=round(float(np.median(np.abs(err))) / 60, 2),
                mape_pct=round(float(np.mean(ape)) * 100, 1), median_ape_pct=round(float(np.median(ape)) * 100, 1),
                bias_min=round(float(np.mean(err)) / 60, 2),
                within_20pct=round(float(np.mean(ape <= 0.2)) * 100, 1),
                within_5min=round(float(np.mean(np.abs(err) <= 300)) * 100, 1))
