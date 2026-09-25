"""Read-only distances per customer/route for the Dias Pico cost calculator."""
import json
import math
import os
import secrets
import unicodedata
from collections import Counter
from datetime import date
from urllib.parse import urlencode

from flask import Blueprint, jsonify, request
import storage

bp = Blueprint('pdv_distances', __name__)


def branch_id(value):
    value = ''.join(c for c in unicodedata.normalize('NFD', str(value or '').upper())
                    if not unicodedata.combining(c)).strip()
    return {'1': '1', 'CASA CENTRAL': '1', 'MAR DE AJO': '1',
            '2': '2', 'DOLORES': '2', 'SUCURSAL DOLORES': '2',
            '3': '3', 'CHASCOMUS': '3', 'SUCURSAL CHASCOMUS': '3'}.get(value, value)


def client_id(value):
    value = str(value or '').strip()
    if value.endswith('.0'):
        value = value[:-2]
    return str(int(value)) if value.isdigit() else value


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def validate_period(desde, hasta):
    start, end = date.fromisoformat(desde), date.fromisoformat(hasta)
    if end < start or (end - start).days > 366:
        raise ValueError('Seleccioná un período de hasta 366 días, con inicio anterior al fin.')
    return start.isoformat(), end.isoformat()


def load_snapshot(desde, hasta):
    """No dashboard LIMIT: a truncated population would understate customer costs."""
    if storage.backend_name() == 'postgres':
        storage.ensure_logistics_tables()
        with storage._conn() as cn, cn.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cur.execute("""SELECT rid, rec - 'raw_foxtrot' FROM rutas_dashboard
                           WHERE rec->>'fecha' BETWEEN %s AND %s""", (desde, hasta))
            routes = dict(cur.fetchall())
            cur.execute("""SELECT rid, rec FROM route_costs
                           WHERE rec->>'fecha' BETWEEN %s AND %s""", (desde, hasta))
            costs = dict(cur.fetchall())
            ids = sorted(set(routes) | set(costs))
            cur.execute('SELECT rid, rec FROM route_customer_costs WHERE rid = ANY(%s)', (ids,))
            customers = cur.fetchall()
            cur.execute("""SELECT route_id, jsonb_build_object(
                'cliente', COALESCE(NULLIF(rec->>'cliente', ''), rec->>'Customer ID'),
                'nombre', COALESCE(rec->'cliente_nombre', rec->'Customer Name'),
                'estado_entrega', COALESCE(rec->'estado_entrega', rec->'Aggregate Visit Status'))
                FROM attempts_dashboard WHERE route_id = ANY(%s)""", (ids,))
            attempts = cur.fetchall()
    else:
        routes = {str(k): r for k, r in storage.load_all().items()
                  if desde <= str(r.get('fecha') or '') <= hasta}
        path = os.path.join(storage.DATA_DIR, 'logistics_costs.json')
        with open(path, encoding='utf-8') if os.path.exists(path) else open(os.devnull) as source:
            content = source.read()
            base = json.loads(content) if content else {}
        costs = {str(k): r for k, r in base.get('route_costs', {}).items()
                 if desde <= str(r.get('fecha') or '') <= hasta}
        ids = set(routes) | set(costs)
        customers = [(str(rid), c) for rid, rows in base.get('route_customer_costs', {}).items()
                     if str(rid) in ids for c in rows]
        attempts = [(str(a.get('route_id') or a.get('Route ID') or ''), a)
                    for a in storage.load_attempts().values()
                    if str(a.get('route_id') or a.get('Route ID') or '') in ids]
    return routes, costs, customers, attempts


