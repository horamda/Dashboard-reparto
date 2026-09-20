"""Read-only access to documented current prices; no historical inference."""
import json
import math
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_cache = {}


def argly_gasoil():
    """Current Buenos Aires grade-2 diesel average, not a historical tariff."""
    key = ('argly', 'buenos-aires', 'gasoil-grado-2')
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < 21600:
        return cached[1]
    url = 'https://api.argly.com.ar/v1/combustibles/promedio?' + urlencode({
        'provincia': 'buenos-aires', 'combustible': 'gasoil-grado-2'})
    with urlopen(Request(url, headers={'Accept': 'application/json'}), timeout=15) as response:
        data = json.loads(response.read(100_000))['data']
    price = float(data['precio_promedio'])
    if not math.isfinite(price) or price <= 0 or data.get('provincia') != 'buenos-aires' or data.get('combustible') != 'Gasoil Grado 2':
        raise ValueError('Respuesta de Argly inválida')
    result = {'rows': [dict(city='Buenos Aires', company='Promedio provincial · Argly',
                           fuel='Gasoil Grado 2', price=price, effective_at=None, period=None)],
              'source': 'https://argly.com.ar/#docs', 'historical': False}
    _cache[key] = (time.monotonic(), result)
    return result


def normalize_prices(payload):
    rows = []
    if not isinstance(payload, dict):
        raise ValueError('Respuesta de precios inválida')
    for city, content in payload.items():
        if not isinstance(content, dict):
            continue
        for company, fuels in content.get('empresas', {}).items():
            for fuel, item in fuels.items():
                if not isinstance(item, dict):
                    continue
                try:
                    price = float(item.get('precio'))
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(price) or price <= 0 or 'gnc' in fuel.lower():
                    continue
                rows.append(dict(city=city, company=company, fuel=fuel, price=price,
                                 effective_at=item.get('fecha_vigencia'), period=item.get('horario')))
    return sorted(rows, key=lambda r: (r['city'], r['company'], r['fuel']))


def current_prices(city):
    city = city.strip().upper()
    if not city or len(city) > 100:
        raise ValueError('Ingresá una ciudad de hasta 100 caracteres')
    cached = _cache.get(city)
    if cached and time.monotonic() - cached[0] < 21600:
        return cached[1]
    req = Request('https://naftas.com.ar/api/fuel?' + urlencode({'city': city}),
                  headers={'Accept': 'application/json'})
    with urlopen(req, timeout=15) as response:
        payload = json.loads(response.read(2_000_000))
    result = {'rows': normalize_prices(payload), 'source': 'https://www.naftas.com.ar/api-docs',
              'historical': False}
    if len(_cache) >= 100:
        _cache.clear()
    _cache[city] = (time.monotonic(), result)
    return result
