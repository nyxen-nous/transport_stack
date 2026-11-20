"""Build the four-route pilot from local GTFS and October ticket files."""
import argparse
import csv
import hashlib
import io
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'pilot'
LABELS = {'85DOWN','990DOWN','85UP','990UP'}
IST = timezone(timedelta(hours=5, minutes=30))
TRAIN = [date(2026,10,d) for d in range(1,25)]
TEST = [date(2026,10,d) for d in range(25,32)]


def norm(name):
    return ' '.join(re.findall(r'[a-z0-9]+',name.casefold()))


def seconds(t):
    h,m,s = map(int,t.split(':'))
    return h*3600+m*60+s


def kind(d):
    return 'weekend' if d.weekday()>=5 else 'weekday'


def bucket_name(b):
    return f'{b//2:02}:{b%2*30:02}'


def build(feed, tickets):
    with ZipFile(feed) as z:
        def rows(file):
            with z.open(file+'.txt') as f:
                yield from csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig'))
        routes = {r['route_id']:r['route_long_name'] for r in rows('routes') if r['route_long_name'] in LABELS}
        stops = {r['stop_id']:r['stop_name'] for r in rows('stops')}
        trips = {r['trip_id']:r for r in rows('trips') if r['route_id'] in routes}
        times = defaultdict(list)
        for r in rows('stop_times'):
            if r['trip_id'] in trips:
                times[r['trip_id']].append((int(r['stop_sequence']),r['stop_id'],seconds(r['arrival_time']),seconds(r['departure_time'])))
        calendar = list(rows('calendar'))
        exceptions = list(rows('calendar_dates')) if 'calendar_dates.txt' in z.namelist() else []
    patterns, departures, lookup = {}, [], defaultdict(set)
    offsets = defaultdict(list)
    for trip, entries in times.items():
        entries.sort()
        t = trips[trip]
        route = t['route_id']
        label = routes[route]
        ids = [e[1] for e in entries]
        direction = t.get('direction_id') or ('DOWN' if label.endswith('DOWN') else 'UP')
        pid = route+'-'+direction+'-'+hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:12]
        arrivals = [e[2] for e in entries]
        dep = [e[3] for e in entries]
        if any(a>d for a,d in zip(arrivals,dep)) or any(d>a for d,a in zip(dep,arrivals[1:])):
            continue
        departures.append(dict(p=pid,service=t['service_id'],a=arrivals,d=dep,id=trip))
        offsets[pid].append([x-dep[0] for x in dep])
        if pid in patterns:
            continue
        patterns[pid] = dict(route=route,label=label,direction=direction,ids=ids,names=[stops[i] for i in ids])
        names = [norm(stops[i]) for i in ids]
        for i,a in enumerate(names):
            for j in range(i+1,len(names)):
                lookup[label,a,names[j]].add((pid,i,j))
    duration = {p:[int(statistics.median(k)) for k in zip(*v)] for p,v in offsets.items()}
    obs = Counter()
    coverage = defaultdict(Counter)
    with tickets.open(newline='',encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            route = row['route'].strip()
            if route not in LABELS:
                continue
            stamp = datetime.fromisoformat(row['created_at']).astimezone(IST)
            day = stamp.date()
            if day not in TRAIN and day not in TEST:
                continue
            qty = int(row['passenger_count'])
            a,b = norm(row['start_stop_name']),norm(row['end_stop_name'])
            options = lookup.get((route,a,b),())
            status = ('matched' if len(options)==1 else 'ambiguous' if options else
                      'reverse_only' if (route,b,a) in lookup else 'unmatched')
            group = coverage['training' if day in TRAIN else 'holdout']
            group[status+'_records'] += 1
            group[status+'_passengers'] += qty
            if len(options)!=1:
                continue
            p,i,j = next(iter(options))
            for k in range(i,j):
                moment = stamp + timedelta(seconds=duration[p][k]-duration[p][i])
                # Keep journeys that cross midnight inside their own evaluation split.
                if (moment.date() in TRAIN) != (day in TRAIN) or moment.date().month!=10:
                    continue
                obs[p,k,moment.date(),moment.hour*2+moment.minute//30] += qty
    metrics = defaultdict(Counter)
    model = {}
    header = ['route_id','direction_id','pattern_id','from_stop_sequence','from_stop_id','to_stop_id','day_type','bucket_start_ist','crowding_label','sample_days','matched_runs','estimate_basis','mean_passengers_per_day']
    def table(filename,header,items):
        with (OUT/filename).open('w',newline='') as f:
            w=csv.writer(f); w.writerow(header); w.writerows(items)
    table('stop_lookup.csv',['route_id','direction_id','pattern_id','stop_sequence','stop_id','stop_name'],
          ((v['route'],v['direction'],p,k+1,s,v['names'][k]) for p,v in patterns.items() for k,s in enumerate(v['ids'])))
    result = []
    for p,v in patterns.items():
        for k,s in enumerate(v['ids'][:-1]):
            for typ in ('weekday','weekend'):
                train=[d for d in TRAIN if kind(d)==typ]
                test=[d for d in TEST if kind(d)==typ]
                slots=[]
                for b in range(48):
                    daily=[obs[p,k,d,b] for d in train]
                    slots.append((sum(daily)/len(train),sum(x>0 for x in daily)))
                supported=[avg for avg,n in slots if n>=3]
                cuts=statistics.quantiles(supported,n=3,method='inclusive') if len(supported)>=3 else []
                for b,(avg,n) in enumerate(slots):
                    def classify(x):
                        if x<cuts[0]: return 'Lower than usual'
                        if x>cuts[1]: return 'Higher than usual'
                        return 'Typical'
                    label=classify(avg) if n>=3 and cuts else 'Limited data'
                    result.append([v['route'],v['direction'],p,k+1,s,v['ids'][k+1],typ,bucket_name(b),label,n,'','ONDC ticket demand',round(avg,2)])
                    if label=='Limited data': continue
                    model[f'{p}|{k}|{typ}|{b}']=[label,round(avg,2),n]
                    observed=sum(obs[p,k,d,b] for d in test)/len(test)
                    met=metrics[typ]
                    met['cells']+=1
                    met['same_label']+=classify(observed)==label
                    met['absolute_error_sum']+=abs(avg-observed)
                    met[label+' cells']+=1
                    met[label+' observed_sum']+=observed
    table('crowding.csv',header,result)
    data=dict(patterns=patterns,trips=departures,calendar=calendar,exceptions=exceptions,model=model)
    (OUT/'data.js').write_text('globalThis.PILOT = '+json.dumps(data,separators=(',',':'))+';\n')
    (OUT/'validation.json').write_text(json.dumps(dict(coverage=coverage,metrics=metrics,gtfs_sha256=hashlib.sha256(feed.read_bytes()).hexdigest()),indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('feed',type=Path)
    parser.add_argument('tickets',type=Path)
    args=parser.parse_args()
    build(args.feed,args.tickets)
