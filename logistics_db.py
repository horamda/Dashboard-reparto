"""Live, read-only logistics data explorer. Never changes source or OTIF values."""
import os
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, timedelta, datetime, timezone

import psycopg2
from psycopg2 import sql

# Only explicitly supported business tables can be queried by the web page.
SOURCES = {
    'analisis': ('Ventas externas y visitas de esta app', 'ventas_detalle', 'fecha', 'cliente', 'sucursal', 'id'),
    'ventas': ('Ventas: todas las columnas de origen', 'ventas_detalle', 'fecha', 'cliente', 'sucursal', 'id'),
}


class SourceNotConfigured(ValueError):
    pass


@contextmanager
def connection():
    dsn = os.environ.get('LOGISTICS_DATABASE_URL', '').strip()
    if not dsn:
        raise SourceNotConfigured('Falta configurar LOGISTICS_DATABASE_URL en esta instalación.')
    cn = psycopg2.connect(dsn, connect_timeout=10,
                         options='-c default_transaction_read_only=on -c statement_timeout=30000',
                         application_name='dashboard_reparto_lectura')
    try:
        cn.set_session(readonly=True, isolation_level='REPEATABLE READ')
        yield cn
    finally:
        cn.rollback()
        cn.close()


def filters(args):
    source = args.get('fuente', 'analisis')
    if source not in SOURCES:
        raise ValueError('Fuente desconocida.')
    result = {k: str(args.get(k, '')).strip() for k in ('desde', 'hasta', 'cliente', 'sucursal', 'q')}
    result['fuente'] = source
    for k in ('desde', 'hasta'):
        if result[k]:
            date.fromisoformat(result[k])
    if result['desde'] and result['hasta'] and result['desde'] > result['hasta']:
        raise ValueError('La fecha desde debe ser anterior o igual a hasta.')
    if len(result['q']) > 200:
        raise ValueError('La búsqueda admite hasta 200 caracteres.')
    return result


def predicate(f):
    _, _, day, customer, branch, _ = SOURCES[f['fuente']]
    parts, params = [], []
    for key, op in (('desde', '>='), ('hasta', '<=')):
        if day and f[key]:
            parts.append(sql.SQL('t.{} ' + op + ' %s').format(sql.Identifier(day)))
            params.append(f[key])
    for key, column in (('cliente', customer), ('sucursal', branch)):
        if f[key]:
            if not column:
                raise ValueError('Esta fuente no tiene el campo ' + key + '.')
            parts.append(sql.SQL('TRIM(t.{}::text) = %s').format(sql.Identifier(column)))
            params.append(f[key])
    if f['q']:
        parts.append(sql.SQL('strpos(lower(to_jsonb(t)::text), lower(%s)) > 0'))
        params.append(f['q'])
    return sql.SQL(' AND ').join(parts) if parts else sql.SQL('TRUE'), params


def read_source(args, export=False):
    f = filters(args)
    label, table, day, _, _, pk = SOURCES[f['fuente']]
    where, params = predicate(f)
    try:
        page = max(1, int(args.get('pagina', 1)))
    except (TypeError, ValueError):
        page = 1
    with connection() as cn, cn.cursor() as cur:
        table_sql = sql.Identifier('public', table)
        cur.execute(sql.SQL('SELECT count(*), {} FROM {}').format(
            sql.SQL('min({0}), max({0})').format(sql.Identifier(day)) if day else sql.SQL('NULL, NULL'), table_sql))
        total, first, last = cur.fetchone()
        cur.execute(sql.SQL('SELECT count(*) FROM {} t WHERE {}').format(table_sql, where), params)
        count = cur.fetchone()[0]
        pages = max(1, (count + 49) // 50)
        page = min(page, pages)
        if export and count > 20000:
            raise ValueError('La descarga admite 20.000 líneas. Acotá el período, cliente o sucursal para descargar todos los resultados del filtro.')
        cur.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t WHERE {} ORDER BY t.{} LIMIT %s OFFSET %s').format(
            table_sql, where, sql.Identifier(pk)), params + [20000 if export else 50, 0 if export else (page-1)*50])
        rows = [r[0] for r in cur.fetchall()]
        # Column schema, rather than a sample, preserves every original field even in an empty result.
        cur.execute('SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position', ('public', table))
        columns = [r[0] for r in cur.fetchall()]
    if f['fuente'] == 'analisis':
        attach_visits(rows)
        columns += ['foxtrot_candidatos', 'estado_cruce']
    return dict(filters=f, label=label, table=table, total=total, count=count, first=first,
                last=last, rows=rows, columns=columns, page=page, pages=pages,
                consulted_at=datetime.now(timezone.utc).isoformat())


