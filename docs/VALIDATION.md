# Validation report

Train on GPS from Oct 1-24, test on real bus trips reconstructed from GPS on Oct 25-31. Trips: one bus departing stop A and arriving at stop B of the same route, passing at least 60% of the stops between. Ride time only (no walking or waiting).

**17885 real bus trips on 964 routes**, ride time only (from leaving the boarding stop to reaching the alighting stop). All methods are scored on exactly the same trips. The live variant only sees GPS that ended before each trip's departure.

## Overall

| Method | Trips | Mean abs. error | Median abs. error | Mean % error | Within 20% | Within 5 min | Bias |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Timetable (GTFS) | 17885 | 10.24 min | 6.72 min | 38.5% | 35.0% | 38.0% | +0.83 min |
| Our model, historical | 17885 | 6.69 min | 2.82 min | 18.7% | 67.7% | 68.1% | -3.3 min |
| Our model + live | 17885 | 6.39 min | 2.6 min | 17.7% | 70.5% | 70.6% | -3.17 min |

80% range coverage: **78.0%** of real trips fall inside the range the API reports (target 80%).

## By time of day (mean absolute error, minutes: timetable / model / live)

| Period | Trips | MAE |
| --- | ---: | --- |
| early 05-08 | 2540 | 12.5 / 5.69 / 5.6 |
| morning peak 08-11 | 4234 | 9.43 / 6.08 / 5.87 |
| midday 11-17 | 5326 | 9.64 / 7.41 / 7.05 |
| evening peak 17-20 | 2686 | 10.43 / 8.16 / 7.58 |
| night 20-24 | 2831 | 10.08 / 6.06 / 5.79 |

## Does the model capture congestion? Minutes per km on the bus

| Period | Real buses | Our model | Timetable |
| --- | ---: | ---: | ---: |
| early 05-08 | 3.23 | 2.76 | 4.0 |
| morning peak 08-11 | 3.89 | 3.43 | 4.0 |
| midday 11-17 | 4.48 | 3.72 | 4.0 |
| evening peak 17-20 | 4.98 | 4.18 | 4.0 |
| night 20-24 | 3.75 | 3.21 | 4.0 |

## By day type and trip length (MAE, minutes: timetable / model / live)

| Group | Trips | MAE |
| --- | ---: | --- |
| weekday | 12760 | 10.16 / 6.69 / 6.42 |
| saturday | 2558 | 10.46 / 7.01 / 6.59 |
| sunday | 2567 | 10.45 / 6.35 / 6.04 |
| 3-6 km | 8481 | 7.7 / 5.62 / 5.4 |
| 6-10 km | 4799 | 10.93 / 7.11 / 6.79 |
| 10-18 km | 4605 | 14.22 / 8.23 / 7.81 |

## Data quality (all processed days)

* 912,877,061 raw GPS pings over 31 days; 38.5% were exact duplicates.
* 34,088 GPS spikes and 851,431 points outside Delhi removed.
* 86,480,607 stop-to-stop measurements kept; 2.0% rejected as implausible (<2 or >80 km/h).
* About 6,221 buses a day; 17,922 of 21,286 GTFS segments measured on a typical day.

## Morning-peak speed by day (holiday check)

| Date | Day | 08–11 median speed | Treated as |
| --- | --- | ---: | --- |
| 2026-10-01 | Thu | 22.5 km/h | weekday |
| 2026-10-02 | Fri | 27.8 km/h | sunday |
| 2026-10-03 | Sat | 24.6 km/h | saturday |
| 2026-10-04 | Sun | 27.0 km/h | sunday |
| 2026-10-05 | Mon | 22.2 km/h | weekday |
| 2026-10-06 | Tue | 22.2 km/h | weekday |
| 2026-10-07 | Wed | 22.5 km/h | weekday |
| 2026-10-08 | Thu | 22.4 km/h | weekday |
| 2026-10-09 | Fri | 22.7 km/h | weekday |
| 2026-10-10 | Sat | 24.7 km/h | saturday |
| 2026-10-11 | Sun | 27.4 km/h | sunday |
| 2026-10-12 | Mon | 21.6 km/h | weekday |
| 2026-10-13 | Tue | 22.1 km/h | weekday |
| 2026-10-14 | Wed | 22.5 km/h | weekday |
| 2026-10-15 | Thu | 22.3 km/h | weekday |
| 2026-10-16 | Fri | 22.5 km/h | weekday |
| 2026-10-17 | Sat | 24.2 km/h | saturday |
| 2026-10-18 | Sun | 26.9 km/h | sunday |
| 2026-10-19 | Mon | 25.2 km/h | weekday |
| 2026-10-20 | Tue | 27.2 km/h | sunday |
| 2026-10-21 | Wed | 23.2 km/h | weekday |
| 2026-10-22 | Thu | 22.7 km/h | weekday |
| 2026-10-23 | Fri | 22.9 km/h | weekday |
| 2026-10-24 | Sat | 23.9 km/h | saturday |
| 2026-10-25 | Sun | 26.4 km/h | sunday |
| 2026-10-26 | Mon | 23.7 km/h | weekday |
| 2026-10-27 | Tue | 21.9 km/h | weekday |
| 2026-10-28 | Wed | 22.0 km/h | weekday |
| 2026-10-29 | Thu | 23.6 km/h | weekday |
| 2026-10-30 | Fri | 21.7 km/h | weekday |
| 2026-10-31 | Sat | 23.2 km/h | saturday |

## Google Maps

Run `validation/google_compare.py` with a Routes API key. It queries Google for a sample of these same trips at the same weekday and time and writes `artifacts/google_comparison.json` (Google vs real vs ours, single-bus trips only).

Per-trip rows: `artifacts/validation_trips.csv`.
