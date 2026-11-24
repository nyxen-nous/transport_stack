"""API checks against the shipped artifacts (skipped if artifacts/ is missing)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytestmark = pytest.mark.skipif(not (ROOT / 'artifacts' / 'model.npz').exists(), reason='no artifacts')


@pytest.fixture(scope='module')
def client():
    from fastapi.testclient import TestClient
    from transit.api import app
    return TestClient(app)


def get(client, **params):
    r = client.get('/api/journeys', params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_example_from_brief(client):
    r = get(client, **{'from': 'Kashmere Gate ISBT', 'to': 'Nehru Place', 'depart': '2026-10-27T08:30'})
    assert 1 <= len(r['options']) <= 3
    o = r['options'][0]
    kinds = [l['type'] for l in o['legs']]
    assert kinds[0] == 'walk' and kinds[-1] == 'walk' and 'bus' in kinds
    bus = next(l for l in o['legs'] if l['type'] == 'bus')
    for k in ('route', 'board_stop', 'alight_stop', 'wait_minutes', 'ride_minutes', 'ride_range_minutes', 'path'):
        assert k in bus
    assert o['quiet_hour']['in_vehicle_minutes'] <= o['in_vehicle_minutes'] + 0.1
    assert o['total_range_minutes'][0] <= o['total_minutes'] <= o['total_range_minutes'][1]


def test_peak_differs_from_late_evening(client):
    peak = get(client, **{'from': 'Kashmere Gate ISBT', 'to': 'Nehru Place', 'depart': '2026-10-27T09:00'})
    late = get(client, **{'from': 'Kashmere Gate ISBT', 'to': 'Nehru Place', 'depart': '2026-10-27T23:00'})
    ivt = lambda r: r['options'][0]['in_vehicle_minutes'] / sum(
        l['distance_km'] for l in r['options'][0]['legs'] if l['type'] == 'bus')
    assert ivt(peak) > ivt(late)        # minutes per km on the bus


def test_coordinates_and_live(client):
    r = get(client, **{'from': '28.6304,77.2177', 'to': '28.5494,77.2001', 'depart': '2026-10-27T18:00', 'live': 'true'})
    assert r['query']['mode'] == 'live+historical'
    assert r['options']


def test_bad_inputs(client):
    assert client.get('/api/journeys', params={'from': 'zzzzqqq', 'to': 'Nehru Place'}).status_code == 400
    assert client.get('/api/journeys', params={'from': '10,10', 'to': 'Nehru Place'}).status_code == 400
    assert client.get('/api/journeys', params={'from': 'Nehru Place', 'to': 'Saket', 'depart': 'tuesday'}).status_code == 400


def test_other_endpoints(client):
    assert client.get('/api/stops', params={'q': 'nehru'}).json()
    seg = client.get('/api/segments', params={'day_type': 'weekday', 'time': '18:00'}).json()
    assert seg['count'] > 1000
    prof = client.get('/api/journeys/profile', params={'from': 'Kashmere Gate ISBT', 'to': 'Nehru Place'}).json()
    assert len(prof['series']['weekday']) == 38
    info = client.get('/api/model').json()
    assert 'validation' in info