def attach_visits(rows):
    """Expose candidates within two days; never turn a candidate into OTIF compliance."""
    import storage
    from otif_customer_day import branch, historical_visits
    if not rows:
        return
    customers = {str(r.get('cliente') or '').strip() for r in rows}
    days = [str(r.get('fecha') or '')[:10] for r in rows]
    valid_days = [date.fromisoformat(d) for d in days if d]
    if not valid_days:
        for row in rows:
            row.update(foxtrot_candidatos=[], estado_cruce='Sin fecha de movimiento')
        return
    first, last = (min(valid_days)-timedelta(days=2)).isoformat(), (max(valid_days)+timedelta(days=2)).isoformat()
    fields = ('fecha','suc','sucursal_id','chofer','clientes_con_ventana','clientes_fuera_ontime',
              'clientes_sin_ventana','pdv_ontime','pdv_fuera_ontime','pdv_sin_ventana','pdv_total')
    routes = {k:v for k,v in storage.load_dashboard_routes(fields).items() if first <= str(v.get('fecha','')) <= last}
    if hasattr(storage, '_conn'):
        with storage._conn() as cn, cn.cursor() as cur:
            cur.execute('SELECT DISTINCT route_id FROM attempts_dashboard WHERE route_id = ANY(%s)', (list(routes),))
            detailed_ids = {r[0] for r in cur.fetchall()}
            cur.execute("SELECT rec FROM attempts_dashboard WHERE route_id = ANY(%s) AND rec->>'cliente' = ANY(%s)", (list(routes), list(customers)))
            visits = [r[0] for r in cur.fetchall()]
    else:
        all_visits = list(storage.load_attempts().values())
        detailed_ids = {str(v.get('route_id') or v.get('Route ID')) for v in all_visits}
        visits = [v for v in all_visits if str(v.get('cliente')) in customers]
    candidates = []
    for v in visits:
        rid = str(v.get('route_id') or v.get('Route ID'))
        r = routes.get(rid)
        if r:
            candidates.append((str(v.get('cliente')), branch(r.get('sucursal_id') or r.get('suc')), dict(
                ruta=rid, fecha=r['fecha'], hora=v.get('driver_click') or v.get('visit_start'),
                chofer=r.get('chofer'), estado=v.get('Aggregate Visit Status'), fuente='Visita Foxtrot')))
    for v in historical_visits(routes, detailed_ids):
        if v['cliente'] in customers:
            r = routes[v['route_id']]
            candidates.append((v['cliente'], branch(r.get('sucursal_id') or r.get('suc')), dict(
                ruta=v['route_id'], fecha=r['fecha'], hora=v['hora'], chofer=r.get('chofer'),
                estado=v['estado'], a_tiempo_historico=v['a_tiempo'], fuente=v['fuente'])))
    match_candidates(rows, candidates)


def match_candidates(rows, candidates):
    from otif_customer_day import branch
    indexed = defaultdict(list)
    for customer, sid, visit in candidates:
        if customer and sid:
            indexed[(customer.strip(), sid)].append(visit)
    for row in rows:
        found = []
        for visit in indexed.get((str(row.get('cliente') or '').strip(), branch(row.get('sucursal'))), []):
            if row.get('fecha'):
                delta = (date.fromisoformat(visit['fecha'])-date.fromisoformat(str(row['fecha'])[:10])).days
                if abs(delta) <= 2:
                    found.append(dict(visit, diferencia_dias=delta))
        row['foxtrot_candidatos'] = found
        same_day = [v for v in found if v['diferencia_dias'] == 0]
        row['estado_cruce'] = ('Coincidencia por cliente, sucursal y dia; no identifica el pedido' if same_day else
                               'Solo candidatos en dias cercanos: verificar' if found else 'Sin visita candidata')
