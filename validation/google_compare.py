"""Compare Google Maps bus times with real GPS trip times and with our model.

Google only answers for present or future departures, so each held-out October
trip is asked for at the same weekday and time in the coming week. We compare
**bus in-vehicle time** (sum of Google's BUS steps) against the time the real
bus took, for trips where Google also suggests a single bus with no change.

    set GOOGLE_MAPS_API_KEY=...         (Routes API enabled)
    python validation/google_compare.py --n 80

Reads artifacts/validation_trips.csv, writes artifacts/google_comparison.csv and
prints the metrics. Uses the Routes API computeRoutes (TRANSIT, BUS only).
"""
import argparse
import csv
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
IST = timezone(timedelta(hours=5, minutes=30))
URL = 'https://routes.googleapis.com/directions/v2:computeRoutes'
FIELDS = 'routes.duration,routes.legs.steps.travelMode,routes.legs.steps.staticDuration,routes.legs.steps.transitDetails.transitLine.nameShort,routes.legs.steps.transitDetails.transitLine.vehicle.type'


def next_same_slot(day: str, hhmm: str) -> datetime:
    d = datetime.fromisoformat(day).date()
    h, m = map(int, hhmm.split(':'))
    now = datetime.now(IST)
    t = datetime(now.year, now.month, now.day, h, m, tzinfo=IST)
    t += timedelta(days=(d.weekday() - t.weekday()) % 7)
    if t <= now + timedelta(minutes=10):
        t += timedelta(days=7)
    return t


def ask(key, row, when):
    body = {
        'origin': {'location': {'latLng': {'latitude': float(row['from_lat']), 'longitude': float(row['from_lon'])}}},
        'destination': {'location': {'latLng': {'latitude': float(row['to_lat']), 'longitude': float(row['to_lon'])}}},
        'travelMode': 'TRANSIT', 'departureTime': when.isoformat(),
        'transitPreferences': {'allowedTravelModes': ['BUS']}, 'computeAlternativeRoutes': False,
    }
    r = requests.post(URL, json=body, timeout=30, headers={'X-Goog-Api-Key': key, 'X-Goog-FieldMask': FIELDS})
    r.raise_for_status()
    routes = r.json().get('routes') or []
    if not routes:
        return None
    steps = [s for leg in routes[0]['legs'] for s in leg.get('steps', [])]
    bus = [s for s in steps if s.get('travelMode') == 'TRANSIT']
    secs = lambda d: float(d.rstrip('s')) if d else 0.0
    return dict(total_min=round(secs(routes[0]['duration']) / 60, 1),
                bus_min=round(sum(secs(s.get('staticDuration')) for s in bus) / 60, 1),
                bus_legs=len(bus),
                lines='/'.join(s.get('transitDetails', {}).get('transitLine', {}).get('nameShort', '?') for s in bus))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--trips', type=Path, default=ROOT / 'artifacts' / 'validation_trips.csv')
    ap.add_argument('--out', type=Path, default=ROOT / 'artifacts' / 'google_comparison.csv')
    ap.add_argument('--n', type=int, default=80)
    a = ap.parse_args()
    key = os.environ.get('GOOGLE_MAPS_API_KEY')
    if not key:
        sys.exit('Set GOOGLE_MAPS_API_KEY (Routes API) first.')
    rows = list(csv.DictReader(open(a.trips, encoding='utf-8')))
    random.Random(3).shuffle(rows)
    out = []
    for row in rows[:a.n]:
        when = next_same_slot(row['date'], row['depart'])
        try:
            g = ask(key, row, when)
        except requests.RequestException as e:
            print('error', e)
            continue
        if g:
            out.append({**row, 'google_when': when.isoformat(), **{'google_' + k: v for k, v in g.items()}})
        time.sleep(0.2)
    with open(a.out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    single = [r for r in out if r['google_bus_legs'] == 1]
    def mae(k):
        return round(sum(abs(float(r[k]) - float(r['observed_min'])) for r in single) / len(single), 2)
    def mape(k):
        return round(100 * sum(abs(float(r[k]) - float(r['observed_min'])) / float(r['observed_min']) for r in single) / len(single), 1)
    summary = {k: dict(mae_min=mae(c), mape_pct=mape(c)) for k, c in
               [('google', 'google_bus_min'), ('our_model', 'model_min'), ('our_live', 'live_min'), ('timetable', 'timetable_min')]}
    summary['trips_single_bus'] = len(single)
    summary['trips_queried'] = len(out)
    (a.out.with_suffix('.json')).write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
