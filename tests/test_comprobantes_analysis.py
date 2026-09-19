from collections import Counter
from comprobantes_analysis import evaluate_documents


def doc(state='sin_rechazo',time=True,**kw):
    return dict(estado_rechazo=state,doc_key=['comprobante','FC','A','1','100'],
                cliente='1',sucursal='1',empresa='1',
                foxtrot_candidatos=[dict(diferencia_dias=0,a_tiempo=time)],**kw)


def test_three_documents_one_total_rejected_gives_two_thirds():
    rows=[doc(),doc(),doc('total')]
    evaluate_documents(rows)
    c=Counter(r['resultado_otif'] for r in rows)
    assert c=={'cumple':2,'no_cumple':1}
    assert round(100*c['cumple']/len(rows),2)==66.67


def test_partial_and_total_both_fail_and_late_fails():
    rows=[doc('total'),doc('parcial'),doc(time=False)]
    evaluate_documents(rows)
    assert all(r['resultado_otif']=='no_cumple' for r in rows)
    assert rows[0]['rechazo_total'] is True and rows[0]['rechazo_parcial'] is False
    assert rows[1]['rechazo_parcial'] is True and rows[1]['rechazo_total'] is False


def test_missing_data_nearby_and_mixed_visits_do_not_enter_denominator():
    rows=[doc('total',None),doc('sin_determinar'),doc()]
    rows[2]['foxtrot_candidatos'][0]['diferencia_dias']=1
    mixed=doc()
    mixed['foxtrot_candidatos'].append(dict(diferencia_dias=0,a_tiempo=False))
    rows.append(mixed)
    evaluate_documents(rows)
    assert all(r['resultado_otif']=='pendiente' for r in rows)


def test_confirmed_rejection_unknown_scope_still_fails():
    rows=[doc('rechazo_sin_alcance')]
    evaluate_documents(rows)
    assert rows[0]['resultado_otif']=='no_cumple'
    assert rows[0]['tiene_rechazo'] is True
    assert rows[0]['rechazo_total'] is None
