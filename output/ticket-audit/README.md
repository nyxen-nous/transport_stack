# October ticket audit — Sammi's first milestone

The local October CSV matches the archived member's byte length and CRC32. The archive passed its integrity check. The file contains 2,559,899 records and nine columns. All rows had the expected field count, positive integer passenger counts, and timezone-aware October timestamps. All had a route label and both stop names. No two complete parsed rows were identical. The file has no ticket identifier, so this check cannot detect repeat transactions whose fields differ.

Vehicle numbers are missing on 1,242,167 rows (48.5%). These rows still contribute to route demand. Assigning a ticket to a particular bus run would require additional evidence, even when a vehicle number is present.

## Pilot candidate

West Patel Nagar to Karol Bagh Metro Station has 1,031 recorded passenger journeys on 85DOWN and 771 on 990DOWN during October 1–24. This stop pair ranked first when ordered by the count on the second-most-used route. The counts span the full training period. Confirm both routes serve the stops in this order in Somay's static GTFS lookup, check their departure times overlap, and check return-direction coverage before selecting the pilot. Measuring segment crowding requires mapping tickets for all overlapping journeys, including riders who board earlier or leave later.

## How to reproduce

From the project folder, run

```sh
python3 scripts/audit_tickets.py ~/Downloads/October-Delhi.csv --archive ~/Downloads/October-Delhi.csv.zip
```

- `audit.json` records file checksums, columns, missing values and split sizes. Missing keys in the counts object mean zero occurrences.
- `training_routes.csv` gives eligible record and passenger totals, records with vehicle numbers, and distinct dates for each of 2,232 ticket route labels.
- `shared_journeys.csv` gives the top 100 directed stop pairs out of 59,933 with at least two observed labels. The ranking uses the second route's passenger count, then the first route's. It measures coverage for a pilot.

The split uses ticket creation date in IST. October 1–24 contains 1,944,871 records. October 25–31 contains 615,028 held-out records. Only training records enter the route and stop-pair tables. The tables omit 84 training records with identical boarding and exit names. Route and stop matching here uses trimmed, exact ticket labels. Spelling variants and direction-specific stops still require GTFS matching. The ticket counts describe ONDC purchases and cannot establish bus capacity, segment traversal time or actual occupancy.