def distance_rows(routes, costs, customers, attempts):
    visits = {}
    for rid, item in attempts:
        cid = client_id(item.get('cliente') or item.get('Customer ID'))
        if cid:
            visits[(str(rid), cid)] = {
                'cliente': cid, 'nombre': item.get('nombre') or item.get('Customer Name'),
                'estado_entrega': item.get('estado_entrega') or item.get('Aggregate Visit Status'),
            }
    for rid, item in customers:
        cid = client_id(item.get('cliente'))
        if cid:
            visits[(str(rid), cid)] = item
    # One allocation unit per identified customer/route, including failed stops.
    # Count before any consumer branch, cluster or search filters.
    counts = Counter(rid for rid, cid in visits)
    rows = []
    for (rid, cid), item in sorted(visits.items()):
        route = {**routes.get(rid, {}), **costs.get(rid, {})}
        segments = route.get('routing_segments') or []
        forward = [s for s in segments if s.get('destination_ref') != 'deposito']
        # No inference from allocated costs: derive kilometers from the actual stored legs.
        matching = [s for s in forward if client_id(s.get('destination_ref')) == cid]
        legs = [number(s.get('distance_km')) for s in segments]
        returns = [s for s in segments if s.get('destination_ref') == 'deposito']
        complete = bool(matching and len(returns) == 1 and all(k is not None for k in legs))
        forward_total = sum(number(s.get('distance_km')) or 0 for s in forward)
        outward = sum(number(s.get('distance_km')) or 0 for s in matching) if complete else None
        back = number(returns[0].get('distance_km')) if complete else None
        # A positive return cannot be allocated proportionally over zero forward kilometers.
        if complete and forward_total == 0 and back:
            complete = False
        assigned_return = (back * outward / forward_total if forward_total else 0) if complete else None
        source_route = routes.get(rid, {})
        meters = number(source_route.get('disp_km_real'))
        if meters is None:
            meters = number((source_route.get('raw_foxtrot') or {}).get('Total Driven Meters'))
        # Zero is not evidence of a measured journey. Do not substitute planned
        # kilometers or GPS proximity-to-customer for actual driven meters.
        route_km = meters / 1000 if meters is not None and meters > 0 else None
        rows.append({
            'rid': rid, 'cliente': cid, 'nombre': item.get('nombre') or '',
            'sucursal': branch_id(route.get('sucursal_id') or route.get('sucursal') or route.get('suc')),
            'fecha': route.get('fecha'), 'orden': item.get('orden_visita'),
            'km_tramo': outward if complete else None,
            'km_regreso': assigned_return,
            'km_asignados': outward + assigned_return if complete else None,
            'km_recorrido_real': route_km,
            'pdv_recorrido': counts[rid],
            'km_prorrateados': route_km / counts[rid] if route_km is not None else None,
            'fuente_km_prorrateados': 'foxtrot_total_driven_meters' if route_km is not None else None,
            'distancia_estimada': any(s.get('fallback') for s in segments) if complete else None,
            'estado_entrega': item.get('estado_entrega') or '',
            'calculation_id': route.get('calculation_id'),
            'detalle_path': '/costos-distribucion?' + urlencode({'rid': rid}),
        })
    return rows


@bp.get('/api/integracion/v1/pdv-distancias')
def distances():
    key = os.environ.get('PDV_COST_API_KEY') or os.environ.get('LOGISTICS_INTEGRATION_API_KEY')
    if not key:
        return jsonify(ok=False, error='Integración de distancias no configurada.'), 503
    authorization = request.headers.get('Authorization', '')
    supplied = authorization[7:] if authorization.startswith('Bearer ') else ''
    if not supplied or not secrets.compare_digest(supplied.encode('utf-8'), key.encode('utf-8')):
        return jsonify(ok=False, error='No autorizado.'), 401
    try:
        desde, hasta = validate_period(request.args.get('desde', ''), request.args.get('hasta', ''))
    except (ValueError, TypeError):
        return jsonify(ok=False, error='Período inválido. Usá desde/hasta YYYY-MM-DD, hasta 366 días.'), 400
    try:
        routes, costs, customers, attempts = load_snapshot(desde, hasta)
        rows = distance_rows(routes, costs, customers, attempts)
        if len(rows) > 100000:
            return jsonify(ok=False, error='Período demasiado grande. Reducí el rango de fechas.'), 422
        return jsonify(ok=True, version=1, desde=desde, hasta=hasta, items=rows,
                       rutas=len(set(routes) | set(costs)),
                       rutas_sin_distancias=sum(not r.get('routing_segments') for r in
                                               ({**routes, **costs}).values()),
                       rutas_sin_km_reales=len((set(routes) | set(costs)) - {
                           r['rid'] for r in rows if r['km_prorrateados'] is not None}),
                       criterio='tramo_hasta_cliente_mas_regreso_proporcional',
                       unidad='cliente_por_recorrido')
    except Exception:
        from flask import current_app
        current_app.logger.exception('No se pudieron exportar distancias por PDV')
        return jsonify(ok=False, error='No se pudieron leer las distancias.'), 503
