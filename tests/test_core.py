"""Core tests on a synthetic 2-route network and simulated GPS. Run: python -m pytest tests -q"""
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transit.gtfs import build_network  # noqa: E402
from transit.ingest import StopGrid, clean, link_segments, stop_passes, to_xy, visits  # noqa: E402
from transit.model import BINS, fit  # noqa: E402
from transit.router import Planner  # noqa: E402

# Route A runs east along latitude 28.60 (stops every ~490 m), route B runs north
# from A's 4th stop. Changing from A to B at that stop is the only way north.
LAT0, LON0 = 28.60, 77.20
D_LON, D_LAT = 0.005, 0.0045


@pytest.fixture(scope='module')
def net(tmp_path_factory):
    d = tmp_path_factory.mktemp('gtfs')
    stops = [(f'a{k}', LAT0, LON0 + k * D_LON) for k in range(6)] + \
            [(f'b{k}', LAT0 + k * D_LAT, LON0 + 3 * D_LON + 0.0002) for k in range(1, 5)]
    (d / 'stops.txt').write_text('stop_id,stop_name,stop_lat,stop_lon\n' +
                                 ''.join(f'{s},{s.upper()} stop,{la},{lo}\n' for s, la, lo in stops))
    (d / 'routes.txt').write_text('route_id,route_short_name,route_long_name,route_type\nA,A,,3\nB,B,,3\n')
    trips, times = ['route_id,service_id,trip_id'], ['trip_id,arrival_time,departure_time,stop_id,stop_sequence']
    for h in range(5, 23):
        for m in (0, 20, 40):
            for route, seq in (('A', [f'a{k}' for k in range(6)]), ('B', ['a3'] + [f'b{k}' for k in range(1, 5)])):
                tid = f'{route}_{h}_{m}'
                trips.append(f'{route},1,{tid}')
                for n, s in enumerate(seq):
                    t = h * 3600 + m * 60 + n * 120
                    hh = f'{t // 3600:02}:{t % 3600 // 60:02}:{t % 60:02}'
                    times.append(f'{tid},{hh},{hh},{s},{n}')
    (d / 'trips.txt').write_text('\n'.join(trips) + '\n')
    (d / 'stop_times.txt').write_text('\n'.join(times) + '\n')
    return build_network(d)


def simulate(net, speed_ms, start_unix, dwell=30, ping=10, vehicle=0, noise=4.0, rng=None):
    """A bus driving route A end to end at speed_ms with dwell at each stop."""
    rng = rng or np.random.default_rng(0)
    p = next(p for p in net.patterns if p.route_id == 'A')
    lat, lon = net.lat[p.stops], net.lon[p.stops]
    pts, t = [], float(start_unix)
    for k in range(len(lat) - 1):
        for _ in range(int(dwell / ping)):
            pts.append((t, lat[k], lon[k]))
            t += ping
        dist = p.cum[k + 1] - p.cum[k]
        steps = max(1, int(dist / speed_ms / ping))
        for s in range(steps):
            f = s / steps
            pts.append((t, lat[k] + f * (lat[k + 1] - lat[k]), lon[k] + f * (lon[k + 1] - lon[k])))
            t += ping
    pts.append((t, lat[-1], lon[-1]))
    ts = np.array([x[0] for x in pts], np.int64)
    la = np.array([x[1] for x in pts]) + rng.normal(0, noise / 110540, len(pts))
    lo = np.array([x[2] for x in pts]) + rng.normal(0, noise / 97700, len(pts))
    return np.full(len(ts), vehicle, np.int32), ts, la, lo


def test_network_shapes(net):
    assert len(net.patterns) == 2
    # a3 is shared, segments are directed consecutive pairs: 5 on A + 4 on B
    assert net.n_segs == 9
    assert all(len(p.trips) == 54 for p in net.patterns)


def test_map_matching_recovers_running_and_dwell(net):
    veh, ts, la, lo = simulate(net, speed_ms=6.0, start_unix=1_792_400_000, dwell=40)
    # inject a GPS spike and duplicate rows, which must be removed
    la[50] += 0.05
    veh = np.concatenate([veh, veh[:20]]); ts = np.concatenate([ts, ts[:20]])
    la = np.concatenate([la, la[:20]]); lo = np.concatenate([lo, lo[:20]])
    v, t, x, y, stats = clean(veh, ts, la, lo)
    assert stats['duplicates'] == 20 and stats['gps_jumps'] >= 1
    grid = StopGrid(net)
    pv, ps, pt, _ = stop_passes(grid, v, t, x, y)
    vv, vs, va, vd = visits(pv, ps, pt)
    a_stops = set(next(p for p in net.patterns if p.route_id == 'A').stops.tolist())
    assert a_stops <= set(vs.tolist())
    inner = [vd[k] - va[k] for k in range(len(vs)) if vs[k] in a_stops]
    assert np.median(inner) >= 25, 'dwell at stops should be measured'
    seg, t0, run = link_segments(net, vv, vs, va, vd)
    assert len(seg) == 5
    expected = net.seg_len[seg] / 6.0
    assert np.allclose(run, expected, rtol=0.25, atol=12)


def test_model_learns_time_of_day_and_router_uses_it(net):
    rng = np.random.default_rng(1)
    obs_seg, obs_t0, obs_run = [], [], []
    base = 1_792_434_600  # 2026-10-20 00:00 IST is a holiday; use day +1 (Wed 21 Oct)
    day = base + 86400
    for h in range(5, 23):
        speed = 3.0 if 8 <= h < 11 else 9.0          # congested morning peak
        for rep in range(6):
            for s in range(net.n_segs):
                obs_seg.append(s)
                obs_t0.append(day + h * 3600 + rep * 500)
                obs_run.append(net.seg_len[s] / speed * rng.uniform(0.9, 1.1))
    path = Path(__file__).parent / '_obs_tmp.npz'
    np.savez(path, seg=np.array(obs_seg, np.int32), t0=np.array(obs_t0, np.int64), run=np.array(obs_run, np.float32),
             dw_stop=np.zeros(0, np.int32), dw_t=np.zeros(0, np.int64), dw_s=np.zeros(0, np.float32))
    try:
        model = fit(net, [path])
    finally:
        path.unlink()
    assert model.run_mu.shape == (net.n_segs, 3, BINS)
    planner = Planner(net, model)
    o = (LAT0 + 0.0002, LON0 + 0.0002)
    dest = (LAT0 + 4 * D_LAT, LON0 + 3 * D_LON)
    dtype, t0, found, _ = planner.plan(*o, *dest, datetime(2026, 10, 21, 9, 0))
    assert found, 'should find A then B'
    legs = found[0][3]
    assert [net.patterns[l[1]].route_id for l in legs if l[0] == 'bus'] == ['A', 'B']
    # the same trip at 9:00 (peak) must be slower than at 14:00 (free flow)
    _, _, peak, _ = planner.plan(*o, *dest, datetime(2026, 10, 21, 9, 0))
    _, _, calm, _ = planner.plan(*o, *dest, datetime(2026, 10, 21, 14, 0))
    ride = lambda f, t: f[0][0] - t
    assert ride(peak, 9 * 3600) > ride(calm, 14 * 3600) + 120
