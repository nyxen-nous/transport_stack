"""Download each October day from the remote archive, map-match it, delete the raw DB.

    python scripts/fetch_and_ingest.py --days 1-31 --workers 3
Raw SQLite files are ~4.5 GB each; only the compact observation files are kept.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))


def one(day, raw, out, network):
    import py7zr
    from remote7z import RangeFile, URL
    from transit.gtfs import Network
    from transit.ingest import process_day
    name = f'bus_movements_2026_10_{day:02}.db'
    target = out / f'2026_10_{day:02}.npz'
    if target.exists():
        return day, 'cached'
    db = raw / name
    t = time.time()
    if not db.exists():
        with py7zr.SevenZipFile(RangeFile(URL), 'r') as z:
            z.extract(path=raw, targets=[name])
    dl = time.time() - t
    stats = process_day(Network.load(network), db, target)
    stats['seconds_download'] = round(dl, 1)
    if not os.environ.get('KEEP_RAW'):
        db.unlink()
    return day, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', default='1-31')
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--raw', type=Path, default=ROOT / 'data' / 'gps')
    ap.add_argument('--out', type=Path, default=ROOT / 'build' / 'obs')
    ap.add_argument('--network', type=Path, default=ROOT / 'build' / 'network.pkl')
    a = ap.parse_args()
    lo, _, hi = a.days.partition('-')
    days = list(range(int(lo), int(hi or lo) + 1))
    a.raw.mkdir(parents=True, exist_ok=True)
    a.out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(a.workers) as ex:
        for day, st in ex.map(one, days, [a.raw] * len(days), [a.out] * len(days), [a.network] * len(days)):
            print(day, json.dumps(st), flush=True)


if __name__ == '__main__':
    main()
