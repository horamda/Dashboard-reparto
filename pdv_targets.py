"""Editable PDV time standards and explicit client-master matching rules."""
import math
from datetime import datetime, timezone
import storage
from text_encoding import repair_values

KEY = 'pdv_time_targets_v1'
DEFAULTS = [('Tradicionales', 8), ('Refrigerados', 11), ('Autoservicios', 16),
            ('Mayoristas', 33), ('SMK Bajo Drop', 60), ('SMK Alto Drop', 180)]
STANDARD_GROUPS = {'Tradicionales': 'K+T', 'Refrigerados': 'REF', 'Autoservicios': 'AS', 'Mayoristas': 'MAY'}


def defaults():
    return {'rules': [dict(name=name, minutes=value, agrupacion=[STANDARD_GROUPS[name]] if name in STANDARD_GROUPS else [], subcanal=[], cliente=[])
                      for name, value in DEFAULTS]}


def group_names():
    from visit_metrics import customer_segments
    if storage.backend_name() == 'postgres':
        with storage._conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT DISTINCT COALESCE(NULLIF(trim(rec #>> '{raw_cliente,Descripcion agrupacion}'),''),'Sin clasificar') FROM clientes_dashboard ORDER BY 1")
            return [row[0] for row in cur.fetchall()]
    return sorted({customer_segments(r)['agrupacion'] for r in storage.load_clientes().values()})


def config():
    return repair_values(storage.load_setting(KEY) or defaults())


def catalog():
    from visit_metrics import customer_segments
    if storage.backend_name() == 'postgres':
        with storage._conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT cliente, jsonb_build_object('nombre',COALESCE(rec->>'nombre',rec->>'razon_social',''),'raw_cliente',rec->'raw_cliente') FROM clientes_dashboard")
            customers = dict(cur.fetchall())
    else:
        customers = storage.load_clientes()
    return catalog_from_customers(customers)


def catalog_from_customers(customers):
    from collections import Counter
    from visit_metrics import customer_segments
    segments = [customer_segments(r) for r in customers.values()]
    counts = Counter((r['agrupacion'], r['subcanal']) for r in segments)
    return dict(agrupacion=sorted({r['agrupacion'] for r in segments}),
                subcanal=sorted({r['subcanal'] for r in segments}),
                relations=[dict(agrupacion=g, subcanal=s, clientes=n) for (g,s),n in sorted(counts.items())],
                cliente=[dict(value=str(k), label=str(k)+' — '+str(v.get('nombre') or v.get('razon_social') or '')) for k,v in sorted(customers.items())])


def validate(rules):
    if not isinstance(rules, list) or not 1 <= len(rules) <= 100:
        raise ValueError('Se requiere entre 1 y 100 objetivos.')
    clean, used = [], {field: set() for field in ('agrupacion', 'subcanal', 'cliente')}
    names = set()
    for rule in rules:
        name = str(rule.get('name', '')).strip()
        try:
            minutes = float(rule.get('minutes', ''))
        except (TypeError, ValueError):
            raise ValueError('Ingresá minutos válidos para cada objetivo.')
        if not name or len(name) > 120 or name.casefold() in names:
            raise ValueError('Cada objetivo debe tener un nombre único de hasta 120 caracteres.')
        if not math.isfinite(minutes) or not 0 < minutes <= 1440:
            raise ValueError('El objetivo debe ser mayor que cero y no superar 1440 minutos.')
        names.add(name.casefold())
        item = dict(name=name, minutes=minutes)
        for field in used:
            values = rule.get(field, [])
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError('Las asignaciones deben ser listas de texto.')
            values = sorted(set(v.strip() for v in values if v.strip()))
            if used[field].intersection(values):
                raise ValueError('Una misma asignación no puede pertenecer a dos objetivos: ' + field)
            used[field].update(values)
            item[field] = values
        clean.append(item)
    return clean


def save(rules, actor):
    value = dict(rules=validate(rules), updated_at=datetime.now(timezone.utc).isoformat(), updated_by=actor)
    return storage.save_setting(KEY, value)
