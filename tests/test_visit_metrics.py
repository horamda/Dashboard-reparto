from visit_metrics import summarize_visits


def test_deduplicate_and_do_not_use_inferred_service():
    a=dict(route_id='r',cliente='287',visit_start='2026-01-01T10:00:00',visit_duration_seconds=120)
    rows=summarize_visits([a,a,{**a,'visit_start':'2026-01-01T11:00:00','visit_duration_seconds':0,'service_duration_seconds':900}])
    assert len(rows)==1
    assert rows[0]['visitas']==2
    assert rows[0]['validas']==1
    assert rows[0]['segundos']==120


def test_revisits_and_invalid_duration():
    a=dict(route_id='r',cliente='287',visit_start='10:00',visit_duration_seconds=60)
    rows=summarize_visits([a,{**a,'route_id':'r2'}, {**a,'visit_start':'11:00','visit_duration_seconds':-5}])
    assert len(rows)==2
    assert sum(r['validas'] for r in rows)==2
    assert sum(r['visitas'] for r in rows)==3
