from otif_customer_day import build_customer_days, latest_snapshot
from pipeline import _otif_window_check


def order(ref='p1', **extra):
    return dict(id_integracion=ref, numero_pedido=ref, cliente_id='001', sucursal_id='1',
                fecha_entrega='2026-05-01', estado_entrega='completa', tipo_identificador='pedido',
                comprobantes=[ref], **extra)


def visit(ref='v1', time='2026-05-01 10:00:00', **extra):
    return dict(attempt_key=ref, cliente='001', route_id='r1', driver_click=time,
                **{'Aggregate Visit Status': 'SUCCESSFUL'}, **extra)


def build(orders=None, visits=None, rejections=None):
    return build_customer_days(
        dict(desde='2026-05-01', hasta='2026-05-02', sucursal='TODAS',
             datos=orders if orders is not None else [order()], rechazos_clientes=rejections or []),
        visits if visits is not None else [visit()],
        {'r1': dict(fecha='2026-05-01', suc='Mar de Ajó', chofer='Ana')},
        {'001': {'ventanas': [{'ini': 540, 'fin': 780}]}}, _otif_window_check)


def test_multiple_orders_and_duplicate_visits_count_one_day():
    rows = build([order(), order('p2'), order()], [visit(), visit()])
    assert len(rows) == 1
    assert rows[0]['pedidos'] == 2
    assert len(rows[0]['visitas']) == 1
    assert rows[0]['resultado'] == 'cumple'


def test_overlap_late_and_rejected_counts_one_failure():
    bad = order()
    bad['estado_entrega'] = 'parcial'
    row = build([bad], [visit(time='2026-05-01 15:00:00')])[0]
    assert row['resultado'] == 'no_cumple'
    assert row['rechazo'] is True


def test_missing_and_mixed_visits_are_pending():
    assert build(visits=[])[0]['resultado'] == 'pendiente'
    assert build(visits=[visit(time='invalid')])[0]['resultado'] == 'pendiente'
    assert build(visits=[visit(), visit('v2', '2026-05-01 15:00:00')])[0]['resultado'] == 'pendiente'


def test_no_orders_is_not_delivery_success():
    assert build(orders=[])[0]['resultado'] == 'pendiente'


def test_branch_and_leading_zeros_do_not_cross():
    bad = order()
    bad['cliente_id'] = '1'
    assert all(r['resultado'] == 'pendiente' for r in build([bad]))
    bad = order()
    bad['sucursal_id'] = '2'
    assert all(r['resultado'] == 'pendiente' for r in build([bad]))


def test_accounting_rejection_requires_document_attribution():
    rejection = dict(cliente_id='001', sucursales=['1'], fecha='2026-05-02',
                     tiene_rechazo=True, documentos=[{'detalle_documento': 'p1'}])
    assert build(rejections=[rejection])[0]['resultado'] == 'no_cumple'
    rejection['documentos'].append({'detalle_documento': 'another-day'})
    assert build(rejections=[rejection])[0]['resultado'] == 'pendiente'
    rejection['documentos'] = [{'detalle_documento': 'unrelated'}]
    assert build(rejections=[rejection])[0]['resultado'] == 'cumple'
    rejection['fecha'] = '2026-05-01'
    assert build(rejections=[rejection])[0]['resultado'] == 'pendiente'


def test_unknown_status_unidentified_and_missing_branch_pending():
    for field, value in [('estado_entrega', 'en_transito'), ('tipo_identificador', 'fila'),
                         ('sucursal_id', None)]:
        bad = order()
        bad[field] = value
        assert build([bad])[0]['resultado'] == 'pendiente'


def test_timestamp_timezone_and_overnight_windows():
    assert _otif_window_check('2026-05-01T13:00:00Z', [{'ini': 540, 'fin': 780}], '2026-05-01') is True
    assert _otif_window_check('2026-05-02 10:00:00', [{'ini': 540, 'fin': 780}], '2026-05-01') is None
    assert _otif_window_check('2026-05-01 23:30:00', [{'ini': 1320, 'fin': 120}], '2026-05-01') is True


def test_latest_snapshot_does_not_resurrect_stale_orders():
    settings = {'otif_ultima_consulta': {'valor': dict(desde='2026-05-01', hasta='2026-05-02', sucursal='TODAS')},
                'otif_pedidos:2026-05-01:2026-05-02:TODAS': {'valor': {'datos': []}},
                'otif_pedidos:2026-01-01:2026-05-02:TODAS': {'valor': {'datos': [order()]}}}
    assert latest_snapshot(settings)['datos'] == []
