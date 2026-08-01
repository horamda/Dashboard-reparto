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

BACKEND = "postgres" if DATABASE_URL else "json"


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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS clientes_dashboard (
                    cliente TEXT PRIMARY KEY,
                    rec JSONB NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rechazos_dashboard (
                    fecha TEXT PRIMARY KEY,
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

    def add_new(recs):
        """Inserta solo las rutas nuevas. Devuelve cuántas se agregaron."""
        if not recs:
            return 0
        rows = [(rid, _extras.Json(rec)) for rid, rec in recs.items()]
        with _conn() as cn, cn.cursor() as cur:
            before = _count(cur)
            _extras.execute_values(
                cur,
                "INSERT INTO rutas_dashboard (rid, rec) VALUES %s "
                "ON CONFLICT (rid) DO NOTHING;",
                rows,
            )
            after = _count(cur)
        return after - before

    def upsert_all(recs):
        """Inserta rutas nuevas y actualiza las existentes. Devuelve cuántas nuevas se agregaron."""
        if not recs:
            return 0
        rows = [(rid, _extras.Json(rec)) for rid, rec in recs.items()]
        with _conn() as cn, cn.cursor() as cur:
            before = _count(cur)
            _extras.execute_values(
                cur,
                "INSERT INTO rutas_dashboard (rid, rec) VALUES %s "
                "ON CONFLICT (rid) DO UPDATE SET rec = EXCLUDED.rec;",
                rows,
            )
            after = _count(cur)
        return after - before

    def _count(cur):
        cur.execute("SELECT COUNT(*) FROM rutas_dashboard;")
        return cur.fetchone()[0]

    def reset():
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("TRUNCATE rutas_dashboard;")

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
            cur.execute("SELECT fecha, rec FROM rechazos_dashboard;")
            return {fecha: rec for fecha, rec in cur.fetchall()}

    def upsert_rechazos(recs):
        if not recs:
            return 0
        rows = [(fecha, _extras.Json(rec)) for fecha, rec in recs.items()]
        with _conn() as cn, cn.cursor() as cur:
            _extras.execute_values(
                cur,
                "INSERT INTO rechazos_dashboard (fecha, rec) VALUES %s "
                "ON CONFLICT (fecha) DO UPDATE SET rec = EXCLUDED.rec;",
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

    def upsert_rechazos(recs):
        path = os.path.join(DATA_DIR, "rechazos_dashboard.json")
        base = load_rechazos()
        base.update(recs)
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({"rechazos": base}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
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
