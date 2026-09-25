import json
from unittest.mock import patch

from flask import Flask
import pdv_distance_api as api


def fixture():
    route = {'fecha': '2026-09-01', 'sucursal': 'Mar de Ajó', 'routing_segments': [
        {'destination_ref': '001', 'distance_km': 3},
        {'destination_ref': '2', 'distance_km': 7},
        {'destination_ref': 'deposito', 'distance_km': 5},
    ]}
    return {'r1': route}, [('r1', {'cliente': '1'}), ('r1', {'cliente': '2'})]


def test_distances_conserve_entire_route_and_normalize_ids():
    costs, customers = fixture()
    rows = api.distance_rows({}, costs, customers, [])
    assert [r['km_asignados'] for r in rows] == [4.5, 10.5]
    assert sum(r['km_asignados'] for r in rows) == 15
    assert sum(r['km_asignados'] * 2500 for r in rows) == 37500
    assert all(r['sucursal'] == '1' for r in rows)


def test_missing_route_and_missing_return_remain_unknown():
    costs, customers = fixture()
    costs['r1']['routing_segments'].pop()
    rows = api.distance_rows({'r2': {'fecha': '2026-09-01', 'suc': 'Dolores'}}, costs, customers,
                             [('r2', {'Customer ID': '3'})])
    assert len(rows) == 3
    assert all(r['km_asignados'] is None for r in rows)


def test_zero_distance_is_valid_and_estimated_is_labelled():
    costs, customers = fixture()
    for leg in costs['r1']['routing_segments']:
        leg.update(distance_km=0, fallback=True)
    rows = api.distance_rows({}, costs, customers, [])
    assert all(r['km_asignados'] == 0 and r['distancia_estimada'] for r in rows)


def test_recalculation_is_not_counted_twice_and_repeated_attempts_are_one_attention(tmp_path):
    costs, customers = fixture()
    (tmp_path / 'logistics_costs.json').write_text(json.dumps({
        'route_costs': costs, 'route_customer_costs': {'r1': [c for _, c in customers]},
        'route_cost_calculations': {'r1': {'old': {'routing_segments': []}}},
    }), encoding='utf-8')
    with patch.object(api.storage, 'backend_name', return_value='json'), \
         patch.object(api.storage, 'DATA_DIR', str(tmp_path)), \
         patch.object(api.storage, 'load_all', return_value={}), \
         patch.object(api.storage, 'load_attempts', return_value={
             'a': {'Route ID': 'r1', 'Customer ID': '1'},
             'b': {'Route ID': 'r1', 'Customer ID': '1'},
         }):
        rows = api.distance_rows(*api.load_snapshot('2026-09-01', '2026-09-30'))
    assert len(rows) == 2
    assert sum(r['km_asignados'] for r in rows) == 15


def test_api_auth_period_and_no_truncation(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    client = app.test_client()
    monkeypatch.setenv('PDV_COST_API_KEY', 'test-key')
    path = '/api/integracion/v1/pdv-distancias?desde=2026-09-01&hasta=2026-09-30'
    assert client.get(path).status_code == 401
    headers = {'Authorization': 'Bearer test-key'}
    assert client.get(path.replace('2026-09-30', '2025-09-01'), headers=headers).status_code == 400
    costs, customers = fixture()
    monkeypatch.setattr(api, 'load_snapshot', lambda *_: ({}, costs, customers, []))
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    assert response.json['version'] == 1
    assert len(response.json['items']) == 2
