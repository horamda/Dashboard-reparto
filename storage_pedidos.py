# -*- coding: utf-8 -*-
"""Persistencia para el modulo de ingreso de pedidos."""

import json
import os

import storage

UPSERT_MODE = "nothing"


def _json_path():
    return os.path.join(storage.DATA_DIR, "pedidos_dashboard.json")


def init_db():
    if storage.backend_name() != "postgres":
        os.makedirs(storage.DATA_DIR, exist_ok=True)
        if not os.path.exists(_json_path()):
            with open(_json_path(), "w", encoding="utf-8") as fh:
                json.dump({"pedidos": {}}, fh, ensure_ascii=False)
        return
    with storage._conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pedidos_dashboard (
                nro_pedido BIGINT PRIMARY KEY,
                rec JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)


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
        storage._extras.execute_values(cur, sql, values, page_size=500)
        return cur.rowcount


def fetch_all():
    init_db()
    if storage.backend_name() != "postgres":
        return sorted(_load_json().values(), key=lambda r: r.get("fecha_alta") or "")
    with storage._conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT rec FROM pedidos_dashboard ORDER BY rec->>'fecha_alta';")
        return [row[0] for row in cur.fetchall()]


def stats():
    init_db()
    if storage.backend_name() != "postgres":
        rows = list(_load_json().values())
        fechas = sorted(r.get("fecha") for r in rows if r.get("fecha"))
        return {"total": len(rows), "desde": fechas[0] if fechas else None, "hasta": fechas[-1] if fechas else None}
    with storage._conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*), MIN(rec->>'fecha'), MAX(rec->>'fecha')
            FROM pedidos_dashboard;
        """)
        n, dmin, dmax = cur.fetchone()
        return {"total": n or 0, "desde": dmin, "hasta": dmax}
