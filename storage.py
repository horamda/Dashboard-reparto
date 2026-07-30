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

    def _count(cur):
        cur.execute("SELECT COUNT(*) FROM rutas_dashboard;")
        return cur.fetchone()[0]

    def reset():
        with _conn() as cn, cn.cursor() as cur:
            cur.execute("TRUNCATE rutas_dashboard;")


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

    def reset():
        if os.path.exists(JSON_PATH):
            os.remove(JSON_PATH)
