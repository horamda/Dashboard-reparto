"""Count complete documents, never article rows, and retain both kinds of rejection."""
from datetime import datetime, timezone, date
from calendar import monthrange
from collections import Counter, defaultdict
from psycopg2 import sql
from logistics_db import connection, filters, predicate, attach_visits


def source_query(args):
    args = dict(args)
    if args.get('dia'):
        args['desde'] = args['hasta'] = date.fromisoformat(args['dia']).isoformat()
    elif args.get('mes'):
        first = date.fromisoformat(args['mes']+'-01')
        args['desde'] = first.isoformat()
        args['hasta'] = first.replace(day=monthrange(first.year,first.month)[1]).isoformat()
    f = filters(dict(args, fuente='analisis'))
    # Search selects entire documents; it must not hide rejected article lines.
    query = f['q']
    where, params = predicate(dict(f, q=''))
    cte = sql.SQL('''WITH base AS MATERIALIZED (
      SELECT t.*, COALESCE(NULLIF(trim(t.empresa),''),'1') AS empresa_key,
        COALESCE(NULLIF(trim(t.sucursal),''),'1') AS sucursal_key,
        CASE WHEN nullif(trim(t.documento),'') IS NOT NULL
                   AND nullif(trim(t.serie),'') IS NOT NULL AND nullif(trim(t.numero),'') IS NOT NULL
             THEN jsonb_build_array('comprobante',trim(t.documento),coalesce(trim(t.letra),''),trim(t.serie),trim(t.numero))
             WHEN nullif(trim(t.detalle_documento),'') IS NOT NULL THEN jsonb_build_array('detalle',trim(t.detalle_documento))
             ELSE jsonb_build_array('fila',t.id) END AS doc_key,
        (coalesce(t.bultos_rechazados,0)>0 OR coalesce(t.unidad_medida_rechazado,0)>0 OR coalesce(t.unidad_paquete_rechazado,0)>0) AS rechazado,
        upper(trim(coalesce(t.rechazo_total,''))) IN ('SI','SÍ','S','YES','Y','TRUE','1','X') AS total_afirmativo,
        (t.bultos_rechazados IS NULL OR t.unidad_medida_rechazado IS NULL OR t.unidad_paquete_rechazado IS NULL
         OR coalesce(t.bultos_rechazados,0)<0 OR coalesce(t.unidad_medida_rechazado,0)<0 OR coalesce(t.unidad_paquete_rechazado,0)<0) AS faltantes
      FROM public.ventas_detalle t JOIN public.articulos a ON a.id_articulo=t.id_articulo
      WHERE {where} AND lower(trim(coalesce(a.tipo_producto,'')))='mercaderia'
        AND lower(trim(coalesce(t.documento,''))) NOT LIKE 'remit%%'
        AND lower(trim(coalesce(t.documento,''))) NOT LIKE 'comod%%'
        AND lower(trim(coalesce(t.detalle_documento,''))) NOT LIKE 'remit%%'
        AND lower(trim(coalesce(t.detalle_documento,''))) NOT LIKE 'comod%%'
    ), documentos AS MATERIALIZED (
      SELECT empresa_key AS empresa,sucursal_key AS sucursal,trim(cliente) AS cliente,fecha,doc_key,
        max(descripcion_cliente) AS nombre, count(*) AS lineas,
        max(detalle_documento) AS documento_origen,
        array_agg(DISTINCT motivo_rechazo) FILTER(WHERE nullif(trim(motivo_rechazo),'') IS NOT NULL) AS motivos,
        CASE WHEN count(bultos)=count(*) THEN sum(bultos) END AS bultos,
        CASE WHEN count(bultos_rechazados)=count(*) THEN sum(bultos_rechazados) END AS bultos_rechazados,
        CASE WHEN count(unidad_medida)=count(*) THEN sum(unidad_medida) END AS hl,
        CASE WHEN count(unidad_medida_rechazado)=count(*) THEN sum(unidad_medida_rechazado) END AS hl_rechazados,
        array_agg(id ORDER BY id) AS ids,
        CASE WHEN doc_key->>0='fila' OR nullif(trim(cliente),'') IS NULL THEN 'sin_determinar'
             WHEN bool_and(rechazado AND total_afirmativo) THEN 'total'
             WHEN bool_or(rechazado) AND bool_and(NOT faltantes AND upper(trim(coalesce(rechazo_total,''))) IN ('SI','SÍ','S','YES','Y','TRUE','1','X','NO','N','FALSE','0')) THEN 'parcial'
             WHEN bool_or(rechazado) THEN 'rechazo_sin_alcance'
             WHEN bool_or(faltantes OR total_afirmativo) THEN 'sin_determinar'
             ELSE 'sin_rechazo' END AS estado_rechazo
      FROM base GROUP BY empresa_key,sucursal_key,trim(cliente),fecha,doc_key
      HAVING {search}
    ) ''').format(where=where,search=sql.SQL('bool_or(strpos(lower(to_jsonb(base)::text),lower(%s))>0)') if query else sql.SQL('TRUE'))
    if query:
        params += [query]
    return f, cte, params


def load_source_documents(args):
    f,cte,params=source_query(args)
    with connection() as cn,cn.cursor() as cur:
        cur.execute(cte + sql.SQL('SELECT to_jsonb(d) FROM documentos d ORDER BY fecha,empresa,sucursal,cliente,doc_key LIMIT 100001'),params)
        rows=[r[0] for r in cur.fetchall()]
    if len(rows)>100000:
        raise ValueError('Acota la sincronizacion a un maximo de 100.000 comprobantes.')
    return rows


def read_comprobantes(args, export=False):
    from processed_logistics import read_processed
    return read_processed(args,export=export)


def evaluate_documents(rows):
    """Operational rule: same-day punctuality plus absence of total OR partial rejection.

    Missing punctuality stays pending even with a known rejection, so successes and
    failures enter the denominator under the same evidence requirements.
    """
    for row in rows:
        state=row['estado_rechazo']
        rejected=None if state=='sin_determinar' else state!='sin_rechazo'
        visits=[v for v in row.get('foxtrot_candidatos',[]) if v['diferencia_dias']==0]
        times=[v.get('a_tiempo',v.get('a_tiempo_historico')) for v in visits]
        on_time=None
        if times and all(t is True for t in times):
            on_time=True
        elif times and all(t is False for t in times):
            on_time=False
        identified=row['doc_key'][0]!='fila' and bool(row.get('cliente')) and bool(row.get('sucursal'))
        # The dashboard operates company 1; never attribute another company's visits.
        identified=identified and str(row.get('empresa'))=='1'
        row['on_time']=on_time
        row['rechazo_total']=True if state=='total' else None if state in ('sin_determinar','rechazo_sin_alcance') else False
        row['rechazo_parcial']=True if state=='parcial' else None if state in ('sin_determinar','rechazo_sin_alcance') else False
        row['tiene_rechazo']=rejected
        row['resultado_otif']=('pendiente' if not identified or on_time is None or rejected is None else
                               'cumple' if on_time and not rejected else 'no_cumple')
