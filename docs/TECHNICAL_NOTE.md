# Technical note: Delhi bus travel-time model and journey planner

Problem 1, Transport Stack Hackathon 2026. This note covers the three questions the brief asks: how GPS points are linked to roads and routes, how travel time is estimated on each stretch, and how live and historical data are combined. It also covers sparse data, waiting at stops, and how we validated the model.

## 1. Data, and what is wrong with it

| Source | What we used it for |
| --- | --- |
| Historical DTC GPS, 1–31 Oct 2026 (31 SQLite files, 124 GB unpacked) | Training (1–24 Oct) and held-out testing (25–31 Oct) |
| Static GTFS (stops, routes, trips, stop_times) | The road network of stop-to-stop segments, route patterns, first-stop departures |
| GTFS-Realtime vehicle positions | Live adjustment (`GtfsRtFeed`), or a replayed October day when no live key is configured |

We read single days straight out of the 16 GB archive with HTTP range requests (`scripts/remote7z.py`), because the archive is non-solid. A day costs about 500 MB of download and is discarded once it has been reduced to observations.

Findings that shaped the design (all measured, see `artifacts/validation.json → data_quality`):

* **The timetable has no traffic in it.** Every stop-to-stop time in `stop_times.txt` corresponds to 15.0 km/h (within-trip spread 0.5%). It is the same at 3 am and 6 pm. Any planner that reads the timetable cannot answer the brief's 9 am vs 11 pm question.
* **The GPS feed and the public GTFS use different route numbering.** Only about 21% of GPS rows carry a `route_id` that exists in the GTFS, and under 1% match a GTFS `trip_id`. Matching by route label would discard most of the data, so we match by geometry instead (section 2).
* **About a third of rows are exact duplicates** (same bus, same timestamp), pings arrive every 11–22 s with gaps, and some points jump off the road.
* **Holidays.** 2 Oct (Gandhi Jayanti) and 20 Oct (Dussehra) fall on weekdays. Their morning-peak speeds match Sundays, not weekdays (`am_peak_speed_by_day`), so the model treats them as Sundays.

## 2. Linking GPS points to roads and routes

We do not trust `route_id`. We match each bus to **stops**, which both sources share.

1. **Clean.** Sort each bus's pings by time, drop duplicates, drop points outside Delhi, and drop single-point spikes (a point that implies more than 90 km/h from both neighbours while the neighbours agree with each other).
2. **Stop passes.** Consecutive pings at most 120 s apart form a *step*, a short straight line. For each step, a spatial grid returns the stops nearby, and we compute the closest approach of the line to each stop. If that distance is 35 m or less, the bus *passed* the stop. The pass time is interpolated to the closest point, so a bus between two pings 20 s apart is still timed to the second.
3. **Visits and dwell.** Passes at the same stop are merged into one *visit*: arrival is the first pass, departure the last. We group per (bus, stop) rather than by adjacency. A bus waiting at a stop also passes the stop across the road on every ping, and grouping by adjacency hid almost all dwell (2% of visits showed any) until we fixed it (58% afterwards).
4. **Segments.** Each visit at stop *a* is linked to the bus's next visit at stop *b* if *a → b* is a directed pair of consecutive stops in any GTFS route pattern, and *b* follows within 20 minutes. **Running time** = arrival at *b* − departure from *a*. Opposite-direction stops never form a valid directed pair, which resolves direction without headings.
5. **Plausibility.** Running speeds outside 2–80 km/h are dropped.

Because segments are directed stop pairs and not routes, **every route on a road shares evidence**. A rare route on a busy road gets times measured by the many other buses on that stretch.

## 3. Estimating travel time on each stretch

Travel time on the bus = Σ running time of each segment + Σ dwell at each intermediate stop. We treat the two separately, as the brief suggests. Signal delays are part of running time (they happen between stops). Dwell is boarding and alighting, and it varies by stop and hour.

**Bins.** Three day types (weekday; Saturday; Sunday or holiday) × 48 half-hours. Bins are smoothed with their neighbours (1-2-1 weights).

**Statistic.** For each segment × day type × half-hour, the median of log running time, and a robust spread (10th–90th percentile range). Medians resist the GPS errors that slip through cleaning.

**Sparse data: hierarchical shrinkage.** Each estimate blends its own evidence with a prior, weighted n / (n + 6) where n is the number of observations:

