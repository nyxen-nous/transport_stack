# Delhi bus planner pilot

Open `pilot/index.html` directly, or run `node pilot/server.js` and visit http://127.0.0.1:8787. The demo and local API share one routing function. Use the morning and afternoon buttons to compare the same journey on Monday, October 19, 2026.

## What the pilot demonstrates

A rider traveling from West Patel Nagar to Karol Bagh Metro Station can compare scheduled journey time, including waiting, with historical ticket demand on the segments they ride. The archived feed provides 505 scheduled trips on six route patterns across the two route families and their return directions.

At 10 am the earliest-arriving 85DOWN takes 12 minutes including wait and shows limited data. The 990DOWN takes 26 minutes. Its busiest measured stretch, Rajendra Place to Telephone Exchange Karol Bagh, averaged 3.94 matched passenger journeys per training weekday in the corresponding half-hour. At 1 pm the 990DOWN takes 16 minutes and its busiest measured stretch averages 0.24. These samples illustrate a difference in recorded demand. Actual passenger load and seat availability require independent observations.

Search considers direct departures in the next two hours and same-stop-ID transfers with at least three minutes to change and arrival within three hours. Results show the earliest arrival for each route or route combination, with up to three transfer options. The service calendar controls operating dates. Times are approximate. Outward and return stops with the same name can have different IDs. Transfers require an identical ID. The pilot does not model walking transfers or traffic delays.

## Evidence and limitations

The original audit checked 2,559,899 records. The October 1–24 training period determined the pilot corridor, match coverage, thresholds, and demo estimates. October 25–31 was held out. The calendar in the archived feed covers October 2026, but the schedule files are dated November 2025. Actual October service can differ. Delhi's published stop times use a constant-speed approximation. [Delhi Open Transit Data documentation](https://otd.delhi.gov.in/documentation/) · [Archived feed source](https://ethanc8.github.io/delhi-gtfs/bus.zip)

Match by ticket route label and normalized boarding and exit names in forward stop order. Accept exactly one combination of route pattern and stop positions. For the four pilot labels, 1,838 of 27,637 training tickets (6.65%) and 620 of 8,978 holdout tickets (6.91%) matched, representing 1,985 and 684 passengers respectively. Training had 3,925 ambiguous tickets, 169 reverse-only matches, and 21,705 unmatched records. Repeated stop names, differences between the sources, and multiple operators sharing a route label limit coverage. Unmatched demand may differ from the matched sample.

Each matched ticket contributes its passenger count to each traversed segment. Ticket creation time approximates boarding time. Median GTFS travel offsets within each pattern approximate when a rider reaches each segment. Group observations into 30-minute IST bins. Average over all comparable training weekdays or weekend days, counting missing observations as zero recorded passengers. Weekend and weekday baselines are separate. At least three days with observations in a bin and three supported bins for the same segment/day type are required. The lower and upper thirds of the supported bin means define the labels. A leg with any unsupported segment shows Limited data. Otherwise its displayed label belongs to the segment with the largest recorded mean. Relative labels are specific to the segment and day type. No bus runs were reconstructed.

### Held-out results

The test compares each supported segment and half-hour with its held-out mean, using frozen training thresholds. Individual cells share tickets across adjacent segments and are correlated. Zero matched tickets can reflect sampling gaps.

| Day type | Supported bins | Same label in holdout | Mean absolute error, recorded passenger journeys per day per bin |
| --- | ---: | ---: | ---: |
| Weekday | 1,118 | 503 (45.0%) | 0.31 |
| Weekend | 271 | 107 (39.5%) | 0.67 |

Weekday bins labeled higher averaged 1.18 recorded passenger journeys in the holdout versus 0.29 for lower bins. Weekend equivalents were 1.31 and 0.53. The broad ordering persisted in aggregate, while most individual bins changed labels. Weekend validation covers only two days. Treat results as exploratory.

## Rebuild and check

Run from the project folder with Python 3.9+ and a modern Node.js runtime. The planner runs with the files in `pilot/`. To rebuild the model, download the [archived GTFS feed](https://ethanc8.github.io/delhi-gtfs/bus.zip) into `data/delhi-bus-gtfs.zip` and compare its SHA-256 with `pilot/validation.json`. Download the [October ticket archive](https://pub-091bd9e794bb4877b10b29e6df80f065.r2.dev/2026-10/October-Delhi.csv.zip) into your Downloads folder and extract the CSV there.

```sh
python3 scripts/audit_tickets.py ~/Downloads/October-Delhi.csv --archive ~/Downloads/October-Delhi.csv.zip
python3 scripts/build_pilot.py data/delhi-bus-gtfs.zip ~/Downloads/October-Delhi.csv
node pilot/server.js
```

From a second terminal run the local API checks.

```sh
postman collection run pilot/requests.postman_collection.json
```

The API accepts GET /api/journeys with from, to, date, and time. See the collection for six runnable examples. The model publishes explicitly unsupported bins in pilot/crowding.csv, sample days count days with at least one matched passenger, and matched_runs is blank. pilot/stop_lookup.csv gives each pattern's ordered stop IDs. The interface embeds the supported subset to run offline. pilot/validation.json gives split-specific record and passenger totals.
