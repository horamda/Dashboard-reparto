# -*- coding: utf-8 -*-
"""Persistencia para el modulo de ingreso de pedidos."""

import copy
import json
import os
import threading
import time

import storage

UPSERT_MODE = "nothing"
_INIT_DONE = False
_CACHE = {}
_CACHE_TTL = float(os.environ.get("PEDIDOS_CACHE_TTL_SECONDS", "300"))
_CACHE_LOCK = threading.RLock()


def clear_cache():
    with _CACHE_LOCK:
        _CACHE.clear()


def _cache_get(key):
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at < time.time():
            _CACHE.pop(key, None)
            return None
        return copy.deepcopy(value)


def _cache_set(key, value):
    cached_value = copy.deepcopy(value)
    with _CACHE_LOCK:
        _CACHE[key] = (time.time() + _CACHE_TTL, cached_value)
    return copy.deepcopy(cached_value)


def _json_path():
    return os.path.join(storage.DATA_DIR, "pedidos_dashboard.json")


def init_db():
    global _INIT_DONE
    if _INIT_DONE:
        return
    if storage.backend_name() != "postgres":
        os.makedirs(storage.DATA_DIR, exist_ok=True)
        if not os.path.exists(_json_path()):
            with open(_json_path(), "w", encoding="utf-8") as fh:
                json.dump({"pedidos": {}}, fh, ensure_ascii=False)
        _INIT_DONE = True
        return
    with storage._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass('pedidos_dashboard') IS NOT NULL, "
            "to_regclass('idx_pedidos_cliente_norm') IS NOT NULL, "
            "to_regclass('idx_pedidos_fecha_entrega') IS NOT NULL, "
            "to_regclass('idx_pedidos_fecha_alta') IS NOT NULL;"
        )
        if all(cur.fetchone()):
            _INIT_DONE = True
            return
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pedidos_dashboard (
                nro_pedido BIGINT PRIMARY KEY,
                rec JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_pedidos_cliente_norm ON pedidos_dashboard "
            "((regexp_replace(split_part(rec->>'cliente', ' - ', 1), '^0+', '')));"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_pedidos_fecha_entrega ON pedidos_dashboard "
            "((COALESCE(NULLIF(rec->>'fecha_entrega', ''), rec->>'fecha')));"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_pedidos_fecha_alta ON pedidos_dashboard "
            "((rec->>'fecha_alta'));"
        )
    _INIT_DONE = True


def _load_json():
    init_db()
    try:
        with open(_json_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("pedidos", {})
    except Exception:
        return {}


def _dump_json(base):
    os.makedirs(storage.DATA_DIR, exist_ok=True)
    with open(_json_path(), "w", encoding="utf-8") as fh:
        json.dump({"pedidos": base}, fh, ensure_ascii=False)


def upsert_records(records):
    """Inserta o actualiza pedidos. Devuelve cantidad afectada."""
    if not records:
        return 0
    init_db()
    if storage.backend_name() != "postgres":
        base = _load_json()
        affected = 0
        for rec in records:
            key = str(rec["nro_pedido"])
            if UPSERT_MODE == "nothing" and key in base:
                continue
            base[key] = rec
            affected += 1
        _dump_json(base)
        clear_cache()
        storage.clear_cache("logistics:")
        return affected

    if UPSERT_MODE == "nothing":
        conflict = "ON CONFLICT (nro_pedido) DO NOTHING"
    else:
        conflict = "ON CONFLICT (nro_pedido) DO UPDATE SET rec = EXCLUDED.rec, updated_at = now()"
    sql = f"""
        INSERT INTO pedidos_dashboard (nro_pedido, rec)
        VALUES %s
        {conflict};
    """
    values = [(r["nro_pedido"], storage._extras.Json(r)) for r in records]
    with storage._conn() as conn, conn.cursor() as cur:
        storage._extras.execute_values(cur, sql, values, page_size=storage.DB_INSERT_PAGE_SIZE)
        affected = cur.rowcount
    clear_cache()
    storage.clear_cache("logistics:")
    return affected


def fetch_all():
    init_db()
    cached = _cache_get("all")
    if cached is not None:
        return cached
    if storage.backend_name() != "postgres":
        return _cache_set("all", sorted(_load_json().values(), key=lambda r: r.get("fecha_alta") or ""))
    with storage._conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT rec FROM pedidos_dashboard ORDER BY rec->>'fecha_alta';")
        return _cache_set("all", [row[0] for row in cur.fetchall()])


def fetch_dashboard():
    init_db()
    cached = _cache_get("dashboard")
    if cached is not None:
        return cached
    if storage.backend_name() != "postgres":
        rows = [
            {key: value for key, value in rec.items() if key != "raw_pedido"}
            for rec in _load_json().values()
        ]
        rows.sort(key=lambda rec: rec.get("fecha_alta") or "")
        return _cache_set("dashboard", rows)
    with storage._conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT rec - 'raw_pedido' FROM pedidos_dashboard ORDER BY rec->>'fecha_alta';")
        return _cache_set("dashboard", [row[0] for row in cur.fetchall()])


def stats():
    init_db()
    cached = _cache_get("stats")
    if cached is not None:
        return cached
    if storage.backend_name() != "postgres":
        rows = list(_load_json().values())
        fechas = sorted(r.get("fecha") for r in rows if r.get("fecha"))
        return _cache_set("stats", {"total": len(rows), "desde": fechas[0] if fechas else None, "hasta": fechas[-1] if fechas else None})
    with storage._conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*), MIN(rec->>'fecha'), MAX(rec->>'fecha')
            FROM pedidos_dashboard;
        """)
        n, dmin, dmax = cur.fetchone()
        return _cache_set("stats", {"total": n or 0, "desde": dmin, "hasta": dmax})
