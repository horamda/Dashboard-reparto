"""Read-only access to documented current prices; no historical inference."""
import json
import math
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_cache = {}


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
