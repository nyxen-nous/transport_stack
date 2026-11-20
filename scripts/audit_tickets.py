"""Audit Delhi October 2026 tickets and shortlist shared journeys using training days."""
import argparse
import csv
import hashlib
import json
import zlib
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZipFile

IST = timezone(timedelta(hours=5, minutes=30))
MISSING = {"", "null", "none", "nan"}
FIELDS = ("route", "start_stop_name", "end_stop_name", "vehicle_number",
          "created_at", "passenger_count")


def fingerprint(path):
    digest = hashlib.sha256()
    crc = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            crc = zlib.crc32(chunk, crc)
    return digest.hexdigest(), crc


def write_table(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(header)
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("output/ticket-audit"))
    args = parser.parse_args()
    sha, crc = fingerprint(args.csv)
    with ZipFile(args.archive) as archive:
        member = archive.getinfo("October-Delhi.csv")
        if (crc, args.csv.stat().st_size) != (member.CRC, member.file_size) or archive.testzip():
            raise ValueError("CSV or archive integrity check failed")

    counts = Counter()
    missing = Counter()
    split = Counter()
    seen = set()
    route_stats = defaultdict(Counter)
    pairs = defaultdict(Counter)
    days = defaultdict(set)
    with args.csv.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.reader(source, strict=True)
        header = next(reader)
        positions = [header.index(field) for field in FIELDS]
        for row in reader:
            counts["rows"] += 1
            if len(row) != len(header):
                counts["malformed_rows"] += 1
                continue
            # Hash an unambiguous representation of the complete parsed row.
            key = hashlib.blake2b(repr(row).encode(), digest_size=16).digest()
            duplicate = key in seen
            seen.add(key)
            counts["repeat_rows_after_first"] += duplicate
            values = [row[i].strip() for i in positions]
            for field, value in zip(FIELDS, values):
                missing[field] += value.casefold() in MISSING
            route, start, end, vehicle, stamp, quantity = values
            try:
                passengers = int(quantity)
                if passengers <= 0:
                    raise ValueError
            except ValueError:
                counts["invalid_passenger_counts"] += 1
                continue
            try:
                timestamp = datetime.fromisoformat(stamp)
                if timestamp.utcoffset() is None:
                    raise ValueError
                date = timestamp.astimezone(IST).date()
            except ValueError:
                counts["invalid_or_naive_timestamps"] += 1
                continue
            if (date.year, date.month) != (2026, 10):
                counts["outside_october"] += 1
                continue
            period = "training" if date.day <= 24 else "holdout"
            split[period] += 1
            if period == "holdout" or duplicate or any(v.casefold() in MISSING for v in (route, start, end)):
                continue
            if start == end:
                counts["training_same_stop_rows_excluded"] += 1
                continue
            stats = route_stats[route]
            stats["records"] += 1
            stats["passengers"] += passengers
            stats["with_vehicle"] += vehicle.casefold() not in MISSING
            days[route].add(date)
            pairs[start, end][route] += passengers

    args.out.mkdir(parents=True, exist_ok=True)
    routes = sorted(route_stats, key=lambda r: (-route_stats[r]["passengers"], r))
    write_table(args.out / "training_routes.csv",
                ("route", "records", "passengers", "with_vehicle_records", "days_seen"),
                ((r, route_stats[r]["records"], route_stats[r]["passengers"],
                  route_stats[r]["with_vehicle"], len(days[r])) for r in routes))
    candidates = []
    for (start, end), options in pairs.items():
        if len(options) < 2:
            continue
        (a, n), (b, m) = sorted(options.items(), key=lambda item: (-item[1], item[0]))[:2]
        candidates.append((start, end, a, n, b, m))
    candidates.sort(key=lambda item: (-item[5], -item[3], item[:2]))
    write_table(args.out / "shared_journeys.csv",
                ("boarding", "destination", "route_a", "passengers_a", "route_b", "passengers_b"),
                candidates[:100])
    report = dict(sha256=sha, verified_archive_crc32=hex(crc), columns=header,
                  counts=counts, missing_fields=missing, dated_rows_before_dedup=split,
                  training_route_labels=len(routes), shared_stop_pairs=len(candidates))
    (args.out / "audit.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
