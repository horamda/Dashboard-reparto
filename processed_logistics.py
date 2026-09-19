"""Transactional, rebuildable logistics projection in the dashboard database."""
import hashlib
import json
import threading
from datetime import date, datetime, timezone
from calendar import monthrange

from psycopg2.extras import Json, execute_values
import storage

_ready = False
_schema_lock = threading.Lock()
_sync_lock = 78201626


def ensure_schema():
    global _ready
    if _ready:
        return
    if not hasattr(storage, '_conn'):
        raise ValueError('El almacenamiento procesado requiere PostgreSQL en esta app.')
    with _schema_lock:
        if _ready:
            return
        with storage._conn() as cn, cn.cursor() as cur:
            cur.execute('''CREATE TABLE IF NOT EXISTS logistics_processed_docs (
                key text PRIMARY KEY, fecha date NOT NULL, empresa text NOT NULL,
                sucursal text NOT NULL, cliente text, rec jsonb NOT NULL,
                ventas_sync_at timestamptz NOT NULL, foxtrot_calculado_at timestamptz NOT NULL);
                CREATE INDEX IF NOT EXISTS logistics_processed_docs_filters
                  ON logistics_processed_docs(fecha,sucursal,cliente);
                CREATE TABLE IF NOT EXISTS logistics_processed_runs (
                  id bigserial PRIMARY KEY, desde date NOT NULL, hasta date NOT NULL,
                  modo text NOT NULL, documentos integer NOT NULL,
                  completed_at timestamptz NOT NULL DEFAULT now());''')
        _ready = True


def period(args):
    from logistics_db import filters
    args = dict(args)
    if args.get('dia'):
        args['desde'] = args['hasta'] = date.fromisoformat(args['dia']).isoformat()
    elif args.get('mes'):
        first = date.fromisoformat(args['mes']+'-01')
        args['desde'], args['hasta'] = first.isoformat(), first.replace(day=monthrange(first.year,first.month)[1]).isoformat()
    return filters(dict(args,fuente='analisis'))