| Level | Prior used when evidence is thin | Typical case |
| --- | --- | --- |
| 0 | Segment's own data at this half hour (≥ 3 obs) | Most of the network in the daytime |
| 1 | Segment's typical time × its 3 km area's time-of-day profile | Segment seen, but not at this hour (late night) |
| 2 | Area speed at this hour × segment length | Segment never observed |
| 3 | City speed at this hour × segment length | Area never observed |

So there is always an answer, and it moves smoothly from "this road at this time" towards "roads like it at this time" as evidence thins. The API reports the share of each journey at each level as `evidence.confidence` (high / medium / low).

**Dwell** is the mean seconds per visit, for each stop × day type × half-hour, shrunk towards the city mean for that half-hour (prior weight 20 visits).

**Calibration.** One scale factor aligns summed segment times with real end-to-end trips (fitted on the last four training days). Then one correlation parameter ρ is fitted so that the 80% range covers 80% of trips. Errors on consecutive segments of one trip are correlated: a jammed road is jammed for several stops.

## 4. Journey planning

* **Origins and destinations** are stop names (fuzzy matching) or coordinates. Access and egress walks reach every stop within 1 km at 4.3 km/h with a 1.3 detour factor. Changes allow walking up to 350 m between stops plus 2 minutes of slack.
* **Predicted timetable.** Each trip's departure from its first stop comes from GTFS. From there the trip is rolled forward segment by segment with the model, using the half-hour in which the bus reaches each segment. A bus that leaves at 07:50 runs into the 08:30 peak partway along its route, and the model accounts for that. We then enforce first-in-first-out, so a later bus never overtakes an earlier one.
* **RAPTOR** (round-based public transit routing) finds the earliest arrival with 0, 1 and 2 changes. We re-run it with the best option's first route banned to find genuinely different alternatives, and return up to three.
* **The quiet-hour comparison** re-times the same legs at every half hour from 06:00 to 22:30 and reports the fastest one. Traffic delay is the difference.

## 5. Combining live and historical data

For every segment crossed in the last 30 minutes we compute a live ratio *r* = median(observed / historical expected for that half-hour). The prediction for that segment is scaled by

  factor = 1 + (r − 1) · w · e^(−h / 40 min),  w = n / (n + 2)

* *w* discounts a ratio seen on one or two buses.
* *h* is how far ahead the bus will reach that segment. Live traffic is informative for the next 15 minutes and much less so in 2 hours, so the estimate decays back to history.
* Segments with no live bus borrow their 3 km area's ratio at reduced weight. With no live data at all the factor is exactly 1, and the answer is the historical model. That is what a quiet road at night gets.

The same code runs on a real GTFS-RT feed (`TRANSIT_RT_URL`), incrementally map-matching a rolling 40-minute buffer per vehicle, or on a replayed held-out October day. The replay only ever sees observations that ended before the query time, so validation has no leakage.

## 6. Validation

Headline (17,885 held-out trips): mean absolute error 10.2 min for the timetable, 6.7 min for our historical model and 6.4 min with live data. Within 20% of the real time: 35%, 68% and 71%. The reported 80% range covered 78% of trips. Calibration needed only a 2.4% scale correction (ρ = 0.8). See `docs/VALIDATION.md` for breakdowns by period, day type and distance.

The residual bias (−3 min on average while the median error is near zero) comes from a long right tail: a few trips hit incidents or very long holds. Medians are the right target for "how long will it usually take", and the range is there for the tail.

 Ground truth is **real bus trips reconstructed from held-out GPS** (25–31 Oct): one bus departing stop A and arriving at stop B of the same route pattern, passing at least 60% of the stops between, in order, with no gap over 10 minutes. We take the bus's last departure from A, so terminal layovers are excluded. Every method is scored on the same trips.

## 7. Limitations and next steps

* **Timetable vintage.** First-stop departures come from the public November 2025 GTFS, because the October 2026 route numbering differs. Swapping in the current Transport Stack GTFS is one command (`python -m transit.gtfs <feed.zip>`), and nothing else changes.
* **No road geometry.** `shapes.txt` is absent from the public feed, so maps draw stop-to-stop lines and segment length is straight-line distance. Running time is measured, so this affects speeds shown on the map, not journey times.
* **Waiting time** uses scheduled first-stop departures. A natural next step is to measure real headways from the GPS (bus passes per stop per hour) and use them for waiting estimates.
* **Google Maps comparison** needs a Routes API key (`validation/google_compare.py`). Google can only answer for future departures, so the script asks for the same weekday and time in the coming week.
