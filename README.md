# Delhi Lines: bus travel times from the bus fleet itself

**Transport Stack Hackathon 2026, Problem 1: real-time bus traffic model and journey planner.**

Delhi's published bus timetable assumes every bus moves at exactly **15.0 km/h on every road at every hour**. We use the GPS traces of 6,000+ DTC buses as moving sensors instead. That gives about 3 million measured stop-to-stop trips a day, which feed a model of how long each stretch of road takes, by day type and half hour. A journey planner on top of it returns bus journeys with walking, waiting, changes, an 80% time range, and how much faster the same trip would be at the quietest hour.

## Results on a held-out week

We trained on GPS from 1–24 October and tested on **17,885 real bus trips** on 964 routes from 25–31 October. Each trip is reconstructed from GPS: a bus leaving stop A and reaching stop B. The table compares ride time on the bus.

| Method | Mean error | Median error | Within 20% of actual |
| --- | ---: | ---: | ---: |
| Published timetable | 10.2 min | 6.7 min | 35% |
| **Our model (historical)** | **6.7 min** | **2.8 min** | **68%** |
| **Our model + live** | **6.4 min** | **2.6 min** | **71%** |

* The quoted "likely" range contains the real trip **78%** of the time (target 80%).
* The model tracks the day: real buses take 3.2 min/km early morning and 5.0 min/km in the evening peak, while the timetable says 4.0 min/km at all hours.
* The data confirmed two weekday holidays: 2 Oct and 20 Oct had Sunday-like peak speeds, so they are treated as Sundays.

Full report: [docs/VALIDATION.md](docs/VALIDATION.md). Per-trip rows: `artifacts/validation_trips.csv`.



## Run it

Python 3.10+.

```powershell
pip install -r requirements.txt
python -m transit.api
```

Open http://127.0.0.1:8000 for the planner, or http://127.0.0.1:8000/docs for the interactive API documentation. The trained model ships in `artifacts/`, so no data download is needed to run the planner.

```powershell
python -m pytest tests -q
```

## API

| Endpoint | What it returns |
| --- | --- |
| `GET /api/journeys?from=Kashmere Gate ISBT&to=Nehru Place&depart=2026-10-27T08:30` | Up to 3 options. Each has the walk to the first stop, bus legs (boarding stop, route, alighting stop, wait, ride time and 80% range, timetable time, stop list, map path), changes, the walk to the destination, the total, the quiet-hour comparison, and evidence confidence. `from`/`to` accept stop names (fuzzy) or `lat,lon`. Add `live=true` to blend the last 30 minutes of bus movements. |
| `GET /api/journeys/profile?from=…&to=…` | Travel time of the best route at every half hour, for weekdays, Saturdays and Sundays, with ranges |
| `GET /api/segments?day_type=weekday&time=18:00` | Modelled speed on every observed road stretch (the "network speeds" map) |
| `GET /api/stops?q=nehru` | Stop search |
| `GET /api/model` | Training coverage, calibration, validation summary, live feed status |

Example response (trimmed):

```json
{"options": [{"summary": "425UP", "total_minutes": 54, "total_range_minutes": [46, 63],
  "in_vehicle_minutes": 45.2, "timetable_in_vehicle_minutes": 62.3,
  "quiet_hour": {"time": "06:00", "in_vehicle_minutes": 36.1, "traffic_delay_minutes": 9.1},
  "evidence": {"confidence": "high"},
  "legs": [{"type": "walk", "to_stop": "ISBT Kashmere Gate Terminal", "minutes": 3.2},
           {"type": "bus", "route": "425UP", "board_stop": "…", "alight_stop": "Nehru Place",
            "wait_minutes": 1.8, "ride_minutes": 45.2, "ride_range_minutes": [38.0, 53.1], "stops": 27},
           {"type": "walk", "minutes": 3.4}]}]}
```

## How it works

Full details are in [docs/TECHNICAL_NOTE.md](docs/TECHNICAL_NOTE.md).

1. **GPS to roads, without trusting route labels.** The October GPS feed numbers routes differently from the public GTFS: only ~21% of rows match. We time each bus passing each stop, using the closest approach of the GPS track to the stop, interpolated between pings. Consecutive stop visits that form a known directed GTFS segment become one measured trip on that stretch. Time spent at a stop is measured separately as dwell.
2. **Travel time per stretch.** Each stretch gets a median running time per weekday, Saturday or Sunday/holiday, and half hour. Sparse stretches borrow, in order, from the same stretch at other hours, nearby roads, and the city average, blended by how much evidence there is. Every answer says which.
3. **Journeys.** RAPTOR routing over a timetable re-timed by the model. Each bus is rolled forward along its route at the speeds of the hour it reaches each stretch.
4. **Live and historical.** Recent buses on a stretch scale its prediction. The effect fades with how far ahead the bus will get there, and falls back to history when no bus has been seen.

## Rebuild the model from raw data

```powershell
python -m transit.gtfs data/delhi-bus-gtfs.zip --out build/network.pkl
python scripts/fetch_and_ingest.py --days 1-31 --workers 3
python scripts/train.py --train 1-24 --test 25-31 --replay-days 27,30
```

The first command uses the [archived Delhi GTFS](https://ethanc8.github.io/delhi-gtfs/bus.zip), or the current feed from Transport Stack if you have it. The second streams each day out of the 16 GB archive with HTTP range requests (~500 MB per day), map-matches it in ~2 minutes and deletes the raw file, taking ~35 minutes in total. The third fits the model, calibrates it, scores it on the held-out week and writes `artifacts/` and the validation tables.

To compare with Google Maps (needs a Routes API key):

```powershell
$env:GOOGLE_MAPS_API_KEY="..."; python validation/google_compare.py --n 80
```

## Live data

By default, "live" replays a held-out October day: only buses that finished a stretch before your departure time are visible. Point it at a real GTFS-Realtime VehiclePositions feed with:

```powershell
$env:TRANSIT_RT_URL="https://…/VehiclePositions.pb?key=…"; python -m transit.api
```

## Repository

| Path | Contents |
| --- | --- |
| `transit/` | `gtfs` network, `ingest` map matching, `model` travel times, `router` RAPTOR, `live` blending, `service` and `api` |
| `scripts/` | Archive download, batch ingest, training and validation |
| `web/` | Planner UI (Leaflet + OpenStreetMap) |
| `artifacts/` | Trained model, network, replay days, validation results |
| `docs/` | Technical note and validation report |
| `archive/p2/` | Earlier Problem 2 work (ticket-based crowding pilot), kept for reference |
