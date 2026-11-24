"""Fit the travel-time model, calibrate it, validate it on the held-out week, write artifacts.

    python scripts/train.py                      # train Oct 1-24, validate Oct 25-31
    python scripts/train.py --train 1-24 --test 25-31 --samples 1500

Writes artifacts/model.npz, artifacts/network.pkl, artifacts/validation.json,
artifacts/validation_trips.csv and docs/VALIDATION.md.
"""
import argparse
import csv
import json
import math
import shutil
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transit.evaluate import (PERIODS, local_of, metrics, observed_trips, ride_seconds,  # noqa: E402
                              sample_od)
from transit.gtfs import Network  # noqa: E402
from transit.live import ReplayFeed  # noqa: E402
from transit.model import DAYTYPES, Z80, daytype_of, fit  # noqa: E402


def days(spec):
    out = []
    for part in str(spec).split(','):
        lo, _, hi = part.partition('-')
        out += range(int(lo), int(hi or lo) + 1)
    return out


def collect(net, obs_dir, day_list, ods, rng, max_per_od):
    trips = []
    for d in day_list:
        v = obs_dir / f'2026_10_{d:02}_visits.npz'
        if v.exists():
            trips += observed_trips(net, v, ods, max_per_od=max_per_od, rng=rng)
    return trips


def predict(net, model, trips, live=None):
    out = []
    for pid, i, j, dep, ride in sorted(trips, key=lambda x: x[3]):
        d, sec = local_of(dep)
        dt = daytype_of(d)
        lv = None
        if live is not None:
            from datetime import datetime, timedelta
            when = datetime(d.year, d.month, d.day) + timedelta(seconds=int(sec))
            if live.set_clock(when):
                lv = live
        pred, sd, _ = ride_seconds(net, model, pid, i, j, sec, dt, lv, sec)
        p = net.patterns[pid]
        sched = float(p.sched_offsets[j] - p.sched_offsets[i])
        out.append(dict(pid=pid, route=p.route_name, i=i, j=j, km=round(float(p.cum[j] - p.cum[i]) / 1000, 2),
                        date=d.isoformat(), depart=f'{sec // 3600:02}:{sec % 3600 // 60:02}', hour=sec / 3600,
                        day_type=DAYTYPES[dt], observed=ride, model=pred, sd=sd, timetable=sched))
    return out


