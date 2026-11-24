"""HTTP API. Run:  python -m transit.api   then open http://127.0.0.1:8000 (UI) or /docs (API docs)."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .gtfs import Network
from .live import GtfsRtFeed, ReplayFeed
from .model import DAYTYPES, Model, daytype_of
from .service import Service

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path(os.environ.get('TRANSIT_ARTIFACTS', ROOT / 'artifacts'))
WEB = ROOT / 'web'

app = FastAPI(
    title='Delhi Lines — bus travel-time model and journey planner',
    version='2.0',
    description=(
        'Bus journeys across Delhi with in-vehicle times predicted from October 2026 DTC GPS traces, '
        'not the timetable. Every estimate depends on the day type and half hour, carries an 80% range, '
        'says how much GPS evidence supports it, and is compared with the quietest hour of the day. '
        'All times are IST.'),
)
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['GET'], allow_headers=['*'])


@lru_cache(maxsize=1)
def service() -> Service:
    t = time.time()
    net = Network.load(ARTIFACTS / 'network.pkl')
    model = Model.load(ARTIFACTS / 'model.npz')
    rt = os.environ.get('TRANSIT_RT_URL')
    live = GtfsRtFeed(net, model, rt) if rt else ReplayFeed(net, model, ARTIFACTS / 'replay')
    svc = Service(net, model, live)
    for d in range(3):
        svc.planner.table(d)
    print(f'model ready in {time.time() - t:.1f}s', flush=True)
    return svc


def _when(depart: str | None) -> datetime:
    if not depart:
        return datetime(2026, 10, 27, 8, 30)
    try:
        return datetime.fromisoformat(depart.replace(' ', 'T'))
    except ValueError:
        raise HTTPException(400, 'depart must look like 2026-10-27T08:30')


@app.get('/api/journeys', summary='Bus journey options between two places')
def journeys(
    from_: str = Query(..., alias='from', description='Stop name (fuzzy) or "lat,lon"', examples=['Kashmere Gate ISBT']),
    to: str = Query(..., description='Stop name (fuzzy) or "lat,lon"', examples=['Nehru Place']),
    depart: str | None = Query(None, description='Local departure time, ISO format', examples=['2026-10-27T08:30']),
    options: int = Query(3, ge=1, le=5),
    live: bool = Query(False, description='Blend the last 30 min of bus movements (replayed October day, or GTFS-RT when configured)'),
):
    """Each option lists the walk to the first stop, the bus legs (boarding stop, route, alighting stop,
    wait, ride time with an 80% range, timetable time for comparison), transfers, the walk to the
    destination and the total. `quiet_hour` gives the same legs at the quietest half hour of that day type."""
    try:
        return service().journeys(from_, to, _when(depart), options, live)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get('/api/journeys/profile', summary='Travel time of the best route at every half hour')
def profile(from_: str = Query(..., alias='from'), to: str = Query(...), date: str = Query('2026-10-27')):
    try:
        return service().profile(from_, to, date)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get('/api/stops', summary='Search stops by name')
def stops(q: str = Query(..., min_length=1), limit: int = Query(8, le=30)):
    return service().search_stops(q, limit)


@app.get('/api/segments', summary='Modelled bus speeds on every observed road stretch')
def segments(day_type: Literal['weekday', 'saturday', 'sunday'] = 'weekday', time: str = '08:30'):
    h, m = map(int, time.split(':'))
    data = service().segment_speeds(DAYTYPES.index(day_type), h * 3600 + m * 60)
    return JSONResponse(data, headers={'Cache-Control': 'public, max-age=3600'})


@app.get('/api/model', summary='Model coverage, training data and validation summary')
def model_info():
    svc = service()
    out = dict(svc.model.meta or {})
    out['calibration_factor'] = svc.model.calib
    out['segment_error_correlation'] = svc.model.rho
    v = ARTIFACTS / 'validation.json'
    if v.exists():
        out['validation'] = json.loads(v.read_text())
    out['live'] = svc.live.status() if svc.live else None
    return out


@app.get('/api/health')
def health():
    return {'ok': True}


if WEB.exists():
    app.mount('/static', StaticFiles(directory=WEB), name='static')

    @app.get('/', include_in_schema=False)
    def index():
        return FileResponse(WEB / 'index.html')


def main():
    import uvicorn
    port = int(os.environ.get('PORT', 8000))
    service()
    uvicorn.run(app, host=os.environ.get('HOST', '127.0.0.1'), port=port)


if __name__ == '__main__':
    main()
