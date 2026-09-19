"""Recover missing rejection metrics without turning absent data into zero."""
from otif_customer_day import branch


def normalize_saved(rows):
    selected = {}
    for original in rows:
        r = dict(original)
        sid = branch(r.get('sucursal') or r.get('sucursal_id'))
        r['sucursal_id'] = sid
        raw = r.get('raw_rechazo') or {}
        if 'pedidos' in raw and 'pedidos_rechazo' in raw:
            try:
                n, rejected = float(raw['pedidos']), float(raw['pedidos_rechazo'])
            except (TypeError, ValueError):
                n, rejected = 0, 0
            r['nds'] = 100 * (n-rejected)/n if n and 0 <= rejected <= n else None
            # This API counts distinct customer/date pairs, not documents.
            r['pdv_unicos'] = n
        key = (r['fecha'], sid)
        if key not in selected or raw:
            selected[key] = r
    return sorted(selected.values(), key=lambda r:(r['fecha'],r['sucursal_id']))


def enrich_customer_ids(recs):
    """Called on import only. Match daily source population before attaching IDs."""
    from logistics_db import connection
    target = [r for r in recs.values() if 'pedidos' in (r.get('raw_rechazo') or {})]
    if not target:
        return
    with connection() as cn, cn.cursor() as cur:
        cur.execute('''SELECT v.fecha::text,coalesce(nullif(trim(v.sucursal),''),'1'),
            array_agg(DISTINCT trim(v.cliente)) FILTER(WHERE nullif(trim(v.cliente),'') IS NOT NULL)
            FROM ventas_detalle v JOIN articulos a ON a.id_articulo=v.id_articulo
            WHERE v.fecha BETWEEN %s AND %s AND lower(trim(coalesce(a.tipo_producto,'')))='mercaderia'
            AND lower(trim(coalesce(v.documento,''))) NOT LIKE 'remit%%'
            AND lower(trim(coalesce(v.documento,''))) NOT LIKE 'comod%%'
            AND lower(trim(coalesce(v.detalle_documento,''))) NOT LIKE 'remit%%'
            AND lower(trim(coalesce(v.detalle_documento,''))) NOT LIKE 'comod%%'
            GROUP BY 1,2''', (min(r['fecha'] for r in target),max(r['fecha'] for r in target)))
        found = {(day,sid): ids or [] for day,sid,ids in cur.fetchall()}
    for r in target:
        ids = found.get((r['fecha'],branch(r['sucursal'])))
        r['pdv_clientes'] = ids if ids is not None and len(ids)==float(r['raw_rechazo']['pedidos']) else None