def write_markdown(report, path):
    names = {'timetable': 'Timetable (GTFS)', 'model': 'Our model, historical', 'live': 'Our model + live'}
    def table(block):
        rows = ['| Method | Trips | Mean abs. error | Median abs. error | Mean % error | Within 20% | Within 5 min | Bias |',
                '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
        for k in ('timetable', 'model', 'live'):
            m = block[k]
            rows.append(f"| {names[k]} | {m['n']} | {m['mae_min']} min | {m['median_ae_min']} min | {m['mape_pct']}% | "
                        f"{m['within_20pct']}% | {m['within_5min']}% | {m['bias_min']:+} min |")
        return '\n'.join(rows)
    def short(block):
        return ' / '.join(f"{block[k]['mae_min']}" for k in ('timetable', 'model', 'live'))
    o = report['overall']
    dq = report['data_quality']
    lines = [
        '# Validation report', '',
        report['method'], '',
        f"**{report['trips']} real bus trips on {report['routes']} routes**, ride time only (from leaving the boarding stop to "
        "reaching the alighting stop). All methods are scored on exactly the same trips. The live variant only sees GPS "
        "that ended before each trip's departure.", '',
        '## Overall', '', table(o), '',
        f"80% range coverage: **{report['range_coverage_pct']}%** of real trips fall inside the range the API reports "
        "(target 80%).", '',
        '## By time of day (mean absolute error, minutes: timetable / model / live)', '',
        '| Period | Trips | MAE |', '| --- | ---: | --- |',
        *[f"| {k} | {v['model']['n']} | {short(v)} |" for k, v in report['by_period'].items()], '',
        '## Does the model capture congestion? Minutes per km on the bus', '',
        '| Period | Real buses | Our model | Timetable |', '| --- | ---: | ---: | ---: |',
        *[f"| {k} | {v['observed']} | {v['model']} | {v['timetable']} |" for k, v in report['minutes_per_km_by_period'].items()], '',
        '## By day type and trip length (MAE, minutes: timetable / model / live)', '',
        '| Group | Trips | MAE |', '| --- | ---: | --- |',
        *[f"| {k} | {v['model']['n']} | {short(v)} |" for k, v in {**report['by_day_type'], **report['by_distance']}.items()], '',
        '## Data quality (all processed days)', '',
        f"* {dq['raw_pings']:,} raw GPS pings over {dq['days']} days; {dq['duplicate_share_pct']}% were exact duplicates.",
        f"* {dq['gps_jumps_removed']:,} GPS spikes and {dq['outside_delhi']:,} points outside Delhi removed.",
        f"* {dq['segment_observations']:,} stop-to-stop measurements kept; {dq['implausible_share_pct']}% rejected as implausible (<2 or >80 km/h).",
        f"* About {dq['buses_per_day']:,} buses a day; {dq['segments_covered_per_day']:,} of {dq['segments_total']:,} GTFS segments measured on a typical day.", '',
        '## Morning-peak speed by day (holiday check)', '',
        '| Date | Day | 08–11 median speed | Treated as |', '| --- | --- | ---: | --- |',
        *[f"| {k} | {v['weekday']} | {v['am_peak_kmh']} km/h | {v['model_day_type']} |" for k, v in report['am_peak_speed_by_day'].items()], '',
        '## Google Maps', '',
        'Run `validation/google_compare.py` with a Routes API key. It queries Google for a sample of these same trips at the '
        'same weekday and time and writes `artifacts/google_comparison.json` (Google vs real vs ours, single-bus trips only).', '',
        'Per-trip rows: `artifacts/validation_trips.csv`.',
    ]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--obs', type=Path, default=ROOT / 'build' / 'obs')
    ap.add_argument('--network', type=Path, default=ROOT / 'build' / 'network.pkl')
    ap.add_argument('--train', default='1-24')
    ap.add_argument('--test', default='25-31')
    ap.add_argument('--samples', type=int, default=1500, help='route O-D pairs to look for')
    ap.add_argument('--out', type=Path, default=ROOT / 'artifacts')
    ap.add_argument('--replay-days', default='27', help='held-out days shipped for the live demo')
    a = ap.parse_args()
    rng = np.random.default_rng(7)
    net = Network.load(a.network)
    train = [d for d in days(a.train) if (a.obs / f'2026_10_{d:02}.npz').exists()]
    test = [d for d in days(a.test) if (a.obs / f'2026_10_{d:02}.npz').exists()]
    print('train days', train, 'test days', test, flush=True)
    t = time.time()
    model = fit(net, [a.obs / f'2026_10_{d:02}.npz' for d in train])
    model.meta['train_days'] = [f'2026-10-{d:02}' for d in train]
    print(f'fit {time.time() - t:.0f}s', flush=True)

    # ---- calibration on the last training days (2 numbers: scale and correlation) ----
    ods = sample_od(net, rng, a.samples)
    cal_days = train[-4:]
    cal = predict(net, model, collect(net, a.obs, cal_days, ods, rng, 3))
    if cal:
        ratio = np.array([c['observed'] / c['model'] for c in cal])
        model.calib = float(np.exp(np.median(np.log(ratio))))
        for c in cal:
            c['model'] *= model.calib
            c['sd'] *= model.calib
        best = None
        cache = [ride_seconds(net, model, c['pid'], c['i'], c['j'], c['hour'] * 3600,
                              DAYTYPES.index(c['day_type']))[2] for c in cal]
        for rho in np.linspace(0, 1, 21):
            cover = []
            for c, sds in zip(cal, cache):
                sd = math.sqrt(rho * sds.sum() ** 2 + (1 - rho) * (sds ** 2).sum())
                cover.append(abs(c['observed'] - c['model']) <= Z80 * sd)
            gap = abs(np.mean(cover) - 0.8)
            if best is None or gap < best[0]:
                best = (gap, float(rho), float(np.mean(cover)))
        model.rho = best[1]
        print(f'calibration: scale {model.calib:.3f}, rho {model.rho:.2f} (cal coverage {best[2]:.2f}) on {len(cal)} trips', flush=True)

    a.out.mkdir(parents=True, exist_ok=True)
    model.save(a.out / 'model.npz')
    shutil.copy(a.network, a.out / 'network.pkl')

    # ---- held-out validation ----
    replay_dir = a.out / 'replay_eval'
    replay_dir.mkdir(exist_ok=True)
    for d in test:
        src = a.obs / f'2026_10_{d:02}.npz'
        z = np.load(src, allow_pickle=True)
        np.savez(replay_dir / src.name, seg=z['seg'], t0=z['t0'], run=z['run'])
    live = ReplayFeed(net, model, replay_dir)
    live.reuse = 300
    test_trips = collect(net, a.obs, test, ods, rng, 2)
    print('held-out trips', len(test_trips), flush=True)
    hist = predict(net, model, test_trips)
    print('historical scored', flush=True)
    lv = predict(net, model, test_trips, live)
    for h, l in zip(hist, lv):
        h['live'] = l['model']
    rows = hist
    obs = [r['observed'] for r in rows]
    report = dict(
        method='Train on GPS from ' + f'Oct {train[0]}-{train[-1]}' + ', test on real bus trips reconstructed from GPS on ' +
               f'Oct {test[0]}-{test[-1]}' + '. Trips: one bus departing stop A and arriving at stop B of the same route, '
               'passing at least 60% of the stops between. Ride time only (no walking or waiting).',
        trips=len(rows), routes=len({r['route'] for r in rows}),
        overall={k: metrics(obs, [r[k] for r in rows]) for k in ('timetable', 'model', 'live')},
        by_period={}, by_day_type={}, by_distance={},
        range_coverage_pct=round(100 * float(np.mean([abs(r['observed'] - r['model']) <= Z80 * r['sd'] for r in rows])), 1),
        calibration=dict(scale=model.calib, rho=model.rho),
        peak_vs_night=None,
    )
    for name, lo, hi in PERIODS:
        sel = [r for r in rows if lo <= r['hour'] < hi]
        if len(sel) >= 20:
            report['by_period'][name] = {k: metrics([r['observed'] for r in sel], [r[k] for r in sel])
                                         for k in ('timetable', 'model', 'live')}
    for dtn in DAYTYPES:
        sel = [r for r in rows if r['day_type'] == dtn]
        if len(sel) >= 20:
            report['by_day_type'][dtn] = {k: metrics([r['observed'] for r in sel], [r[k] for r in sel])
                                          for k in ('timetable', 'model', 'live')}
    for name, lo, hi in [('3-6 km', 3, 6), ('6-10 km', 6, 10), ('10-18 km', 10, 18)]:
        sel = [r for r in rows if lo <= r['km'] < hi]
        if len(sel) >= 20:
            report['by_distance'][name] = {k: metrics([r['observed'] for r in sel], [r[k] for r in sel])
                                           for k in ('timetable', 'model', 'live')}
    # does the model capture the time-of-day effect? observed minutes per km by period
    report['minutes_per_km_by_period'] = {
        name: dict(observed=round(float(np.mean([r['observed'] / 60 / r['km'] for r in sel])), 2),
                   model=round(float(np.mean([r['model'] / 60 / r['km'] for r in sel])), 2),
                   timetable=round(float(np.mean([r['timetable'] / 60 / r['km'] for r in sel])), 2))
        for name, lo, hi in PERIODS if len(sel := [r for r in rows if lo <= r['hour'] < hi]) >= 20}
    # ---- data quality and a data-driven holiday check -------------------------
    dq, day_speed = {}, {}
    for d in train + test:
        z = np.load(a.obs / f'2026_10_{d:02}.npz', allow_pickle=True)
        st = z['stats'][0]
        for k in ('raw', 'duplicates', 'gps_jumps', 'outside_bbox', 'segment_obs', 'segment_obs_implausible',
                  'vehicles', 'segments_covered'):
            dq.setdefault(k, []).append(st.get(k, 0))
        sec = (z['t0'] + 19800) % 86400
        m = (sec >= 8 * 3600) & (sec < 11 * 3600)
        day_speed[f'2026-10-{d:02}'] = dict(
            weekday=date(2026, 10, d).strftime('%a'),
            am_peak_kmh=round(float(np.median(net.seg_len[z['seg'][m]] / z['run'][m])) * 3.6, 1),
            model_day_type=DAYTYPES[daytype_of(date(2026, 10, d))])
    report['data_quality'] = dict(
        days=len(dq['raw']), raw_pings=int(sum(dq['raw'])),
        duplicate_share_pct=round(100 * sum(dq['duplicates']) / sum(dq['raw']), 1),
        gps_jumps_removed=int(sum(dq['gps_jumps'])), outside_delhi=int(sum(dq['outside_bbox'])),
        segment_observations=int(sum(dq['segment_obs'])),
        implausible_share_pct=round(100 * sum(dq['segment_obs_implausible']) /
                                    max(1, sum(dq['segment_obs']) + sum(dq['segment_obs_implausible'])), 1),
        buses_per_day=int(np.median(dq['vehicles'])),
        segments_covered_per_day=int(np.median(dq['segments_covered'])), segments_total=int(net.n_segs))
    report['am_peak_speed_by_day'] = day_speed
    report['model_coverage'] = {k: v for k, v in model.meta.items() if k.startswith('cells_') or k.startswith('segments')}
    (a.out / 'validation.json').write_text(json.dumps(report, indent=1))
    with open(a.out / 'validation_trips.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['date', 'depart', 'day_type', 'route', 'from_stop', 'to_stop', 'from_lat', 'from_lon', 'to_lat',
                    'to_lon', 'km', 'observed_min',
                    'model_min', 'live_min', 'timetable_min', 'model_p10_min', 'model_p90_min'])
        for r in rows:
            p = net.patterns[r['pid']]
            w.writerow([r['date'], r['depart'], r['day_type'], r['route'], net.stop_names[p.stops[r['i']]],
                        net.stop_names[p.stops[r['j']]], round(float(net.lat[p.stops[r['i']]]), 6),
                        round(float(net.lon[p.stops[r['i']]]), 6), round(float(net.lat[p.stops[r['j']]]), 6),
                        round(float(net.lon[p.stops[r['j']]]), 6), r['km'], round(r['observed'] / 60, 1), round(r['model'] / 60, 1),
                        round(r['live'] / 60, 1), round(r['timetable'] / 60, 1),
                        round((r['model'] - Z80 * r['sd']) / 60, 1), round((r['model'] + Z80 * r['sd']) / 60, 1)])
    shutil.rmtree(replay_dir)
    # ship one or more held-out days, slimmed, for the live replay demo
    rdir = a.out / 'replay'
    rdir.mkdir(exist_ok=True)
    for d in days(a.replay_days):
        src = a.obs / f'2026_10_{d:02}.npz'
        if src.exists():
            z = np.load(src, allow_pickle=True)
            np.savez_compressed(rdir / src.name, seg=z['seg'].astype(np.int32), t0=z['t0'].astype(np.int64),
                                run=z['run'].astype(np.float32))
    (ROOT / 'docs').mkdir(exist_ok=True)
    write_markdown(report, ROOT / 'docs' / 'VALIDATION.md')
    print(json.dumps(report['overall'], indent=1))
    print('range coverage', report['range_coverage_pct'])


if __name__ == '__main__':
    main()
