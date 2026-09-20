from fuel_prices import normalize_prices, current_prices
import pytest


def test_prices_keep_source_and_drop_invalid_or_gnc():
    rows = normalize_prices({'CITY': {'empresas': {'BRAND': {
        'Gasoil': {'precio': 1500.5, 'fecha_vigencia': '2026-09-01', 'horario': 'dia'},
        'GNC': {'precio': 800}, 'Invalid': {'precio': 'nan'}, 'Negative': {'precio': -1}
    }}}})
    assert len(rows) == 1
    assert rows[0]['price'] == 1500.5
    assert rows[0]['fuel'] == 'Gasoil'
    assert rows[0]['effective_at'] == '2026-09-01'


def test_city_required():
    with pytest.raises(ValueError):
        current_prices(' ')


def test_current_prices_cache(monkeypatch):
    import fuel_prices
    from io import BytesIO
    calls = []
    def fake(req, timeout):
        calls.append(req.full_url)
        return BytesIO(b'{"TEST":{"empresas":{"BRAND":{"Gasoil":{"precio":1000}}}}}')
    monkeypatch.setattr(fuel_prices, 'urlopen', fake)
    monkeypatch.setattr(fuel_prices, '_cache', {})
    assert current_prices('test')['historical'] is False
    assert current_prices('TEST')['rows'][0]['price'] == 1000
    assert len(calls) == 1
