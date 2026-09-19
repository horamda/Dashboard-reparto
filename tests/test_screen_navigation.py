import os
import re
from pathlib import Path
from unittest.mock import patch

os.environ['DISABLE_CACHE_PREWARM'] = '1'
from app import app


def test_static_internal_links_resolve():
    routes = {r.rule for r in app.url_map.iter_rules()}
    for p in [Path('app.py'), Path('plantilla_dashboard.html'), *Path('templates').glob('*.html')]:
        for link in re.findall(r'''href=["'](/[^"'{}<> ]*)''', p.read_text(encoding='utf-8')):
            path = link.split('?')[0].split('#')[0]
            assert not path or path in routes or path.startswith('/static/'), (p, link)


def test_dashboard_does_not_build_unused_legacy_otif():
    from contextlib import ExitStack
    import pipeline
    with ExitStack() as stack:
        for name in ('storage.load_clientes', 'storage.load_rechazos', 'storage.load_rechazos_detalle',
                     'cargar_satisfaccion', 'cargar_dqi', 'cargar_dpo_gkpis'):
            stack.enter_context(patch('pipeline.' + name, return_value={}))
        stack.enter_context(patch('pipeline.aplicar_tiempos_fichaya_guardados'))
        stack.enter_context(patch('pipeline.dqi_objetivo_bultos_mes', return_value=1))
        stack.enter_context(patch('pipeline.cargar_otif_clientes', side_effect=AssertionError('legacy calculation')))
        result = pipeline._data_desde_base({})
    assert result['rutas'] == []
    assert 'otif_clientes_dia' not in result


def test_sync_is_centralized_and_old_api_is_distinct():
    from app import _admin_page
    app.config.update(TESTING=True, SECRET_KEY='test')
    with app.test_request_context('/admin'), patch('pipeline.dqi_objetivo_bultos_mes', return_value=10):
        html = _admin_page()
        assert html.count('action="/cumplimiento-comprobantes/sincronizar"') == 1
        assert 'name=csrf' in html and 'name=modo' in html
        assert '<details><summary>Consulta histórica de pedidos por API</summary>' in html
    evidence = Path('templates/comprobantes.html').read_text(encoding='utf-8')
    assert 'method="post"' not in evidence
    assert evidence.count('<form ') == 1
    assert 'name="q"' in evidence and 'name="mes"' in evidence and 'name="dia"' in evidence


def test_sync_from_admin_preserves_mode_and_redirects_with_notice():
    app.config.update(TESTING=True, SECRET_KEY='test')
    client = app.test_client()
    with client.session_transaction() as s:
        s.update(admin_logged_in=True, logistics_csrf='token')
    result = dict(documentos=25, desde='2026-09-01', hasta='2026-09-19', modo='foxtrot')
    with patch('processed_logistics.sync_processed', return_value=result) as sync:
        response = client.post('/cumplimiento-comprobantes/sincronizar', data=dict(
            csrf='token', desde=result['desde'], hasta=result['hasta'], modo='foxtrot', volver='admin'))
        assert response.status_code == 302
        assert '/admin' in response.location
        sync.assert_called_once_with('2026-09-01', '2026-09-19', 'foxtrot')
    with client.session_transaction() as s:
        assert '25 comprobantes' in s['logistics_notice']
