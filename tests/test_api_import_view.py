import os
from unittest.mock import patch

os.environ['DISABLE_CACHE_PREWARM'] = '1'
from app import app
from api_import_view import select_records


def test_all_fields_nested_values_pagination_and_download():
    app.config.update(TESTING=True, SECRET_KEY='test')
    client = app.test_client()
    assert client.get('/datos-api').status_code == 302
    with client.session_transaction() as session:
        session['admin_logged_in'] = True
    records = [dict(cliente_id=str(i), fecha_entrega='2026-01-02', detalle=[{'extra': '<script>alert(1)</script>'}]) for i in range(51)]
    records[-1]['campo_solo_ultimo'] = {'null': None, 'zero': 0, 'false': False}
    snapshot = dict(datos=records, rechazos_clientes=[{'fecha': '2026-02-01', 'motivos': [{'motivo': 'Cerrado'}]}], coverage=[{'total': 51}])
    with patch('api_import_view.latest_import', return_value=snapshot):
        response = client.get('/datos-api')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'campo_solo_ultimo' in html
        assert '<script>alert(1)</script>' not in html
        assert '&lt;script&gt;' in html
        assert 'Página 1 de 2' in html
        assert 'campo_solo_ultimo' in client.get('/datos-api?pagina=2').get_data(as_text=True)
        assert client.get('/datos-api?descargar=completo').json == snapshot
        filtered = client.get('/datos-api?fuente=rechazos_clientes&q=Cerrado&descargar=filtrado').json
        assert filtered['registros'] == snapshot['rechazos_clientes']
        assert client.get('/datos-api?pagina=invalid').status_code == 200


def test_dates_are_not_substituted_and_sparse_fields_are_preserved():
    snapshot = dict(contrato='comprobantes_ventas_v2', datos=[
        {'fecha_movimiento': '2026-09-01', 'fecha_entrega': None},
        {'fecha_movimiento': '2026-09-02', 'rare': 1},
    ])
    _, _, columns, rows, field = select_records(snapshot, {'hasta': '2026-09-01'})
    assert field == 'fecha_movimiento'
    assert len(rows) == 1 and 'rare' in columns
    assert select_records(snapshot, {'campo_fecha': 'fecha_entrega', 'desde': '2026-01-01'})[3] == []
