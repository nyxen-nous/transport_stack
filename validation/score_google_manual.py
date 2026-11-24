"""Score the hand-collected Google Maps times in validation/google_manual.csv.

For each of the 20 held-out trips, open the link, choose Transit -> Bus and
"Depart at" the listed weekday and time, then type into the sheet:
  google_bus_min    minutes on the bus (sum of bus legs, no walking or waiting)
  google_total_min  Google's door-to-door total (optional)
  google_routes     the route numbers Google suggests (optional)

Then run:  python validation/score_google_manual.py
It prints the comparison and writes docs/GOOGLE_COMPARISON.md.
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / 'validation' / 'google_manual.csv'
OUT = ROOT / 'docs' / 'GOOGLE_COMPARISON.md'


def main():
    rows = [r for r in csv.DictReader(open(SHEET, encoding='utf-8')) if r['google_bus_min'].strip()]
    if not rows:
        raise SystemExit('Fill google_bus_min in validation/google_manual.csv first.')
    obs = [float(r['observed_min']) for r in rows]
    methods = {'Google Maps': 'google_bus_min', 'Timetable (GTFS)': 'timetable_min',
               'Our model (historical)': 'model_min', 'Our model + live': 'live_min'}
    lines = ['# Google Maps comparison', '',
             f'{len(rows)} held-out trips (Oct 25-31). "Real" is the time the actual bus took, measured from GPS. '
             'Google was asked for the same origin, destination, weekday and departure time in the following week, '
             'because it does not answer for past dates. All values are minutes on the bus.', '',
             '| Method | Mean abs. error | Mean % error | Within 20% | Bias |', '| --- | ---: | ---: | ---: | ---: |']
    for name, col in methods.items():
        pred = [float(r[col]) for r in rows]
        err = [p - o for p, o in zip(pred, obs)]
        mae = sum(abs(e) for e in err) / len(err)
        mape = 100 * sum(abs(e) / o for e, o in zip(err, obs)) / len(err)
        w20 = 100 * sum(abs(e) / o <= 0.2 for e, o in zip(err, obs)) / len(err)
        bias = sum(err) / len(err)
        lines.append(f'| {name} | {mae:.1f} | {mape:.0f}% | {w20:.0f}% | {bias:+.1f} |')
    lines += ['', '| # | Time | Real bus | From -> To | Real | Google | Ours | Timetable |',
              '| ---: | --- | --- | --- | ---: | ---: | ---: | ---: |']
    for r in rows:
        lines.append(f"| {r['n']} | {r['google_weekday'][:3]} {r['depart_time']} | {r['route_real_bus']} | "
                     f"{r['from_stop']} -> {r['to_stop']} | {r['observed_min']} | {r['google_bus_min']} | "
                     f"{r['model_min']} | {r['timetable_min']} |")
    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines[:10]))
    print(f'\nWrote {OUT}')


if __name__ == '__main__':
    main()
