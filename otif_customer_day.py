"""Operational OTIF at branch/customer/scheduled-day grain; never infer missing deliveries."""
from collections import defaultdict
from datetime import datetime
import unicodedata


def text(value):
    return str(value if value is not None else '').strip()


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', text(value).upper())
                   if not unicodedata.combining(c))


def branch(value):
    value = normalized(value)
    if value in ('1', 'CASA CENTRAL', 'MARDE AJO', 'MARDEAJO') or 'MAR DE AJO' in value:
        return '1'
    if value == '2' or 'DOLORES' in value:
        return '2'
    if value == '3' or 'CHASCOMUS' in value:
        return '3'
    return value


def documents(order):
    return {text(x) for x in order.get('comprobantes', []) if text(x)}


def build_customer_days(snapshot, attempts, routes, clients, window_check):
    """Use one complete snapshot, not a union of stale overlapping queries.

    Full delivery means ENTREGADO in repartos, not ordered-quantity reconciliation.
    Multiple visits with mixed punctuality stay pending without an order/visit link.
    Sales rejections are attributed only when all their documents belong to this day.
    """
    groups = {}
    start, end = snapshot.get('desde', ''), snapshot.get('hasta', '')
    scope_branch = snapshot.get('sucursal', 'TODAS')

    def group(key):
        if key not in groups:
            sid, customer, day = key
            groups[key] = dict(key='|'.join(key), sucursal_id=sid, cliente=customer,
                               fecha=day, mes=day[:7], orders=[], visits=[])
        return groups[key]

    seen = set()
    for index, order in enumerate(snapshot.get('datos', [])):
        identity = text(order.get('id_integracion')) or 'unidentified:' + str(index)
        if identity in seen:
            continue
        seen.add(identity)
        day = text(order.get('fecha_entrega'))
        if not start <= day <= end:
            continue
        customer = text(order.get('cliente_id'))
        sid = branch(order.get('sucursal_id'))
        # Unknown customers are never merged with one another.
        key = (sid, customer or 'SIN CLIENTE:' + identity, day)
        group(key)['orders'].append(order)

    seen_visits = set()
    for visit in attempts:
        rid = text(visit.get('route_id') or visit.get('Route ID'))
        route = routes.get(rid, {})
        day = text(route.get('fecha'))
        sid = branch(visit.get('sucursal_id') or route.get('sucursal_id') or route.get('suc'))
        customer = text(visit.get('cliente'))
        if not customer or not start <= day <= end or (scope_branch != 'TODAS' and sid != branch(scope_branch)):
            continue
        stamp = visit.get('driver_click') or visit.get('visit_start')
        identity = text(visit.get('attempt_key')) or (rid, customer, text(stamp), text(visit.get('Waypoint ID')))
        if identity in seen_visits:
            continue
        seen_visits.add(identity)
        value = window_check(stamp, clients.get(customer, {}).get('ventanas', []), day)
        group((sid, customer, day))['visits'].append(dict(
            rid=rid, chofer=route.get('chofer', ''), suc=route.get('suc', ''),
            hora=text(stamp), a_tiempo=value,
            estado=text(visit.get('Aggregate Visit Status'))))

    rejections_by_customer = defaultdict(list)
    for rejection in snapshot.get('rechazos_clientes', []):
        rejections_by_customer[text(rejection.get('cliente_id'))].append(rejection)

    output = []
    for row in groups.values():
        orders, visits = row.pop('orders'), row.pop('visits')
        sid, customer = row['sucursal_id'], row['cliente']
        reasons = []
        states = [o.get('estado_entrega') for o in orders]
        docs = set().union(*(documents(o) for o in orders)) if orders else set()
        rejected = any(s in ('rechazada', 'parcial') for s in states)
        motives = set()
        for order in orders:
            for line in order.get('detalle', []):
                if normalized(line.get('estado')) in ('RECHAZADO', 'PARCIAL'):
                    rejected = True
                motive = text(line.get('motivo_rechazo'))
                if motive and normalized(motive) not in ('0', 'NINGUNO', 'SIN RECHAZO', 'NO APLICA'):
                    motives.add(motive)
                    # A motive alone with no rejected state is a conflict, not proof of quantity.
                    if normalized(line.get('estado')) not in ('RECHAZADO', 'PARCIAL'):
                        reasons.append('Motivo de rechazo con estado contradictorio')
        rejection_ambiguous = False
        for rec in rejections_by_customer.get(customer, []):
            if rec.get('tiene_rechazo') is not True:
                continue
            rec_branches = {branch(s) for s in rec.get('sucursales', [])}
            if sid not in rec_branches:
                continue
            # detalle_documento is the only exact printable reference in this contract.
            rec_docs = {text(d.get('detalle_documento')) for d in rec.get('documentos', [])}
            overlap = bool(docs & rec_docs)
            if overlap and '' not in rec_docs and rec_docs <= docs and rec_branches == {sid}:
                rejected = True
                motives.update(text(m.get('motivo')) for m in rec.get('motivos', []) if m.get('motivo'))
            elif overlap or rec.get('fecha') == row['fecha']:
                rejection_ambiguous = True
        if rejection_ambiguous:
            reasons.append('Rechazo de ventas pendiente de atribuir a la entrega')
        identified = bool(sid and not customer.startswith('SIN CLIENTE:'))
        if not identified:
            reasons.append('Falta cliente o sucursal')
        if not orders:
            reasons.append('Visita sin pedidos en la consulta API')
        if any(o.get('tipo_identificador') == 'fila' for o in orders):
            reasons.append('Registro sin pedido ni comprobante')
        complete = bool(orders) and all(s == 'completa' for s in states)
        if orders and not complete and not rejected:
            reasons.append('Entrega pendiente o sin confirmar')
        times = [v['a_tiempo'] for v in visits]
        on_time = None
        if not visits:
            reasons.append('Sin visita Foxtrot del cliente en el dia')
        elif any(t is None for t in times):
            reasons.append('Falta horario valido o ventana horaria')
        elif all(times):
            on_time = True
        elif not any(times):
            on_time = False
        else:
            reasons.append('Varias visitas con puntualidad diferente')
        if any(normalized(v['estado']) != 'SUCCESSFUL' for v in visits):
            reasons.append('Visita sin confirmacion de exito')
        # An identified physical rejection proves failure independently of punctuality.
        if identified and (rejected or (complete and on_time is False)):
            result = 'no_cumple'
        elif identified and complete and on_time is True and not reasons:
            result = 'cumple'
        else:
            result = 'pendiente'
        if rejected:
            reasons.insert(0, 'Entrega parcial o rechazo registrado')
        if on_time is False:
            reasons.insert(0, 'Fuera de ventana horaria')
        row.update(nombre=next((o.get('cliente_nombre') for o in orders if o.get('cliente_nombre')),
                               clients.get(customer, {}).get('nombre', '')),
                   suc=next((v['suc'] for v in visits if v['suc']),
                            {'1': 'Mar de Ajo', '2': 'Dolores', '3': 'Chascomus'}.get(sid, sid or 'Sin sucursal')),
                   choferes=sorted({v['chofer'] for v in visits if v['chofer']}),
                   rutas=sorted({v['rid'] for v in visits}), pedidos=len(orders),
                   visitas=visits, a_tiempo=on_time, completa=complete,
                   rechazo=rejected, motivos=sorted(motives), resultado=result,
                   razones=list(dict.fromkeys(reasons)),
                   detalle=[dict(pedido=o.get('numero_pedido') or '',
                                 comprobantes=sorted(documents(o)), estado=o.get('estado_entrega')) for o in orders])
        output.append(row)
    return sorted(output, key=lambda r: (r['fecha'], r['sucursal_id'], r['cliente']))


def latest_snapshot(settings):
    last = (settings.get('otif_ultima_consulta') or {}).get('valor', {})
    if not last:
        return {}
    key = 'otif_pedidos:' + ':'.join(text(last.get(k)) for k in ('desde', 'hasta', 'sucursal'))
    return (settings.get(key) or {}).get('valor', {})
