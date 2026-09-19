import os
from unittest.mock import patch, MagicMock
import pytest
from logistics_db import connection, filters, predicate, match_candidates, SourceNotConfigured


def test_connection_is_readonly_and_isolated_from_dashboard():
    cn = MagicMock()
    with patch.dict(os.environ, {'LOGISTICS_DATABASE_URL': 'test-source', 'DATABASE_URL': 'test-dashboard'}), patch('logistics_db.psycopg2.connect', return_value=cn) as connect:
        with connection() as actual:
            assert actual is cn
        assert connect.call_args.args == ('test-source',)
        assert 'default_transaction_read_only=on' in connect.call_args.kwargs['options']
        cn.set_session.assert_called_once_with(readonly=True, isolation_level='REPEATABLE READ')
        cn.rollback.assert_called_once()
        cn.close.assert_called_once()
    with patch.dict(os.environ, {'LOGISTICS_DATABASE_URL': ''}):
        with pytest.raises(SourceNotConfigured):
            with connection():
                pass


def test_filters_whitelist_dates_and_parameters():
    with pytest.raises(ValueError):
        filters({'fuente': 'settings_dashboard'})
    with pytest.raises(ValueError):
        filters({'desde': '2026-10-01', 'hasta': '2026-01-01'})
    f = filters({'q': "'; DROP TABLE ventas_detalle; --", 'cliente': '001', 'sucursal': '2'})
    where, params = predicate(f)
    assert f['q'] not in str(where)
    assert params == ['001', '2', f['q']]


def test_candidates_keep_customer_branch_day_and_never_claim_delivery():
    rows = [dict(cliente='001', sucursal='1', fecha='2026-09-17'),
            dict(cliente='1', sucursal='1', fecha='2026-09-17'),
            dict(cliente='001', sucursal='2', fecha='2026-09-17')]
    candidates = [('001','1',dict(fecha='2026-09-18', hora='08:00')),
                  ('001','1',dict(fecha='2026-09-22', hora='08:00'))]
    match_candidates(rows, candidates)
    assert len(rows[0]['foxtrot_candidatos']) == 1
    assert rows[0]['foxtrot_candidatos'][0]['diferencia_dias'] == 1
    assert rows[1]['foxtrot_candidatos'] == rows[2]['foxtrot_candidatos'] == []
    assert 'cercanos' in rows[0]['estado_cruce']
    candidates.append(('001','1',dict(fecha='2026-09-17', hora='08:00')))
    match_candidates(rows,candidates)
    assert 'no identifica el pedido' in rows[0]['estado_cruce']
    assert 'resultado' not in rows[0]


def test_page_login_and_all_fields():
    os.environ['DISABLE_CACHE_PREWARM'] = '1'
    from app import app
    app.config.update(TESTING=True, SECRET_KEY='test')
    client = app.test_client()
    assert client.get('/datos-logistica').status_code == 302
    with client.session_transaction() as s:
        s['admin_logged_in'] = True
    data = dict(filters=filters({'fuente':'ventas'}), rows=[{'rare_field': '<script>bad</script>'}],
                columns=['rare_field'], count=1,total=1,page=1,pages=1,first=None,last=None,
                table='ventas_detalle',consulted_at='2026-09-19')
    with patch('logistics_db_view.read_source', return_value=data):
        response = client.get('/datos-logistica')
        assert response.status_code == 200
        assert b'rare_field' in response.data
        assert b'<script>bad</script>' not in response.data
        assert client.get('/datos-logistica?descargar=json').json['rows'] == data['rows']
