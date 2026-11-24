"""Static GTFS loading: stops, route patterns, directed stop-to-stop segments.

A *pattern* is one ordered stop sequence of one route. A *segment* is a directed
pair of consecutive stops (a -> b). Segments are shared between every route that
drives that stretch, which is what lets sparse routes borrow speed evidence from
busy ones on the same road.
"""
from __future__ import annotations

import csv
import io
import pickle
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

EARTH = 6371000.0


def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    h = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH * np.arcsin(np.sqrt(h))


def hms(t: str) -> int:
    h, m, s = t.split(':')
    return int(h) * 3600 + int(m) * 60 + int(s)


@dataclass
class Pattern:
    pid: int
    route_id: str
    route_name: str
    stops: np.ndarray            # stop indices, in order
    segs: np.ndarray             # segment ids, len(stops)-1
    cum: np.ndarray              # metres along the pattern at each stop
    trips: list = field(default_factory=list)       # trip ids sorted by first departure
    first_dep: np.ndarray = None                     # seconds after midnight, sorted
    sched_offsets: np.ndarray = None                 # median scheduled offset per stop


@dataclass
class Network:
    stop_ids: list
    stop_names: list
    lat: np.ndarray
    lon: np.ndarray
    routes: dict                  # route_id -> name
    patterns: list
    seg_from: np.ndarray
    seg_to: np.ndarray
    seg_len: np.ndarray
    route_patterns: dict          # route_id -> [pid]
    stop_patterns: list           # stop idx -> [(pid, position)]
    source: str = ''

    @property
    def n_segs(self):
        return len(self.seg_from)

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'wb') as f:
            pickle.dump(self, f, protocol=pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def load(path: Path) -> 'Network':
        with open(path, 'rb') as f:
            return pickle.load(f)


def _reader(z: zipfile.ZipFile | Path, name: str):
    if isinstance(z, Path):
        f = open(z / name, encoding='utf-8-sig', newline='')
    else:
        f = io.TextIOWrapper(z.open(name), encoding='utf-8-sig', newline='')
    with f:
        yield from csv.DictReader(f)


def build_network(feed: Path) -> Network:
    src = zipfile.ZipFile(feed) if feed.suffix == '.zip' else feed
    stop_ids, names, lat, lon = [], [], [], []
    for r in _reader(src, 'stops.txt'):
        stop_ids.append(r['stop_id'])
        names.append(r['stop_name'].strip())
        lat.append(float(r['stop_lat']))
        lon.append(float(r['stop_lon']))
    sidx = {s: i for i, s in enumerate(stop_ids)}
    lat, lon = np.array(lat), np.array(lon)
    routes = {r['route_id']: (r.get('route_short_name') or r.get('route_long_name') or r['route_id']).strip()
              for r in _reader(src, 'routes.txt')}
    trip_route = {r['trip_id']: r['route_id'] for r in _reader(src, 'trips.txt')}

    # stop_times is ~3.7M rows: stream it once, grouping by trip.
    seqs: dict[str, list] = defaultdict(list)
    for r in _reader(src, 'stop_times.txt'):
        seqs[r['trip_id']].append((int(r['stop_sequence']), sidx[r['stop_id']], hms(r['departure_time'])))

    pat_key: dict[tuple, int] = {}
    pat_trips: dict[int, list] = defaultdict(list)
    for trip, rows in seqs.items():
        rows.sort()
        stops = tuple(x[1] for x in rows)
        # drop immediate duplicate stops (data glitch) keeping the first
        if len(stops) < 2 or trip not in trip_route:
            continue
        key = (trip_route[trip], stops)
        pid = pat_key.setdefault(key, len(pat_key))
        pat_trips[pid].append((rows[0][2], trip, np.array([x[2] for x in rows], dtype=np.int32)))

    seg_key: dict[tuple, int] = {}
    patterns = []
    for (route_id, stops), pid in pat_key.items():
        st = np.array(stops, dtype=np.int32)
        segs = np.array([seg_key.setdefault((a, b), len(seg_key)) for a, b in zip(stops, stops[1:])], dtype=np.int32)
        d = haversine(lat[st[:-1]], lon[st[:-1]], lat[st[1:]], lon[st[1:]])
        cum = np.concatenate([[0.0], np.cumsum(d)])
        tr = sorted(pat_trips[pid], key=lambda x: x[0])
        offsets = np.median(np.stack([t[2] - t[0] for t in tr]), axis=0).astype(np.int32)
        patterns.append(Pattern(pid, route_id, routes.get(route_id, route_id), st, segs, cum,
                                trips=[t[1] for t in tr],
                                first_dep=np.array([t[0] for t in tr], dtype=np.int32),
                                sched_offsets=offsets))
    patterns.sort(key=lambda p: p.pid)
    seg_from = np.zeros(len(seg_key), dtype=np.int32)
    seg_to = np.zeros(len(seg_key), dtype=np.int32)
    for (a, b), s in seg_key.items():
        seg_from[s], seg_to[s] = a, b
    seg_len = haversine(lat[seg_from], lon[seg_from], lat[seg_to], lon[seg_to])
    route_patterns = defaultdict(list)
    stop_patterns = [[] for _ in stop_ids]
    for p in patterns:
        route_patterns[p.route_id].append(p.pid)
        for k, s in enumerate(p.stops):
            stop_patterns[s].append((p.pid, k))
    return Network(stop_ids, names, lat, lon, routes, patterns, seg_from, seg_to, seg_len,
                   dict(route_patterns), stop_patterns, source=str(feed))


if __name__ == '__main__':
    import argparse
    import time
    ap = argparse.ArgumentParser()
    ap.add_argument('feed', type=Path)
    ap.add_argument('--out', type=Path, default=Path('build/network.pkl'))
    a = ap.parse_args()
    t = time.time()
    from transit.gtfs import build_network as _build  # pickle classes under their module name
    net = _build(a.feed)
    net.save(a.out)
    print(f'{len(net.stop_ids)} stops, {len(net.routes)} routes, {len(net.patterns)} patterns, '
          f'{net.n_segs} segments, {sum(len(p.trips) for p in net.patterns)} trips in {time.time() - t:.0f}s')
