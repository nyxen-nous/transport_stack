# Problem 2 — Sammi and Somay’s project guide

**Goal.** Build a Delhi bus planner that shows scheduled journey time and expected crowding for the stretch each rider will travel. Start with one corridor that has at least two usable route options. Show the same journey at a busy time and a quieter time, using real estimates from the October ticket data.

| Person | Owns | Hands over |
| --- | --- | --- |
| **Sammi** | Ticket data, crowding model, and model validation | A repeatable data pipeline, a crowding table, and a short evidence report |
| **Somay** | Delhi bus schedules, journey routing, API, and demo | A shared stop and route lookup, a working planner, and run instructions |
| **Both** | Data access, corridor choice, integration, and submission | An end-to-end prototype and a rehearsed demo |

## Sammi — build the crowding model

- [ ] **Get and inspect the data.** Download and verify the October CSV. Check columns, missing vehicle numbers, duplicate records, valid passenger counts, and route coverage. The public file contains ONDC e-tickets. Ask the organizers whether the team can also access ETM records or independent passenger counts.
- [ ] **Match tickets to the bus network.** Use Somay’s GTFS lookup to match the ticket’s route and boarding and destination stop names to a direction and stop sequence. Flag ambiguous or backwards matches and report how many records and passengers were matched. Prefer a corridor with good coverage in both directions.
- [ ] **Estimate load along the ride.** A ticket for three people traveling from B to D adds three recorded passengers to B→C and C→D. Use the ticket’s `passenger_count`. Group tickets by run only where vehicle, timing, and stop order support the match. Use the estimated time the bus travels a segment to put it in a time bucket. Where runs cannot be reconstructed, report aggregated ticket demand as a proxy and keep it distinct from per-bus load.
- [ ] **Publish a table and validate it.** Separate weekdays and weekends and start with 30-minute buckets in IST. Hold out the last seven days of October before tuning the model. Check whether estimates from the earlier days recover busy and quiet periods in the holdout. Report matching coverage, sample sizes, and where patterns or rankings break down. Use ETM or observed loads for any claim about actual occupancy. Write down every assumption and how to rerun the pipeline.

## Somay — build the journey planner and demo

- [ ] **Get static GTFS and share the lookup.** Verify the feed includes stops, routes, trips, stop times, and service calendars. Export the route IDs, direction, route patterns, ordered stops, and stop IDs for the pilot corridor. Agree on the same feed version with Sammi so the model can join to the planner.
- [ ] **Build the first working journey search.** Take an origin stop, destination stop, and departure time. Use the GTFS calendar and timetable to return upcoming journeys. Show route, boarding and exit stops, departure, arrival, and total time including wait. Start with direct journeys and add one transfer once the direct flow works.
- [ ] **Connect the crowding estimates.** Start with a small, clearly labeled sample table while Sammi works. For each bus leg, look up the segments the rider will actually travel and use each segment’s scheduled traversal time. Show the busiest stretch, the crowding label, and **Limited data** when coverage is low. Compare crowding across choices only where the estimates are comparable. With route-specific labels, show whether each option is busier or quieter than its own route’s usual level.
- [ ] **Build the demo and document how to run it.** A simple screen with a departure picker and route cards is enough. Include a way to inspect crowding by route, segment, and time, such as an API endpoint or a segment breakdown on each journey. Use Postman to check a valid trip, opposite directions, a query with no service, and a trip with missing crowding estimates.

## Connect your work

Pick a corridor together once you’ve checked the ticket coverage and bus schedules. Use the same GTFS version and agree on the table fields below so Somay can build the planner while Sammi works on the model.

**Somay → Sammi.** A GTFS lookup with one row per stop in each ordered route pattern, including `route_id`, `direction_id`, `pattern_id`, `stop_sequence`, `stop_id`, and `stop_name`. A route pattern is a particular sequence of stops under a route and direction. Use a stable pattern ID derived from the ordered stop IDs. Number stops from 1 within each pattern so both sides use the same `stop_sequence` even if raw GTFS numbering varies by trip.

**Sammi → Somay.** One crowding row per route pattern, directed segment, day type, and 30-minute time bucket. Include `route_id`, `direction_id`, `pattern_id`, `from_stop_sequence`, `from_stop_id`, `to_stop_id`, `day_type`, `bucket_start_ist`, `crowding_label`, `sample_days`, `matched_runs` (if known), and `estimate_basis`. Agree on simple labels such as **Lower than usual**, **Typical**, **Higher than usual**, and **Limited data**. Use seat availability labels only where occupancy and bus capacity can support them.

## Ready to submit when

- [ ] Someone else can rebuild the crowding table and run the planner using the README.
- [ ] The planner shows journey time and crowding for real routes, including what it says when data is sparse.
- [ ] The crowding estimates can be inspected by route, segment, direction, and time.
- [ ] The technical note reports data coverage, assumptions, and results from the held-out week. It explains what the public ticket sample can support.
- [ ] The proposal describes the prototype and its coverage. In the demo, **Somay shows the commuter journey** and **Sammi explains where the estimates came from and how they were checked**.

[Official Problem 2 brief](https://docs.google.com/document/d/1ocS9rG4_E_Fb9DJcqTG8SClrLJJ_UJqGGAhgPbV8tr4/preview) · [October ticket download](https://pub-091bd9e794bb4877b10b29e6df80f065.r2.dev/2026-10/October-Delhi.csv.zip) · [Dataset notes](https://pub-091bd9e794bb4877b10b29e6df80f065.r2.dev/2026-10/README.md) · [Delhi Transport Stack](https://delhi.transportstack.in/)
