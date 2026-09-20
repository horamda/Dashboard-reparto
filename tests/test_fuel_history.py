from datetime import date, datetime
from fuel_history import daily_series, collect_events


def test_no_future_price_or_stale_carryover():
    events = {'a': {datetime(2026,1,1):100, datetime(2026,1,3):200}, 'b': {datetime(2026,1,2):300}}
    prices, coverage = daily_series(events, date(2025,12,31), date(2026,3,10))
    assert '2025-12-31' not in prices
    assert prices['2026-01-01'] == 100
    assert prices['2026-01-02'] == 200
    assert prices['2026-01-03'] == 250
    assert coverage['2026-01-02'] == 2
    assert '2026-03-10' not in prices


def test_only_diurnal_grade2_buenos_aires():
    r=dict(provincia='BUENOS AIRES',idproducto='19',idtipohorario='2',fecha_vigencia='01/01/2026 12:00',precio='100',idempresa='1')
    events={}
    collect_events([r,r,{**r,'idtipohorario':'3','precio':'900'},{**r,'idproducto':'21','precio':'800'}],events)
    prices,coverage=daily_series(events,date(2026,1,1),date(2026,1,1))
    assert prices['2026-01-01']==100
    assert coverage['2026-01-01']==1

def test_sync_persists_source_and_preserves_on_failure(monkeypatch):
    import fuel_history as h
    from io import BytesIO
    saved=[]
    csv=b'{"success":true,"result":{"records":[{"idempresa":1,"provincia":"BUENOS AIRES","idproducto":19,"idtipohorario":2,"fecha_vigencia":"2026-01-01T12:00:00","precio":100}]}}' 
    monkeypatch.setattr(h,'urlopen',lambda *a,**k:BytesIO(csv))
    monkeypatch.setattr(h.storage,'save_setting',lambda key,value:saved.append((key,value)))
    result=h.sync()
    assert result['prices']['2026-01-01']==100
    assert saved[0][0]==h.KEY
    assert result['sources']==h.URLS
    def fail(*a,**k): raise OSError('offline')
    monkeypatch.setattr(h,'urlopen',fail)
    import pytest
    with pytest.raises(OSError): h.sync()
    assert len(saved)==1
