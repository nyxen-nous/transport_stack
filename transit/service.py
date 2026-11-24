"""Glue between the router, the model and live data: builds the JSON the API returns."""
from __future__ import annotations

import difflib
import math
import re
from datetime import datetime, timedelta

import numpy as np

from .gtfs import Network
from .model import BINS, DAYTYPES, Model, Z80, bin_of, daytype_of, IST
from .router import Planner, walk_seconds

LEVEL_NAMES = ('observed on this stretch at this hour', 'observed on this stretch, other hours',
               'nearby roads only', 'city-wide average')
QUIET_WINDOW = (6 * 3600, 22 * 3600 + 1800)


def clock(sec):
    sec = int(round(sec))
    day = sec // 86400
    s = sec % 86400
    return f'{s // 3600:02}:{s % 3600 // 60:02}' + (f' (+{day}d)' if day else '')


def norm(s):
    return ' '.join(re.findall(r'[a-z0-9]+', s.casefold()))


class Service:
    def __init__(self, net: Network, model: Model, live=None):
        self.net, self.model, self.live = net, model, live
        self.planner = Planner(net, model)
        self._names = sorted({n for n in net.stop_names})
        self._norm = {}
        for i, n in enumerate(net.stop_names):
            self._norm.setdefault(norm(n), []).append(i)
        self._norm_keys = list(self._norm)

    # ---- places ---------------------------------------------------------------
    def search_stops(self, q, limit=10):
        nq = norm(q)
        if not nq:
            return []
        words = nq.split()
        hits = [k for k in self._norm_keys if all(w in k for w in words)]
        hits.sort(key=lambda k: (not k.startswith(nq), len(k)))
        if len(hits) < limit:
            for k in difflib.get_close_matches(nq, self._norm_keys, n=limit, cutoff=0.6):
                if k not in hits:
                    hits.append(k)
        out = []
        for k in hits[:limit]:
            idx = self._norm[k]
            out.append(dict(name=self.net.stop_names[idx[0]], stop_ids=[self.net.stop_ids[i] for i in idx],
                            lat=round(float(np.mean(self.net.lat[idx])), 6),
                            lon=round(float(np.mean(self.net.lon[idx])), 6),
                            routes=len({self.net.patterns[p].route_id for i in idx for p, _ in self.net.stop_patterns[i]})))
        return out

    def resolve(self, text):
        """'28.66,77.22' or a stop name -> (lat, lon, label)."""
        m = re.fullmatch(r'\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*', text or '')
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if not (28.2 < lat < 29.1 and 76.6 < lon < 77.6):
                raise ValueError('Coordinates are outside the Delhi bus network.')
            return lat, lon, f'{lat:.5f}, {lon:.5f}'
        hits = self.search_stops(text, 1)
        if not hits:
            raise ValueError(f'No stop matches "{text}". Try /api/stops?q=...')
        h = hits[0]
        return h['lat'], h['lon'], h['name']

    # ---- model helpers ------------------------------------------------------------
    def ride(self, pid, i, j, start_sec, daytype, live_now=None):
        """In-vehicle seconds from stop i to j departing i at start_sec, with spread."""
        p = self.net.patterns[pid]
        m = self.model
        t = float(start_sec)
        sds, run_total, dwell_total, levels, obs = [], 0.0, 0.0, [], 0.0
        live_used = 0
        for k in range(i, j):
            s = int(p.segs[k])
            b = int(bin_of(t))
            run = math.exp(m.run_mu[s, daytype, b]) * m.calib
            if live_now is not None and self.live is not None:
                f, used = self.live.factor(s, t, live_now)
                run *= f
                live_used += used
            sds.append(run * float(m.run_sigma[s, daytype, b]))
            levels.append(int(m.run_level[s, daytype, b]))
            obs += float(m.run_n[s, daytype, b])
            t += run
            run_total += run
            if k + 1 < j:
                d = float(m.dwell[p.stops[k + 1], daytype, int(bin_of(t))])
                t += d
                dwell_total += d
        sds = np.array(sds)
        sd = math.sqrt(m.rho * sds.sum() ** 2 + (1 - m.rho) * (sds ** 2).sum()) if len(sds) else 0.0
        total = run_total + dwell_total
        return dict(seconds=total, running=run_total, dwell=dwell_total, sd=sd, levels=levels,
                    observations=obs, live_segments=live_used)

    def quiet_hour(self, bus_legs, daytype):
        """In-vehicle time of the same legs at every half hour; return the quietest."""
        best = None
        for b in range(QUIET_WINDOW[0] // 1800, QUIET_WINDOW[1] // 1800 + 1):
            t, total = b * 1800, 0.0
            for pid, i, j in bus_legs:
                total += self.ride(pid, i, j, t + total, daytype)['seconds']
            if best is None or total < best[1]:
                best = (b * 1800, total)
        return best

    # ---- journeys -----------------------------------------------------------------
    def journeys(self, origin, destination, when: datetime, max_options=3, live=False):
        o_lat, o_lon, o_label = self.resolve(origin)
        d_lat, d_lon, d_label = self.resolve(destination)
        dtype, t0, found, direct_walk = self.planner.plan(o_lat, o_lon, d_lat, d_lon, when, max_options)
        live_now = t0 if (live and self.live is not None) else None
        if live_now is not None:
            self.live.set_clock(when)
        options = [self._describe(f, t0, dtype, live_now) for f in found]
        options = [o for o in options if o]
        options.sort(key=lambda o: o['arrive_sec'])
        for n, o in enumerate(options):
            o['rank'] = n + 1
        net = self.net
        return dict(
            query=dict(origin=dict(label=o_label, lat=o_lat, lon=o_lon),
                       destination=dict(label=d_label, lat=d_lat, lon=d_lon),
                       depart=when.strftime('%Y-%m-%dT%H:%M'), day_type=DAYTYPES[dtype],
                       mode='live+historical' if live_now is not None else 'historical'),
            walk_only_minutes=round(direct_walk / 60),
            options=options,
            notes=([] if options else ['No bus journey found within 3 bus legs. Try a nearby stop or a different time.']) +
                  ([f'Walking all the way takes about {round(direct_walk / 60)} min, which is faster than the bus.']
                   if options and direct_walk < options[0]['total_minutes'] * 60 else []),
        )

    def _describe(self, found, t0, dtype, live_now):
        arrival, k, key, legs = found
        net, tt = self.net, self.planner.table(dtype)
        out, t = [], float(t0)
        bus_legs = []
        sched_total, model_total, sd2_parts = 0.0, 0.0, []
        evidence = []
        for leg in legs:
            kind = leg[0]
            if kind == 'access':
                s, w = leg[1], leg[2]
                out.append(dict(type='walk', minutes=round(w / 60, 1), to_stop=net.stop_names[s],
                                to=[float(net.lat[s]), float(net.lon[s])], depart=clock(t), arrive=clock(t + w),
                                metres=round(w * 1.2 / 1.3)))
                t += w
            elif kind == 'transfer_walk':
                a, b, w = leg[1], leg[2], leg[3]
                out.append(dict(type='walk', minutes=round(w / 60, 1), from_stop=net.stop_names[a], to_stop=net.stop_names[b],
                                from_=[float(net.lat[a]), float(net.lon[a])], to=[float(net.lat[b]), float(net.lon[b])],
                                depart=clock(t), arrive=clock(t + w), metres=round(w * 1.2 / 1.3), transfer=True))
                t += w
            elif kind == 'bus':
                pid, trip, i, j = leg[1], leg[2], leg[3], leg[4]
                p = net.patterns[pid]
                board = float(tt.dep[pid][i, trip])
                if live_now is not None:
                    # re-time the bus's approach to the boarding stop with live conditions
                    approach = self.ride(pid, 0, i, int(p.first_dep[trip]), dtype, live_now) if i > 0 else None
                    if approach:
                        board = max(board - 600, min(board + 1800, p.first_dep[trip] + approach['seconds']))
                board = max(board, t)
                wait = board - t
                r = self.ride(pid, i, j, board, dtype, live_now)
                sched = float(p.sched_offsets[j] - p.sched_offsets[i])
                sched_total += sched
                model_total += r['seconds']
                sd2_parts.append(r['sd'])
                evidence += r['levels']
                bus_legs.append((pid, i, j))
                stops = p.stops[i:j + 1]
                out.append(dict(
                    type='bus', route=p.route_name, route_id=p.route_id, pattern=[int(pid), int(i), int(j)],
                    board_stop=net.stop_names[p.stops[i]], alight_stop=net.stop_names[p.stops[j]],
                    stops=int(j - i), wait_minutes=round(wait / 60, 1),
                    depart=clock(board), arrive=clock(board + r['seconds']),
                    ride_minutes=round(r['seconds'] / 60, 1),
                    ride_range_minutes=[round(max(r['seconds'] - Z80 * r['sd'], r['seconds'] * 0.6) / 60, 1),
                                        round((r['seconds'] + Z80 * r['sd']) / 60, 1)],
                    running_minutes=round(r['running'] / 60, 1), dwell_minutes=round(r['dwell'] / 60, 1),
                    timetable_minutes=round(sched / 60, 1),
                    distance_km=round(float(p.cum[j] - p.cum[i]) / 1000, 2),
                    live_segments=r['live_segments'],
                    path=[[round(float(net.lat[s]), 6), round(float(net.lon[s]), 6)] for s in stops],
                    stop_names=[net.stop_names[s] for s in stops],
                ))
                t = board + r['seconds']
            elif kind == 'egress':
                s, w = leg[1], leg[2]
                out.append(dict(type='walk', minutes=round(w / 60, 1), from_stop=net.stop_names[s],
                                from_=[float(net.lat[s]), float(net.lon[s])], depart=clock(t), arrive=clock(t + w),
                                metres=round(w * 1.2 / 1.3)))
                t += w
        if not bus_legs:
            return None
        sd = math.sqrt(sum(x * x for x in sd2_parts) + 2 * self.model.rho * sum(
            a * b for n, a in enumerate(sd2_parts) for b in sd2_parts[n + 1:]))
        total = t - t0
        quiet_t, quiet_ride = self.quiet_hour(bus_legs, dtype)
        walk_wait = total - model_total
        lv = np.bincount(np.array(evidence, int), minlength=4) / max(len(evidence), 1)
        confidence = 'high' if lv[0] >= 0.8 else 'medium' if lv[0] + lv[1] >= 0.8 else 'low'
        return dict(
            summary=' → '.join(l['route'] for l in out if l['type'] == 'bus'),
            depart=clock(t0), arrive=clock(t), arrive_sec=int(t),
            total_minutes=round(total / 60),
            total_range_minutes=[round((total - Z80 * sd) / 60), round((total + Z80 * sd) / 60)],
            in_vehicle_minutes=round(model_total / 60, 1),
            walk_and_wait_minutes=round(walk_wait / 60, 1),
            transfers=len(bus_legs) - 1,
            timetable_in_vehicle_minutes=round(sched_total / 60, 1),
            quiet_hour=dict(time=clock(quiet_t), in_vehicle_minutes=round(quiet_ride / 60, 1),
                            total_minutes=round((quiet_ride + walk_wait) / 60),
                            traffic_delay_minutes=round((model_total - quiet_ride) / 60, 1)),
            evidence=dict(confidence=confidence,
                          share_by_level={LEVEL_NAMES[n]: round(float(lv[n]), 3) for n in range(4)}),
            legs=out,
        )

    def profile(self, origin, destination, date_str, route_hint=None):
        """In-vehicle + walk/wait-free time of the best option at every half hour, for 3 day types."""
        when = datetime.fromisoformat(date_str + 'T08:30')
        res = self.journeys(origin, destination, when, 1)
        if not res['options']:
            return dict(points=[], summary=None)
        opt = res['options'][0]
        legs = [tuple(l['pattern']) for l in opt['legs'] if l['type'] == 'bus']
        walk = sum(l['minutes'] for l in opt['legs'] if l['type'] == 'walk')
        series = {}
        for d, name in enumerate(DAYTYPES):
            pts = []
            for b in range(5 * 2, 24 * 2):
                t, total, sd2 = b * 1800, 0.0, 0.0
                for pid, i, j in legs:
                    r = self.ride(pid, i, j, t + total, d)
                    total += r['seconds']
                    sd2 += r['sd'] ** 2
                pts.append(dict(time=clock(b * 1800), minutes=round(total / 60 + walk, 1),
                                low=round((total - Z80 * math.sqrt(sd2)) / 60 + walk, 1),
                                high=round((total + Z80 * math.sqrt(sd2)) / 60 + walk, 1)))
            series[name] = pts
        timetable = opt['timetable_in_vehicle_minutes'] + walk
        return dict(summary=opt['summary'], walk_minutes=round(walk, 1), timetable_minutes=round(timetable, 1),
                    series=series)

    # ---- network view ---------------------------------------------------------------
    def segment_speeds(self, daytype, sec, min_level=1):
        m, net = self.model, self.net
        b = int(bin_of(sec))
        run = np.exp(m.run_mu[:, daytype, b]) * m.calib
        with np.errstate(invalid='ignore', divide='ignore'):
            speed = net.seg_len / run * 3.6
            free = net.seg_len / (np.exp(m.run_mu[:, daytype, :].min(axis=1)) * m.calib) * 3.6
        keep = (m.run_level[:, daytype, b] <= min_level) & (net.seg_len > 60)
        idx = np.nonzero(keep)[0]
        f, t = net.seg_from[idx], net.seg_to[idx]
        return dict(
            day_type=DAYTYPES[daytype], time=clock(b * 1800), count=int(len(idx)),
            segments=[[round(float(net.lat[a]), 5), round(float(net.lon[a]), 5), round(float(net.lat[c]), 5),
                       round(float(net.lon[c]), 5), round(float(sp), 1), round(float(sp / fr), 2)]
                      for a, c, sp, fr in zip(f, t, speed[idx], free[idx])],
            fields=['lat1', 'lon1', 'lat2', 'lon2', 'speed_kmh', 'ratio_to_free_flow'],
        )
