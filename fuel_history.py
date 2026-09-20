"""Persist daily provincial diesel references reconstructed from official declarations."""
import json
import math
import threading
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from urllib.request import urlopen
from urllib.parse import urlencode
import storage

BASE = 'https://datos.energia.gob.ar/dataset/1c181390-5045-475e-94dc-410429be4b17/resource/'
URLS = [BASE+'f8dda0d5-2a9f-4d34-b79b-4e63de3995df/download/precios-historicos.csv',
        BASE+'80ac25de-a44a-4445-9215-090cf55cfda5/download/precios-en-surtidor-resolucin-3142016.csv']
KEY = 'fuel_daily_buenos_aires_grade2_v1'
_lock = threading.Lock()
_last_attempt = None
_error = None


def collect_events(rows, events):
    for index, r in enumerate(rows):
        if index and index % 500000 == 0:
            print("Fuel history: scanned", index, "source rows", flush=True)
        if r.get('provincia','').strip().upper() != 'BUENOS AIRES' or str(r.get('idproducto')) != '19' or str(r.get('idtipohorario')) != '2':
            continue
        try:
            raw = r['fecha_vigencia']
            when = datetime.strptime(raw, '%d/%m/%Y %H:%M') if '/' in raw else datetime.fromisoformat(raw)
            price = float(r['precio'])
            if not math.isfinite(price) or price <= 0:
                continue
        except (ValueError, KeyError):
            continue
        # A station contributes once per day; multiple changes use its last declaration.
        station = (r.get('idempresa',''), r.get('direccion',''), r.get('localidad',''))
        if not station[0]:
            continue
        events.setdefault(station, {})[when] = price


def daily_series(events, start, end):
    series = [(sorted(v), v) for v in events.values()]
    prices, coverage = {}, {}
    day = start
    while day <= end:
        cutoff = datetime.combine(day, datetime.max.time())
        values = []
        for dates, vals in series:
            index = bisect_right(dates, cutoff)-1
            if index >= 0 and (day-dates[index].date()).days <= 60:
                values.append(vals[dates[index]])
        if values:
            prices[day.isoformat()] = round(sum(values)/len(values), 6)
            coverage[day.isoformat()] = len(values)
        day += timedelta(days=1)
    return prices, coverage


def sync():
    global _last_attempt, _error
    if not _lock.acquire(blocking=False):
        return
    _last_attempt = datetime.now(timezone.utc)
    try:
        events = {}
        for resource in ['f8dda0d5-2a9f-4d34-b79b-4e63de3995df', '80ac25de-a44a-4445-9215-090cf55cfda5']:
            offset = 0
            while True:
                sql = ('SELECT idempresa,direccion,localidad,provincia,idproducto,idtipohorario,precio,fecha_vigencia '
                       'FROM "'+resource+'" WHERE provincia=\'BUENOS AIRES\' AND idproducto=19 AND idtipohorario=2 '
                       'AND fecha_vigencia >= \'2023-11-01\' ORDER BY idempresa,fecha_vigencia,direccion,localidad '
                       'LIMIT 10000 OFFSET '+str(offset))
                url = 'https://datos.energia.gob.ar/api/3/action/datastore_search_sql?'+urlencode({'sql':sql})
                with urlopen(url, timeout=45) as response:
                    payload = json.load(response)
                if not payload.get('success'):
                    raise ValueError('La consulta oficial fallo')
                rows = payload['result']['records']
                collect_events(rows, events)
                print('Fuel history: imported',len(rows),'filtered rows',flush=True)
                if len(rows)<10000:
                    break
                offset += len(rows)
        today = datetime.now(timezone(timedelta(hours=-3))).date()
        prices, coverage = daily_series(events, date(2024,1,1), today)
        if not prices:
            raise ValueError('No hay precios historicos validos')
        result = dict(prices=prices, coverage=coverage, sources=URLS,
                      updated_at=datetime.now(timezone.utc).isoformat(), province='Buenos Aires',
                      fuel='Gasoil Grado 2', currency='ARS', unit='litro',
                      methodology='Promedio simple provincial diario de la ultima declaracion diurna por estacion, con antiguedad maxima de 60 dias. Sin usar declaraciones futuras.')
        storage.save_setting(KEY, result)
        _error = None
        return result
    except Exception:
        _error = 'No se pudo actualizar la fuente oficial; se conservan los precios guardados.'
        raise
    finally:
        _lock.release()


def get_history():
    saved = storage.load_setting(KEY) or {}
    now = datetime.now(timezone.utc)
    updated = datetime.fromisoformat(saved['updated_at']) if saved.get('updated_at') else None
    if (not updated or (now-updated).total_seconds()>86400) and (not _last_attempt or (now-_last_attempt).total_seconds()>1800):
        def run():
            try:
                sync()
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()
    return {**saved, 'updating': _lock.locked(), 'error': _error}
