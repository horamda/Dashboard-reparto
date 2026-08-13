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
import json
import re
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


def backend_name():
    return BACKEND


# ======================= POSTGRES =======================
if BACKEND == "postgres":
    import psycopg2
    import psycopg2.extras as _extras

    def _conn():
        # Railway entrega postgres://...  psycopg2 lo acepta tal cual.
        return psycopg2.connect(DATABASE_URL)

    def init():
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

    def load_all():
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT rid, rec FROM rutas_dashboard;")
            return {rid: rec for rid, rec in cur.fetchall()}

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
            )
            after = _count(cur)
        return after - before

    def upsert_all(recs):
        """Inserta rutas nuevas y actualiza las existentes. Devuelve cuántas nuevas se agregaron."""
        if not recs:
            return 0
        cols = _route_cols()
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "rid")
        rows = [_route_row(rid, rec) for rid, rec in recs.items()]
        with _conn() as cn, cn.cursor() as cur:
            before = _count(cur)
            _extras.execute_values(
                cur,
                f"INSERT INTO rutas_dashboard ({', '.join(cols)}) VALUES %s "
                f"ON CONFLICT (rid) DO UPDATE SET {updates};",
                rows,
            )
            after = _count(cur)
        return after - before

    def _count(cur):
        cur.execute("SELECT COUNT(*) FROM rutas_dashboard;")
        return cur.fetchone()[0]

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
                f"ON CONFLICT (attempt_key) DO UPDATE SET {updates};",
                rows,
            )
        return len(recs)

    def load_attempts():
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT attempt_key, rec FROM attempts_dashboard;")
            return {key: rec for key, rec in cur.fetchall()}

    def reset():
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("TRUNCATE rutas_dashboard;")
            cur.execute("TRUNCATE attempts_dashboard;")

    def load_clientes():
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT cliente, rec FROM clientes_dashboard;")
            return {cliente: rec for cliente, rec in cur.fetchall()}

    def replace_clientes(recs):
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("TRUNCATE clientes_dashboard;")
            if recs:
                rows = [(cliente, _extras.Json(rec)) for cliente, rec in recs.items()]
                _extras.execute_values(
                    cur,
                    "INSERT INTO clientes_dashboard (cliente, rec) VALUES %s;",
                    rows,
                )
        return len(recs)

    def load_rechazos():
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT key, rec FROM rechazos_dashboard;")
            return {key: rec for key, rec in cur.fetchall()}

    def load_rechazos_detalle():
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT key, rec FROM rechazos_detalle_dashboard;")
            return {key: rec for key, rec in cur.fetchall()}

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
            )
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
            )
        return len(recs)

    def load_articulos():
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT articulo, rec FROM articulos_dashboard;")
            return {articulo: rec for articulo, rec in cur.fetchall()}

    def replace_articulos(recs):
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("TRUNCATE articulos_dashboard;")
            if recs:
                rows = [(articulo, _extras.Json(rec)) for articulo, rec in recs.items()]
                _extras.execute_values(
                    cur,
                    "INSERT INTO articulos_dashboard (articulo, rec) VALUES %s;",
                    rows,
                )
        return len(recs)

    def load_settings():
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT key, rec FROM settings_dashboard;")
            return {key: rec for key, rec in cur.fetchall()}

    def save_setting(key, rec):
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                "INSERT INTO settings_dashboard (key, rec) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET rec = EXCLUDED.rec;",
                (key, _extras.Json(rec)),
            )
        return rec

    def save_record(table, key, rec):
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
            cur.execute(
                f"INSERT INTO {table_name} ({key_col}, rec) VALUES (%s, %s) "
                "ON CONFLICT ({}) DO UPDATE SET rec = EXCLUDED.rec;".format(key_col),
                (key, _extras.Json(rec)),
            )
        return rec

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

    def upsert_all(recs):
        base = load_all()
        added = 0
        for rid, rec in recs.items():
            if rid not in base:
                added += 1
            base[rid] = rec
        _dump(base)
        return added

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

    def load_clientes():
        if os.path.exists(CLIENTES_JSON_PATH):
            try:
                return json.load(open(CLIENTES_JSON_PATH, encoding="utf-8"))["clientes"]
            except Exception:
                return {}
        return {}

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

    def save_setting(key, rec):
        path = os.path.join(DATA_DIR, "settings_dashboard.json")
        base = load_settings()
        base[key] = rec
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"settings": base}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        return rec

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
