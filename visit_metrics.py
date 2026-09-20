"""Customer stop durations from Foxtrot visits; no inferred service-time substitution."""
import math
import storage

FIELDS = ['route_id','Route ID','cliente','Customer ID','cliente_nombre','Customer Name',
          'Waypoint ID','Visit Start Timestamp','visit_start','Driver Click Timestamp',
          'driver_click','Visit Duration Seconds','visit_duration_seconds']


def summarize_visits(items):
    seen,groups=set(),{}
    for r in items:
        rid=str(r.get('route_id') or r.get('Route ID') or '').strip()
        customer=str(r.get('cliente') or r.get('Customer ID') or '').strip()
        if customer.endswith('.0'): customer=customer[:-2]
        if not rid or not customer or customer.lower() in ('nan','none'): continue
        start=r.get('visit_start') or r.get('Visit Start Timestamp')
        click=r.get('driver_click') or r.get('Driver Click Timestamp')
        identity=(rid,customer,str(r.get('Waypoint ID') or ''),str(start or ''),str(click or ''))
        # Without a timestamp there is no reliable identity for de-duplicating visits.
        if start or click:
            if identity in seen: continue
            seen.add(identity)
        key=(rid,customer)
        g=groups.setdefault(key,dict(rid=rid,cliente=customer,nombre=str(r.get('cliente_nombre') or r.get('Customer Name') or ''),visitas=0,validas=0,segundos=0))
        g['visitas']+=1
        raw=r.get('Visit Duration Seconds')
        if raw is None or raw=='': raw=r.get('visit_duration_seconds')
        try: seconds=float(raw)
        except (TypeError,ValueError): continue
        if math.isfinite(seconds) and seconds>0:
            g['validas']+=1;g['segundos']+=seconds
    return list(groups.values())


def dashboard_rows():
    cached=storage._cache_get('attempts:pdv_times')
    if cached is not None:
        return cached
    if storage.backend_name()=='postgres':
        with storage._conn() as cn,cn.cursor() as cur:
            args=','.join("%s,rec->%s" for _ in FIELDS)
            cur.execute('SELECT jsonb_build_object('+args+') FROM attempts_dashboard', [x for f in FIELDS for x in (f,f)])
            items=[row[0] for row in cur.fetchall()]
            cur.execute("SELECT cliente, COALESCE(rec->>'nombre',rec->>'razon_social','') FROM clientes_dashboard")
            customers={key:{'nombre':name} for key,name in cur.fetchall()}
    else:
        items=storage.load_attempts().values()
        customers=storage.load_clientes()
    rows=summarize_visits(items)
    for r in rows:
        master=customers.get(r['cliente'],{})
        if not r['nombre']: r['nombre']=master.get('nombre') or master.get('razon_social') or ''
    return storage._cache_set('attempts:pdv_times',rows)
