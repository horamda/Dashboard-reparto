# -*- coding: utf-8 -*-
"""
Capa de persistencia de la base del dashboard.

- Si existe la variable de entorno DATABASE_URL (Railway con plugin Postgres):
  guarda en una tabla Postgres  rutas_dashboard(rid TEXT PK, rec JSONB).
- Si no existe (desarrollo local sin DB): cae a un archivo JSON en DATA_DIR.

En ambos casos la inserción es INCREMENTAL: solo se agregan rutas cuyo rid
no exista todavía; las ya cargadas nunca se modifican (semántica ON CONFLICT DO NOTHING).
"""

import os
import copy
import json
import re
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from urllib.parse import quote


def _load_local_env():
    """Carga variables desde .env local si existen y no estaban definidas."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key.lower().startswith("$env:"):
                    key = key[5:].strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        return


_load_local_env()


def _database_url_from_env():
    url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if url:
        return url

    required = ("PGHOST", "PGUSER", "PGPASSWORD", "PGDATABASE")
    if all(os.environ.get(k) for k in required):
        user = quote(os.environ["PGUSER"], safe="")
        password = quote(os.environ["PGPASSWORD"], safe="")
        host = os.environ["PGHOST"]
        port = os.environ.get("PGPORT", "5432")
        database = quote(os.environ["PGDATABASE"], safe="")
        return f"postgresql://{user}:{password}@{host}:{port}/{database}"

    return None


DATABASE_URL = _database_url_from_env()
AQUI = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(AQUI, "data"))
JSON_PATH = os.path.join(DATA_DIR, "datos_dashboard.json")
CLIENTES_JSON_PATH = os.path.join(DATA_DIR, "clientes_dashboard.json")
ARTICULOS_JSON_PATH = os.path.join(DATA_DIR, "articulos_dashboard.json")
ATTEMPTS_JSON_PATH = os.path.join(DATA_DIR, "attempts_dashboard.json")

BACKEND = "postgres" if DATABASE_URL else "json"
CACHE_TTL_SECONDS = float(os.environ.get("STORAGE_CACHE_TTL_SECONDS", "300"))
DB_STATEMENT_TIMEOUT_SECONDS = float(os.environ.get("PGSTATEMENT_TIMEOUT_SECONDS", "60"))
DB_INSERT_PAGE_SIZE = int(os.environ.get("PG_INSERT_PAGE_SIZE", "500"))
CACHE_MAX_ITEMS = max(16, int(os.environ.get("STORAGE_CACHE_MAX_ITEMS", "128")))
_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()


def clear_cache(prefix=None):
    with _CACHE_LOCK:
        if prefix is None:
            _CACHE.clear()
            return
        for key in list(_CACHE):
            if key.startswith(prefix):
                _CACHE.pop(key, None)


def _cache_get(key):
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at < time.time():
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        return copy.deepcopy(value)


def _cache_set(key, value):
    cached_value = copy.deepcopy(value)
    with _CACHE_LOCK:
        _CACHE[key] = (time.time() + CACHE_TTL_SECONDS, cached_value)
        _CACHE.move_to_end(key)
        now = time.time()
        for expired_key in [name for name, item in _CACHE.items() if item[0] < now]:
            _CACHE.pop(expired_key, None)
        while len(_CACHE) > CACHE_MAX_ITEMS:
            _CACHE.popitem(last=False)
    return copy.deepcopy(cached_value)


ROUTE_RAW_COLUMNS = [
    "DC ID", "DC Name", "DC Time Zone", "Route ID", "Route Name", "Planned Route Start Date",
    "Planned Route Start Timestamp", "Driver ID", "Driver Name", "Driver Groups", "Is Digital Route",
    "Digital Route: Driver Trace Passed", "Digital Route: Driver Click Score Passed",
    "Digital Route: Hardware Trace Passed", "Imported Customers Count", "Real-time Sequencing Enabled",
    "Customers with Confident Locations Count", "Customers with Unconfident Locations Count",
    "Customers with Unknown Locations Count", "Successful Customers Count", "Failed Customers Count",
    "Try Again Later Customers Count", "Mixed Status Customers Count", "Total Visited Customers Count",
    "Total Unvisited Customers Count", "Reattempt Authorizations Count", "Total Clicks at Confident Locations",
    "Total Clicks at Unconfident Locations", "Total Clicks at Unknown Locations", "Actual Route Departure Time",
    "Actual Route Arrival Time", "Driver Click Score", "Total Customers Clicked",
    "Total Visits Clicked with Distance Measured", "Total Clicks at Customer",
    "Total Sequence Adhered Clicks", "Total Sequence Not Adhered Clicks",
    "Total Sequence Adhered Clicks with No Decision", "Total Sequence Forgiven Clicks",
    "Sequence Adherence", "Planned Foxtrot Driving Meters", "Total Driven Meters",
    "Planned Foxtrot Driving Seconds", "Total Driven Seconds", "Planned Foxtrot Journey Seconds",
    "Total Journey Seconds", "Total Stops Count", "Total Stop Time Seconds",
    "Total Authorized Stops Count", "Total Authorized Stops Seconds", "Total Unauthorized Stops Count",
    "Total Unauthorized Stops Seconds", "Total Data Gaps Count", "Total Data Gaps Seconds",
    "Customers with Additional Visits", "Total Additional Visits",
    "Additional Visits With Final Result Success", "Driver Marked Route Start Timestamp",
    "Driver Marked Route Start Latitude", "Driver Marked Route Start Longitude",
    "Driver Marked Route End Timestamp", "Driver Marked Route End Latitude",
    "Driver Marked Route End Longitude", "End Terminus Changed", "Planned Total Waiting Time Seconds",
    "Stem Start Duration (Seconds)", "Stem Start Distance (Meters)", "Stem End Duration (Seconds)",
    "Stem End Distance (Meters)", "Beta: GPS Spoofer Suspected",
]

ATTEMPT_RAW_COLUMNS = [
    "DC ID", "DC Name", "DC Time Zone", "Route ID", "Route Name", "Planned Route Start Date",
    "Planned Route Start Timestamp", "Driver ID", "Driver Name", "Driver Groups", "Waypoint ID",
    "Customer ID", "Customer Name", "Customer Location Confidence", "Visit Start Timestamp",
    "Visit Duration Seconds", "Visit Meters from Customer", "Driver Click Timestamp",
    "Aggregate Visit Status", "Sequence Adherence Status", "Waypoint Time Windows", "Visit Timeliness",
    "Beta: Suspicious Drive By Attempt Flag", "Beta: Inferred Service Duration Seconds",
]

ROUTE_TYPED_COLUMNS = {
    "fecha": "DATE",
    "mes": "TEXT",
    "anio": "INTEGER",
    "sucursal": "TEXT",
    "chofer": "TEXT",
    "camion": "TEXT",
    "inicio_foxtrot": "TIME",
    "fin_foxtrot": "TIME",
    "horas": "DOUBLE PRECISION",
    "usable": "BOOLEAN",
    "alerta": "BOOLEAN",
    "tml": "DOUBLE PRECISION",
    "ti": "DOUBLE PRECISION",
    "adhsec": "DOUBLE PRECISION",
    "adhcli": "DOUBLE PRECISION",
    "bultos": "DOUBLE PRECISION",
    "hl": "DOUBLE PRECISION",
    "salidas": "INTEGER",
}


def _sql_col(name, prefix="fox"):
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return f"{prefix}_{s}"


def _sql_scalar(v):
    if v is None or v == "":
        return None
    mod = type(v).__module__
    if mod and mod.startswith("numpy"):
        try:
            return v.item()
        except Exception:
            return float(v)
    return v


def backend_name():
    return BACKEND


# ======================= POSTGRES =======================
if BACKEND == "postgres":
    import psycopg2
    import psycopg2.extras as _extras
    from psycopg2.pool import ThreadedConnectionPool
    _LOGISTICS_INIT_DONE = False
    _CONNECTION_POOL = None
    _CONNECTION_POOL_LOCK = threading.Lock()

    def _get_connection_pool():
        global _CONNECTION_POOL
        if _CONNECTION_POOL is None:
            with _CONNECTION_POOL_LOCK:
                if _CONNECTION_POOL is None:
                    timeout = int(os.environ.get("PGCONNECT_TIMEOUT", "10"))
                    statement_timeout = int(DB_STATEMENT_TIMEOUT_SECONDS * 1000)
                    _CONNECTION_POOL = ThreadedConnectionPool(
                        1,
                        max(2, int(os.environ.get("PG_POOL_MAX", "5"))),
                        DATABASE_URL,
                        connect_timeout=timeout,
                        options=f"-c statement_timeout={statement_timeout}",
                        keepalives=1,
                        keepalives_idle=30,
                        keepalives_interval=10,
                        keepalives_count=3,
                    )
        return _CONNECTION_POOL

    @contextmanager
    def _conn():
        pool = _get_connection_pool()
        cn = pool.getconn()
        while cn.closed:
            pool.putconn(cn, close=True)
            cn = pool.getconn()
        discard = False
        try:
            yield cn
            cn.commit()
        except Exception:
            discard = bool(cn.closed)
            if not discard:
                try:
                    cn.rollback()
                except Exception:
                    discard = True
            raise
        finally:
            pool.putconn(cn, close=discard or bool(cn.closed))

    def init():
        global _LOGISTICS_INIT_DONE
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rutas_dashboard (
                    rid TEXT PRIMARY KEY,
                    rec JSONB NOT NULL
                );
            """)
            for col, typ in ROUTE_TYPED_COLUMNS.items():
                cur.execute(f"ALTER TABLE rutas_dashboard ADD COLUMN IF NOT EXISTS {col} {typ};")
            for raw_col in ROUTE_RAW_COLUMNS:
                cur.execute(f"ALTER TABLE rutas_dashboard ADD COLUMN IF NOT EXISTS {_sql_col(raw_col)} TEXT;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS attempts_dashboard (
                    attempt_key TEXT PRIMARY KEY,
                    route_id TEXT,
                    rec JSONB NOT NULL
                );
            """)
            for raw_col in ATTEMPT_RAW_COLUMNS:
                cur.execute(f"ALTER TABLE attempts_dashboard ADD COLUMN IF NOT EXISTS {_sql_col(raw_col, 'att')} TEXT;")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_attempts_dashboard_route_id ON attempts_dashboard (route_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_attempts_customer_norm ON attempts_dashboard ((regexp_replace(COALESCE(rec->>'cliente', rec->>'Customer ID', ''), '^0+', '')));")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_routes_fecha ON rutas_dashboard (fecha);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_routes_sucursal ON rutas_dashboard (sucursal);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_routes_chofer ON rutas_dashboard (chofer);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_routes_camion ON rutas_dashboard (camion);")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS clientes_dashboard (
                    cliente TEXT PRIMARY KEY,
                    rec JSONB NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rechazos_dashboard (
                    key TEXT PRIMARY KEY,
                    rec JSONB NOT NULL
                );
            """)
            cur.execute("ALTER TABLE rechazos_dashboard ADD COLUMN IF NOT EXISTS key TEXT;")
            cur.execute("UPDATE rechazos_dashboard SET key = COALESCE(rec->>'key', (rec->>'fecha') || '|' || COALESCE(rec->>'sucursal', '')) WHERE key IS NULL;")
            cur.execute("ALTER TABLE rechazos_dashboard DROP CONSTRAINT IF EXISTS rechazos_dashboard_pkey;")
            cur.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='rechazos_dashboard' AND column_name='fecha'
                    ) THEN
                        ALTER TABLE rechazos_dashboard ALTER COLUMN fecha DROP NOT NULL;
                    END IF;
                END $$;
            """)
            cur.execute("ALTER TABLE rechazos_dashboard ADD PRIMARY KEY (key);")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rechazos_detalle_dashboard (
                    key TEXT PRIMARY KEY,
                    rec JSONB NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS articulos_dashboard (
                    articulo TEXT PRIMARY KEY,
                    rec JSONB NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS settings_dashboard (
                    key TEXT PRIMARY KEY,
                    rec JSONB NOT NULL
                );
            """)
            _ensure_logistics_tables(cur)
        _LOGISTICS_INIT_DONE = True

    def _ensure_logistics_tables(cur):
        cur.execute("""
            CREATE TABLE IF NOT EXISTS logistics_cost_config (
                key TEXT PRIMARY KEY,
                rec JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS route_costs (
                rid TEXT PRIMARY KEY,
                rec JSONB NOT NULL,
                calculated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS route_customer_costs (
                key TEXT PRIMARY KEY,
                rid TEXT NOT NULL,
                cliente TEXT NOT NULL,
                rec JSONB NOT NULL,
                calculated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS routing_cache (
                key TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                rec JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_route_customer_costs_rid ON route_customer_costs (rid);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_route_customer_costs_cliente ON route_customer_costs (cliente);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_route_customer_costs_localidad ON route_customer_costs ((rec->>'localidad'));")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_route_costs_fecha ON route_costs ((rec->>'fecha'));")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_route_costs_sucursal ON route_costs ((rec->>'sucursal'));")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_route_costs_camion ON route_costs ((rec->>'camion'));")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_route_costs_chofer ON route_costs ((rec->>'chofer'));")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS logistics_depots (
                sucursal TEXT PRIMARY KEY,
                rec JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS route_cost_calculations (
                calculation_id TEXT PRIMARY KEY,
                rid TEXT NOT NULL,
                rec JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS route_customer_cost_calculations (
                key TEXT PRIMARY KEY,
                calculation_id TEXT NOT NULL,
                rid TEXT NOT NULL,
                cliente TEXT NOT NULL,
                rec JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_route_cost_calculations_rid ON route_cost_calculations (rid, created_at DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_route_customer_calculations_id ON route_customer_cost_calculations (calculation_id);")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS logistics_cost_rates (
                valid_from DATE PRIMARY KEY,
                rec JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS logistics_vehicle_costs (
                key TEXT PRIMARY KEY,
                vehicle TEXT NOT NULL,
                valid_from DATE NOT NULL,
                rec JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (vehicle, valid_from)
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_logistics_vehicle_costs_lookup ON logistics_vehicle_costs (vehicle, valid_from DESC);")
        cur.execute("""
            INSERT INTO route_cost_calculations (calculation_id, rid, rec, created_at)
            SELECT 'legacy:' || rid, rid,
                   rec || jsonb_build_object('calculation_id', 'legacy:' || rid, 'calculation_type', 'legacy'),
                   calculated_at
            FROM route_costs
            ON CONFLICT (calculation_id) DO NOTHING;
        """)
        cur.execute("""
            INSERT INTO route_customer_cost_calculations (key, calculation_id, rid, cliente, rec, created_at)
            SELECT 'legacy:' || key, 'legacy:' || rid, rid, cliente,
                   rec || jsonb_build_object('calculation_id', 'legacy:' || rid), calculated_at
            FROM route_customer_costs
            ON CONFLICT (key) DO NOTHING;
        """)

    def load_all():
        cached = _cache_get("routes:all")
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT rid, rec FROM rutas_dashboard;")
            return _cache_set("routes:all", {rid: rec for rid, rec in cur.fetchall()})

    def load_dashboard_routes(fields=None):
        fields = tuple(sorted({str(field) for field in (fields or ()) if re.fullmatch(r"[a-zA-Z0-9_]+", str(field))}))
        cache_key = "routes:dashboard:" + ",".join(fields)
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        projection = (
            "jsonb_strip_nulls(jsonb_build_object(" + ",".join(f"'{field}', rec->'{field}'" for field in fields) + "))"
            if fields else "rec - 'raw_foxtrot'"
        )
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(f"SELECT rid, {projection} FROM rutas_dashboard;")
            return _cache_set(cache_key, {rid: rec for rid, rec in cur.fetchall()})

    def load_fichaya_dimensions(desde="2026-08-01"):
        cache_key = f"routes:fichaya-dimensions:{desde}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(array_agg(DISTINCT sucursal ORDER BY sucursal)
                        FILTER (WHERE sucursal IS NOT NULL AND sucursal <> ''), '{}'),
                    COALESCE(array_agg(DISTINCT chofer ORDER BY chofer)
                        FILTER (WHERE chofer IS NOT NULL AND chofer <> ''), '{}'),
                    MIN(fecha)::text,
                    MAX(fecha)::text
                FROM rutas_dashboard
                WHERE usable IS TRUE AND fecha >= %s::date;
                """,
                (str(desde),),
            )
            sucursales, choferes, first_date, last_date = cur.fetchone()
        return _cache_set(cache_key, {
            "sucursales": list(sucursales or []),
            "choferes": list(choferes or []),
            "desde": first_date or "",
            "hasta": last_date or "",
        })

    def load_fichaya_routes(desde, hasta, suc="", chofer=""):
        cache_key = f"routes:fichaya:{desde}:{hasta}:{suc}:{chofer}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        clauses = ["usable IS TRUE", "fecha >= %s::date", "fecha <= %s::date"]
        params = [str(desde), str(hasta)]
        if suc:
            clauses.append("sucursal = %s")
            params.append(str(suc))
        if chofer:
            clauses.append("chofer = %s")
            params.append(str(chofer))
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                SELECT rid, fecha::text, sucursal, chofer,
                       to_char(inicio_foxtrot, 'HH24:MI'),
                       to_char(fin_foxtrot, 'HH24:MI')
                FROM rutas_dashboard
                WHERE """ + " AND ".join(clauses) + """
                ORDER BY fecha DESC, sucursal, chofer, inicio_foxtrot DESC;
                """,
                params,
            )
            rows = [
                {
                    "rid": rid,
                    "fecha": fecha or "",
                    "suc": sucursal or "",
                    "chofer": driver or "",
                    "inicio_foxtrot": start_time or "",
                    "fin_foxtrot": end_time or "",
                    "usable": True,
                }
                for rid, fecha, sucursal, driver, start_time, end_time in cur.fetchall()
            ]
        return _cache_set(cache_key, rows)

    def load_foxtrot_quality(columns, filters=None, q="", limit=300):
        columns = tuple(str(col) for col in columns if str(col) in ROUTE_RAW_COLUMNS)
        filters = filters or {}
        limit = max(1, min(int(limit), 500))
        result_key = "routes:quality:" + json.dumps(
            [columns, sorted((str(key), str(value)) for key, value in filters.items()), str(q or ""), limit],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        cached_result = _cache_get(result_key)
        if cached_result is not None:
            return cached_result
        stats_key = "routes:quality-stats:" + "|".join(columns)
        stats = _cache_get(stats_key)
        with _conn() as cn, cn.cursor() as cur:
            def raw_expr(column):
                literal = cur.mogrify("%s", (column,)).decode("utf-8")
                return f"rec->'raw_foxtrot'->>{literal}"

            def blank_expr(column):
                expr = raw_expr(column)
                normalized = f"LOWER(BTRIM(COALESCE({expr}, '')))"
                return f"({normalized} = '' OR {normalized} IN ('nan','none','null','nat'))"

            if stats is None:
                aggregates = ["COUNT(*)::int"] + [
                    f"COUNT(*) FILTER (WHERE {blank_expr(column)})::int"
                    for column in columns
                ]
                cur.execute("SELECT " + ", ".join(aggregates) + " FROM rutas_dashboard;")
                values = cur.fetchone()
                stats = _cache_set(stats_key, {
                    "total": int(values[0] or 0),
                    "missing": {column: int(value or 0) for column, value in zip(columns, values[1:])},
                })

            clauses = []
            params = []
            query = str(q or "").strip()
            if query:
                clauses.append("rec::text ILIKE %s")
                params.append(f"%{query}%")
            for column in columns:
                mode = str(filters.get(column) or "")
                if mode == "empty":
                    clauses.append(blank_expr(column))
                elif mode == "present":
                    clauses.append("NOT " + blank_expr(column))
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            raw_pairs = ", ".join(
                f"{cur.mogrify('%s', (column,)).decode('utf-8')}, rec->'raw_foxtrot'->{cur.mogrify('%s', (column,)).decode('utf-8')}"
                for column in columns
            )
            projection = (
                "jsonb_build_object("
                "'rid', rid, 'fecha', fecha::text, 'suc', sucursal, 'chofer', chofer, "
                f"'raw_foxtrot', jsonb_build_object({raw_pairs}))"
            )
            cur.execute(
                f"SELECT {projection} FROM rutas_dashboard{where} "
                "ORDER BY fecha DESC NULLS LAST, sucursal, chofer LIMIT %s;",
                [*params, limit],
            )
            rows = [row[0] for row in cur.fetchall()]
        return _cache_set(result_key, {**stats, "rows": rows})

    def _route_row(rid, rec):
        raw = rec.get("raw_foxtrot") or {}
        typed = [
            rec.get("fecha"), rec.get("mes"),
            int(rec["anio"]) if rec.get("anio") not in (None, "") else None,
            rec.get("suc"), rec.get("chofer"), rec.get("camion"),
            rec.get("inicio_foxtrot") or None, rec.get("fin_foxtrot") or None,
            rec.get("horas"), rec.get("usable"), rec.get("alerta"),
            rec.get("tml"), rec.get("ti"), rec.get("adhsec"), rec.get("adhcli"),
            rec.get("bultos"), rec.get("hl"), rec.get("salidas"),
        ]
        typed = [_sql_scalar(v) for v in typed]
        raw_vals = [None if raw.get(c) is None else str(raw.get(c)) for c in ROUTE_RAW_COLUMNS]
        return tuple([rid, _extras.Json(rec)] + typed + raw_vals)

    def _route_cols():
        return ["rid", "rec"] + list(ROUTE_TYPED_COLUMNS) + [_sql_col(c) for c in ROUTE_RAW_COLUMNS]

    def add_new(recs):
        """Inserta solo las rutas nuevas. Devuelve cuántas se agregaron."""
        if not recs:
            return 0
        cols = _route_cols()
        rows = [_route_row(rid, rec) for rid, rec in recs.items()]
        with _conn() as cn, cn.cursor() as cur:
            before = _count(cur)
            _extras.execute_values(
                cur,
                f"INSERT INTO rutas_dashboard ({', '.join(cols)}) VALUES %s "
                "ON CONFLICT (rid) DO NOTHING;",
                rows,
                page_size=DB_INSERT_PAGE_SIZE,
            )
            after = _count(cur)
        clear_cache("routes:")
        return after - before

    def upsert_all(recs, count_new=True):
        """Inserta rutas nuevas y actualiza las existentes. Devuelve cuántas nuevas se agregaron."""
        if not recs:
            return 0
        cols = _route_cols()
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "rid")
        rows = [_route_row(rid, rec) for rid, rec in recs.items()]
        with _conn() as cn, cn.cursor() as cur:
            before = _count(cur) if count_new else 0
            _extras.execute_values(
                cur,
                f"INSERT INTO rutas_dashboard ({', '.join(cols)}) VALUES %s "
                f"ON CONFLICT (rid) DO UPDATE SET {updates} "
                "WHERE rutas_dashboard.rec IS DISTINCT FROM EXCLUDED.rec;",
                rows,
                page_size=DB_INSERT_PAGE_SIZE,
            )
            after = _count(cur) if count_new else 0
        clear_cache("routes:")
        return after - before

    def _count(cur):
        cur.execute("SELECT COUNT(*) FROM rutas_dashboard;")
        return cur.fetchone()[0]

    def count_routes():
        cached = _cache_get("routes:count")
        if cached is not None:
            return int(cached)
        with _conn() as cn, cn.cursor() as cur:
            return int(_cache_set("routes:count", _count(cur)))

    def route_import_stats(tml_obj=30, ti_obj=30):
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*)::int,
                    COUNT(*) FILTER (WHERE usable)::int,
                    AVG(tml) FILTER (WHERE usable AND tml IS NOT NULL),
                    AVG(CASE WHEN usable AND tml IS NOT NULL THEN CASE WHEN tml <= %s THEN 1.0 ELSE 0.0 END END),
                    AVG(ti) FILTER (WHERE usable AND ti IS NOT NULL),
                    AVG(CASE WHEN usable AND ti IS NOT NULL THEN CASE WHEN ti <= %s THEN 1.0 ELSE 0.0 END END)
                FROM rutas_dashboard;
                """,
                (tml_obj, ti_obj),
            )
            total, validas, tml_prom, tml_cumpl, ti_prom, ti_cumpl = cur.fetchone()
        return {
            "total": total or 0,
            "validas": validas or 0,
            "sin_cierre": (total or 0) - (validas or 0),
            "tml_prom": round(float(tml_prom), 1) if tml_prom is not None else None,
            "tml_cumpl": round(100 * float(tml_cumpl)) if tml_cumpl is not None else None,
            "ti_prom": round(float(ti_prom), 1) if ti_prom is not None else None,
            "ti_cumpl": round(100 * float(ti_cumpl)) if ti_cumpl is not None else None,
        }

    def upsert_attempts(recs):
        if not recs:
            return 0
        cols = ["attempt_key", "route_id", "rec"] + [_sql_col(c, "att") for c in ATTEMPT_RAW_COLUMNS]
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "attempt_key")
        rows = []
        for key, rec in recs.items():
            raw_vals = [None if rec.get(c) is None else str(rec.get(c)) for c in ATTEMPT_RAW_COLUMNS]
            rows.append(tuple([key, str(rec.get("Route ID") or ""), _extras.Json(rec)] + raw_vals))
        with _conn() as cn, cn.cursor() as cur:
            _extras.execute_values(
                cur,
                f"INSERT INTO attempts_dashboard ({', '.join(cols)}) VALUES %s "
                f"ON CONFLICT (attempt_key) DO UPDATE SET {updates} "
                "WHERE attempts_dashboard.rec IS DISTINCT FROM EXCLUDED.rec;",
                rows,
                page_size=DB_INSERT_PAGE_SIZE,
            )
        clear_cache("attempts:")
        return len(recs)

    def load_attempts():
        cached = _cache_get("attempts:all")
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT attempt_key, rec FROM attempts_dashboard;")
            return _cache_set("attempts:all", {key: rec for key, rec in cur.fetchall()})

    def load_attempts_by_route(route_id):
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                SELECT attempt_key, rec
                FROM attempts_dashboard
                WHERE route_id = %s
                ORDER BY
                    NULLIF(rec->>'Driver Click Timestamp', ''),
                    NULLIF(rec->>'Visit Start Timestamp', ''),
                    attempt_key;
                """,
                (str(route_id),),
            )
            return {key: rec for key, rec in cur.fetchall()}

    def load_route_sales(route_id):
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                WITH visits AS (
                    SELECT DISTINCT a.route_id, r.fecha::text AS fecha,
                        regexp_replace(COALESCE(a.rec->>'cliente', a.rec->>'Customer ID', ''), '^0+', '') AS cliente
                    FROM attempts_dashboard a
                    JOIN rutas_dashboard r ON r.rid = a.route_id
                ), orders AS (
                    SELECT p.nro_pedido,
                        COALESCE(NULLIF(p.rec->>'fecha_entrega', ''), p.rec->>'fecha') AS fecha,
                        regexp_replace(split_part(p.rec->>'cliente', ' - ', 1), '^0+', '') AS cliente,
                        COALESCE((p.rec->>'total')::numeric, 0) AS venta
                    FROM pedidos_dashboard p
                    WHERE COALESCE((p.rec->>'facturado')::boolean, false)
                      AND NOT COALESCE((p.rec->>'anulado')::boolean, false)
                ), unique_matches AS (
                    SELECT o.nro_pedido, o.cliente, o.venta, MIN(v.route_id) AS route_id
                    FROM orders o
                    JOIN visits v ON v.fecha = o.fecha AND v.cliente = o.cliente
                    GROUP BY o.nro_pedido, o.cliente, o.venta
                    HAVING COUNT(DISTINCT v.route_id) = 1
                )
                SELECT cliente, SUM(venta), COUNT(*)
                FROM unique_matches
                WHERE route_id = %s
                GROUP BY cliente;
                """,
                (str(route_id),),
            )
            return {
                cliente: {"venta": float(venta or 0), "pedidos_facturados": int(pedidos or 0)}
                for cliente, venta, pedidos in cur.fetchall()
            }

    def sample_attempt(limit=1):
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT rec FROM attempts_dashboard LIMIT %s;", (int(limit),))
            return [row[0] for row in cur.fetchall()]

    def update_attempt_deliveries(records):
        if not records:
            return 0
        deduplicated = {}
        for rec in records:
            key = (str(rec["route_id"]), str(rec["cliente"]).lstrip("0"))
            deduplicated[key] = rec.get("values") or {}
        rows = [
            (route_id, cliente, _extras.Json(values))
            for (route_id, cliente), values in deduplicated.items()
        ]
        updated = 0
        with _conn() as cn, cn.cursor() as cur:
            for start in range(0, len(rows), DB_INSERT_PAGE_SIZE):
                batch = rows[start:start + DB_INSERT_PAGE_SIZE]
                _extras.execute_values(
                    cur,
                    """
                    UPDATE attempts_dashboard AS attempt
                    SET rec = attempt.rec || incoming.payload
                    FROM (VALUES %s) AS incoming(route_id, cliente, payload)
                    WHERE attempt.route_id = incoming.route_id
                      AND regexp_replace(
                          COALESCE(attempt.rec->>'cliente', attempt.rec->>'Customer ID', ''),
                          '^0+', ''
                      ) = incoming.cliente;
                    """,
                    batch,
                    template="(%s, %s, %s::jsonb)",
                    page_size=len(batch),
                )
                updated += cur.rowcount
        clear_cache("attempts:")
        clear_cache("logistics:")
        return updated

    def update_route_vehicles(records):
        if not records:
            return 0
        deduplicated = {str(rec["rid"]): rec for rec in records}
        rows = [
            (
                rid,
                str(rec["camion"]),
                _extras.Json({
                    "camion": str(rec["camion"]),
                    "raw_vehicle_assignment": rec.get("raw") or {},
                }),
            )
            for rid, rec in deduplicated.items()
        ]
        updated = 0
        with _conn() as cn, cn.cursor() as cur:
            for start in range(0, len(rows), DB_INSERT_PAGE_SIZE):
                batch = rows[start:start + DB_INSERT_PAGE_SIZE]
                _extras.execute_values(
                    cur,
                    """
                    UPDATE rutas_dashboard AS current_route
                    SET camion = incoming.camion,
                        rec = current_route.rec || incoming.payload
                    FROM (VALUES %s) AS incoming(rid, camion, payload)
                    WHERE current_route.rid = incoming.rid;
                    """,
                    batch,
                    template="(%s, %s, %s::jsonb)",
                    page_size=len(batch),
                )
                updated += cur.rowcount
        clear_cache("routes:")
        clear_cache("logistics:")
        return updated

    def reset():
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("TRUNCATE rutas_dashboard;")
            cur.execute("TRUNCATE attempts_dashboard;")
        clear_cache()

    def load_clientes():
        cached = _cache_get("clientes:all")
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT cliente, rec FROM clientes_dashboard;")
            return _cache_set("clientes:all", {cliente: rec for cliente, rec in cur.fetchall()})

    def list_records(table, q="", limit=100, offset=0):
        specs = {
            "rutas": ("rutas_dashboard", "rid"),
            "attempts": ("attempts_dashboard", "attempt_key"),
            "clientes": ("clientes_dashboard", "cliente"),
            "rechazos": ("rechazos_dashboard", "key"),
            "rechazos_detalle": ("rechazos_detalle_dashboard", "key"),
            "articulos": ("articulos_dashboard", "articulo"),
            "settings": ("settings_dashboard", "key"),
        }
        if table not in specs:
            raise ValueError("Tabla no permitida.")
        table_name, key_col = specs[table]
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        query = str(q or "").strip()
        where = " WHERE rec::text ILIKE %s" if query else ""
        params = ([f"%{query}%"] if query else []) + [limit, offset]
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                f"SELECT {key_col}, rec FROM {table_name}{where} "
                f"ORDER BY {key_col} LIMIT %s OFFSET %s;",
                params,
            )
            return cur.fetchall()

    def get_record(table, key):
        specs = {
            "rutas": ("rutas_dashboard", "rid"),
            "attempts": ("attempts_dashboard", "attempt_key"),
            "clientes": ("clientes_dashboard", "cliente"),
            "rechazos": ("rechazos_dashboard", "key"),
            "rechazos_detalle": ("rechazos_detalle_dashboard", "key"),
            "articulos": ("articulos_dashboard", "articulo"),
            "settings": ("settings_dashboard", "key"),
        }
        if table not in specs:
            raise ValueError("Tabla no permitida.")
        table_name, key_col = specs[table]
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(f"SELECT rec FROM {table_name} WHERE {key_col} = %s;", (str(key),))
            row = cur.fetchone()
            return row[0] if row else None

    def count_data_tables():
        cached = _cache_get("counts:data_tables")
        if cached is not None:
            return cached
        tables = {
            "rutas": "rutas_dashboard",
            "attempts": "attempts_dashboard",
            "clientes": "clientes_dashboard",
            "rechazos": "rechazos_dashboard",
            "rechazos_detalle": "rechazos_detalle_dashboard",
            "articulos": "articulos_dashboard",
            "settings": "settings_dashboard",
        }
        sql = "SELECT " + ", ".join(f"(SELECT COUNT(*) FROM {name})" for name in tables.values()) + ";"
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(sql)
            counts = cur.fetchone()
        return _cache_set("counts:data_tables", dict(zip(tables, counts)))

    def health_stats():
        cached = _cache_get("health:stats")
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                WITH route_stats AS (
                    SELECT
                        COUNT(*)::bigint AS rutas,
                        COUNT(*) FILTER (WHERE usable IS TRUE)::bigint AS validas,
                        COUNT(*) FILTER (WHERE rec ? 'pdv_total')::bigint AS ontime_rutas,
                        COALESCE(SUM(CASE WHEN (rec->>'pdv_total') ~ '^-?[0-9]+$' THEN (rec->>'pdv_total')::bigint ELSE 0 END), 0)::bigint AS pdv_total,
                        COALESCE(SUM(CASE WHEN (rec->>'pdv_ontime') ~ '^-?[0-9]+$' THEN (rec->>'pdv_ontime')::bigint ELSE 0 END), 0)::bigint AS pdv_ok,
                        COALESCE(SUM(CASE WHEN (rec->>'pdv_fuera_ontime') ~ '^-?[0-9]+$' THEN (rec->>'pdv_fuera_ontime')::bigint ELSE 0 END), 0)::bigint AS pdv_fuera,
                        COALESCE(SUM(CASE WHEN (rec->>'pdv_sin_ventana') ~ '^-?[0-9]+$' THEN (rec->>'pdv_sin_ventana')::bigint ELSE 0 END), 0)::bigint AS pdv_sin_ventana
                    FROM rutas_dashboard
                ), client_stats AS (
                    SELECT
                        COUNT(*)::bigint AS clientes,
                        COUNT(*) FILTER (
                            WHERE jsonb_typeof(rec->'ventanas') = 'array'
                              AND jsonb_array_length(rec->'ventanas') > 0
                        )::bigint AS clientes_con_ventana
                    FROM clientes_dashboard
                ), foxtrot_clients AS (
                    SELECT COALESCE(item->>'cliente', item #>> '{}') AS cliente, TRUE AS con_ventana
                    FROM rutas_dashboard AS route
                    CROSS JOIN LATERAL jsonb_array_elements(
                        CASE WHEN jsonb_typeof(route.rec->'clientes_con_ventana') = 'array'
                             THEN route.rec->'clientes_con_ventana' ELSE '[]'::jsonb END
                    ) AS item
                    UNION ALL
                    SELECT COALESCE(item->>'cliente', item #>> '{}') AS cliente, FALSE AS con_ventana
                    FROM rutas_dashboard AS route
                    CROSS JOIN LATERAL jsonb_array_elements(
                        CASE WHEN jsonb_typeof(route.rec->'clientes_sin_ventana') = 'array'
                             THEN route.rec->'clientes_sin_ventana' ELSE '[]'::jsonb END
                    ) AS item
                ), foxtrot_stats AS (
                    SELECT
                        COUNT(DISTINCT cliente) FILTER (WHERE cliente IS NOT NULL AND cliente <> '')::bigint AS total,
                        COUNT(DISTINCT cliente) FILTER (WHERE con_ventana AND cliente IS NOT NULL AND cliente <> '')::bigint AS con_ventana,
                        COUNT(DISTINCT cliente) FILTER (WHERE NOT con_ventana AND cliente IS NOT NULL AND cliente <> '')::bigint AS sin_ventana
                    FROM foxtrot_clients
                ), rejection_stats AS (
                    SELECT
                        COUNT(*)::bigint AS dias,
                        COALESCE(SUM(CASE WHEN (rec->>'rechazos') ~ '^-?[0-9]+$' THEN (rec->>'rechazos')::bigint ELSE 0 END), 0)::bigint AS total
                    FROM rechazos_dashboard
                )
                SELECT
                    route_stats.rutas, route_stats.validas,
                    client_stats.clientes, client_stats.clientes_con_ventana,
                    foxtrot_stats.total, foxtrot_stats.con_ventana, foxtrot_stats.sin_ventana,
                    rejection_stats.dias,
                    (SELECT COUNT(*)::bigint FROM rechazos_detalle_dashboard),
                    rejection_stats.total,
                    (SELECT COUNT(*)::bigint FROM articulos_dashboard),
                    route_stats.ontime_rutas, route_stats.pdv_total,
                    route_stats.pdv_ok + route_stats.pdv_fuera,
                    route_stats.pdv_ok, route_stats.pdv_fuera, route_stats.pdv_sin_ventana
                FROM route_stats, client_stats, foxtrot_stats, rejection_stats;
                """
            )
            row = cur.fetchone()
        keys = (
            "rutas", "validas", "clientes", "clientes_con_ventana",
            "clientes_foxtrot_unicos", "clientes_foxtrot_con_ventana",
            "clientes_foxtrot_sin_ventana", "rechazos_dias", "rechazos_detalle",
            "rechazos_total", "articulos", "ontime_rutas", "ontime_pdv_total",
            "ontime_pdv_evaluables", "ontime_pdv_ok", "ontime_pdv_fuera",
            "ontime_pdv_sin_ventana",
        )
        return _cache_set("health:stats", {key: int(value or 0) for key, value in zip(keys, row)})

    def backfill_clientes_gps_from_raw():
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                WITH coords AS (
                    SELECT
                        cliente,
                        COALESCE(
                            NULLIF(rec #>> '{raw_cliente,Coord Y de entrega}', '0'),
                            NULLIF(rec #>> '{raw_cliente,Coord Y}', '0')
                        ) AS lat,
                        COALESCE(
                            NULLIF(rec #>> '{raw_cliente,Coord X de entrega}', '0'),
                            NULLIF(rec #>> '{raw_cliente,Coord X}', '0')
                        ) AS lon
                    FROM clientes_dashboard
                )
                UPDATE clientes_dashboard c
                SET rec = jsonb_set(
                    jsonb_set(c.rec, '{latitud}', to_jsonb((coords.lat)::numeric), true),
                    '{longitud}', to_jsonb((coords.lon)::numeric), true
                )
                FROM coords
                WHERE c.cliente = coords.cliente
                  AND coords.lat IS NOT NULL
                  AND coords.lon IS NOT NULL
                  AND coords.lat ~ '^-?[0-9]+([.,][0-9]+)?$'
                  AND coords.lon ~ '^-?[0-9]+([.,][0-9]+)?$';
                """
            )
            changed = cur.rowcount
        clear_cache("clientes:")
        return changed

    def replace_clientes(recs):
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("TRUNCATE clientes_dashboard;")
            if recs:
                rows = [(cliente, _extras.Json(rec)) for cliente, rec in recs.items()]
                _extras.execute_values(
                    cur,
                    "INSERT INTO clientes_dashboard (cliente, rec) VALUES %s;",
                    rows,
                    page_size=DB_INSERT_PAGE_SIZE,
                )
        clear_cache("clientes:")
        return len(recs)

    def load_rechazos():
        cached = _cache_get("rechazos:all")
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT key, rec FROM rechazos_dashboard;")
            return _cache_set("rechazos:all", {key: rec for key, rec in cur.fetchall()})

    def load_rechazos_detalle():
        cached = _cache_get("rechazos_detalle:all")
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT key, rec FROM rechazos_detalle_dashboard;")
            return _cache_set("rechazos_detalle:all", {key: rec for key, rec in cur.fetchall()})

    def upsert_rechazos(recs):
        if not recs:
            return 0
        rows = [(key, _extras.Json(rec)) for key, rec in recs.items()]
        with _conn() as cn, cn.cursor() as cur:
            _extras.execute_values(
                cur,
                "INSERT INTO rechazos_dashboard (key, rec) VALUES %s "
                "ON CONFLICT (key) DO UPDATE SET rec = EXCLUDED.rec;",
                rows,
                page_size=DB_INSERT_PAGE_SIZE,
            )
        clear_cache("rechazos:")
        return len(recs)

    def upsert_rechazos_detalle(recs):
        if not recs:
            return 0
        rows = [(key, _extras.Json(rec)) for key, rec in recs.items()]
        with _conn() as cn, cn.cursor() as cur:
            _extras.execute_values(
                cur,
                "INSERT INTO rechazos_detalle_dashboard (key, rec) VALUES %s "
                "ON CONFLICT (key) DO UPDATE SET rec = EXCLUDED.rec;",
                rows,
                page_size=DB_INSERT_PAGE_SIZE,
            )
        clear_cache("rechazos_detalle:")
        return len(recs)

    def load_articulos():
        cached = _cache_get("articulos:all")
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT articulo, rec FROM articulos_dashboard;")
            return _cache_set("articulos:all", {articulo: rec for articulo, rec in cur.fetchall()})

    def replace_articulos(recs):
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("TRUNCATE articulos_dashboard;")
            if recs:
                rows = [(articulo, _extras.Json(rec)) for articulo, rec in recs.items()]
                _extras.execute_values(
                    cur,
                    "INSERT INTO articulos_dashboard (articulo, rec) VALUES %s;",
                    rows,
                    page_size=DB_INSERT_PAGE_SIZE,
                )
        clear_cache("articulos:")
        return len(recs)

    def load_settings():
        cached = _cache_get("settings:all")
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT key, rec FROM settings_dashboard;")
            return _cache_set("settings:all", {key: rec for key, rec in cur.fetchall()})

    def load_setting(key):
        cache_key = f"settings:item:{key}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT rec FROM settings_dashboard WHERE key = %s;", (str(key),))
            row = cur.fetchone()
        return _cache_set(cache_key, row[0] if row else {})

    def load_fichaya_marks(desde, hasta):
        cache_key = f"settings:fichaya-marks:{desde}:{hasta}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                SELECT item.value
                FROM settings_dashboard AS setting
                CROSS JOIN LATERAL jsonb_each(
                    CASE WHEN jsonb_typeof(setting.rec->'valor') = 'object'
                         THEN setting.rec->'valor' ELSE '{}'::jsonb END
                ) AS item
                WHERE setting.key = 'fichaya_marcas_cache'
                  AND item.value->>'fecha' >= %s
                  AND item.value->>'fecha' <= %s;
                """,
                (str(desde), str(hasta)),
            )
            rows = [row[0] for row in cur.fetchall()]
        return _cache_set(cache_key, rows)

    def load_fichaya_cache_info():
        cached = _cache_get("settings:fichaya-cache-info")
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(item.value)::int,
                       MIN(item.value->>'fecha'),
                       MAX(item.value->>'fecha'),
                       setting.rec->>'actualizado'
                FROM settings_dashboard AS setting
                LEFT JOIN LATERAL jsonb_each(
                    CASE WHEN jsonb_typeof(setting.rec->'valor') = 'object'
                         THEN setting.rec->'valor' ELSE '{}'::jsonb END
                ) AS item ON TRUE
                WHERE setting.key = 'fichaya_marcas_cache'
                GROUP BY setting.rec->>'actualizado';
                """
            )
            row = cur.fetchone()
        info = {
            "total": int(row[0] or 0) if row else 0,
            "desde": (row[1] or "") if row else "",
            "hasta": (row[2] or "") if row else "",
            "actualizado": (row[3] or "") if row else "",
        }
        return _cache_set("settings:fichaya-cache-info", info)

    def save_fichaya_marks(records, updated_at):
        records = records or {}
        payload = {"valor": records, "actualizado": str(updated_at or "")}
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO settings_dashboard (key, rec)
                VALUES ('fichaya_marcas_cache', %s)
                ON CONFLICT (key) DO UPDATE SET rec =
                    jsonb_set(
                        settings_dashboard.rec || jsonb_build_object(
                            'actualizado', EXCLUDED.rec->>'actualizado'
                        ),
                        '{valor}',
                        COALESCE(settings_dashboard.rec->'valor', '{}'::jsonb)
                            || COALESCE(EXCLUDED.rec->'valor', '{}'::jsonb),
                        true
                    );
                """,
                (_extras.Json(payload),),
            )
        clear_cache("settings:")
        return len(records)

    def save_setting(key, rec):
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                "INSERT INTO settings_dashboard (key, rec) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET rec = EXCLUDED.rec;",
                (key, _extras.Json(rec)),
            )
        clear_cache("settings:")
        return rec

    def ensure_logistics_tables():
        global _LOGISTICS_INIT_DONE
        if _LOGISTICS_INIT_DONE:
            return
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    to_regclass('logistics_cost_config') IS NOT NULL,
                    to_regclass('route_costs') IS NOT NULL,
                    to_regclass('route_customer_costs') IS NOT NULL,
                    to_regclass('routing_cache') IS NOT NULL,
                    to_regclass('logistics_depots') IS NOT NULL,
                    to_regclass('route_cost_calculations') IS NOT NULL,
                    to_regclass('route_customer_cost_calculations') IS NOT NULL,
                    to_regclass('logistics_cost_rates') IS NOT NULL,
                    to_regclass('logistics_vehicle_costs') IS NOT NULL,
                    to_regclass('idx_route_customer_costs_localidad') IS NOT NULL;
                """
            )
            if not all(cur.fetchone()):
                _ensure_logistics_tables(cur)
        _LOGISTICS_INIT_DONE = True

    def load_logistics_config():
        ensure_logistics_tables()
        cached = _cache_get("logistics:config")
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT rec FROM logistics_cost_config WHERE key = 'default';")
            row = cur.fetchone()
            return _cache_set("logistics:config", row[0] if row else {})

    def save_logistics_config(rec):
        ensure_logistics_tables()
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                "INSERT INTO logistics_cost_config (key, rec) VALUES ('default', %s) "
                "ON CONFLICT (key) DO UPDATE SET rec = EXCLUDED.rec, updated_at = now();",
                (_extras.Json(rec),),
            )
        clear_cache("logistics:config")
        clear_cache("logistics:setup")
        return rec

    def load_logistics_cost_rates():
        ensure_logistics_tables()
        cached = _cache_get("logistics:rates")
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT valid_from::text, rec FROM logistics_cost_rates ORDER BY valid_from DESC;")
            return _cache_set("logistics:rates", [{"valid_from": valid_from, **rec} for valid_from, rec in cur.fetchall()])

    def save_logistics_cost_rate(valid_from, rec):
        ensure_logistics_tables()
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                "INSERT INTO logistics_cost_rates (valid_from, rec) VALUES (%s, %s) "
                "ON CONFLICT (valid_from) DO UPDATE SET rec = EXCLUDED.rec, updated_at = now();",
                (str(valid_from), _extras.Json(rec)),
            )
        clear_cache("logistics:rates")
        clear_cache("logistics:setup")
        return {"valid_from": str(valid_from), **rec}

    def delete_logistics_cost_rate(valid_from):
        ensure_logistics_tables()
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("DELETE FROM logistics_cost_rates WHERE valid_from = %s;", (str(valid_from),))
            deleted = cur.rowcount > 0
        clear_cache("logistics:rates")
        clear_cache("logistics:setup")
        return deleted

    def get_effective_logistics_cost_rate(route_date):
        route_date = str(route_date)
        return next((row for row in load_logistics_cost_rates() if row["valid_from"] <= route_date), None)

    def load_logistics_vehicle_costs(vehicle=None):
        ensure_logistics_tables()
        cache_key = "logistics:vehicles:" + str(vehicle or "*")
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            if vehicle:
                cur.execute("SELECT valid_from::text, rec FROM logistics_vehicle_costs WHERE vehicle = %s ORDER BY valid_from DESC;", (str(vehicle),))
            else:
                cur.execute("SELECT valid_from::text, rec FROM logistics_vehicle_costs ORDER BY vehicle, valid_from DESC;")
            return _cache_set(cache_key, [{"valid_from": valid_from, **rec} for valid_from, rec in cur.fetchall()])

    def save_logistics_vehicle_cost(vehicle, valid_from, rec):
        ensure_logistics_tables()
        item = {**rec, "vehicle": str(vehicle)}
        key = f"{vehicle}|{valid_from}"
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                "INSERT INTO logistics_vehicle_costs (key, vehicle, valid_from, rec) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (vehicle, valid_from) DO UPDATE SET rec = EXCLUDED.rec, updated_at = now();",
                (key, str(vehicle), str(valid_from), _extras.Json(item)),
            )
        clear_cache("logistics:vehicles:")
        clear_cache("logistics:setup")
        return {"valid_from": str(valid_from), **item}

    def delete_logistics_vehicle_cost(vehicle, valid_from):
        ensure_logistics_tables()
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("DELETE FROM logistics_vehicle_costs WHERE vehicle = %s AND valid_from = %s;", (str(vehicle), str(valid_from)))
            deleted = cur.rowcount > 0
        clear_cache("logistics:vehicles:")
        clear_cache("logistics:setup")
        return deleted

    def get_effective_logistics_vehicle_cost(vehicle, route_date):
        route_date = str(route_date)
        return next((row for row in load_logistics_vehicle_costs(vehicle) if row["valid_from"] <= route_date), None)

    def load_logistics_depots():
        ensure_logistics_tables()
        cached = _cache_get("logistics:depots")
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT sucursal, rec FROM logistics_depots ORDER BY sucursal;")
            return _cache_set("logistics:depots", {sucursal: rec for sucursal, rec in cur.fetchall()})

    def load_logistics_setup():
        ensure_logistics_tables()
        cached = _cache_get("logistics:setup")
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE((SELECT rec FROM logistics_cost_config WHERE key = 'default'), '{}'::jsonb),
                    COALESCE((
                        SELECT jsonb_agg(jsonb_build_object('valid_from', valid_from::text) || rec ORDER BY valid_from DESC)
                        FROM logistics_cost_rates
                    ), '[]'::jsonb),
                    COALESCE((
                        SELECT jsonb_agg(jsonb_build_object('valid_from', valid_from::text, 'vehicle', vehicle) || rec ORDER BY vehicle, valid_from DESC)
                        FROM logistics_vehicle_costs
                    ), '[]'::jsonb),
                    COALESCE((SELECT jsonb_object_agg(sucursal, rec) FROM logistics_depots), '{}'::jsonb);
                """
            )
            config, rates, vehicles, depots = cur.fetchone()
        _cache_set("logistics:config", config)
        _cache_set("logistics:rates", rates)
        _cache_set("logistics:vehicles:*", vehicles)
        _cache_set("logistics:depots", depots)
        return _cache_set("logistics:setup", {
            "config": config,
            "rates": rates,
            "vehicles": vehicles,
            "depots": depots,
        })

    def save_logistics_depot(sucursal, rec):
        ensure_logistics_tables()
        item = dict(rec)
        item["sucursal"] = str(sucursal)
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                "INSERT INTO logistics_depots (sucursal, rec) VALUES (%s, %s) "
                "ON CONFLICT (sucursal) DO UPDATE SET rec = EXCLUDED.rec, updated_at = now();",
                (str(sucursal), _extras.Json(item)),
            )
        clear_cache("logistics:depots")
        clear_cache("logistics:setup")
        return item

    def delete_logistics_depot(sucursal):
        ensure_logistics_tables()
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("DELETE FROM logistics_depots WHERE sucursal = %s;", (str(sucursal),))
            deleted = cur.rowcount > 0
        clear_cache("logistics:depots")
        clear_cache("logistics:setup")
        return deleted

    def get_route(rid):
        cache_key = "routes:item:" + str(rid)
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT rec FROM rutas_dashboard WHERE rid = %s;", (str(rid),))
            row = cur.fetchone()
            return _cache_set(cache_key, row[0]) if row else None

    def list_recent_routes(limit=200):
        limit = max(1, min(int(limit), 500))
        cache_key = f"routes:recent:{limit}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                SELECT rid, fecha::text, sucursal, chofer, camion
                FROM rutas_dashboard
                ORDER BY fecha DESC NULLS LAST, rid DESC
                LIMIT %s;
                """,
                (limit,),
            )
            return _cache_set(cache_key, [
                {"rid": rid, "fecha": fecha, "suc": sucursal, "chofer": chofer, "camion": camion}
                for rid, fecha, sucursal, chofer, camion in cur.fetchall()
            ])

    def routing_cache_get(key):
        ensure_logistics_tables()
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT rec FROM routing_cache WHERE key = %s;", (key,))
            row = cur.fetchone()
            return row[0] if row else None

    def routing_cache_get_many(keys):
        ensure_logistics_tables()
        keys = list(dict.fromkeys(str(key) for key in keys if key))
        if not keys:
            return {}
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT key, rec FROM routing_cache WHERE key = ANY(%s);", (keys,))
            return {key: rec for key, rec in cur.fetchall()}

    def routing_cache_set(key, provider, rec):
        ensure_logistics_tables()
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                "INSERT INTO routing_cache (key, provider, rec) VALUES (%s, %s, %s) "
                "ON CONFLICT (key) DO UPDATE SET provider = EXCLUDED.provider, rec = EXCLUDED.rec, updated_at = now();",
                (key, provider, _extras.Json(rec)),
            )
        return rec

    def routing_cache_set_many(items):
        ensure_logistics_tables()
        rows = [
            (str(key), str(rec.get("provider") or "unknown"), _extras.Json(rec))
            for key, rec in items.items()
        ]
        if not rows:
            return 0
        with _conn() as cn, cn.cursor() as cur:
            _extras.execute_values(
                cur,
                "INSERT INTO routing_cache (key, provider, rec) VALUES %s "
                "ON CONFLICT (key) DO UPDATE SET provider = EXCLUDED.provider, rec = EXCLUDED.rec, updated_at = now();",
                rows,
                page_size=DB_INSERT_PAGE_SIZE,
            )
            return cur.rowcount

    def save_route_cost(rid, route_cost, customer_costs):
        ensure_logistics_tables()
        calculation_id = str(route_cost.get("calculation_id") or f"legacy:{rid}")
        rows = [
            (f"{rid}|{item.get('cliente')}|{item.get('orden_visita')}", rid, str(item.get("cliente") or ""), _extras.Json(item))
            for item in customer_costs
        ]
        history_rows = [
            (f"{calculation_id}|{item.get('cliente')}|{item.get('orden_visita')}", calculation_id, rid,
             str(item.get("cliente") or ""), _extras.Json({**item, "calculation_id": calculation_id}))
            for item in customer_costs
        ]
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                "INSERT INTO route_cost_calculations (calculation_id, rid, rec) VALUES (%s, %s, %s) "
                "ON CONFLICT (calculation_id) DO NOTHING;",
                (calculation_id, str(rid), _extras.Json(route_cost)),
            )
            if history_rows:
                _extras.execute_values(
                    cur,
                    "INSERT INTO route_customer_cost_calculations (key, calculation_id, rid, cliente, rec) VALUES %s "
                    "ON CONFLICT (key) DO NOTHING;",
                    history_rows,
                    page_size=DB_INSERT_PAGE_SIZE,
                )
            cur.execute(
                "INSERT INTO route_costs (rid, rec) VALUES (%s, %s) "
                "ON CONFLICT (rid) DO UPDATE SET rec = EXCLUDED.rec, calculated_at = now();",
                (str(rid), _extras.Json(route_cost)),
            )
            cur.execute("DELETE FROM route_customer_costs WHERE rid = %s;", (str(rid),))
            if rows:
                _extras.execute_values(
                    cur,
                    "INSERT INTO route_customer_costs (key, rid, cliente, rec) VALUES %s "
                    "ON CONFLICT (key) DO UPDATE SET rec = EXCLUDED.rec, calculated_at = now();",
                    rows,
                    page_size=DB_INSERT_PAGE_SIZE,
                )
        clear_cache("logistics:")
        return route_cost

    def load_route_cost_history(rid, limit=50):
        ensure_logistics_tables()
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                "SELECT rec FROM route_cost_calculations WHERE rid = %s ORDER BY created_at DESC LIMIT %s;",
                (str(rid), max(1, min(int(limit), 200))),
            )
            return [row[0] for row in cur.fetchall()]

    def load_route_cost_calculation(calculation_id):
        ensure_logistics_tables()
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT rec FROM route_cost_calculations WHERE calculation_id = %s;", (str(calculation_id),))
            row = cur.fetchone()
            route = row[0] if row else None
            cur.execute(
                "SELECT rec FROM route_customer_cost_calculations WHERE calculation_id = %s "
                "ORDER BY (rec->>'orden_visita')::int NULLS LAST;",
                (str(calculation_id),),
            )
            customers = [item[0] for item in cur.fetchall()]
        return {"route": route, "customers": customers}

    def load_logistics_dashboard(filters=None, limit=500):
        ensure_logistics_tables()
        filters = filters or {}
        limit = max(1, min(int(limit), 2000))
        cache_key = "logistics:dashboard:" + json.dumps([filters, limit], sort_keys=True, ensure_ascii=False)
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        clauses, params = [], []
        for key in ("sucursal", "camion", "chofer"):
            value = str(filters.get(key) or "").strip()
            if value:
                clauses.append(f"rec->>'{key}' = %s")
                params.append(value)
        rid = str(filters.get("rid") or "").strip()
        if rid:
            clauses.append("rid = %s")
            params.append(rid)
        if filters.get("desde"):
            clauses.append("rec->>'fecha' >= %s")
            params.append(str(filters["desde"]))
        if filters.get("hasta"):
            clauses.append("rec->>'fecha' <= %s")
            params.append(str(filters["hasta"]))
        customer_clauses, customer_params = [], []
        cliente = str(filters.get("cliente") or "").strip()
        localidad = str(filters.get("localidad") or "").strip()
        if cliente:
            customer_clauses.append("customer.cliente = %s")
            customer_params.append(cliente)
        if localidad:
            customer_clauses.append("customer.rec->>'localidad' = %s")
            customer_params.append(localidad)
        if customer_clauses:
            clauses.append(
                "EXISTS (SELECT 1 FROM route_customer_costs AS customer "
                "WHERE customer.rid = route_costs.rid AND " + " AND ".join(customer_clauses) + ")"
            )
            params.extend(customer_params)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query_params = params + [limit] + customer_params
        customer_where = " WHERE " + " AND ".join(customer_clauses) if customer_clauses else ""
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                f"""
                WITH selected_routes AS (
                    SELECT rid, rec
                    FROM route_costs
                    {where}
                    ORDER BY rec->>'fecha' DESC NULLS LAST
                    LIMIT %s
                ), selected_customers AS (
                    SELECT customer.rec
                    FROM route_customer_costs AS customer
                    JOIN selected_routes ON selected_routes.rid = customer.rid
                    {customer_where}
                ), dimension_options AS (
                    SELECT DISTINCT rec->>'sucursal' AS sucursal, rec->>'camion' AS camion, rec->>'chofer' AS chofer
                    FROM route_costs
                ), route_options AS (
                    SELECT
                        rid AS value,
                        concat_ws(' · ', NULLIF(rec->>'fecha', ''), NULLIF(rec->>'sucursal', ''), NULLIF(rec->>'chofer', '')) AS label,
                        rec->>'fecha' AS sort_date
                    FROM route_costs
                    ORDER BY rec->>'fecha' DESC NULLS LAST
                    LIMIT 2000
                ), customer_options AS (
                    SELECT
                        cliente AS value,
                        concat_ws(' · ', cliente, NULLIF(MAX(rec->>'nombre'), '')) AS label,
                        MAX(rec->>'localidad') AS localidad
                    FROM route_customer_costs
                    GROUP BY cliente
                    ORDER BY cliente
                    LIMIT 5000
                )
                SELECT
                    COALESCE((SELECT jsonb_agg(rec ORDER BY rec->>'fecha' DESC NULLS LAST) FROM selected_routes), '[]'::jsonb),
                    COALESCE((SELECT jsonb_agg(rec) FROM selected_customers), '[]'::jsonb),
                    COALESCE((SELECT jsonb_agg(jsonb_build_array(sucursal, camion, chofer)) FROM dimension_options), '[]'::jsonb),
                    COALESCE((SELECT jsonb_agg(jsonb_build_object('value', value, 'label', label) ORDER BY sort_date DESC NULLS LAST) FROM route_options), '[]'::jsonb),
                    COALESCE((SELECT jsonb_agg(jsonb_build_object('value', value, 'label', label, 'localidad', localidad) ORDER BY value) FROM customer_options), '[]'::jsonb),
                    jsonb_build_object(
                        'routes_total', (SELECT COUNT(*) FROM rutas_dashboard),
                        'routes_calculated', (SELECT COUNT(*) FROM route_costs),
                        'routes_with_volume', (SELECT COUNT(DISTINCT route_id) FROM attempts_dashboard
                            WHERE CASE WHEN (rec->>'bultos') ~ '^-?[0-9]+([.][0-9]+)?$' THEN (rec->>'bultos')::numeric ELSE 0 END > 0
                               OR CASE WHEN (rec->>'hl') ~ '^-?[0-9]+([.][0-9]+)?$' THEN (rec->>'hl')::numeric ELSE 0 END > 0
                               OR CASE WHEN (rec->>'pallets') ~ '^-?[0-9]+([.][0-9]+)?$' THEN (rec->>'pallets')::numeric ELSE 0 END > 0),
                        'routes_with_vehicle', (SELECT COUNT(*) FROM rutas_dashboard
                            WHERE camion IS NOT NULL AND camion NOT IN ('', 'Sin camion', 'Sin camión')),
                        'clients_total', (SELECT COUNT(*) FROM clientes_dashboard),
                        'clients_with_gps', (SELECT COUNT(*) FROM clientes_dashboard
                            WHERE NULLIF(rec->>'latitud','') IS NOT NULL AND NULLIF(rec->>'longitud','') IS NOT NULL),
                        'orders_total', (SELECT COUNT(*) FROM pedidos_dashboard),
                        'orders_with_volume', (SELECT COUNT(*) FROM pedidos_dashboard
                            WHERE CASE WHEN (rec->>'bultos') ~ '^-?[0-9]+([.][0-9]+)?$' THEN (rec->>'bultos')::numeric ELSE 0 END > 0
                               OR CASE WHEN (rec->>'hl') ~ '^-?[0-9]+([.][0-9]+)?$' THEN (rec->>'hl')::numeric ELSE 0 END > 0
                               OR CASE WHEN (rec->>'pallets') ~ '^-?[0-9]+([.][0-9]+)?$' THEN (rec->>'pallets')::numeric ELSE 0 END > 0)
                    );
                """,
                query_params,
            )
            routes, customers, option_rows, route_options, customer_option_rows, coverage = cur.fetchone()
        _cache_set("logistics:coverage", coverage)
        result = {
            "routes": routes,
            "customers": customers,
            "options": {
                "sucursales": sorted({row[0] for row in option_rows if row[0]}),
                "camiones": sorted({row[1] for row in option_rows if row[1]}),
                "choferes": sorted({row[2] for row in option_rows if row[2]}),
                "rutas": route_options,
                "clientes": [
                    {"value": row["value"], "label": row["label"]}
                    for row in customer_option_rows
                ],
                "localidades": sorted({row["localidad"] for row in customer_option_rows if row.get("localidad")}),
            },
        }
        return _cache_set(cache_key, result)

    def logistics_data_coverage():
        ensure_logistics_tables()
        cached = _cache_get("logistics:coverage")
        if cached is not None:
            return cached
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM rutas_dashboard),
                    (SELECT COUNT(*) FROM route_costs),
                    (SELECT COUNT(DISTINCT route_id) FROM attempts_dashboard
                     WHERE CASE WHEN (rec->>'bultos') ~ '^-?[0-9]+([.][0-9]+)?$' THEN (rec->>'bultos')::numeric ELSE 0 END > 0
                        OR CASE WHEN (rec->>'hl') ~ '^-?[0-9]+([.][0-9]+)?$' THEN (rec->>'hl')::numeric ELSE 0 END > 0
                        OR CASE WHEN (rec->>'pallets') ~ '^-?[0-9]+([.][0-9]+)?$' THEN (rec->>'pallets')::numeric ELSE 0 END > 0),
                    (SELECT COUNT(*) FROM rutas_dashboard
                     WHERE camion IS NOT NULL AND camion NOT IN ('', 'Sin camion', 'Sin camión')),
                    (SELECT COUNT(*) FROM clientes_dashboard),
                    (SELECT COUNT(*) FROM clientes_dashboard
                     WHERE NULLIF(rec->>'latitud','') IS NOT NULL AND NULLIF(rec->>'longitud','') IS NOT NULL),
                    (SELECT COUNT(*) FROM pedidos_dashboard),
                    (SELECT COUNT(*) FROM pedidos_dashboard
                     WHERE CASE WHEN (rec->>'bultos') ~ '^-?[0-9]+([.][0-9]+)?$' THEN (rec->>'bultos')::numeric ELSE 0 END > 0
                        OR CASE WHEN (rec->>'hl') ~ '^-?[0-9]+([.][0-9]+)?$' THEN (rec->>'hl')::numeric ELSE 0 END > 0
                        OR CASE WHEN (rec->>'pallets') ~ '^-?[0-9]+([.][0-9]+)?$' THEN (rec->>'pallets')::numeric ELSE 0 END > 0);
                """
            )
            values = cur.fetchone()
        result = {
            "routes_total": values[0], "routes_calculated": values[1],
            "routes_with_volume": values[2], "routes_with_vehicle": values[3],
            "clients_total": values[4], "clients_with_gps": values[5],
            "orders_total": values[6], "orders_with_volume": values[7],
        }
        return _cache_set("logistics:coverage", result)

    def load_route_cost(rid):
        ensure_logistics_tables()
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT rec FROM route_costs WHERE rid = %s;", (str(rid),))
            row = cur.fetchone()
            route = row[0] if row else None
            cur.execute("SELECT rec FROM route_customer_costs WHERE rid = %s ORDER BY (rec->>'orden_visita')::int NULLS LAST;", (str(rid),))
            customers = [r[0] for r in cur.fetchall()]
        return {"route": route, "customers": customers}

    def save_record(table, key, rec):
        if table == "rutas":
            upsert_all({key: rec})
            return rec
        specs = {
            "attempts": ("attempts_dashboard", "attempt_key"),
            "clientes": ("clientes_dashboard", "cliente"),
            "rechazos": ("rechazos_dashboard", "key"),
            "rechazos_detalle": ("rechazos_detalle_dashboard", "key"),
            "articulos": ("articulos_dashboard", "articulo"),
            "settings": ("settings_dashboard", "key"),
        }
        if table not in specs:
            raise ValueError("Tabla no permitida.")
        table_name, key_col = specs[table]
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {table_name} ({key_col}, rec) VALUES (%s, %s) "
                "ON CONFLICT ({}) DO UPDATE SET rec = EXCLUDED.rec;".format(key_col),
                (key, _extras.Json(rec)),
            )
        clear_cache()
        return rec

    def fast_autofill_foxtrot_missing():
        def blank_expr(path):
            return f"(NULLIF(BTRIM(COALESCE(rec #>> '{{raw_foxtrot,{path}}}', '')), '') IS NULL OR LOWER(BTRIM(COALESCE(rec #>> '{{raw_foxtrot,{path}}}', ''))) IN ('nan','none','null','nat'))"

        rules = [
            ("Total Driven Meters", "Planned Foxtrot Driving Meters", "number", "fox_total_driven_meters"),
            ("Total Driven Seconds", "Planned Foxtrot Driving Seconds", "number", "fox_total_driven_seconds"),
            ("Total Journey Seconds", "Planned Foxtrot Journey Seconds", "number", "fox_total_journey_seconds"),
            ("Actual Route Departure Time", "Driver Marked Route Start Timestamp", "timestamp", "fox_actual_route_departure_time"),
            ("Actual Route Arrival Time", "Driver Marked Route End Timestamp", "timestamp", "fox_actual_route_arrival_time"),
        ]
        by_col = {}
        changed_routes = set()
        with _conn() as cn, cn.cursor() as cur:
            for target, source, kind, raw_sql_col in rules:
                if kind == "number":
                    value_sql = (
                        "ROUND(((rec #>> '{raw_foxtrot," + source + "}')::numeric * 1.10))::bigint::text"
                    )
                    source_ok = f"NULLIF(BTRIM(COALESCE(rec #>> '{{raw_foxtrot,{source}}}', '')), '') IS NOT NULL"
                else:
                    value_sql = f"(rec #>> '{{raw_foxtrot,{source}}}')"
                    source_ok = f"NULLIF(BTRIM(COALESCE(rec #>> '{{raw_foxtrot,{source}}}', '')), '') IS NOT NULL"
                where = f"rec IS NOT NULL AND {blank_expr(target)} AND {source_ok}"
                cur.execute(
                    f"""
                    WITH target_rows AS (
                        SELECT rid, {value_sql} AS new_value
                        FROM rutas_dashboard
                        WHERE {where}
                    )
                    UPDATE rutas_dashboard r
                    SET rec = jsonb_set(COALESCE(r.rec, '{{}}'::jsonb), '{{raw_foxtrot,{target}}}', to_jsonb(t.new_value), true),
                        {raw_sql_col} = t.new_value
                    FROM target_rows t
                    WHERE r.rid = t.rid
                    RETURNING r.rid;
                    """
                )
                ids = [row[0] for row in cur.fetchall()]
                by_col[target] = len(ids)
                changed_routes.update(ids)

        clear_cache("routes:")
        return {"rutas": len(changed_routes), "celdas": sum(by_col.values()), "por_columna": by_col}

    def delete_record(table, key):
        specs = {
            "rutas": ("rutas_dashboard", "rid"),
            "attempts": ("attempts_dashboard", "attempt_key"),
            "clientes": ("clientes_dashboard", "cliente"),
            "rechazos": ("rechazos_dashboard", "key"),
            "rechazos_detalle": ("rechazos_detalle_dashboard", "key"),
            "articulos": ("articulos_dashboard", "articulo"),
            "settings": ("settings_dashboard", "key"),
        }
        if table not in specs:
            raise ValueError("Tabla no permitida.")
        table_name, key_col = specs[table]
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(f"DELETE FROM {table_name} WHERE {key_col} = %s;", (key,))
        clear_cache()
        return True


# ========================= JSON =========================
else:
    def init():
        os.makedirs(DATA_DIR, exist_ok=True)

    def load_all():
        if os.path.exists(JSON_PATH):
            try:
                return {r["rid"]: r for r in json.load(open(JSON_PATH, encoding="utf-8"))["rutas"]}
            except Exception:
                return {}
        return {}

    def load_dashboard_routes(fields=None):
        fields = set(fields or ())
        return {
            rid: {
                key: value for key, value in rec.items()
                if (key in fields if fields else key != "raw_foxtrot")
            }
            for rid, rec in load_all().items()
        }

    def load_fichaya_dimensions(desde="2026-08-01"):
        rows = [
            rec for rec in load_all().values()
            if rec.get("usable") and str(rec.get("fecha") or "") >= str(desde)
        ]
        fechas = sorted(str(rec.get("fecha") or "") for rec in rows if rec.get("fecha"))
        return {
            "sucursales": sorted({str(rec.get("suc")) for rec in rows if rec.get("suc")}),
            "choferes": sorted({str(rec.get("chofer")) for rec in rows if rec.get("chofer")}),
            "desde": fechas[0] if fechas else "",
            "hasta": fechas[-1] if fechas else "",
        }

    def load_fichaya_routes(desde, hasta, suc="", chofer=""):
        rows = []
        for rec in load_all().values():
            fecha = str(rec.get("fecha") or "")
            if not rec.get("usable") or fecha < str(desde) or fecha > str(hasta):
                continue
            if suc and str(rec.get("suc") or "") != str(suc):
                continue
            if chofer and str(rec.get("chofer") or "") != str(chofer):
                continue
            rows.append({
                "rid": rec.get("rid") or "",
                "fecha": fecha,
                "suc": rec.get("suc") or "",
                "chofer": rec.get("chofer") or "",
                "inicio_foxtrot": rec.get("inicio_foxtrot") or "",
                "fin_foxtrot": rec.get("fin_foxtrot") or "",
                "usable": True,
            })
        return sorted(
            rows,
            key=lambda rec: (
                rec.get("fecha") or "", rec.get("suc") or "",
                rec.get("chofer") or "", rec.get("inicio_foxtrot") or "",
            ),
            reverse=True,
        )

    def load_foxtrot_quality(columns, filters=None, q="", limit=300):
        columns = tuple(str(col) for col in columns if str(col) in ROUTE_RAW_COLUMNS)
        filters = filters or {}

        def blank(value):
            text = "" if value is None else str(value).strip().lower()
            return text in ("", "nan", "none", "null", "nat")

        all_rows = list(load_all().values())
        missing = {
            column: sum(blank((rec.get("raw_foxtrot") or {}).get(column)) for rec in all_rows)
            for column in columns
        }
        query = str(q or "").strip().lower()
        rows = []
        for rec in all_rows:
            raw = rec.get("raw_foxtrot") or {}
            if query and query not in json.dumps(rec, ensure_ascii=False, default=str).lower():
                continue
            if any(
                (mode == "empty" and not blank(raw.get(column)))
                or (mode == "present" and blank(raw.get(column)))
                for column in columns
                for mode in [str(filters.get(column) or "")]
            ):
                continue
            rows.append({
                "rid": rec.get("rid") or "",
                "fecha": rec.get("fecha") or "",
                "suc": rec.get("suc") or "",
                "chofer": rec.get("chofer") or "",
                "raw_foxtrot": {column: raw.get(column) for column in columns},
            })
        rows.sort(key=lambda rec: (rec["fecha"], rec["suc"], rec["chofer"]), reverse=True)
        return {"total": len(all_rows), "missing": missing, "rows": rows[:max(1, min(int(limit), 500))]}

    def _dump(base):
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"rutas": list(base.values())}, open(JSON_PATH, "w", encoding="utf-8"), ensure_ascii=False)

    def add_new(recs):
        base = load_all()
        added = 0
        for rid, rec in recs.items():
            if rid not in base:
                base[rid] = rec
                added += 1
        _dump(base)
        return added

    def upsert_all(recs, count_new=True):
        base = load_all()
        added = 0
        for rid, rec in recs.items():
            if rid not in base:
                added += 1
            base[rid] = rec
        _dump(base)
        return added

    def count_routes():
        return len(load_all())

    def route_import_stats(tml_obj=30, ti_obj=30):
        rows = list(load_all().values())
        us = [r for r in rows if r.get("usable")]
        tml = [r["tml"] for r in us if r.get("tml") is not None]
        ti = [r["ti"] for r in us if r.get("ti") is not None]
        tml_ok = [v <= tml_obj for v in tml]
        ti_ok = [v <= ti_obj for v in ti]
        return {
            "total": len(rows),
            "validas": len(us),
            "sin_cierre": len(rows) - len(us),
            "tml_prom": round(sum(tml) / len(tml), 1) if tml else None,
            "tml_cumpl": round(100 * sum(tml_ok) / len(tml_ok)) if tml_ok else None,
            "ti_prom": round(sum(ti) / len(ti), 1) if ti else None,
            "ti_cumpl": round(100 * sum(ti_ok) / len(ti_ok)) if ti_ok else None,
        }

    def reset():
        if os.path.exists(JSON_PATH):
            os.remove(JSON_PATH)
        if os.path.exists(ATTEMPTS_JSON_PATH):
            os.remove(ATTEMPTS_JSON_PATH)

    def upsert_attempts(recs):
        base = load_attempts()
        base.update(recs)
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"attempts": base}, open(ATTEMPTS_JSON_PATH, "w", encoding="utf-8"), ensure_ascii=False)
        return len(recs)

    def load_attempts():
        if os.path.exists(ATTEMPTS_JSON_PATH):
            try:
                return json.load(open(ATTEMPTS_JSON_PATH, encoding="utf-8"))["attempts"]
            except Exception:
                return {}
        return {}

    def load_attempts_by_route(route_id):
        rid = str(route_id)
        return {
            key: rec for key, rec in load_attempts().items()
            if str(rec.get("Route ID") or rec.get("route_id") or "") == rid
        }

    def load_route_sales(route_id):
        import storage_pedidos
        route = get_route(route_id) or {}
        route_date = str(route.get("fecha") or "")
        route_dates = {str(rid): str(rec.get("fecha") or "") for rid, rec in load_all().items()}
        matches = {}
        for rec in load_attempts().values():
            rid = str(rec.get("Route ID") or rec.get("route_id") or "")
            cliente = str(rec.get("cliente") or rec.get("Customer ID") or "").lstrip("0")
            if rid and cliente:
                matches.setdefault((route_dates.get(rid, ""), cliente), set()).add(rid)
        out = {}
        for order in storage_pedidos.fetch_all():
            if not order.get("facturado") or order.get("anulado"):
                continue
            order_date = str(order.get("fecha_entrega") or order.get("fecha") or "")
            cliente = str(order.get("cliente") or "").split(" - ", 1)[0].strip().lstrip("0")
            if order_date != route_date or matches.get((order_date, cliente)) != {str(route_id)}:
                continue
            item = out.setdefault(cliente, {"venta": 0.0, "pedidos_facturados": 0})
            item["venta"] += float(order.get("total") or 0)
            item["pedidos_facturados"] += 1
        return out

    def sample_attempt(limit=1):
        return list(load_attempts().values())[:int(limit)]

    def update_attempt_deliveries(records):
        attempts = load_attempts()
        updated = 0
        for item in records:
            for rec in attempts.values():
                rid = str(rec.get("Route ID") or rec.get("route_id") or "")
                cliente = str(rec.get("cliente") or rec.get("Customer ID") or "").lstrip("0")
                if rid == str(item["route_id"]) and cliente == str(item["cliente"]).lstrip("0"):
                    rec.update(item["values"])
                    updated += 1
        upsert_attempts(attempts)
        return updated

    def update_route_vehicles(records):
        routes = load_all()
        updated = 0
        for item in records:
            route = routes.get(str(item["rid"]))
            if route is None:
                continue
            route["camion"] = str(item["camion"])
            route["raw_vehicle_assignment"] = item.get("raw") or {}
            updated += 1
        upsert_all(routes, count_new=False)
        return updated

    def load_clientes():
        if os.path.exists(CLIENTES_JSON_PATH):
            try:
                return json.load(open(CLIENTES_JSON_PATH, encoding="utf-8"))["clientes"]
            except Exception:
                return {}
        return {}

    def backfill_clientes_gps_from_raw():
        base = load_clientes()
        changed = 0
        for rec in base.values():
            raw = rec.get("raw_cliente") or {}
            lat = raw.get("Coord Y de entrega") if raw.get("Coord Y de entrega") not in (None, "", "0", 0) else raw.get("Coord Y")
            lon = raw.get("Coord X de entrega") if raw.get("Coord X de entrega") not in (None, "", "0", 0) else raw.get("Coord X")
            if lat in (None, "", "0", 0) or lon in (None, "", "0", 0):
                continue
            try:
                rec["latitud"] = float(str(lat).replace(",", "."))
                rec["longitud"] = float(str(lon).replace(",", "."))
            except ValueError:
                continue
            changed += 1
        replace_clientes(base)
        return changed

    def replace_clientes(recs):
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"clientes": recs}, open(CLIENTES_JSON_PATH, "w", encoding="utf-8"), ensure_ascii=False)
        return len(recs)

    def load_rechazos():
        path = os.path.join(DATA_DIR, "rechazos_dashboard.json")
        if os.path.exists(path):
            try:
                return json.load(open(path, encoding="utf-8"))["rechazos"]
            except Exception:
                return {}
        return {}

    def load_rechazos_detalle():
        path = os.path.join(DATA_DIR, "rechazos_detalle_dashboard.json")
        if os.path.exists(path):
            try:
                return json.load(open(path, encoding="utf-8"))["rechazos_detalle"]
            except Exception:
                return {}
        return {}

    def upsert_rechazos(recs):
        path = os.path.join(DATA_DIR, "rechazos_dashboard.json")
        base = load_rechazos()
        base.update(recs)
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"rechazos": base}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        return len(recs)

    def upsert_rechazos_detalle(recs):
        path = os.path.join(DATA_DIR, "rechazos_detalle_dashboard.json")
        base = load_rechazos_detalle()
        base.update(recs)
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"rechazos_detalle": base}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        return len(recs)

    def load_articulos():
        if os.path.exists(ARTICULOS_JSON_PATH):
            try:
                return json.load(open(ARTICULOS_JSON_PATH, encoding="utf-8"))["articulos"]
            except Exception:
                return {}
        return {}

    def replace_articulos(recs):
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"articulos": recs}, open(ARTICULOS_JSON_PATH, "w", encoding="utf-8"), ensure_ascii=False)
        return len(recs)

    def load_settings():
        path = os.path.join(DATA_DIR, "settings_dashboard.json")
        if os.path.exists(path):
            try:
                return json.load(open(path, encoding="utf-8"))["settings"]
            except Exception:
                return {}
        return {}

    def load_setting(key):
        return load_settings().get(str(key)) or {}

    def load_fichaya_marks(desde, hasta):
        current = load_setting("fichaya_marcas_cache")
        records = current.get("valor") if isinstance(current, dict) else {}
        records = records if isinstance(records, dict) else {}
        return [
            rec for rec in records.values()
            if str(desde) <= str(rec.get("fecha") or "") <= str(hasta)
        ]

    def load_fichaya_cache_info():
        current = load_setting("fichaya_marcas_cache")
        records = current.get("valor") if isinstance(current, dict) else {}
        records = records if isinstance(records, dict) else {}
        fechas = sorted({str(rec.get("fecha") or "") for rec in records.values() if rec.get("fecha")})
        return {
            "total": len(records),
            "actualizado": current.get("actualizado", "") if isinstance(current, dict) else "",
            "desde": fechas[0] if fechas else "",
            "hasta": fechas[-1] if fechas else "",
        }

    def save_fichaya_marks(records, updated_at):
        current = load_setting("fichaya_marcas_cache")
        base = current.get("valor") if isinstance(current, dict) else {}
        base = dict(base) if isinstance(base, dict) else {}
        base.update(records or {})
        save_setting("fichaya_marcas_cache", {
            "valor": base,
            "actualizado": str(updated_at or ""),
        })
        return len(records or {})

    def save_setting(key, rec):
        path = os.path.join(DATA_DIR, "settings_dashboard.json")
        base = load_settings()
        base[key] = rec
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"settings": base}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        return rec

    def ensure_logistics_tables():
        os.makedirs(DATA_DIR, exist_ok=True)

    def load_logistics_config():
        return load_settings().get("logistics_cost_config") or {}

    def save_logistics_config(rec):
        return save_setting("logistics_cost_config", rec)

    def _logistics_rates_path():
        return os.path.join(DATA_DIR, "logistics_cost_rates.json")

    def _load_logistics_rates_file():
        try:
            return json.load(open(_logistics_rates_path(), encoding="utf-8")) if os.path.exists(_logistics_rates_path()) else {"rates": {}, "vehicles": {}}
        except Exception:
            return {"rates": {}, "vehicles": {}}

    def _save_logistics_rates_file(base):
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump(base, open(_logistics_rates_path(), "w", encoding="utf-8"), ensure_ascii=False)

    def load_logistics_cost_rates():
        rows = [{"valid_from": date, **rec} for date, rec in _load_logistics_rates_file().get("rates", {}).items()]
        return sorted(rows, key=lambda rec: rec["valid_from"], reverse=True)

    def save_logistics_cost_rate(valid_from, rec):
        base = _load_logistics_rates_file()
        base.setdefault("rates", {})[str(valid_from)] = rec
        _save_logistics_rates_file(base)
        return {"valid_from": str(valid_from), **rec}

    def delete_logistics_cost_rate(valid_from):
        base = _load_logistics_rates_file()
        existed = str(valid_from) in base.setdefault("rates", {})
        base["rates"].pop(str(valid_from), None)
        _save_logistics_rates_file(base)
        return existed

    def get_effective_logistics_cost_rate(route_date):
        return next((rec for rec in load_logistics_cost_rates() if rec["valid_from"] <= str(route_date)), None)

    def load_logistics_vehicle_costs(vehicle=None):
        rows = []
        for key, rec in _load_logistics_rates_file().get("vehicles", {}).items():
            if not vehicle or str(rec.get("vehicle")) == str(vehicle):
                rows.append({"valid_from": key.split("|", 1)[1], **rec})
        return sorted(rows, key=lambda rec: (str(rec.get("vehicle")), rec["valid_from"]), reverse=True)

    def save_logistics_vehicle_cost(vehicle, valid_from, rec):
        base = _load_logistics_rates_file()
        item = {**rec, "vehicle": str(vehicle)}
        base.setdefault("vehicles", {})[f"{vehicle}|{valid_from}"] = item
        _save_logistics_rates_file(base)
        return {"valid_from": str(valid_from), **item}

    def delete_logistics_vehicle_cost(vehicle, valid_from):
        base = _load_logistics_rates_file()
        existed = f"{vehicle}|{valid_from}" in base.setdefault("vehicles", {})
        base["vehicles"].pop(f"{vehicle}|{valid_from}", None)
        _save_logistics_rates_file(base)
        return existed

    def get_effective_logistics_vehicle_cost(vehicle, route_date):
        return next((rec for rec in load_logistics_vehicle_costs(vehicle) if rec["valid_from"] <= str(route_date)), None)

    def load_logistics_depots():
        path = os.path.join(DATA_DIR, "logistics_depots.json")
        try:
            return json.load(open(path, encoding="utf-8")).get("depots", {}) if os.path.exists(path) else {}
        except Exception:
            return {}

    def load_logistics_setup():
        return {
            "config": load_logistics_config(),
            "rates": load_logistics_cost_rates(),
            "vehicles": load_logistics_vehicle_costs(),
            "depots": load_logistics_depots(),
        }

    def save_logistics_depot(sucursal, rec):
        path = os.path.join(DATA_DIR, "logistics_depots.json")
        depots = load_logistics_depots()
        item = dict(rec)
        item["sucursal"] = str(sucursal)
        depots[str(sucursal)] = item
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"depots": depots}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        return item

    def delete_logistics_depot(sucursal):
        path = os.path.join(DATA_DIR, "logistics_depots.json")
        depots = load_logistics_depots()
        existed = str(sucursal) in depots
        depots.pop(str(sucursal), None)
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"depots": depots}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        return existed

    def get_route(rid):
        return load_all().get(str(rid))

    def list_recent_routes(limit=200):
        rows = [{"rid": rid, **rec} for rid, rec in load_all().items()]
        rows.sort(key=lambda rec: (str(rec.get("fecha") or ""), str(rec.get("rid") or "")), reverse=True)
        return rows[:max(1, min(int(limit), 500))]

    def routing_cache_get(key):
        path = os.path.join(DATA_DIR, "routing_cache.json")
        if not os.path.exists(path):
            return None
        try:
            return json.load(open(path, encoding="utf-8")).get("routing_cache", {}).get(key)
        except Exception:
            return None

    def routing_cache_get_many(keys):
        path = os.path.join(DATA_DIR, "routing_cache.json")
        try:
            base = json.load(open(path, encoding="utf-8")).get("routing_cache", {}) if os.path.exists(path) else {}
        except Exception:
            base = {}
        return {key: base[key] for key in keys if key in base}

    def routing_cache_set(key, provider, rec):
        path = os.path.join(DATA_DIR, "routing_cache.json")
        try:
            base = json.load(open(path, encoding="utf-8")).get("routing_cache", {}) if os.path.exists(path) else {}
        except Exception:
            base = {}
        item = dict(rec)
        item["provider"] = provider
        base[key] = item
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"routing_cache": base}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        return rec

    def routing_cache_set_many(items):
        path = os.path.join(DATA_DIR, "routing_cache.json")
        try:
            base = json.load(open(path, encoding="utf-8")).get("routing_cache", {}) if os.path.exists(path) else {}
        except Exception:
            base = {}
        for key, rec in items.items():
            base[key] = dict(rec)
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"routing_cache": base}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        return len(items)

    def save_route_cost(rid, route_cost, customer_costs):
        path = os.path.join(DATA_DIR, "logistics_costs.json")
        try:
            base = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        except Exception:
            base = {}
        base.setdefault("route_costs", {})[str(rid)] = route_cost
        base.setdefault("route_customer_costs", {})[str(rid)] = customer_costs
        calculation_id = str(route_cost.get("calculation_id") or f"legacy:{rid}")
        base.setdefault("route_cost_calculations", {}).setdefault(str(rid), {})[calculation_id] = route_cost
        base.setdefault("route_customer_cost_calculations", {})[calculation_id] = [
            {**item, "calculation_id": calculation_id} for item in customer_costs
        ]
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump(base, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        return route_cost

    def load_route_cost_history(rid, limit=50):
        path = os.path.join(DATA_DIR, "logistics_costs.json")
        try:
            base = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        except Exception:
            base = {}
        history = list(base.get("route_cost_calculations", {}).get(str(rid), {}).values())
        history.sort(key=lambda rec: str(rec.get("calculated_at") or ""), reverse=True)
        return history[:max(1, min(int(limit), 200))]

    def load_route_cost_calculation(calculation_id):
        path = os.path.join(DATA_DIR, "logistics_costs.json")
        try:
            base = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        except Exception:
            base = {}
        route = None
        for calculations in base.get("route_cost_calculations", {}).values():
            if str(calculation_id) in calculations:
                route = calculations[str(calculation_id)]
                break
        return {"route": route, "customers": base.get("route_customer_cost_calculations", {}).get(str(calculation_id), [])}

    def load_logistics_dashboard(filters=None, limit=500):
        filters = filters or {}
        path = os.path.join(DATA_DIR, "logistics_costs.json")
        try:
            base = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        except Exception:
            base = {}
        all_routes = list(base.get("route_costs", {}).values())
        all_customers = [
            item
            for items in base.get("route_customer_costs", {}).values()
            for item in items
        ]
        options = {
            "sucursales": sorted({r.get("sucursal") for r in all_routes if r.get("sucursal")}),
            "camiones": sorted({r.get("camion") for r in all_routes if r.get("camion")}),
            "choferes": sorted({r.get("chofer") for r in all_routes if r.get("chofer")}),
            "rutas": [
                {
                    "value": str(r.get("rid") or ""),
                    "label": " · ".join(str(r.get(key) or "") for key in ("fecha", "sucursal", "chofer")),
                }
                for r in sorted(all_routes, key=lambda item: str(item.get("fecha") or ""), reverse=True)
                if r.get("rid")
            ],
            "clientes": [
                {"value": cliente, "label": f"{cliente} · {rec.get('nombre') or ''}".rstrip(" ·")}
                for cliente, rec in sorted({str(c.get("cliente") or ""): c for c in all_customers if c.get("cliente")}.items())
            ],
            "localidades": sorted({c.get("localidad") for c in all_customers if c.get("localidad")}),
        }
        routes = []
        for rec in all_routes:
            if any(filters.get(k) and str(rec.get(k) or "") != str(filters[k]) for k in ("sucursal", "camion", "chofer")):
                continue
            if filters.get("rid") and str(rec.get("rid") or "") != str(filters["rid"]):
                continue
            if filters.get("desde") and str(rec.get("fecha") or "") < str(filters["desde"]):
                continue
            if filters.get("hasta") and str(rec.get("fecha") or "") > str(filters["hasta"]):
                continue
            route_customers = base.get("route_customer_costs", {}).get(str(rec.get("rid") or ""), [])
            if filters.get("cliente") and not any(str(c.get("cliente") or "") == str(filters["cliente"]) for c in route_customers):
                continue
            if filters.get("localidad") and not any(str(c.get("localidad") or "") == str(filters["localidad"]) for c in route_customers):
                continue
            routes.append(rec)
        routes.sort(key=lambda rec: str(rec.get("fecha") or ""), reverse=True)
        routes = routes[:max(1, min(int(limit), 2000))]
        rids = {str(rec.get("rid")) for rec in routes}
        customers = [item for rid, items in base.get("route_customer_costs", {}).items() if rid in rids for item in items]
        if filters.get("cliente"):
            customers = [item for item in customers if str(item.get("cliente") or "") == str(filters["cliente"])]
        if filters.get("localidad"):
            customers = [item for item in customers if str(item.get("localidad") or "") == str(filters["localidad"])]
        return {"routes": routes, "customers": customers, "options": options}

    def logistics_data_coverage():
        routes = list(load_all().values())
        clients = list(load_clientes().values())
        try:
            import storage_pedidos
            orders = storage_pedidos.fetch_all()
        except Exception:
            orders = []
        costs = load_logistics_dashboard({}, 2000)["routes"]
        volume_routes = {
            str(rec.get("Route ID") or rec.get("route_id") or "")
            for rec in load_attempts().values()
            if float(rec.get("bultos") or 0) > 0 or float(rec.get("hl") or 0) > 0 or float(rec.get("pallets") or 0) > 0
        }
        return {
            "routes_total": len(routes), "routes_calculated": len(costs),
            "routes_with_volume": len(volume_routes),
            "routes_with_vehicle": sum(str(r.get("camion") or "").lower() not in ("", "sin camion", "sin camión") for r in routes),
            "clients_total": len(clients),
            "clients_with_gps": sum(r.get("latitud") is not None and r.get("longitud") is not None for r in clients),
            "orders_total": len(orders),
            "orders_with_volume": sum(bool(float(r.get("bultos") or 0) or float(r.get("hl") or 0) or float(r.get("pallets") or 0)) for r in orders),
        }

    def load_route_cost(rid):
        path = os.path.join(DATA_DIR, "logistics_costs.json")
        try:
            base = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        except Exception:
            base = {}
        return {
            "route": base.get("route_costs", {}).get(str(rid)),
            "customers": base.get("route_customer_costs", {}).get(str(rid), []),
        }

    def list_records(table, q="", limit=100, offset=0):
        loaders = {
            "rutas": load_all,
            "attempts": load_attempts,
            "clientes": load_clientes,
            "rechazos": load_rechazos,
            "rechazos_detalle": load_rechazos_detalle,
            "articulos": load_articulos,
            "settings": load_settings,
        }
        if table not in loaders:
            raise ValueError("Tabla no permitida.")
        query = str(q or "").strip().lower()
        rows = sorted(loaders[table]().items(), key=lambda item: str(item[0]))
        if query:
            rows = [
                item for item in rows
                if query in json.dumps(item[1], ensure_ascii=False, default=str).lower()
            ]
        start = max(0, int(offset))
        end = start + max(1, min(int(limit), 500))
        return rows[start:end]

    def get_record(table, key):
        loaders = {
            "rutas": load_all,
            "attempts": load_attempts,
            "clientes": load_clientes,
            "rechazos": load_rechazos,
            "rechazos_detalle": load_rechazos_detalle,
            "articulos": load_articulos,
            "settings": load_settings,
        }
        if table not in loaders:
            raise ValueError("Tabla no permitida.")
        return loaders[table]().get(str(key))

    def count_data_tables():
        return {
            "rutas": len(load_all()),
            "attempts": len(load_attempts()),
            "clientes": len(load_clientes()),
            "rechazos": len(load_rechazos()),
            "rechazos_detalle": len(load_rechazos_detalle()),
            "articulos": len(load_articulos()),
            "settings": len(load_settings()),
        }

    def health_stats():
        cached = _cache_get("health:stats")
        if cached is not None:
            return cached
        base = load_all()
        clientes = load_clientes()
        rechazos = load_rechazos()
        rutas = list(base.values())

        def client_id(item):
            return str(item.get("cliente") or "") if isinstance(item, dict) else str(item or "")

        con_ventana = {
            client_id(item)
            for route in rutas
            for item in route.get("clientes_con_ventana", [])
            if client_id(item)
        }
        sin_ventana = {
            client_id(item)
            for route in rutas
            for item in route.get("clientes_sin_ventana", [])
            if client_id(item)
        }
        return _cache_set("health:stats", {
            "rutas": len(base),
            "validas": sum(bool(route.get("usable")) for route in rutas),
            "clientes": len(clientes),
            "clientes_con_ventana": sum(bool(client.get("ventanas")) for client in clientes.values()),
            "clientes_foxtrot_unicos": len(con_ventana | sin_ventana),
            "clientes_foxtrot_con_ventana": len(con_ventana),
            "clientes_foxtrot_sin_ventana": len(sin_ventana),
            "rechazos_dias": len(rechazos),
            "rechazos_detalle": len(load_rechazos_detalle()),
            "rechazos_total": sum(route.get("rechazos", 0) for route in rechazos.values()),
            "articulos": len(load_articulos()),
            "ontime_rutas": sum("pdv_total" in route for route in rutas),
            "ontime_pdv_total": sum(route.get("pdv_total", 0) for route in rutas),
            "ontime_pdv_evaluables": sum(route.get("pdv_ontime", 0) + route.get("pdv_fuera_ontime", 0) for route in rutas),
            "ontime_pdv_ok": sum(route.get("pdv_ontime", 0) for route in rutas),
            "ontime_pdv_fuera": sum(route.get("pdv_fuera_ontime", 0) for route in rutas),
            "ontime_pdv_sin_ventana": sum(route.get("pdv_sin_ventana", 0) for route in rutas),
        })

    def save_record(table, key, rec):
        loaders = {
            "rutas": (load_all, _dump),
            "attempts": (load_attempts, lambda base: _dump_attempts(base)),
            "clientes": (load_clientes, lambda base: replace_clientes(base)),
            "rechazos": (load_rechazos, lambda base: upsert_rechazos(base)),
            "rechazos_detalle": (load_rechazos_detalle, lambda base: upsert_rechazos_detalle(base)),
            "articulos": (load_articulos, lambda base: replace_articulos(base)),
            "settings": (load_settings, lambda base: _dump_settings(base)),
        }
        if table not in loaders:
            raise ValueError("Tabla no permitida.")
        load, dump = loaders[table]
        base = load()
        base[key] = rec
        dump(base)
        return rec

    def delete_record(table, key):
        loaders = {
            "rutas": (load_all, _dump),
            "attempts": (load_attempts, lambda base: _dump_attempts(base)),
            "clientes": (load_clientes, lambda base: replace_clientes(base)),
            "rechazos": (load_rechazos, lambda base: _dump_rechazos(base)),
            "rechazos_detalle": (load_rechazos_detalle, lambda base: _dump_rechazos_detalle(base)),
            "articulos": (load_articulos, lambda base: replace_articulos(base)),
            "settings": (load_settings, lambda base: _dump_settings(base)),
        }
        if table not in loaders:
            raise ValueError("Tabla no permitida.")
        load, dump = loaders[table]
        base = load()
        base.pop(key, None)
        dump(base)
        return True

    def _dump_rechazos(base):
        path = os.path.join(DATA_DIR, "rechazos_dashboard.json")
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"rechazos": base}, open(path, "w", encoding="utf-8"), ensure_ascii=False)

    def _dump_attempts(base):
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"attempts": base}, open(ATTEMPTS_JSON_PATH, "w", encoding="utf-8"), ensure_ascii=False)

    def _dump_rechazos_detalle(base):
        path = os.path.join(DATA_DIR, "rechazos_detalle_dashboard.json")
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"rechazos_detalle": base}, open(path, "w", encoding="utf-8"), ensure_ascii=False)

    def _dump_settings(base):
        path = os.path.join(DATA_DIR, "settings_dashboard.json")
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"settings": base}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