def document_key(row):
    return hashlib.sha256(json.dumps([row['empresa'],row['sucursal'],row['cliente'],row['fecha'],row['doc_key']],
                                     ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def sync_processed(desde, hasta, mode='ventas'):
    from comprobantes_analysis import load_source_documents, evaluate_documents
    from logistics_db import attach_visits
    start,end=date.fromisoformat(desde),date.fromisoformat(hasta)
    if start>end or (end-start).days>366 or mode not in ('ventas','foxtrot'):
        raise ValueError('Seleccioná un rango válido de hasta 367 días.')
    ensure_schema()
    with storage._conn() as cn, cn.cursor() as cur:
        cur.execute('SELECT pg_try_advisory_xact_lock(%s)',(_sync_lock,))
        if not cur.fetchone()[0]:
            raise ValueError('Ya hay una sincronización en curso. Esperá a que termine.')
        if mode=='ventas':
            rows=load_source_documents({'desde':desde,'hasta':hasta})
        else:
            cur.execute('SELECT rec FROM logistics_processed_docs WHERE fecha BETWEEN %s AND %s ORDER BY fecha,key',(desde,hasta))
            rows=[r[0] for r in cur.fetchall()]
            if not rows:
                raise ValueError('No hay ventas procesadas en el período. Primero sincronizá las ventas.')
        # Cached route projections are invalidated when users adjust Foxtrot.
        storage.clear_cache()
        attach_visits(rows)
        evaluate_documents(rows)
        now=datetime.now(timezone.utc).isoformat()
        for r in rows:
            r['comprobante']=' / '.join(str(x) for x in r['doc_key'][1:])
            r['ventas_sync_at']=now if mode=='ventas' else r['ventas_sync_at']
            r['foxtrot_calculado_at']=now
        if mode=='ventas':
            # Replace only the completely fetched period. A failure rolls back the old projection.
            cur.execute('DELETE FROM logistics_processed_docs WHERE fecha BETWEEN %s AND %s',(desde,hasta))
        if rows:
            execute_values(cur,'''INSERT INTO logistics_processed_docs
                (key,fecha,empresa,sucursal,cliente,rec,ventas_sync_at,foxtrot_calculado_at) VALUES %s
                ON CONFLICT(key) DO UPDATE SET rec=EXCLUDED.rec,
                    ventas_sync_at=EXCLUDED.ventas_sync_at,foxtrot_calculado_at=EXCLUDED.foxtrot_calculado_at''',
                [(document_key(r),r['fecha'],r['empresa'],r['sucursal'],r['cliente'],Json(r),r['ventas_sync_at'],now) for r in rows],page_size=500)
        cur.execute('INSERT INTO logistics_processed_runs(desde,hasta,modo,documentos,completed_at) VALUES(%s,%s,%s,%s,clock_timestamp())',
                    (desde,hasta,mode,len(rows)))
    return dict(documentos=len(rows),desde=desde,hasta=hasta,modo=mode,completed_at=datetime.now(timezone.utc).isoformat())


def covered(ranges, start, end):
    if not start or not end:
        return False
    from datetime import timedelta
    cursor=date.fromisoformat(start)
    finish=date.fromisoformat(end)
    for a,b in sorted(ranges):
        if a>cursor:
            return False
        if b>=cursor:
            cursor=b+timedelta(days=1)
        if cursor>finish:
            return True
    return False


def page_number(value,pages):
    try:
        return min(pages,max(1,int(value)))
    except (ValueError,TypeError):
        return 1


def dashboard_record(r):
    from otif_customer_day import branch
    sid = branch(r.get('sucursal'))
    return dict(fecha=r['fecha'], mes=r['fecha'][:7],
                suc={'1': 'Mar de Ajo', '2': 'Dolores', '3': 'Chascomus'}.get(sid, sid),
                empresa=r.get('empresa'), cliente=r.get('cliente'), nombre=r.get('nombre'),
                comprobante=r.get('comprobante'), rechazo_total=r.get('rechazo_total'),
                estado_rechazo=r.get('estado_rechazo'), tiene_rechazo=r.get('tiene_rechazo'),
                rechazo_parcial=r.get('rechazo_parcial'), on_time=r.get('on_time'),
                resultado=r.get('resultado_otif', 'pendiente'),
                choferes=sorted({v['chofer'] for v in r.get('foxtrot_candidatos', [])
                                if v.get('diferencia_dias') == 0 and v.get('chofer')}))


def dashboard_rows():
    ensure_schema()
    with storage._conn() as cn, cn.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cur.execute('SELECT rec FROM logistics_processed_docs ORDER BY fecha DESC,key')
        rows = [dashboard_record(r[0]) for r in cur.fetchall()]
        cur.execute('SELECT max(ventas_sync_at),max(foxtrot_calculado_at) FROM logistics_processed_docs')
        sales, visits = cur.fetchone()
    return dict(rows=rows, ventas_sync_at=sales, foxtrot_calculado_at=visits)


def read_processed(args,export=False):
    ensure_schema()
    f=period(args)
    parts,params=[],[]
    for key,op in (('desde','>='),('hasta','<=')):
        if f[key]:
            parts.append('fecha '+op+' %s');params.append(f[key])
    for key in ('cliente','sucursal'):
        if f[key]:
            parts.append(key+' = %s');params.append(f[key])
    if f['q']:
        parts.append('strpos(lower(rec::text),lower(%s))>0');params.append(f['q'])
    where=' AND '.join(parts) if parts else 'TRUE'
    with storage._conn() as cn,cn.cursor() as cur:
        # All cards, document pages and day totals describe the same database snapshot.
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cur.execute('SELECT desde,hasta FROM logistics_processed_runs WHERE modo=%s',('ventas',))
        ranges=cur.fetchall()
        cur.execute('SELECT max(ventas_sync_at),max(foxtrot_calculado_at) FROM logistics_processed_docs')
        synced,recalculated=cur.fetchone()
        cur.execute('SELECT * FROM (SELECT desde,hasta,modo,documentos,completed_at FROM logistics_processed_runs ORDER BY id DESC LIMIT 1) r')
        last_run=cur.fetchone()
        if last_run and last_run[2]=='ventas':
            synced=max(synced,last_run[4]) if synced else last_run[4]
        cur.execute("SELECT rec->>'estado_rechazo',rec->>'resultado_otif',count(*) FROM logistics_processed_docs WHERE "+where+" GROUP BY 1,2",params)
        counts,outcomes={},{}
        for state,result,n in cur.fetchall():
            counts[state]=counts.get(state,0)+n
            outcomes[result]=outcomes.get(result,0)+n
        count=sum(outcomes.values())
        if export and count>20000:
            raise ValueError('Acotá el filtro a 20.000 comprobantes para descargar.')
        pages=max(1,(count+49)//50);page=page_number(args.get('pagina',1),pages)
        cur.execute('SELECT rec FROM logistics_processed_docs WHERE '+where+' ORDER BY fecha,empresa,sucursal,cliente,key LIMIT %s OFFSET %s',params+[20000 if export else 50,0 if export else (page-1)*50])
        rows=[r[0] for r in cur.fetchall()]
        cur.execute('SELECT count(*) FROM (SELECT 1 FROM logistics_processed_docs WHERE '+where+' GROUP BY fecha,empresa,sucursal,cliente) d',params)
        day_count=cur.fetchone()[0];day_pages=max(1,(day_count+49)//50)
        day_page=page_number(args.get('pagina_dias',1),day_pages)
        cur.execute('''SELECT fecha,empresa,sucursal,cliente,max(rec->>'nombre'),count(*),
            count(*) FILTER(WHERE rec->>'resultado_otif'='cumple'),
            count(*) FILTER(WHERE rec->>'resultado_otif'='no_cumple'),
            count(*) FILTER(WHERE rec->>'resultado_otif'='pendiente')
            FROM logistics_processed_docs WHERE '''+where+''' GROUP BY fecha,empresa,sucursal,cliente
            ORDER BY fecha,empresa,sucursal,cliente LIMIT %s OFFSET %s''',params+[20000 if export else 50,0 if export else (day_page-1)*50])
        days=[]
        for d in cur.fetchall():
            r=dict(zip(('fecha','empresa','sucursal','cliente','nombre','comprobantes','cumplen','no_cumplen','pendientes'),d))
            n=r['cumplen']+r['no_cumplen']
            r['otif']=100*r['cumplen']/n if n else None
            days.append(r)
    evaluated=outcomes.get('cumple',0)+outcomes.get('no_cumple',0)
    return dict(day_page=day_page,day_pages=day_pages,day_count=day_count,rows=rows,days=days,counts=counts,
                outcomes=outcomes,evaluated=evaluated,otif=100*outcomes.get('cumple',0)/evaluated if evaluated else None,
                coverage=100*evaluated/count if count else None,count=count,page=page,pages=pages,filters=f,
                synced_at=synced,recalculated_at=recalculated,sync_complete=covered(ranges,f['desde'],f['hasta']),
                consulted_at=datetime.now(timezone.utc).isoformat())
