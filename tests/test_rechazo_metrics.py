from rechazo_metrics import normalize_saved


def test_missing_api_metrics_are_derived_and_branch_does_not_leak():
    rows = normalize_saved([dict(fecha='2026-01-02',sucursal='1',sucursal_id='2',nds=0,
        raw_rechazo=dict(pedidos=100,pedidos_rechazo=7))])
    assert rows[0]['nds'] == 93
    assert rows[0]['pdv_unicos'] == 100
    assert rows[0]['sucursal_id'] == '1'


def test_alias_days_are_counted_once_and_real_zero_is_preserved():
    rows = normalize_saved([dict(fecha='2026-01-02',sucursal='Dolores',nds=90),
        dict(fecha='2026-01-02',sucursal='2',raw_rechazo=dict(pedidos=2,pedidos_rechazo=2))])
    assert len(rows) == 1 and rows[0]['nds'] == 0
    assert normalize_saved([dict(fecha='2026-01-02',sucursal='1',
        raw_rechazo=dict(pedidos=0,pedidos_rechazo=0))])[0]['nds'] is None
