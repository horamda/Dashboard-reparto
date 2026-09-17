"""Borradores y envíos persistentes; los tokens nunca se guardan aquí."""
import copy
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager

import storage

_LOCK = threading.RLock()
_SEND_LOCK = threading.Lock()


def _key(identifier):
    if not re.fullmatch(r"[a-z0-9_-]{1,80}", identifier):
        raise ValueError("Identificador de envío inválido.")
    return "fichaya_kpis:" + identifier


def load(identifier):
    key = _key(identifier)
    if storage.BACKEND == "postgres":
        with storage._conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT rec FROM settings_dashboard WHERE key = %s;", (key,))
            row = cur.fetchone()
            return row[0] if row else {}
    path = os.path.join(storage.DATA_DIR, "fichaya_kpis", identifier + ".json")
    with _LOCK:
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)


def update(identifier, transform):
    key = _key(identifier)
    if storage.BACKEND == "postgres":
        with storage._conn() as cn, cn.cursor() as cur:
            cur.execute("INSERT INTO settings_dashboard (key, rec) VALUES (%s, '{}'::jsonb) ON CONFLICT (key) DO NOTHING;", (key,))
            cur.execute("SELECT rec FROM settings_dashboard WHERE key = %s FOR UPDATE;", (key,))
            result = transform(copy.deepcopy(cur.fetchone()[0]))
            cur.execute("UPDATE settings_dashboard SET rec = %s WHERE key = %s;", (storage._extras.Json(result), key))
        storage.clear_cache("settings:")
        return result
    with _LOCK:
        result = transform(load(identifier))
        directory = os.path.join(storage.DATA_DIR, "fichaya_kpis")
        os.makedirs(directory, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False) as fh:
                temporary = fh.name
                json.dump(result, fh, ensure_ascii=False)
            os.replace(temporary, os.path.join(directory, identifier + ".json"))
        finally:
            if temporary and os.path.exists(temporary):
                os.remove(temporary)
        return result


def recent(limit=30):
    if storage.BACKEND == "postgres":
        with storage._conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT rec->>'id', rec->>'creado', rec->>'desde', rec->>'hasta', rec->>'estado' "
                        "FROM settings_dashboard WHERE key LIKE 'fichaya_kpis:run_%' ORDER BY rec->>'creado' DESC LIMIT %s;", (limit,))
            return [dict(zip(("id", "creado", "desde", "hasta", "estado"), row)) for row in cur.fetchall()]
    directory = os.path.join(storage.DATA_DIR, "fichaya_kpis")
    if not os.path.isdir(directory):
        return []
    items = []
    for filename in os.listdir(directory):
        if filename.startswith("run_") and filename.endswith(".json"):
            rec = load(filename[:-5])
            items.append({key: rec.get(key) for key in ("id", "creado", "desde", "hasta", "estado")})
    return sorted(items, key=lambda r: r["creado"], reverse=True)[:limit]


@contextmanager
def sender_lock():
    """Un solo envío activo para evitar correcciones concurrentes fuera de orden."""
    if storage.BACKEND == "postgres":
        with storage._conn() as cn, cn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(842193701);")
            if not cur.fetchone()[0]:
                raise ValueError("Hay otro envío en curso. Esperá a que termine.")
            yield
        return
    if not _SEND_LOCK.acquire(blocking=False):
        raise ValueError("Hay otro envío en curso. Esperá a que termine.")
    try:
        yield
    finally:
        _SEND_LOCK.release()
