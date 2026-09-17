# -*- coding: utf-8 -*-
"""
Lógica de datos del dashboard de Tiempos de reparto - Foxtrot.
La usa app.py (Flask). La persistencia (Postgres o JSON) está en storage.py.

Base incremental: cada ruta se identifica por Route ID. Desde agosto 2026 TI/TML
pueden calcularse con fichadas FichaYA; si faltan marcas, se usa estimación
determinística por ID para mantener consistencia histórica.
"""

import os, json, hashlib, re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from datetime import date, datetime, timedelta
from io import StringIO
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
import numpy as np
import pandas as pd
from openpyxl import load_workbook

import storage

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ---------------- Parámetros ajustables ----------------
TI_CENTRO  = 35
TML_CENTRO = 27.5
TI_SD, TML_SD = 7, 6
OBJ = {"tml": 30, "ti": 30, "ruta": 7, "ruta_max": 8, "alerta_h": 12, "adh": 85, "disp": 10, "disp_error": 80}
DQI_COMPARISON_YEARS = ("2024", "2025", "2026")
CASA_CENTRAL_TML_REFERENCIA_2026 = {
    "2026-01": 32,
    "2026-02": 28,
    "2026-03": 21,
    "2026-04": 28,
    "2026-05": 27,
    "2026-06": 27,
    "2026-07": 28,
}
CASA_CENTRAL_TI_REFERENCIA_2026 = {
    "2026-01": 42,
    "2026-02": 30,
    "2026-03": 30,
    "2026-04": 30,
    "2026-05": 33,
    "2026-06": 34,
    "2026-07": 30,
}

AQUI = os.path.dirname(os.path.abspath(__file__))
PLANTILLA = os.path.join(AQUI, "plantilla_dashboard.html")
RECHAZOS_API_URL = os.environ.get(
    "RECHAZOS_API_URL",
    "https://web-production-f968ec.up.railway.app/api/rechazos/diario/integracion",
)
RECHAZOS_SUCURSAL = os.environ.get("RECHAZOS_SUCURSAL", "Dolores")
RECHAZOS_SUCURSAL_ID = os.environ.get("RECHAZOS_SUCURSAL_ID", "2")
RECHAZOS_SUCURSALES_IMPORT = os.environ.get("RECHAZOS_API_SUCURSAL", "TODAS")
LOGISTICS_INTEGRATION_API_BASE_URL = os.environ.get(
    "LOGISTICS_INTEGRATION_API_BASE_URL",
    RECHAZOS_API_URL.split("/api/", 1)[0],
).rstrip("/")
FICHAYA_API_BASE_URL = os.environ.get("FICHAYA_API_BASE_URL", "https://control-asistencia.up.railway.app").rstrip("/")
FICHAYA_API_USERNAME = os.environ.get("FICHAYA_API_USERNAME") or os.environ.get("EXTERNAL_API_USERNAME")
FICHAYA_API_PASSWORD = os.environ.get("FICHAYA_API_PASSWORD") or os.environ.get("EXTERNAL_API_PASSWORD")
FICHAYA_WEB_USERNAME = os.environ.get("FICHAYA_WEB_USERNAME") or FICHAYA_API_USERNAME
FICHAYA_WEB_PASSWORD = os.environ.get("FICHAYA_WEB_PASSWORD") or FICHAYA_API_PASSWORD
FICHAYA_TML_TI_DESDE = os.environ.get("FICHAYA_TML_TI_DESDE", "2026-08-01")
FICHAYA_MANUAL_FIELDS = (
    "fichada_ingreso",
    "inicio_foxtrot",
    "finalizacion_foxtrot",
    "fichada_salida",
)
SATISFACCION_CSV_URL = os.environ.get(
    "SATISFACCION_CSV_URL",
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vQkEVyl9kmmMf5vsi--tz5mf39u80tJoFcBzWFFLWhHuXepY5dBEqmSzXLbD0AXapFPj9DLMBqii7TA/pub?gid=0&single=true&output=csv",
)
NPS_CSV_URL = os.environ.get(
    "NPS_CSV_URL",
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vQkEVyl9kmmMf5vsi--tz5mf39u80tJoFcBzWFFLWhHuXepY5dBEqmSzXLbD0AXapFPj9DLMBqii7TA/pub?gid=1806046627&single=true&output=csv",
)
DQI_CSV_URL = os.environ.get(
    "DQI_CSV_URL",
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vSC5R8XlW4kETbkIDmX95n_XEVJE4JMf-NNp7wYi6mE5OAfj-EENAC9jK0-IlkN1A/pub?gid=1861746295&single=true&output=csv",
)
DPO_GKPI_URLS = [
    ("Casa Central", "Mar de Ajo", "1", os.environ.get("DPO_GKPI_MDA_URL", "https://docs.google.com/spreadsheets/d/e/2PACX-1vTRrt57z-QDSRmDblvUV6AHs_Q1og0qgW0Ec-fp1L0QjLr8R_346nhHEkKsndqka-wQUdKSc2-3PizX/pub?gid=1241670089&single=true&output=csv")),
    ("Sucursal Dolores", "Dolores", "2", os.environ.get("DPO_GKPI_DOL_URL", "https://docs.google.com/spreadsheets/d/e/2PACX-1vTRrt57z-QDSRmDblvUV6AHs_Q1og0qgW0Ec-fp1L0QjLr8R_346nhHEkKsndqka-wQUdKSc2-3PizX/pub?gid=249846040&single=true&output=csv")),
    ("Casa Central", "Mar de Ajo", "1", os.environ.get("DPO_GKPI_MDA_EXTRA_URL", "https://docs.google.com/spreadsheets/d/e/2PACX-1vTRrt57z-QDSRmDblvUV6AHs_Q1og0qgW0Ec-fp1L0QjLr8R_346nhHEkKsndqka-wQUdKSc2-3PizX/pub?gid=1883083406&single=true&output=csv")),
    ("Sucursal Dolores", "Dolores", "2", os.environ.get("DPO_GKPI_DOL_EXTRA_URL", "https://docs.google.com/spreadsheets/d/e/2PACX-1vTRrt57z-QDSRmDblvUV6AHs_Q1og0qgW0Ec-fp1L0QjLr8R_346nhHEkKsndqka-wQUdKSc2-3PizX/pub?gid=680588527&single=true&output=csv")),
]

STORAGE_INIT_ERROR = None

EXTERNAL_CACHE_TTL_SECONDS = float(os.environ.get("EXTERNAL_DATA_CACHE_TTL_SECONDS", "300"))
DASHBOARD_CACHE_TTL_SECONDS = float(os.environ.get("DASHBOARD_CACHE_TTL_SECONDS", "30"))
EXTERNAL_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("EXTERNAL_REQUEST_TIMEOUT_SECONDS", "8"))


def _ttl_cached(ttl_seconds):
    def decorator(func):
        state = {
            "expires_at": 0.0,
            "value": None,
            "loaded": False,
            "refreshing": False,
            "generation": 0,
        }
        lock = threading.RLock()

        def refresh(generation, args, kwargs):
            try:
                value = func(*args, **kwargs)
            except Exception:
                with lock:
                    if state["generation"] == generation:
                        state["refreshing"] = False
                        state["expires_at"] = time.monotonic() + min(30.0, ttl_seconds)
                return
            with lock:
                if state["generation"] != generation:
                    return
                state.update({
                    "expires_at": time.monotonic() + ttl_seconds,
                    "value": value,
                    "loaded": True,
                    "refreshing": False,
                })

        @wraps(func)
        def wrapper(*args, **kwargs):
            now = time.monotonic()
            with lock:
                if state["loaded"] and now < state["expires_at"]:
                    return state["value"]
                if state["loaded"]:
                    if not state["refreshing"]:
                        state["refreshing"] = True
                        generation = state["generation"]
                        threading.Thread(
                            target=refresh,
                            args=(generation, args, kwargs),
                            name=f"cache-refresh-{func.__name__}",
                            daemon=True,
                        ).start()
                    return state["value"]
                value = func(*args, **kwargs)
                state.update({
                    "expires_at": time.monotonic() + ttl_seconds,
                    "value": value,
                    "loaded": True,
                    "refreshing": False,
                })
                return value

        def cache_clear():
            with lock:
                state.update({
                    "expires_at": 0.0,
                    "value": None,
                    "loaded": False,
                    "refreshing": False,
                    "generation": state["generation"] + 1,
                })

        wrapper.cache_clear = cache_clear
        return wrapper
    return decorator
try:
    if storage.backend_name() == "json" or os.environ.get("RUN_DB_MIGRATIONS_ON_START") == "1":
        storage.init()
except Exception as exc:
    STORAGE_INIT_ERROR = exc


def _today():
    return date.today().strftime("%Y-%m-%d")


def rng_de_ruta(rid):
    seed = int(hashlib.md5(str(rid).encode()).hexdigest()[:8], 16)
    return np.random.default_rng(seed)


def _clamp_normal(rng, center, sd, lo, hi):
    return int(round(min(max(rng.normal(center, sd), lo), hi)))


def _dispersion(pl, real):
    if pd.notna(pl) and pd.notna(real) and pl > 0:
        return round((pl - real) / pl * 100, 1)
    return None


def _num_or_none(v):
    if pd.notna(v):
        return float(v)
    return None


def _descartar_dispersion_anomala(rec):
    """Excluye dispersiones extremas causadas por una planificacion Foxtrot invalida."""
    dk, dh = rec.get("dispkm"), rec.get("disphs")
    vals = [v for v in (dk, dh) if v is not None]
    if vals and any(abs(v) > OBJ["disp_error"] for v in vals):
        rec["disp_descartada"] = True
        rec["disp_motivo"] = "Error de planificacion Foxtrot"
        rec["dispkm_original"] = dk
        rec["disphs_original"] = dh
        rec["dispkm"] = None
        rec["disphs"] = None
    return rec


def _leer_excel(f, filename=""):
    name = (filename or getattr(f, "name", "") or str(f)).lower()
    eng = "xlrd" if name.endswith(".xls") else None
    return pd.read_excel(f, engine=eng)


def _leer_excel_visitas(f, filename=""):
    name = (filename or getattr(f, "name", "") or str(f)).lower()
    eng = "xlrd" if name.endswith(".xls") else None
    sheets = pd.read_excel(f, sheet_name=None, engine=eng)
    required = {"Route ID", "Customer ID", "Visit Start Timestamp"}
    for df in sheets.values():
        if required.issubset(set(df.columns)):
            return df
    for sheet_name, df in sheets.items():
        if "visita" in str(sheet_name).lower() or "visit" in str(sheet_name).lower():
            return df
    return next(iter(sheets.values()))


def _norm_id(v):
    if pd.isna(v):
        return ""
    s = str(v).strip()
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    m = re.fullmatch(r"0*(\d+)", s)
    if m:
        return m.group(1)
    return s


def fichaya_legajo(v):
    """Preserva los ceros del identificador oficial; no normaliza como número."""
    if v is None or pd.isna(v):
        return ""
    text = str(v).strip()
    return text[:-2] if re.fullmatch(r"\d+\.0", text) else text


def _norm_persona_key(v):
    s = unicodedata.normalize("NFKD", str(v or "").strip().upper())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s)


def _parse_fecha_fichaya(v):
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    return None


def _parse_hora_fichaya(v):
    s = str(v or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(s[:8], fmt).time()
        except ValueError:
            pass
    return None


def _first_env(*names):
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _fichaya_credentials():
    common_username = _first_env("FICHAYA_USERNAME", "FICHAYA_USER")
    common_password = _first_env("FICHAYA_PASSWORD")
    api_username = _first_env(
        "FICHAYA_API_USERNAME", "FICHAYA_API_USER", "EXTERNAL_API_USERNAME"
    ) or FICHAYA_API_USERNAME
    api_password = _first_env(
        "FICHAYA_API_PASSWORD", "EXTERNAL_API_PASSWORD"
    ) or FICHAYA_API_PASSWORD
    web_username = _first_env(
        "FICHAYA_WEB_USERNAME", "FICHAYA_WEB_USER"
    ) or FICHAYA_WEB_USERNAME
    web_password = _first_env("FICHAYA_WEB_PASSWORD") or FICHAYA_WEB_PASSWORD

    # Ambos accesos usan la misma cuenta en la instalacion actual. Los fallbacks
    # permiten configurar un solo par sin depender del nombre historico usado.
    api_username = api_username or web_username or common_username
    api_password = api_password or web_password or common_password
    web_username = web_username or api_username or common_username
    web_password = web_password or api_password or common_password
    return {
        "base_url": (_first_env("FICHAYA_API_BASE_URL") or FICHAYA_API_BASE_URL).rstrip("/"),
        "api_username": api_username,
        "api_password": api_password,
        "web_username": web_username,
        "web_password": web_password,
    }


def _fichaya_integration_mode():
    mode = (_first_env("FICHAYA_INTEGRATION_MODE", "FICHAYA_MODE") or "web").lower()
    return mode if mode in {"web", "api", "auto"} else "web"


def fichaya_credentials_status():
    credentials = _fichaya_credentials()
    return {
        "base_url": credentials["base_url"],
        "mode": _fichaya_integration_mode(),
        "api_configured": bool(credentials["api_username"] and credentials["api_password"]),
        "web_configured": bool(credentials["web_username"] and credentials["web_password"]),
    }


def _fichaya_token():
    credentials = _fichaya_credentials()
    if not credentials["api_username"] or not credentials["api_password"]:
        return None
    url = credentials["base_url"] + "/api/v1/external/auth/token"
    body = json.dumps({
        "username": credentials["api_username"],
        "password": credentials["api_password"],
    }).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    with urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8")).get("access_token")


def _csrf_from_html(html):
    m = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', html or "")
    return m.group(1) if m else ""


def _fichaya_web_csv(desde, hasta):
    credentials = _fichaya_credentials()
    if not credentials["web_username"] or not credentials["web_password"]:
        raise RuntimeError("faltan FICHAYA_WEB_USERNAME/FICHAYA_WEB_PASSWORD")
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    login_url = credentials["base_url"] + "/login"
    with opener.open(Request(login_url, headers={"Accept": "text/html"}), timeout=30) as res:
        login_html = res.read().decode("utf-8", errors="replace")
    csrf = _csrf_from_html(login_html)
    if not csrf:
        raise RuntimeError("no se pudo obtener CSRF del login web FichaYA")
    body = urlencode({
        "username": credentials["web_username"],
        "password": credentials["web_password"],
        "csrf_token": csrf,
    }).encode("utf-8")
    req = Request(
        login_url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html",
            "Origin": credentials["base_url"],
            "Referer": login_url,
        },
        method="POST",
    )
    with opener.open(req, timeout=30) as res:
        final_url = getattr(res, "url", "")
        body_preview = res.read(500).decode("utf-8", errors="replace")
    if "/login" in final_url:
        raise RuntimeError("login web FichaYA invalido")
    params = urlencode({
        "fecha_desde": desde,
        "fecha_hasta": hasta,
        "tipo_marca": "jornada",
    })
    req = Request(credentials["base_url"] + "/asistencias/marcas.csv?" + params, headers={"Accept": "text/csv"})
    with opener.open(req, timeout=60) as res:
        ctype = res.headers.get("Content-Type", "")
        raw = res.read().decode("utf-8-sig", errors="replace")
    if "text/csv" not in ctype:
        raise RuntimeError("FichaYA web no devolvio CSV de marcas")
    return raw


def _fichaya_external_csv(desde, hasta):
    credentials = _fichaya_credentials()
    token = _fichaya_token()
    if not token:
        raise RuntimeError("faltan credenciales de API externa FichaYA")
    params = urlencode({
        "fecha_desde": desde,
        "fecha_hasta": hasta,
        "tipo_marca": "jornada",
        "estado": "all",
        "limit": 20000,
    })
    url = credentials["base_url"] + "/api/v1/external/reportes/asistencia.csv?" + params
    req = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "text/csv"})
    with urlopen(req, timeout=60) as res:
        return res.read().decode("utf-8-sig", errors="replace")


def _indexar_fichadas_csv(raw):
    df = pd.read_csv(StringIO(raw))
    idx = {}
    for _, row in df.fillna("").iterrows():
        fecha = _parse_fecha_fichaya(row.get("FECHA") or row.get("fecha"))
        hora = _parse_hora_fichaya(row.get("HORA") or row.get("hora"))
        nombre = _norm_persona_key(row.get("NOMBRE") or row.get("empleado"))
        if not fecha or not hora or not nombre:
            continue
        mov = _norm_persona_key(row.get("TIPO MOV") or row.get("accion"))
        key = (fecha.strftime("%Y-%m-%d"), nombre)
        item = idx.setdefault(key, {"ingresos": [], "egresos": []})
        codigo = fichaya_legajo(row.get("CODIGO") or row.get("legajo"))
        if codigo:
            item["legajo"] = codigo
            idx[(fecha.strftime("%Y-%m-%d"), "LEGAJO:" + codigo)] = item
        if mov in ("ENTRADA", "INGRESO"):
            item["ingresos"].append(hora)
        elif mov in ("SALIDA", "EGRESO"):
            item["egresos"].append(hora)
    for item in idx.values():
        item["ingreso"] = min(item["ingresos"]) if item["ingresos"] else None
        item["egreso"] = max(item["egresos"]) if item["egresos"] else None
    return idx


def _fichada_item_to_cache(fecha, nombre, item):
    return {
        "fecha": fecha,
        "nombre": nombre,
        "legajo": item.get("legajo") or "",
        "ingreso": item["ingreso"].strftime("%H:%M:%S") if item.get("ingreso") else "",
        "egreso": item["egreso"].strftime("%H:%M:%S") if item.get("egreso") else "",
        "ingresos": [h.strftime("%H:%M:%S") for h in item.get("ingresos", [])],
        "egresos": [h.strftime("%H:%M:%S") for h in item.get("egresos", [])],
    }


def _cache_record_to_item(rec):
    item = {
        "legajo": fichaya_legajo(rec.get("legajo")),
        "ingresos": [_parse_hora_fichaya(h) for h in rec.get("ingresos", []) if _parse_hora_fichaya(h)],
        "egresos": [_parse_hora_fichaya(h) for h in rec.get("egresos", []) if _parse_hora_fichaya(h)],
    }
    item["ingreso"] = _parse_hora_fichaya(rec.get("ingreso")) or (min(item["ingresos"]) if item["ingresos"] else None)
    item["egreso"] = _parse_hora_fichaya(rec.get("egreso")) or (max(item["egresos"]) if item["egresos"] else None)
    return item


def _guardar_fichadas_cache(fichadas):
    records = {}
    for key, item in fichadas.items():
        fecha, nombre = key
        if nombre.startswith("LEGAJO:"):
            continue
        records[f"{fecha}|{nombre}"] = _fichada_item_to_cache(fecha, nombre, item)
    storage.save_fichaya_marks(records, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return records


def cargar_fichadas_cache(desde, hasta):
    records = storage.load_fichaya_marks(desde, hasta)
    idx = {}
    for rec in records:
        fecha = str(rec.get("fecha") or "")
        nombre = _norm_persona_key(rec.get("nombre"))
        if not fecha or not nombre or fecha < desde or fecha > hasta:
            continue
        item = _cache_record_to_item(rec)
        idx[(fecha, nombre)] = item
        if item.get("legajo"):
            idx[(fecha, "LEGAJO:" + item["legajo"])] = item
    return idx


def fichaya_cache_info():
    return storage.load_fichaya_cache_info()


def cargar_fichadas(desde, hasta, force_live=False):
    cached = {}
    if not force_live:
        cached = cargar_fichadas_cache(desde, hasta)
        if cached:
            return cached
    status = fichaya_credentials_status()
    mode = status["mode"]
    sources = []
    if mode in {"web", "auto"}:
        sources.append((
            "acceso web",
            status["web_configured"],
            _fichaya_web_csv,
            "faltan FICHAYA_WEB_USERNAME/FICHAYA_WEB_PASSWORD en las variables del servicio",
        ))
    if mode in {"api", "auto"}:
        sources.append((
            "API externa",
            status["api_configured"],
            _fichaya_external_csv,
            "faltan FICHAYA_API_USERNAME/FICHAYA_API_PASSWORD en las variables del servicio",
        ))

    errors = []
    for label, configured, loader, missing_message in sources:
        if not configured:
            errors.append(f"{label}: {missing_message}")
            continue
        try:
            fichadas = _indexar_fichadas_csv(loader(desde, hasta))
            _guardar_fichadas_cache(fichadas)
            return fichadas
        except Exception as exc:
            errors.append(f"{label}: {exc}")

    # Un refresco solicitado por el usuario debe informar el fallo real. La capa
    # de reporte decide si muestra el cache anterior como respaldo identificado.
    if force_live:
        raise RuntimeError("; ".join(errors) or "no hay un método FichaYA habilitado")

    cached = cargar_fichadas_cache(desde, hasta)
    if cached:
        return cached
    raise RuntimeError("; ".join(errors) or "no hay un método FichaYA habilitado")


def _logistics_integration_config():
    base_url = (
        _first_env("LOGISTICS_INTEGRATION_API_BASE_URL", "LOGISTICS_API_BASE_URL")
        or LOGISTICS_INTEGRATION_API_BASE_URL
    ).rstrip("/")
    return {
        "base_url": base_url,
        "endpoint": base_url + "/api/v1/integracion/logistica/diaria",
        "api_key": _first_env("LOGISTICS_INTEGRATION_API_KEY", "INTEGRATION_API_KEY"),
        "empresa_id": _first_env("LOGISTICS_INTEGRATION_EMPRESA_ID") or "1",
        "timeout": float(_first_env("LOGISTICS_INTEGRATION_TIMEOUT_SECONDS") or "30"),
    }


def logistics_integration_status():
    config = _logistics_integration_config()
    return {
        "base_url": config["base_url"],
        "endpoint": config["endpoint"],
        "configured": bool(config["api_key"]),
        "empresa_id": config["empresa_id"],
    }


def _logistics_api_error(exc):
    raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {}
    message = str(payload.get("error") or raw or exc.reason or exc)
    if payload.get("codigo") == "integration_not_configured":
        message += " Configurá INTEGRATION_API_KEY en el servicio productor de Railway."
    return f"API logística HTTP {getattr(exc, 'code', '')}: {message}".strip()


def _consultar_logistica_api_chunk(desde, hasta, sucursal="TODAS"):
    config = _logistics_integration_config()
    if not config["api_key"]:
        raise RuntimeError(
            "Falta LOGISTICS_INTEGRATION_API_KEY en las variables del servicio consumidor."
        )
    rows, offset, pages = [], 0, 0
    while True:
        params = urlencode({
            "desde": desde.isoformat(),
            "hasta": hasta.isoformat(),
            "empresa_id": config["empresa_id"],
            "sucursal": sucursal or "TODAS",
            "incluir_clientes": 0,
            "limit": 1000,
            "offset": offset,
        })
        req = Request(
            config["endpoint"] + "?" + params,
            headers={"X-API-Key": config["api_key"], "Accept": "application/json"},
        )
        try:
            with urlopen(req, timeout=config["timeout"]) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(_logistics_api_error(exc)) from exc
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"No se pudo consultar la API logística: {exc}") from exc
        if payload.get("api_version") != "v1" or not isinstance(payload.get("datos"), list):
            raise RuntimeError("La API logística devolvió una respuesta incompatible con el contrato v1.")
        page_rows = payload["datos"]
        rows.extend(page_rows)
        pages += 1
        pagination = payload.get("paginacion") or {}
        total = int(pagination.get("total") or len(rows))
        if not pagination.get("hay_mas") or not page_rows or len(rows) >= total:
            break
        offset += len(page_rows)
    return rows, pages


def consultar_logistica_api(desde, hasta, sucursal="TODAS"):
    start = date.fromisoformat(str(desde))
    end = date.fromisoformat(str(hasta))
    if start > end:
        raise ValueError("Desde no puede ser posterior a hasta.")
    if (end - start).days > 366:
        raise ValueError("La sincronización admite como máximo 367 días por ejecución.")
    rows, pages, chunks = [], 0, 0
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=30))
        chunk_rows, chunk_pages = _consultar_logistica_api_chunk(cursor, chunk_end, sucursal)
        rows.extend(chunk_rows)
        pages += chunk_pages
        chunks += 1
        cursor = chunk_end + timedelta(days=1)
    return {"datos": rows, "paginas": pages, "rangos": chunks}


def _norm_logistics_match(value):
    normalized = _norm_persona_key(value)
    return re.sub(r"[^A-Z0-9]+", " ", normalized).strip()


def _norm_logistics_branch(value):
    normalized = _norm_logistics_match(value)
    if normalized in {"1", "CASA CENTRAL"} or "MAR DE AJO" in normalized:
        return "CASA CENTRAL"
    if normalized == "2" or "DOLORES" in normalized:
        return "DOLORES"
    if normalized == "3" or "CHASCOMUS" in normalized:
        return "CHASCOMUS"
    return re.sub(r"\b(SUCURSAL|DEPOSITO)\b", "", normalized).strip()


def _driver_match_keys(name, code=""):
    keys = set()
    normalized = _norm_logistics_match(name)
    if normalized and normalized not in {"SIN CHOFER", "SIN CONDUCTOR"}:
        keys.add("NAME:" + normalized)
        keys.add("TOKENS:" + " ".join(sorted(normalized.split())))
    normalized_code = _norm_id(code)
    if normalized_code:
        keys.add("ID:" + normalized_code)
    return keys


def _vehicle_match_keys(item):
    values = item if isinstance(item, (list, tuple, set)) else [item]
    keys = set()
    for value in values:
        normalized = _norm_logistics_match(value)
        if not normalized or normalized in {"SIN CAMION", "SIN TRANSPORTE"}:
            continue
        keys.add(normalized)
        keys.add(normalized.replace(" ", ""))
    return keys


def _route_needs_logistics_data(route):
    missing_vehicle = _norm_logistics_match(route.get("camion")) in {"", "SIN CAMION", "SIN TRANSPORTE"}
    missing_volume = any(_to_float(route.get(key)) <= 0 for key in ("bultos", "hl", "pallets", "unidades"))
    return missing_vehicle or missing_volume


def _logistics_api_source(item):
    fields = (
        "fecha", "empresa_id", "sucursal_id", "sucursal", "camion_codigo",
        "camion_descripcion", "patente", "marca", "modelo", "carga_maxima_kg",
        "capacidad_up", "camion_en_maestro_flota", "chofer_codigo", "chofer",
        "clientes", "documentos", "bultos", "hl", "pallets_estimados", "up",
        "lineas_origen", "calidad",
    )
    return {key: item.get(key) for key in fields if key in item}


def completar_rutas_desde_api_logistica(desde, hasta, sucursal="TODAS"):
    api_result = consultar_logistica_api(desde, hasta, sucursal)
    api_rows = [row for row in api_result["datos"] if isinstance(row, dict)]
    routes = storage.load_routes_for_logistics_sync(desde, hasta)
    incomplete_routes = [route for route in routes if _route_needs_logistics_data(route)]

    api_by_day_branch = {}
    for index, item in enumerate(api_rows):
        key = (str(item.get("fecha") or ""), _norm_logistics_branch(item.get("sucursal") or item.get("sucursal_id")))
        api_by_day_branch.setdefault(key, []).append((index, item))

    proposals = []
    unmatched = ambiguous = 0
    for route in incomplete_routes:
        base_key = (str(route.get("fecha") or ""), _norm_logistics_branch(route.get("suc")))
        route_driver = _driver_match_keys(route.get("chofer"), route.get("chofer_codigo"))
        candidates = [
            (index, item) for index, item in api_by_day_branch.get(base_key, [])
            if route_driver & _driver_match_keys(item.get("chofer"), item.get("chofer_codigo"))
        ]
        existing_vehicle = _vehicle_match_keys(route.get("camion"))
        if len(candidates) > 1 and existing_vehicle:
            vehicle_matches = [
                candidate for candidate in candidates
                if existing_vehicle & _vehicle_match_keys((
                    candidate[1].get("camion_codigo"), candidate[1].get("patente"),
                    candidate[1].get("camion_descripcion"),
                ))
            ]
            if vehicle_matches:
                candidates = vehicle_matches
        if len(candidates) == 1:
            proposals.append((route, candidates[0][0], candidates[0][1]))
        elif candidates:
            ambiguous += 1
        else:
            unmatched += 1

    api_usage = {}
    for _, api_index, _ in proposals:
        api_usage[api_index] = api_usage.get(api_index, 0) + 1

    updates = []
    fields_updated = {key: 0 for key in ("camion", "bultos", "hl", "pallets", "unidades")}
    matched = 0
    synced_at = datetime.now().astimezone().isoformat()
    for route, api_index, item in proposals:
        if api_usage[api_index] > 1:
            ambiguous += 1
            continue
        matched += 1
        values = {}
        if _norm_logistics_match(route.get("camion")) in {"", "SIN CAMION", "SIN TRANSPORTE"}:
            vehicle = item.get("patente") or item.get("camion_codigo")
            if _vehicle_match_keys(vehicle):
                values["camion"] = str(vehicle).strip()
        api_fields = {
            "bultos": item.get("bultos"),
            "hl": item.get("hl"),
            "pallets": item.get("pallets_estimados"),
            "unidades": item.get("up"),
        }
        for field, value in api_fields.items():
            if _to_float(route.get(field)) <= 0 and _to_float(value) > 0:
                values[field] = round(_to_float(value), 4)
        if not values:
            continue
        for field in values:
            fields_updated[field] += 1
        updates.append({
            "rid": route["rid"],
            "values": values,
            "payload": {
                "logistics_data_sources": {field: "api_logistica_v1" for field in values},
                "logistics_api": {
                    "api_version": "v1",
                    "synced_at": synced_at,
                    "match_criterion": "fecha_sucursal_chofer_unico",
                    "source": _logistics_api_source(item),
                },
            },
        })
    updated = storage.update_routes_from_logistics_api(updates)
    return {
        "api_rows": len(api_rows),
        "api_pages": api_result["paginas"],
        "api_ranges": api_result["rangos"],
        "routes_considered": len(routes),
        "routes_incomplete": len(incomplete_routes),
        "routes_matched": matched,
        "routes_updated": updated,
        "routes_unmatched": unmatched,
        "routes_ambiguous": ambiguous,
        "fields_updated": fields_updated,
    }


def _minutos_entre(a, b):
    if not a or not b:
        return None
    return int(round((datetime.combine(date.today(), b) - datetime.combine(date.today(), a)).total_seconds() / 60))


def _fichaya_setting_dict(key):
    rec = storage.load_setting(key) or {}
    raw = rec.get("valor") if isinstance(rec, dict) else rec
    return raw if isinstance(raw, dict) else {}


def fichaya_nombre_map():
    return _fichaya_setting_dict("fichaya_nombre_map")


def fichaya_empleados():
    return _fichaya_setting_dict("fichaya_empleados")


def fichaya_ajustes_manuales():
    return _fichaya_setting_dict("fichaya_ajustes_manuales")


def fichaya_lookup_ref(foxtrot_name, mapping=None, empleados=None):
    mapping = mapping if mapping is not None else fichaya_nombre_map()
    empleados = empleados if empleados is not None else fichaya_empleados()
    entry = mapping.get(_norm_persona_key(foxtrot_name))
    if isinstance(entry, dict):
        legajo = fichaya_legajo(entry.get("legajo"))
        nombre = entry.get("nombre") or (empleados.get(legajo) or {}).get("nombre") or ""
        return {"legajo": legajo, "nombre": nombre or foxtrot_name or ""}
    if isinstance(entry, str) and entry:
        emp = empleados.get(fichaya_legajo(entry))
        if emp:
            return {"legajo": emp["legajo"], "nombre": emp.get("nombre") or foxtrot_name or ""}
        return {"legajo": "", "nombre": entry}
    return {"legajo": "", "nombre": foxtrot_name or ""}


def fichaya_override_key(rec):
    route_id = str(rec.get("rid") or rec.get("route_id") or "").strip()
    if route_id:
        return "RID:" + route_id
    parts = (
        rec.get("fecha") or "",
        _norm_persona_key(rec.get("suc") or rec.get("sucursal")),
        _norm_persona_key(rec.get("chofer") or rec.get("empleado")),
    )
    return "ROW:" + "|".join(parts)


def calcular_tiempos_fichaya_ruta(
    rec,
    fichadas,
    mapping=None,
    empleados=None,
    manual_overrides=None,
):
    """Calcula TI/TML con la misma fuente y ajustes para reporte y dashboard."""
    nombre = rec.get("chofer") or rec.get("empleado") or ""
    fecha = str(rec.get("fecha") or "")
    ref = fichaya_lookup_ref(nombre, mapping, empleados)
    legajo = ref.get("legajo") or ""
    nombre_fichaya = ref.get("nombre") or nombre
    item = None
    if fichadas and legajo:
        item = fichadas.get((fecha, "LEGAJO:" + legajo))
    if fichadas and item is None:
        item = fichadas.get((fecha, _norm_persona_key(nombre_fichaya)))

    sources = {
        "fichada_ingreso": item.get("ingreso") if item else None,
        "inicio_foxtrot": _parse_hora_fichaya(rec.get("inicio_foxtrot")),
        "finalizacion_foxtrot": _parse_hora_fichaya(
            rec.get("fin_foxtrot") or rec.get("finalizacion_foxtrot")
        ),
        "fichada_salida": item.get("egreso") if item else None,
    }
    manual_key = fichaya_override_key(rec)
    manual_overrides = manual_overrides if manual_overrides is not None else fichaya_ajustes_manuales()
    manual = manual_overrides.get(manual_key) or {}
    effective = {
        field: (
            _parse_hora_fichaya(manual.get(field))
            if field in manual
            else sources[field]
        )
        for field in FICHAYA_MANUAL_FIELDS
    }
    tml = _minutos_entre(effective["fichada_ingreso"], effective["inicio_foxtrot"])
    ti = _minutos_entre(effective["finalizacion_foxtrot"], effective["fichada_salida"])
    tml_ok = tml is not None and 0 <= tml <= 240
    ti_ok = ti is not None and 0 <= ti <= 240
    return {
        "legajo": legajo,
        "nombre": nombre_fichaya,
        "item_encontrado": item is not None,
        "manual_key": manual_key,
        "manual": manual,
        "sources": sources,
        "effective": effective,
        "tml_raw": tml,
        "ti_raw": ti,
        "tml": tml if tml_ok else None,
        "ti": ti if ti_ok else None,
        "tml_ok": tml_ok,
        "ti_ok": ti_ok,
    }


def aplicar_tiempos_fichaya_guardados(rutas):
    """Superpone marcas y ajustes guardados sin alterar los registros persistidos."""
    candidatas = [
        rec for rec in rutas
        if rec.get("usable") and str(rec.get("fecha") or "") >= FICHAYA_TML_TI_DESDE
    ]
    if not candidatas:
        return rutas
    fechas = sorted(str(rec["fecha"]) for rec in candidatas if rec.get("fecha"))
    if not fechas:
        return rutas
    fichadas = cargar_fichadas_cache(fechas[0], fechas[-1])
    mapping = fichaya_nombre_map()
    empleados = fichaya_empleados()
    manual_overrides = fichaya_ajustes_manuales()
    for rec in candidatas:
        calc = calcular_tiempos_fichaya_ruta(
            rec,
            fichadas,
            mapping=mapping,
            empleados=empleados,
            manual_overrides=manual_overrides,
        )
        if calc["tml_ok"]:
            rec["tml"] = calc["tml"]
        if calc["ti_ok"]:
            rec["ti"] = calc["ti"]
        if calc["tml_ok"] or calc["ti_ok"]:
            rec["tml_ti_origen"] = (
                "fichaya" if calc["tml_ok"] and calc["ti_ok"] else "fichaya_parcial"
            )
        rec["tml_ti_ajuste_manual"] = any(
            field in calc["manual"] for field in FICHAYA_MANUAL_FIELDS
        )
        effective = calc["effective"]
        for source_field, route_field in (
            ("fichada_ingreso", "fichaya_ingreso"),
            ("fichada_salida", "fichaya_egreso"),
            ("inicio_foxtrot", "inicio_foxtrot"),
            ("finalizacion_foxtrot", "fin_foxtrot"),
        ):
            value = effective[source_field]
            if value is not None or source_field in calc["manual"]:
                rec[route_field] = value.strftime("%H:%M") if value else ""
        duracion = _minutos_entre(
            effective["inicio_foxtrot"], effective["finalizacion_foxtrot"]
        )
        if duracion is not None and 0 < duracion <= 14 * 60:
            rec["horas"] = round(duracion / 60, 3)
    return rutas


def _tml_ti_desde_fichadas(fichadas, fecha, chofer, fox_ini, fox_fin):
    item = fichadas.get((fecha, _norm_persona_key(chofer))) if fichadas else None
    if not item or pd.isna(fox_ini) or pd.isna(fox_fin):
        return None
    tml = _minutos_entre(item.get("ingreso"), fox_ini.time())
    ti = _minutos_entre(fox_fin.time(), item.get("egreso"))
    if tml is None or ti is None or not (0 <= tml <= 240) or not (0 <= ti <= 240):
        return None
    return {
        "tml": tml,
        "ti": ti,
        "tml_ti_origen": "fichaya",
        "fichaya_ingreso": item["ingreso"].strftime("%H:%M"),
        "fichaya_egreso": item["egreso"].strftime("%H:%M"),
    }


def _norm_customer_id_foxtrot(v):
    s = _norm_id(v)
    if len(s) > 8 and s.isdigit():
        return str(int(s[-8:]))
    return s


def _json_items(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "resultados", "results", "rows", "rechazos"):
            if isinstance(payload.get(key), list):
                return payload[key]
        if any(k in payload for k in ("fecha", "dia", "cantidad", "rechazos", "total")):
            return [payload]
    return []


def _pick_value(row, names, default=None):
    if not isinstance(row, dict):
        return default
    lower = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name in row:
            return row[name]
        v = lower.get(name.lower())
        if v is not None:
            return v
    return default


def _pick_col_value(row, names, default=None):
    return _pick_value(_row_raw_dict(row), names, default)


def _to_int(v):
    if v is None or v == "":
        return 0
    try:
        return int(_to_float(v))
    except Exception:
        return 0


def _to_float(v):
    if v is None or v == "":
        return 0.0
    try:
        if isinstance(v, (int, float, np.integer, np.floating)):
            return float(v)
        s = str(v).strip().replace("\xa0", "").replace(" ", "")
        if not s:
            return 0.0
        if "," in s and "." in s:
            return float(s.replace(".", "").replace(",", ".")) if s.rfind(",") > s.rfind(".") else float(s.replace(",", ""))
        if "," in s:
            return float(s.replace(".", "").replace(",", "."))
        return float(s)
    except Exception:
        return 0.0


def _json_safe(v):
    if pd.isna(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            pass
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def _row_raw_dict(row):
    return {str(k): _json_safe(v) for k, v in row.items()}


def _first_text(row, names):
    v = _pick_col_value(row, names)
    if v is None or pd.isna(v):
        return ""
    return str(v).strip()


def _first_float_or_none(row, names):
    v = _pick_col_value(row, names)
    if v is None or pd.isna(v) or str(v).strip() == "":
        return None
    val = _to_float(v)
    return val if val != 0 else None


def _parse_fecha_ar(v):
    dt = pd.to_datetime(v, dayfirst=True, errors="coerce")
    if pd.isna(dt):
        return ""
    return dt.strftime("%Y-%m-%d")


@_ttl_cached(EXTERNAL_CACHE_TTL_SECONDS)
def cargar_satisfaccion():
    rows, errors = [], []

    def norm_tipo(v):
        s = str(v or "").strip()
        low = s.lower()
        if low == "rate my delivery % de respuestas":
            return "Rate My Delivery % de respuestas"
        if low == "rmd puntaje":
            return "RMD Puntaje"
        if low == "nps gral":
            return "NPS GRAL"
        if low == "nps delivery (entrega)":
            return "NPS DELIVERY (ENTREGA)"
        if low == "% de detractores":
            return "% DE DETRACTORES"
        return s

    def leer_csv(url, fecha_col, tipo_col, resultado_col):
        req = Request(url, headers={"Accept": "text/csv"})
        with urlopen(req, timeout=EXTERNAL_REQUEST_TIMEOUT_SECONDS) as res:
            raw = res.read().decode("utf-8-sig", errors="replace")
        df = pd.read_csv(StringIO(raw), dtype=str).fillna("")
        out = []
        for _, r in df.iterrows():
            fecha = _parse_fecha_ar(r.get(fecha_col, ""))
            if not fecha:
                continue
            out.append({
                "anio": int(fecha[:4]),
                "mes": fecha[:7],
                "fecha": fecha,
                "tipo": norm_tipo(r.get(tipo_col, "")),
                "resultado": _to_float(str(r.get(resultado_col, "")).replace("%", "")),
            })
        return out

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="dashboard-satisfaction") as executor:
        pending = (
            (executor.submit(leer_csv, SATISFACCION_CSV_URL, "Fecha", "Tipo", "Resultado"), "No se pudo leer el CSV publicado de RMD."),
            (executor.submit(leer_csv, NPS_CSV_URL, "FECHA", "TIPO", "RESULTADO"), "No se pudo leer el CSV publicado de NPS."),
        )
        for future, error_message in pending:
            try:
                rows.extend(future.result())
            except Exception:
                errors.append(error_message)
    return {"rows": rows, "error": " ".join(errors)}


@_ttl_cached(EXTERNAL_CACHE_TTL_SECONDS)
def cargar_dqi():
    req = Request(DQI_CSV_URL, headers={"Accept": "text/csv"})
    try:
        with urlopen(req, timeout=EXTERNAL_REQUEST_TIMEOUT_SECONDS) as res:
            raw = res.read().decode("utf-8-sig", errors="replace")
        df = pd.read_csv(StringIO(raw), dtype=str).fillna("")
    except Exception:
        return {"rows": [], "error": "No se pudo leer el CSV publicado de DQI."}
    source_rows = len(df)
    df = df.copy()
    duplicates_detected = int(df.duplicated(keep="first").sum())

    def exact_col(name):
        return next((c for c in df.columns if str(c).strip() == name), None)

    fecha_col = _pick_col(df, ["Fecha Mvto", "Fecha Movimiento", "Fecha"])
    unids_col = _pick_col(df, ["Unids", "Unidades"])
    bultos_col = _pick_col(df, ["Bultos"])
    uxb_col = _pick_col(df, ["UXB", "Unidades por bulto"])
    dqi_bultos_col = _pick_col(df, ["DQI_WQI_BULTOS", "DQI WQI Bultos"])
    bultos_real_col = _pick_col(df, ["BULTOS_REAL", "Bultos Real", "Bultos reales"])
    dqi_hl_col = _pick_col(df, ["DQI_WQI_HL", "DQI WQI HL"])
    hl_real_col = _pick_col(df, ["ROTURA_HL_REAL", "Rotura HL Real", "HL Real"])
    sucursal_col = _pick_col(df, ["Sucursal", "SUCURSAL", "Suc", "SUC", "Zona", "Centro", "Unidad", "Branch"])
    sucursal_id_col = _pick_col(df, ["sucursal_id", "Sucursal ID", "ID Sucursal", "SucursalId", "Id Sucursal"])
    deposito_col = _pick_col(df, ["Depósito", "Deposito"])
    articulo_col = _pick_col(df, ["Artículo", "Articulo"])
    articulo_descripcion_col = _pick_col(df, ["Descripción Artículo", "Descripcion Articulo"])
    tipo_dqi_col = exact_col("TIPO")
    tipo_dqi_values = (
        {_norm_persona_key(v) for v in df[tipo_dqi_col].unique()}
        if tipo_dqi_col is not None else set()
    )
    if not tipo_dqi_values.intersection({"DQI", "WQI"}):
        tipo_dqi_col = None
    tipo_mercaderia_col = exact_col("TIPOMERC") or _pick_col(
        df, ["Tipo mercadería", "Tipo mercaderia"]
    )
    sector_col = exact_col("SECTOR") or _pick_col(df, ["Sector"])
    transporte_col = _pick_col(df, ["Transporte"])
    transporte_descripcion_cols = list(dict.fromkeys(
        c for c in [
            _pick_col(df, ["Descripción Transporte", "Descripcion Transporte"]),
            _pick_col(df, ["ALMACENAMIENTO", "Almacenamiento"]),
            _pick_col(df, ["Vehículo", "Vehiculo", "Camión", "Camion"]),
            _pick_col(df, ["Descripción", "Descripcion"]),
        ] if c is not None
    ))
    has_fallback_volume = bultos_col is not None or unids_col is not None
    has_volume = dqi_bultos_col is not None or bultos_real_col is not None or has_fallback_volume
    if fecha_col is None or not has_volume:
        return {"rows": [], "error": "El CSV de DQI no trae fecha ni volumen de roturas utilizables."}
    if tipo_mercaderia_col is None:
        return {"rows": [], "error": "El CSV de DQI no trae la columna TIPOMERC para filtrar mercadería."}
    articulos = storage.load_articulos()

    def parse_sucursal(row):
        raw = str(row.get(sucursal_col, "")).strip() if sucursal_col is not None else ""
        raw_id = str(row.get(sucursal_id_col, "")).strip() if sucursal_id_col is not None else ""
        sid = raw_id or (raw if _norm_id(raw) in {"1", "2"} else "")
        if _norm_id(sid) == "1":
            return "Mar de Ajo", "1"
        if _norm_id(sid) == "2":
            return "Dolores", "2"
        return raw, raw_id

    def include_dqi_branch(row):
        if sucursal_col is not None or sucursal_id_col is not None:
            _, sid = parse_sucursal(row)
            return not sid or _norm_id(sid) in {"1", "2"}
        return deposito_col is None or _norm_id(row.get(deposito_col)) == "7"

    def tipo_calidad(row):
        if tipo_dqi_col is not None:
            tipo = _norm_persona_key(row.get(tipo_dqi_col))
            return tipo if tipo in {"DQI", "WQI"} else ""
        if sector_col is not None:
            sector = _norm_persona_key(row.get(sector_col))
            if sector:
                return "DQI" if sector in {"REPARTO", "DISTRIBUCION", "ENTREGA"} else ""
        return "DQI"

    def bultos_equivalentes(row):
        if bultos_real_col is not None:
            raw_real = str(row.get(bultos_real_col, "")).strip()
            if raw_real:
                return max(0.0, _to_float(raw_real))
        bultos = _to_float(row.get(bultos_col)) if bultos_col is not None else 0.0
        unids = _to_float(row.get(unids_col)) if unids_col is not None else 0.0
        articulo = _norm_id(row.get(articulo_col)) if articulo_col is not None else ""
        upb = _to_float(row.get(uxb_col)) if uxb_col is not None else 0.0
        if upb <= 0:
            upb = (articulos.get(articulo) or {}).get("unidades_por_bulto")
        extra = (unids / upb) if upb and upb > 0 else 0.0
        return bultos + extra

    def valor_metrica(row, column, fallback):
        if column is not None:
            raw_value = str(row.get(column, "")).strip()
            if raw_value:
                return max(0.0, _to_float(raw_value))
        return max(0.0, fallback)

    daily = {}
    quality_daily = {}
    source_dates = []
    detalles = []
    included_wqi_rows = 0
    for _, r in df.iterrows():
        if not include_dqi_branch(r):
            continue
        if (
            tipo_mercaderia_col is not None
            and _norm_persona_key(r.get(tipo_mercaderia_col)) != "MERCADERIA"
        ):
            continue
        quality_type = tipo_calidad(r)
        if not quality_type:
            continue
        fecha = _parse_fecha_ar(r.get(fecha_col, ""))
        if not fecha:
            continue
        if fecha[:4] not in DQI_COMPARISON_YEARS:
            continue
        source_dates.append(fecha)
        bultos_real = bultos_equivalentes(r)
        dqi_bultos = valor_metrica(r, dqi_bultos_col, bultos_real)
        hl_real = valor_metrica(r, hl_real_col, 0.0)
        dqi_hl = valor_metrica(r, dqi_hl_col, hl_real)
        if max(dqi_bultos, bultos_real, dqi_hl, hl_real) <= 0:
            continue
        sucursal, sucursal_id = parse_sucursal(r)
        quality_day = quality_daily.setdefault((fecha, sucursal, sucursal_id), {
            "fecha": fecha,
            "suc": sucursal,
            "sucursal": sucursal,
            "sucursal_id": sucursal_id,
            "dqi_bultos": 0.0,
            "wqi_bultos": 0.0,
            "dqi_hl": 0.0,
            "wqi_hl": 0.0,
        })
        quality_key = quality_type.lower()
        quality_day[f"{quality_key}_bultos"] += dqi_bultos
        quality_day[f"{quality_key}_hl"] += dqi_hl
        if quality_type == "WQI":
            included_wqi_rows += 1
            continue
        articulo = _norm_id(r.get(articulo_col)) if articulo_col is not None else ""
        art = articulos.get(articulo) or {}
        descripcion_camion = next((
            str(r.get(c, "")).strip() for c in transporte_descripcion_cols
            if str(r.get(c, "")).strip()
        ), "")
        camion = " · ".join(dict.fromkeys(
            value for value in (
                str(r.get(transporte_col, "")).strip() if transporte_col is not None else "",
                descripcion_camion,
            ) if value
        ))
        if not camion:
            camion = "Sin camion"
        day = daily.setdefault((fecha, sucursal, sucursal_id), {
            "fecha": fecha,
            "suc": sucursal,
            "sucursal": sucursal,
            "sucursal_id": sucursal_id,
            "dqi": 0.0,
            "bultos_real": 0.0,
            "dqi_hl": 0.0,
            "hl_real": 0.0,
        })
        day["dqi"] += dqi_bultos
        day["bultos_real"] += bultos_real
        day["dqi_hl"] += dqi_hl
        day["hl_real"] += hl_real
        detalles.append({
            "fecha": fecha,
            "mes": fecha[:7],
            "suc": sucursal,
            "sucursal": sucursal,
            "sucursal_id": sucursal_id,
            "camion": camion,
            "articulo": articulo,
            "descripcion": art.get("descripcion") or (
                str(r.get(articulo_descripcion_col, "")).strip()
                if articulo_descripcion_col is not None else ""
            ),
            "tipo_mercaderia": "MERCADERIA",
            "bultos": round(dqi_bultos, 4),
            "bultos_real": round(bultos_real, 4),
            "hl": round(dqi_hl, 4),
            "hl_real": round(hl_real, 4),
        })
    rows = [
        {
            "fecha": values["fecha"],
            "mes": values["fecha"][:7],
            **(
                {
                    "suc": values["suc"],
                    "sucursal": values["sucursal"],
                    "sucursal_id": values["sucursal_id"],
                }
                if values["sucursal"] or values["sucursal_id"] else {}
            ),
            "dqi": round(values["dqi"], 4),
            "bultos_real": round(values["bultos_real"], 4),
            "dqi_hl": round(values["dqi_hl"], 4),
            "hl_real": round(values["hl_real"], 4),
        }
        for _, values in sorted(daily.items(), key=lambda item: item[0])
    ]
    dqi_wqi_rows = [
        {
            "fecha": values["fecha"],
            "mes": values["fecha"][:7],
            **(
                {
                    "suc": values["suc"],
                    "sucursal": values["sucursal"],
                    "sucursal_id": values["sucursal_id"],
                }
                if values["sucursal"] or values["sucursal_id"] else {}
            ),
            "dqi_bultos": round(values["dqi_bultos"], 4),
            "wqi_bultos": round(values["wqi_bultos"], 4),
            "total_bultos": round(values["dqi_bultos"] + values["wqi_bultos"], 4),
            "dqi_hl": round(values["dqi_hl"], 4),
            "wqi_hl": round(values["wqi_hl"], 4),
            "total_hl": round(values["dqi_hl"] + values["wqi_hl"], 4),
        }
        for _, values in sorted(quality_daily.items(), key=lambda item: item[0])
    ]
    return {
        "rows": rows,
        "detalles": detalles,
        "dqi_wqi_rows": dqi_wqi_rows,
        "quality": {
            "source_rows": source_rows,
            "duplicates_removed": 0,
            "duplicates_detected": duplicates_detected,
            "included_rows": len(detalles),
            "included_wqi_rows": included_wqi_rows,
            "comparison_years": list(DQI_COMPARISON_YEARS),
            "source_date_from": min(source_dates) if source_dates else "",
            "source_date_to": max(source_dates) if source_dates else "",
            "latest_dqi_date": max((values["fecha"] for values in daily.values()), default=""),
            "latest_quality_date": max((values["fecha"] for values in quality_daily.values()), default=""),
            "merchandise_filter": "MERCADERIA",
            "metric": "DQI_WQI_BULTOS" if dqi_bultos_col is not None else "BULTOS_REAL",
            "quality_hl_metric": "DQI_WQI_HL" if dqi_hl_col is not None else "ROTURA_HL_REAL",
            "physical_metric": "BULTOS_REAL" if bultos_real_col is not None else "calculado",
        },
        "error": "",
    }


def _pick_col_norm(df, candidates):
    def clean(s):
        s = "".join(c for c in unicodedata.normalize("NFD", str(s)) if unicodedata.category(c) != "Mn")
        return re.sub(r"\s+", " ", s).strip().lower()
    lookup = {clean(c): c for c in df.columns}
    for name in candidates:
        col = lookup.get(clean(name))
        if col is not None:
            return col
    for c in df.columns:
        low = clean(c)
        if any(clean(name) in low for name in candidates):
            return c
    return None


def _dpo_ok(v):
    s = str(v or "").strip().upper()
    if not s:
        return None
    return s in ("OK", "SI", "SÍ", "TRUE", "1", "A TIEMPO")


@_ttl_cached(EXTERNAL_CACHE_TTL_SECONDS)
def cargar_dpo_gkpis():
    rows, errors = [], []

    def fetch_source(source):
        unidad, sucursal, sid_default, url = source
        try:
            req = Request(url, headers={"Accept": "text/csv", "User-Agent": "Mozilla/5.0"})
            raw = urlopen(req, timeout=EXTERNAL_REQUEST_TIMEOUT_SECONDS).read().decode("utf-8-sig", errors="replace")
        except Exception as e:
            return source, None, f"No se pudo leer DPO {sucursal}: {e}"
        return source, raw, ""

    with ThreadPoolExecutor(max_workers=min(4, len(DPO_GKPI_URLS)), thread_name_prefix="dashboard-dpo") as executor:
        sources = list(executor.map(fetch_source, DPO_GKPI_URLS))
    for source, raw, fetch_error in sources:
        unidad, sucursal, sid_default, _url = source
        if fetch_error:
            errors.append(fetch_error)
            continue
        try:
            df = pd.read_csv(StringIO(raw), dtype=str).fillna("")
        except Exception as e:
            errors.append(f"No se pudo procesar DPO {sucursal}: {e}")
            continue
        fecha_col = _pick_col_norm(df, ["Fecha"])
        camion_col = _pick_col_norm(df, ["Camion", "Camión"])
        nro_col = _pick_col_norm(df, ["N° Camion", "N Camion", "Numero Camion"])
        chofer_col = _pick_col_norm(df, ["Chofer / Responsable billetera", "Chofer"])
        ay1_col = _pick_col_norm(df, ["Ayudante1", "Ayudante 1"])
        ay2_col = _pick_col_norm(df, ["Ayudante2", "Ayudante 2"])
        up_col = _pick_col_norm(df, ["UP"])
        clientes_col = _pick_col_norm(df, ["CLIENTES", "Clientes"])
        personas_col = _pick_col_norm(df, ["PERSONAS", "Personas"])
        pallets_col = _pick_col_norm(df, ["Pallets"])
        obs_col = _pick_col_norm(df, ["Observaciones"])
        carga_col = _pick_col_norm(df, ["Cargado a tiempo?"])
        descarga_col = _pick_col_norm(df, ["Descargado a tiempo?"])
        hora_carga_col = _pick_col_norm(df, ["Hora de carga?"])
        estado_col = _pick_col_norm(df, ["Estado"])
        sid_col = _pick_col_norm(df, ["sucursal_id"])
        if fecha_col is None or camion_col is None:
            errors.append(f"DPO {sucursal}: faltan columnas Fecha/Camion.")
            continue
        for idx, r in df.iterrows():
            fecha = _parse_fecha_ar(r.get(fecha_col, ""))
            camion = str(r.get(camion_col, "") or "").strip()
            if not fecha or not camion:
                continue
            sid = str(r.get(sid_col, "") or sid_default).strip() if sid_col else sid_default
            suc = "Mar de Ajo" if sid == "1" else "Dolores"
            nro = str(r.get(nro_col, "") or "").strip() if nro_col else ""
            estado = str(r.get(estado_col, "") or "").strip() if estado_col else ""
            rows.append({
                "key": f"{fecha}|{sid}|{nro or camion}|{idx}",
                "fuente": hashlib.sha256(_url.encode("utf-8")).hexdigest()[:16],
                "fecha": fecha,
                "mes": fecha[:7],
                "anio": int(fecha[:4]),
                "unidad": "Casa Central" if sid == "1" else "Sucursal Dolores",
                "suc": suc,
                "sucursal_id": sid,
                "camion": camion,
                "nro_camion": nro or camion,
                "chofer": str(r.get(chofer_col, "") or "").strip() if chofer_col else "",
                "ayudante1": str(r.get(ay1_col, "") or "").strip() if ay1_col else "",
                "ayudante2": str(r.get(ay2_col, "") or "").strip() if ay2_col else "",
                "up": _to_float(r.get(up_col)) if up_col else None,
                "clientes": _to_float(r.get(clientes_col)) if clientes_col else None,
                "personas": _to_float(r.get(personas_col)) if personas_col else None,
                "pallets": _to_float(r.get(pallets_col)) if pallets_col else None,
                "cargado_ok": _dpo_ok(r.get(carga_col)) if carga_col else None,
                "descargado_ok": _dpo_ok(r.get(descarga_col)) if descarga_col else None,
                "hora_carga": str(r.get(hora_carga_col, "") or "").strip() if hora_carga_col else "",
                "estado": estado,
                "recarga": bool(estado),
                "observaciones": str(r.get(obs_col, "") or "").strip() if obs_col else "",
            })
    rows = sorted(rows, key=lambda r: (r["fecha"], r["suc"], r["nro_camion"], r["chofer"]))
    return {"rows": rows, "error": " ".join(errors)}


def dqi_objetivo_bultos_mes():
    cfg = storage.load_setting("dqi_objetivo_bultos_mes") or {}
    val = _to_float(cfg.get("valor"))
    return val if val > 0 else 1


def guardar_dqi_objetivo(valor):
    val = _to_float(valor)
    if val <= 0:
        raise ValueError("El objetivo mensual DQI debe ser mayor a cero.")
    return storage.save_setting("dqi_objetivo_bultos_mes", {"valor": val})


def _norm_rechazo(row):
    fecha = str(_pick_value(row, ("fecha", "dia", "date", "Fecha", "Día"), "") or "")[:10]
    cantidad = _to_int(_pick_value(row, ("pedidos_rechazo", "rechazo_pedidos", "rechazos", "cantidad", "total", "count", "valor"), 0))
    motivo = str(_pick_value(row, ("motivo", "causa", "tipo", "descripcion", "descripción", "evento", "feriado"), "") or "")
    suc = str(_pick_value(row, ("sucursal", "Sucursal"), RECHAZOS_SUCURSAL) or RECHAZOS_SUCURSAL)
    sid = str(_pick_value(row, ("sucursal_id", "sucursalId", "id_sucursal"), RECHAZOS_SUCURSAL_ID) or RECHAZOS_SUCURSAL_ID)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
        return None
    key = f"{fecha}|{suc}"
    return {
        "key": key,
        "fecha": fecha,
        "mes": fecha[:7],
        "sucursal": suc,
        "sucursal_id": sid,
        "rechazos": cantidad,
        "motivo": motivo,
        "pedidos_pdv_atendidos": _to_int(_pick_value(row, ("pedidos_pdv_atendidos", "pedidos", "pdv_unicos"), 0)),
        "pdv_unicos": _to_int(_pick_value(row, ("pdv_unicos",), 0)),
        "nds": _to_float(_pick_value(row, ("nds",), 0)),
        "bultos": _to_float(_pick_value(row, ("bultos",), 0)),
        "rechazo_bultos": _to_float(_pick_value(row, ("rechazo_bultos", "bultos_rechazo"), 0)),
        "rechazo_bultos_total": _to_float(_pick_value(row, ("rechazo_bultos_total", "bultos_rechazo"), 0)),
        "pct_rechazo_bultos": _to_float(_pick_value(row, ("pct_rechazo_bultos",), 0)),
        "hl": _to_float(_pick_value(row, ("hl",), 0)),
        "rechazo_hl": _to_float(_pick_value(row, ("rechazo_hl", "hl_rechazo"), 0)),
        "rechazo_hl_total": _to_float(_pick_value(row, ("rechazo_hl_total", "hl_rechazo"), 0)),
        "pct_rechazo_hl": _to_float(_pick_value(row, ("pct_rechazo_hl",), 0)),
        "pallets": _to_float(_pick_value(row, ("pallets",), 0)),
        "rechazo_pallets": _to_float(_pick_value(row, ("rechazo_pallets", "pallets_rechazo"), 0)),
        "pct_rechazo_pallets": _to_float(_pick_value(row, ("pct_rechazo_pallets",), 0)),
        "salidas": _to_int(_pick_value(row, ("salidas",), 0)),
        "pct_rechazo_pedidos": _to_float(_pick_value(row, ("pct_rechazo_pedidos", "pct_rechazo", "porcentaje"), 0)),
        "pico": str(_pick_value(row, ("pico",), "")).lower() == "true",
        "feriado": str(_pick_value(row, ("feriado",), "") or ""),
        "evento": str(_pick_value(row, ("evento",), "") or ""),
        "raw_rechazo": {str(k): _json_safe(v) for k, v in row.items()},
    }


def _norm_rechazo_detalle(row):
    fecha = str(_pick_value(row, ("fecha", "dia", "date"), "") or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
        return None
    chofer = str(_pick_value(row, ("chofer", "driver", "repartidor"), "Sin chofer") or "Sin chofer").strip() or "Sin chofer"
    motivo = str(_pick_value(row, ("motivo", "causa"), "Sin motivo") or "Sin motivo").strip() or "Sin motivo"
    sector = str(_pick_value(row, ("sector",), "Sin sector") or "Sin sector").strip() or "Sin sector"
    suc = str(_pick_value(row, ("sucursal", "Sucursal"), RECHAZOS_SUCURSAL) or RECHAZOS_SUCURSAL)
    rec = {
        "fecha": fecha,
        "mes": fecha[:7],
        "sucursal": suc,
        "chofer": chofer,
        "chofer_codigo": str(_pick_value(row, ("chofer_codigo", "codigo_chofer"), "") or ""),
        "sector": sector,
        "motivo": motivo,
        "pedidos_rechazo": _to_int(_pick_value(row, ("pedidos_rechazo", "rechazos"), 0)),
        "ocurrencias": _to_int(_pick_value(row, ("ocurrencias", "cantidad", "total"), 0)),
        "bultos_rechazo": _to_float(_pick_value(row, ("bultos_rechazo", "rechazo_bultos"), 0)),
        "hl_rechazo": _to_float(_pick_value(row, ("hl_rechazo", "rechazo_hl"), 0)),
        "pallets_rechazo": _to_float(_pick_value(row, ("pallets_rechazo", "rechazo_pallets"), 0)),
        "raw_rechazo_detalle": {str(k): _json_safe(v) for k, v in row.items()},
    }
    key_parts = [fecha, suc, rec["chofer_codigo"] or chofer, sector, motivo]
    return "|".join(str(x).replace("|", "/") for x in key_parts), rec


def _fetch_rechazos(url):
    req = Request(url, headers={"Accept": "text/csv, application/json"})
    try:
        with urlopen(req, timeout=30) as res:
            return res.read().decode("utf-8"), res.headers.get("Content-Type", ""), url
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise ValueError(f"El endpoint respondio HTTP {e.code}. URL: {url}. Respuesta: {body}") from e


def importar_rechazos(desde=None, hasta=None):
    desde = desde or "2026-01-01"
    hasta = hasta or _today()
    params = {"desde": desde, "hasta": hasta, "sucursal": RECHAZOS_SUCURSALES_IMPORT}
    url = RECHAZOS_API_URL + "?" + urlencode(params)
    try:
        raw, ctype, final_url = _fetch_rechazos(url)
        if "csv" in ctype.lower() or final_url.endswith("formato=csv"):
            return guardar_rechazos_csv(raw, desde, hasta, final_url)
        if "json" in ctype.lower():
            return guardar_rechazos_payload(json.loads(raw), desde, hasta, final_url)
    except ValueError as first_error:
        if "HTTP 404" not in str(first_error) or not RECHAZOS_API_URL.endswith("/diario/integracion"):
            raise
        base = RECHAZOS_API_URL.rsplit("/diario/integracion", 1)[0]
        resumen_url = base + "/diario/resumen?" + urlencode(params)
        detalle_url = base + "/diario/detalle?" + urlencode(params)
        try:
            resumen_raw, _, _ = _fetch_rechazos(resumen_url)
            detalle_raw, _, _ = _fetch_rechazos(detalle_url)
        except ValueError as second_error:
            raise ValueError(
                "La API de rechazos no esta disponible en la URL configurada. "
                f"Probo integracion, resumen y detalle. Ultimo error: {second_error}. "
                "Verifica que la app origen tenga registrado/deployado app.routes.rechazos "
                "o configura RECHAZOS_API_URL con la URL correcta."
            ) from second_error
        resumen_payload = json.loads(resumen_raw)
        detalle_payload = json.loads(detalle_raw)
        payload = {
            "resumen_diario": resumen_payload.get("datos", resumen_payload),
            "detalle_diario": detalle_payload.get("datos", detalle_payload),
        }
        return guardar_rechazos_payload(payload, desde, hasta, resumen_url)
    raise ValueError(f"El endpoint no devolvio CSV ni JSON. URL: {url}")


def guardar_rechazos_csv(raw, desde="", hasta="", origen="archivo"):
    df = pd.read_csv(StringIO(raw))
    payload = df.fillna("").to_dict(orient="records")
    return guardar_rechazos_payload(payload, desde, hasta, origen)


def _norm_header(v):
    s = unicodedata.normalize("NFKD", str(v or "").replace("\n", " ").strip())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s).upper()


def guardar_rechazos_excel(file_obj, desde="", hasta="", origen="archivo"):
    if hasattr(file_obj, "seek"):
        file_obj.seek(0)
    wb = load_workbook(file_obj, read_only=True, data_only=True)
    ws = wb["BASE"] if "BASE" in wb.sheetnames else wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = None
    for row in rows:
        vals = [_norm_header(v) for v in row]
        if "FECHA" in vals and "BULTOS" in vals and ("UNIDAD PAQUETE" in vals or "UNIDAD DE MEDIDA" in vals):
            header = vals
            break
    if not header:
        raise ValueError("No se encontro una hoja de rechazos con columnas FECHA, BULTOS y UNIDAD PAQUETE.")
    idx = {name: i for i, name in enumerate(header) if name}

    def val(row, name, default=0):
        i = idx.get(name)
        return row[i] if i is not None and i < len(row) else default

    recs, det, docs_por_fecha, docs_detalle = {}, {}, {}, {}
    for nrow, row in enumerate(rows, start=1):
        fecha = _parse_fecha_ar(val(row, "FECHA", ""))
        if not fecha:
            continue
        bultos = _to_float(val(row, "BULTOS"))
        bultos_rech = _to_float(val(row, "BULTOS RECHAZADOS"))
        hl = _to_float(val(row, "UNIDAD PAQUETE", val(row, "UNIDAD DE MEDIDA")))
        hl_rech = _to_float(val(row, "UNIDAD PAQUETE RECHAZADO", val(row, "UNIDAD DE MEDIDA RECHAZADO")))
        pallets = _to_float(val(row, "UNIDAD DE MEDIDA"))
        pallets_rech = _to_float(val(row, "UNIDAD DE MEDIDA RECHAZADO"))
        doc = str(val(row, "DETALLE DOCUMENTO", "") or val(row, "NUMERO", "") or nrow).strip()
        rechazo_flag = str(val(row, "RECHAZO", "")).strip().upper()
        es_rechazo = bultos_rech > 0 or hl_rech > 0 or rechazo_flag not in ("", "0", "NO")

        suc = RECHAZOS_SUCURSAL
        key_rec = f"{fecha}|{suc}"
        rec = recs.setdefault(key_rec, {
            "key": key_rec, "fecha": fecha, "mes": fecha[:7], "sucursal": suc, "sucursal_id": RECHAZOS_SUCURSAL_ID,
            "rechazos": 0, "motivo": "", "pedidos_pdv_atendidos": 0, "pdv_unicos": 0, "nds": 0,
            "bultos": 0.0, "rechazo_bultos": 0.0, "rechazo_bultos_total": 0.0, "pct_rechazo_bultos": 0.0,
            "hl": 0.0, "rechazo_hl": 0.0, "rechazo_hl_total": 0.0, "pct_rechazo_hl": 0.0,
            "pallets": 0.0, "rechazo_pallets": 0.0, "pct_rechazo_pallets": 0.0,
            "salidas": 0, "pct_rechazo_pedidos": 0.0, "pico": False, "feriado": "", "evento": "",
        })
        rec["bultos"] += bultos
        rec["rechazo_bultos"] += bultos_rech
        rec["rechazo_bultos_total"] += bultos_rech
        rec["hl"] += hl
        rec["rechazo_hl"] += hl_rech
        rec["rechazo_hl_total"] += hl_rech
        rec["pallets"] += pallets
        rec["rechazo_pallets"] += pallets_rech
        if es_rechazo:
            docs_por_fecha.setdefault(key_rec, set()).add(doc)
            chofer = str(val(row, "DESCRIPCION CHOFER", val(row, "DESCRIPCION DETALLDA CHOFER", "Sin chofer")) or "Sin chofer").strip()
            motivo = str(val(row, "MOTIVO DE RECHAZO", val(row, "DESCRIPCION DETALLADA MOTIVO", "Sin motivo")) or "Sin motivo").strip()
            sector = str(val(row, "DESCRIPCION RUTA", val(row, "RUTA", "Sin sector")) or "Sin sector").strip()
            key = "|".join(x.replace("|", "/") for x in [fecha, suc, chofer, sector, motivo])
            d = det.setdefault(key, {"fecha": fecha, "mes": fecha[:7], "sucursal": RECHAZOS_SUCURSAL, "chofer": chofer,
                                     "chofer_codigo": str(val(row, "CHOFER", "") or ""), "sector": sector, "motivo": motivo,
                                     "pedidos_rechazo": 0, "ocurrencias": 0, "bultos_rechazo": 0.0,
                                     "hl_rechazo": 0.0, "pallets_rechazo": 0.0})
            docs_detalle.setdefault(key, set()).add(doc)
            d["ocurrencias"] += 1
            d["bultos_rechazo"] += bultos_rech
            d["hl_rechazo"] += hl_rech
            d["pallets_rechazo"] += pallets_rech

    for key_rec, rec in recs.items():
        rec["rechazos"] = len(docs_por_fecha.get(key_rec, set()))
        rec["pct_rechazo_bultos"] = (rec["rechazo_bultos"] / rec["bultos"] * 100) if rec["bultos"] else 0.0
        rec["pct_rechazo_hl"] = (rec["rechazo_hl"] / rec["hl"] * 100) if rec["hl"] else 0.0
        rec["pct_rechazo_pallets"] = (rec["rechazo_pallets"] / rec["pallets"] * 100) if rec["pallets"] else 0.0
    for key, item in det.items():
        item["pedidos_rechazo"] = len(docs_detalle.get(key, set()))

    guardados = storage.upsert_rechazos(recs)
    detalle_guardados = storage.upsert_rechazos_detalle(det) if det else 0
    return {"desde": desde, "hasta": hasta, "url": origen, "recibidos": len(recs), "guardados": guardados, "detalle_guardados": detalle_guardados}


def guardar_rechazos_payload(payload, desde="", hasta="", origen="archivo"):
    recs = {}
    resumen = payload.get("resumen_diario") if isinstance(payload, dict) else None
    detalle = payload.get("detalle_diario") if isinstance(payload, dict) else None
    for row in (resumen if resumen is not None else _json_items(payload)):
        rec = _norm_rechazo(row)
        if not rec:
            continue
        key = rec["key"]
        if key not in recs:
            recs[key] = rec
        else:
            recs[key]["rechazos"] += rec["rechazos"]
            recs[key]["pedidos_pdv_atendidos"] += rec.get("pedidos_pdv_atendidos", 0)
            recs[key]["pdv_unicos"] += rec.get("pdv_unicos", 0)
            recs[key]["bultos"] += rec.get("bultos", 0)
            recs[key]["rechazo_bultos"] += rec.get("rechazo_bultos", 0)
            recs[key]["rechazo_bultos_total"] += rec.get("rechazo_bultos_total", 0)
            recs[key]["hl"] += rec.get("hl", 0)
            recs[key]["rechazo_hl"] += rec.get("rechazo_hl", 0)
            recs[key]["rechazo_hl_total"] += rec.get("rechazo_hl_total", 0)
            recs[key]["pallets"] += rec.get("pallets", 0)
            recs[key]["rechazo_pallets"] += rec.get("rechazo_pallets", 0)
            if rec["motivo"] and not recs[key].get("motivo"):
                recs[key]["motivo"] = rec["motivo"]
    guardados = storage.upsert_rechazos(recs)
    det_recs = {}
    if detalle is not None:
        for row in detalle:
            item = _norm_rechazo_detalle(row)
            if item:
                key, rec = item
                det_recs[key] = rec
    detalle_guardados = storage.upsert_rechazos_detalle(det_recs) if det_recs else 0
    return {"desde": desde, "hasta": hasta, "url": origen, "recibidos": len(recs), "guardados": guardados, "detalle_guardados": detalle_guardados}


def _time_to_min(h, m="0"):
    return int(h) * 60 + int(m)


def parse_horario_entrega(texto):
    """Convierte '09:00 A 13:00 Y DE 17:00 A 21:00' en rangos en minutos."""
    if pd.isna(texto):
        return []
    s = str(texto).upper().strip()
    if not s or s in {"0", "0.00%", "NAN"}:
        return []
    s = (s.replace("HS", "").replace("HRS", "").replace("HORAS", "")
           .replace("–", " A ").replace("-", " A ").replace("A.", "A"))
    pairs = re.findall(r"(\d{1,2})(?::(\d{2}))?\s*(?:A|/|HASTA)\s*(\d{1,2})(?::(\d{2}))?", s)
    rangos = []
    for h1, m1, h2, m2 in pairs:
        ini = _time_to_min(h1, m1 or "0")
        fin = _time_to_min(h2, m2 or "0")
        if 0 <= ini < 24 * 60 and 0 < fin <= 24 * 60 and ini != fin:
            rangos.append({"ini": ini, "fin": fin})
    return rangos


def _en_ventana(ts, ventanas):
    if pd.isna(ts) or not ventanas:
        return None
    minuto = int(ts.hour) * 60 + int(ts.minute)
    for v in ventanas:
        ini, fin = v["ini"], v["fin"]
        if ini <= fin and ini <= minuto <= fin:
            return True
        if ini > fin and (minuto >= ini or minuto <= fin):
            return True
    return False


def _fmt_minuto(m):
    return f"{int(m) // 60:02d}:{int(m) % 60:02d}"


def _fmt_ventanas(ventanas):
    return " / ".join(f"{_fmt_minuto(v['ini'])}-{_fmt_minuto(v['fin'])}" for v in ventanas)


def procesar_clientes(clientes_file):
    c = pd.read_csv(clientes_file, sep=";", dtype=str, encoding="cp1252")
    out = {}
    for _, r in c.iterrows():
        cliente = _norm_id(r.get("Cliente"))
        if not cliente:
            continue
        horario = r.get("Horario de entrega")
        ventanas = parse_horario_entrega(horario)
        out[cliente] = {
            "cliente": cliente,
            "sucursal": _norm_id(r.get("Sucursal")),
            "razon_social": "" if pd.isna(r.get("Razon social")) else str(r.get("Razon social")).strip(),
            "nombre": "" if pd.isna(r.get("Nombre de fantasia")) else str(r.get("Nombre de fantasia")).strip(),
            "direccion": _first_text(r, ("Direccion", "DirecciÃ³n", "Domicilio", "Calle")),
            "localidad": _first_text(r, (
                "Nombre de localidad",
                "Nombre localidad",
                "Localidad de entrega",
                "Localidad",
                "Ciudad",
                "Poblacion",
                "Población",
                "PoblaciÃ³n",
            )),
            "provincia": _first_text(r, ("Provincia",)),
            "codigo_postal": _first_text(r, ("Codigo postal", "CÃ³digo postal", "CP")),
            "latitud": _first_float_or_none(r, ("Latitud", "Latitude", "GPS Latitud", "GPS Latitude", "Coord Y de entrega", "Coord Y", "Y")),
            "longitud": _first_float_or_none(r, ("Longitud", "Longitude", "GPS Longitud", "GPS Longitude", "Coord X de entrega", "Coord X", "X")),
            "horario_entrega": "" if pd.isna(horario) else str(horario).strip(),
            "ventanas": ventanas,
            "raw_cliente": _row_raw_dict(r),
        }
    return out


def actualizar_clientes(clientes_file):
    clientes = procesar_clientes(clientes_file)
    count = storage.replace_clientes(clientes)
    clear_dashboard_cache()
    return count


def procesar_articulos(articulos_file):
    try:
        df = pd.read_csv(articulos_file, dtype=str, sep=None, engine="python", encoding="utf-8-sig", encoding_errors="replace").fillna("")
    except UnicodeDecodeError:
        if hasattr(articulos_file, "seek"):
            articulos_file.seek(0)
        df = pd.read_csv(articulos_file, dtype=str, sep=None, engine="python", encoding="cp1252", encoding_errors="replace").fillna("")
    art_col = _pick_col(df, ["Artículo", "Articulo", "Codigo", "Código", "SKU"])
    desc_col = _pick_col(df, ["Descripción Artículo", "Descripcion Articulo", "Descripcion", "Descripción"])
    upb_col = _pick_col(df, [
        "Unidades por bulto", "Unidades x bulto", "Unid x bulto", "UxB",
        "Unidades/Bulto", "Unidades por caja", "Factor", "Contenido",
    ])
    if art_col is None or upb_col is None:
        raise ValueError("El archivo de articulos debe tener articulo y unidades por bulto.")
    out = {}
    for _, r in df.iterrows():
        articulo = _norm_id(r.get(art_col))
        upb = _to_float(r.get(upb_col))
        if not articulo or upb <= 0:
            continue
        out[articulo] = {
            "articulo": articulo,
            "descripcion": str(r.get(desc_col, "")).strip() if desc_col is not None else "",
            "unidades_por_bulto": upb,
            "raw_articulo": _row_raw_dict(r),
        }
    return out


def actualizar_articulos(articulos_file):
    articulos = procesar_articulos(articulos_file)
    return storage.replace_articulos(articulos)


def _leer_tabular(file_obj, filename=""):
    name = (filename or getattr(file_obj, "name", "") or "").lower()
    if hasattr(file_obj, "seek"):
        file_obj.seek(0)
    if name.endswith((".xls", ".xlsx")):
        return _leer_excel(file_obj, name)
    last_error = None
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            if hasattr(file_obj, "seek"):
                file_obj.seek(0)
            return pd.read_csv(file_obj, encoding=encoding, sep=None, engine="python")
        except Exception as exc:
            last_error = exc
    raise ValueError(f"No se pudo leer el archivo tabular: {last_error}")


def importar_volumen_entregas(file_obj, filename=""):
    df = _leer_tabular(file_obj, filename)
    route_col = _pick_col(df, ("Route ID", "Ruta ID", "ID Ruta"))
    customer_col = _pick_col(df, ("Customer ID", "Cliente ID", "Código Cliente", "Codigo Cliente"))
    if route_col is None or customer_col is None:
        raise ValueError("El archivo debe contener Route ID y Customer ID.")
    volume_columns = {
        "bultos": _pick_col(df, ("Bultos entregados", "Bultos", "Cajas entregadas")),
        "hl": _pick_col(df, ("HL entregados", "HL", "Hectolitros")),
        "pallets": _pick_col(df, ("Pallets entregados", "Pallets", "Pallet")),
        "unidades": _pick_col(df, ("Unidades entregadas", "Unidades")),
    }
    if not any(volume_columns.values()):
        raise ValueError("El archivo no contiene Bultos, HL, Pallets ni Unidades entregadas.")
    status_col = _pick_col(df, ("Estado entrega", "Estado", "Resultado entrega"))
    records, invalid = [], 0
    for _, row in df.iterrows():
        route_id = _norm_id(row.get(route_col))
        cliente = _norm_customer_id_foxtrot(row.get(customer_col))
        if not route_id or not cliente:
            invalid += 1
            continue
        values = {}
        bad = False
        for key, col in volume_columns.items():
            if col is None:
                continue
            value = _to_float(row.get(col))
            if value < 0:
                bad = True
                break
            values[key] = value
        if bad:
            invalid += 1
            continue
        values["estado_entrega"] = str(row.get(status_col) or "").strip() if status_col is not None else ""
        values["raw_entrega"] = _row_raw_dict(row)
        records.append({"route_id": route_id, "cliente": cliente, "values": values})
    updated = storage.update_attempt_deliveries(records)
    return {"filas_validas": len(records), "filas_invalidas": invalid, "intentos_actualizados": updated}


def importar_asignacion_vehiculos(file_obj, filename=""):
    df = _leer_tabular(file_obj, filename)
    route_col = _pick_col(df, ("Route ID", "Ruta ID", "ID Ruta"))
    vehicle_col = _pick_col(df, ("Vehículo", "Vehiculo", "Patente", "Camión", "Camion", "Unidad"))
    if route_col is None or vehicle_col is None:
        raise ValueError("El archivo debe contener Route ID y Vehículo o Patente.")
    records, invalid = {}, 0
    for _, row in df.iterrows():
        rid = _norm_id(row.get(route_col))
        vehicle = "" if pd.isna(row.get(vehicle_col)) else str(row.get(vehicle_col) or "").strip()
        if not rid or not vehicle or vehicle.lower() in ("sin camion", "sin camión"):
            invalid += 1
            continue
        records[rid] = {"rid": rid, "camion": vehicle, "raw": _row_raw_dict(row)}
    updated = storage.update_route_vehicles(list(records.values()))
    return {"filas_validas": len(records), "filas_invalidas": invalid, "rutas_actualizadas": updated}


def _pick_col(df, candidates):
    lookup = {str(c).strip().lower(): c for c in df.columns}
    for name in candidates:
        col = lookup.get(name.lower())
        if col is not None:
            return col
    for c in df.columns:
        low = str(c).lower()
        if any(name.lower() in low for name in candidates):
            return c
    return None


def _leer_csv_visitas(csv_files):
    frames = []
    for f, name in csv_files:
        try:
            if hasattr(f, "seek"):
                f.seek(0)
            n = (name or getattr(f, "name", "") or "").lower()
            if n.endswith((".xls", ".xlsx")):
                c = _leer_excel_visitas(f, n)
            else:
                c = pd.read_csv(f)
        except Exception:
            continue
        if "Route ID" not in c.columns:
            continue
        c = c.copy()
        c["click"] = pd.to_datetime(c.get("Driver Click Timestamp"), errors="coerce")
        c["vs"] = pd.to_datetime(c.get("Visit Start Timestamp"), errors="coerce")
        c["visend"] = c["vs"] + pd.to_timedelta(c["Visit Duration Seconds"], "s") \
            if "Visit Duration Seconds" in c.columns else c["vs"]
        frames.append(c)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _visitas_df(csv_files_or_df):
    if isinstance(csv_files_or_df, pd.DataFrame):
        return csv_files_or_df
    return _leer_csv_visitas(csv_files_or_df or [])


def _attempt_key(row, idx):
    parts = [
        row.get("Route ID"),
        row.get("Waypoint ID"),
        row.get("Customer ID"),
        row.get("Visit Start Timestamp"),
        row.get("Driver Click Timestamp"),
        idx,
    ]
    return "|".join("" if pd.isna(p) else str(p) for p in parts)


def procesar_attempts(csv_files):
    visitas = _visitas_df(csv_files)
    if visitas.empty:
        return {}
    out = {}
    for idx, row in visitas.iterrows():
        rec = _row_raw_dict(row)
        key = _attempt_key(row, idx)
        rec["attempt_key"] = key
        rec["route_id"] = str(row.get("Route ID") or "").strip()
        rec["cliente"] = _norm_customer_id_foxtrot(row.get("Customer ID"))
        rec["cliente_nombre"] = str(row.get("Customer Name") or "").strip()
        rec["orden_visita"] = _to_int(_pick_col_value(row, (
            "Stop Sequence", "Sequence", "Secuencia", "Orden", "Visit Sequence",
            "Planned Sequence", "Waypoint Sequence", "Waypoint ID",
        ))) or None
        rec["visit_start"] = _json_safe(row.get("Visit Start Timestamp"))
        rec["driver_click"] = _json_safe(row.get("Driver Click Timestamp"))
        rec["visit_duration_seconds"] = _to_int(row.get("Visit Duration Seconds"))
        rec["service_duration_seconds"] = _to_int(_pick_col_value(row, (
            "Beta: Inferred Service Duration Seconds", "Service Duration Seconds",
            "Tiempo Atencion Segundos", "Tiempo AtenciÃ³n Segundos",
        )))
        rec["latitud"] = _first_float_or_none(row, (
            "Customer Latitude", "Customer Lat", "Latitude", "Latitud",
            "Visit Latitude", "Driver Click Latitude",
        ))
        rec["longitud"] = _first_float_or_none(row, (
            "Customer Longitude", "Customer Lon", "Longitude", "Longitud",
            "Visit Longitude", "Driver Click Longitude",
        ))
        rec["bultos"] = _first_float_or_none(row, (
            "Bultos", "Bultos Despachados", "Cases", "Delivered Cases",
            "Actual Cases", "Packages", "Unidades Paquete", "Unidad Paquete",
        ))
        rec["hl"] = _first_float_or_none(row, (
            "HL", "HLS", "Hectolitros", "Volume HL", "Delivered HL",
            "Actual HL", "Volumen HL",
        ))
        rec["pallets"] = _first_float_or_none(row, (
            "Pallets", "Pallet", "Tarimas", "Unidad de Medida",
        ))
        out[key] = rec
    return out


def _mapa_correccion(csv_files):
    c = _visitas_df(csv_files)
    if c.empty:
        return {}
    lv = c.groupby("Route ID").agg(u1=("click", "max"), u2=("visend", "max"))
    lv["f"] = lv["u1"].fillna(lv["u2"])
    return lv["f"].to_dict()


def _mapa_ontime(csv_files):
    visitas = _visitas_df(csv_files)
    if visitas.empty:
        return {}
    clientes = storage.load_clientes()
    cli_col = _pick_col(visitas, ["Customer ID", "Customer Id", "Customer", "Client ID", "Cliente"])
    if cli_col is None:
        return {}
    ts = visitas["click"].fillna(visitas["vs"])
    visitas = visitas.assign(cliente=visitas[cli_col].map(_norm_customer_id_foxtrot), paso=ts)
    stats = {}
    for rid, grp in visitas.groupby("Route ID"):
        total = ontime = fuera = sin_ventana = 0
        fuera_clientes = []
        clientes_con_ventana = {}
        clientes_sin_ventana = {}
        for _, v in grp.iterrows():
            cid = v["cliente"]
            if not cid:
                continue
            total += 1
            cliente = clientes.get(cid) or {}
            nombre = cliente.get("nombre") or cliente.get("razon_social") or str(v.get("Customer Name", ""))
            ventanas = cliente.get("ventanas", [])
            if ventanas:
                clientes_con_ventana[cid] = cid
            else:
                cliente_ref = {"cliente": cid, "nombre": nombre}
                cliente_ref["motivo"] = "sin ventana cargada" if cliente else "no encontrado en base de clientes"
                clientes_sin_ventana[cid] = cliente_ref
            ok = _en_ventana(v["paso"], ventanas)
            if ok is True:
                ontime += 1
            elif ok is False:
                fuera += 1
                fuera_clientes.append({
                    "cliente": cid,
                    "nombre": nombre,
                    "visita": v["paso"].strftime("%H:%M") if pd.notna(v["paso"]) else "",
                    "ventana": _fmt_ventanas(ventanas),
                })
            else:
                sin_ventana += 1
        if total:
            evaluables = ontime + fuera
            stats[str(rid)] = {
                "pdv_total": int(total),
                "pdv_ontime": int(ontime),
                "pdv_fuera_ontime": int(fuera),
                "pdv_sin_ventana": int(sin_ventana),
                "ontime_pct": round(100 * ontime / evaluables, 1) if evaluables else None,
                "clientes_fuera_ontime": fuera_clientes[:50],
                "clientes_con_ventana": list(clientes_con_ventana.values()),
                "clientes_sin_ventana": list(clientes_sin_ventana.values()),
            }
    return stats


def procesar_export(xls_file, xls_name="", csv_files=None, visitas=None):
    """Devuelve dict rid -> registro, calculado desde el export."""
    csv_files = csv_files or []
    visitas = _visitas_df(visitas if visitas is not None else csv_files)
    x = _leer_excel(xls_file, xls_name)
    hl_col = _pick_col(x, ["HL", "HLS", "Hectolitros", "Hectolitro", "Hectoliter", "Hectoliters", "Volume HL", "Delivered HL", "Planned HL", "Actual HL", "Volumen HL", "Volumen Hectolitros"])
    bultos_col = _pick_col(x, ["Bultos", "Bultos Despachados", "Cases", "Delivered Cases", "Planned Cases", "Actual Cases", "Packages", "Unidades Paquete", "Unidad Paquete"])
    salidas_col = _pick_col(x, ["Salidas", "Stops", "Stops Count", "Customers", "Deliveries", "Pedidos", "PDV"])
    camion_col = _pick_col(x, ["Camion", "Camión", "Truck", "Vehicle", "Vehicle Name", "Vehicle ID", "Plate", "License Plate", "Patente", "Transporte", "Descripcion Transporte", "Descripción Transporte"])
    x["fox_ini"] = pd.to_datetime(x["Driver Marked Route Start Timestamp"], errors="coerce")
    x["fox_fin"] = pd.to_datetime(x["Driver Marked Route End Timestamp"], errors="coerce")
    x["suc"] = x["DC Name"].str.replace(" - del Palacio S.A.", "", regex=False)
    valid = x["fox_ini"].notna() & x["fox_fin"].notna()
    same_day = x["fox_ini"].dt.date == x["fox_fin"].dt.date
    x["raw_h"] = (x["fox_fin"] - x["fox_ini"]).dt.total_seconds() / 3600
    fmap = _mapa_correccion(visitas)
    omap = _mapa_ontime(visitas)

    x["fin_final"] = x["fox_fin"]; x["usable"] = False
    for i in x[valid].index:
        if same_day[i]:
            x.at[i, "usable"] = True
        else:
            nf = fmap.get(x.at[i, "Route ID"])
            if nf is not None and pd.notna(nf) and nf.date() == x.at[i, "fox_ini"].date() \
               and 0 < (nf - x.at[i, "fox_ini"]).total_seconds() / 60 <= 14 * 60:
                x.at[i, "fin_final"] = nf; x.at[i, "usable"] = True
    x["dur_h"] = (x["fin_final"] - x["fox_ini"]).dt.total_seconds() / 3600
    x.loc[(x["dur_h"] <= 0) | (x["dur_h"] > 14), "usable"] = False

    fichadas = {}
    fechas_fichaya = sorted({
        d.strftime("%Y-%m-%d")
        for d in x.loc[valid, "fox_ini"].dropna()
        if d.strftime("%Y-%m-%d") >= FICHAYA_TML_TI_DESDE
    })
    if fechas_fichaya:
        try:
            fichadas = cargar_fichadas(fechas_fichaya[0], fechas_fichaya[-1])
        except Exception:
            fichadas = {}

    out = {}
    for i in x[valid].index:
        r = x.loc[i]; rid = str(r["Route ID"]); usable = bool(r["usable"])
        ah = r["dur_h"] if usable else r["raw_h"]
        rec = {"rid": rid, "suc": r["suc"], "chofer": r["Driver Name"],
               "mes": r["fox_ini"].strftime("%Y-%m"), "fecha": r["fox_ini"].strftime("%Y-%m-%d"),
               "anio": r["fox_ini"].strftime("%Y"),
               "inicio_foxtrot": r["fox_ini"].strftime("%H:%M") if pd.notna(r["fox_ini"]) else "",
               "fin_foxtrot": r["fin_final"].strftime("%H:%M") if pd.notna(r["fin_final"]) else "",
               "camion": str(r.get(camion_col, "")).strip() if camion_col is not None and str(r.get(camion_col, "")).strip() else "Sin camion",
               "usable": usable, "alerta": bool(ah > OBJ["alerta_h"]),
               "hl": _to_float(r.get(hl_col)) if hl_col is not None else 0.0,
               "bultos": _to_float(r.get(bultos_col)) if bultos_col is not None else 0.0,
               "salidas": _to_int(r.get(salidas_col)) if salidas_col is not None else 0,
               "raw_foxtrot": _row_raw_dict(r)}
        if rid in omap:
            rec.update(omap[rid])
        if usable:
            g = rng_de_ruta(rid)
            km_plan = _num_or_none(r.get("Planned Foxtrot Driving Meters"))
            km_real = _num_or_none(r.get("Total Driven Meters"))
            hs_plan = _num_or_none(r.get("Planned Foxtrot Driving Seconds"))
            hs_real = _num_or_none(r.get("Total Driven Seconds"))
            tml_ti_real = None
            if rec["fecha"] >= FICHAYA_TML_TI_DESDE:
                tml_ti_real = _tml_ti_desde_fichadas(fichadas, rec["fecha"], rec["chofer"], r["fox_ini"], r["fin_final"])
            rec.update({"ti": tml_ti_real["ti"] if tml_ti_real else _clamp_normal(g, TI_CENTRO, TI_SD, 25, 45),
                        "tml": tml_ti_real["tml"] if tml_ti_real else _clamp_normal(g, TML_CENTRO, TML_SD, 20, 45),
                        "tml_ti_origen": "fichaya" if tml_ti_real else "estimado",
                        "horas": round(r["dur_h"], 3),
                        "adhsec": round(r["Sequence Adherence"] * 100, 1) if pd.notna(r.get("Sequence Adherence")) else None,
                        "adhcli": round(r["Driver Click Score"] * 100, 1) if pd.notna(r.get("Driver Click Score")) else None,
                        "disp_km_plan": km_plan,
                        "disp_km_real": km_real,
                        "disp_hs_plan": hs_plan,
                        "disp_hs_real": hs_real,
                        "dispkm": _dispersion(km_plan, km_real),
                        "disphs": _dispersion(hs_plan, hs_real)})
            if tml_ti_real:
                rec.update({k: v for k, v in tml_ti_real.items() if k not in ("tml", "ti", "tml_ti_origen")})
            _descartar_dispersion_anomala(rec)
        out[rid] = rec
    return out


DASHBOARD_ROUTE_FIELDS = {
    "rid", "suc", "chofer", "mes", "fecha", "anio", "inicio_foxtrot", "fin_foxtrot",
    "camion", "usable", "alerta", "ti", "tml", "tml_ti_origen", "horas", "adhsec",
    "adhcli", "disp_km_plan", "disp_km_real", "disp_hs_plan", "disp_hs_real", "dispkm",
    "disphs", "disp_descartada", "disp_motivo", "pdv_total", "pdv_ontime",
    "pdv_fuera_ontime", "pdv_sin_ventana", "ontime_pct", "clientes_fuera_ontime",
    "clientes_con_ventana", "clientes_sin_ventana", "ti_estimado", "ti_estimacion_metodo", "tml_estimado", "tml_estimacion_metodo",
}


def _dashboard_route(rec):
    projected = {key: value for key, value in rec.items() if key in DASHBOARD_ROUTE_FIELDS}
    projected["clientes_con_ventana"] = [
        str(item.get("cliente") or "") if isinstance(item, dict) else str(item or "")
        for item in projected.get("clientes_con_ventana") or []
        if (item.get("cliente") if isinstance(item, dict) else item)
    ]
    return _descartar_dispersion_anomala(projected)


def _calibrar_metrica_historica_casa_central(rutas, campo, referencias):
    por_mes = {}
    for rec in rutas:
        mes = str(rec.get("mes") or "")
        if (
            mes not in referencias
            or rec.get(campo + "_estimado")
            or not rec.get("usable")
            or rec.get(campo) is None
            or str(rec.get("tml_ti_origen") or "").lower() == "fichaya"
            or _norm_logistics_branch(rec.get("suc")) != "CASA CENTRAL"
        ):
            continue
        por_mes.setdefault(mes, []).append(rec)

    for mes, rows in por_mes.items():
        objetivo = float(referencias[mes])
        promedio_actual = sum(float(rec[campo]) for rec in rows) / len(rows)
        ajuste = objetivo - promedio_actual
        for rec in rows:
            rec[campo] = round(float(rec[campo]) + ajuste, 3)
            rec[f"{campo}_referencia_mensual"] = objetivo

        # Compensa el redondeo para que el promedio del mes sea exactamente el objetivo.
        diferencia = objetivo * len(rows) - sum(float(rec[campo]) for rec in rows)
        rows[-1][campo] = round(float(rows[-1][campo]) + diferencia, 3)
    return rutas


def _calibrar_tiempos_historicos_casa_central(rutas):
    """Alinea TML y TI simulados con las referencias mensuales de Casa Central."""
    _calibrar_metrica_historica_casa_central(
        rutas, "tml", CASA_CENTRAL_TML_REFERENCIA_2026
    )
    _calibrar_metrica_historica_casa_central(
        rutas, "ti", CASA_CENTRAL_TI_REFERENCIA_2026
    )
    return rutas


def _estimar_ti_enero_2026(rutas):
    """Simulacion solicitada: TI uniforme 25-45 min, estable por ruta."""
    for rec in rutas:
        if (rec.get("mes") != "2026-01" or not rec.get("usable")
                or str(rec.get("tml_ti_origen") or "").startswith("fichaya")):
            continue
        if rec.get("ti_estimado") and rec.get("ti_estimacion_metodo") == "uniforme_25_45_v1":
            continue
        identity = rec.get("rid") or "|".join(str(rec.get(k) or "") for k in ("fecha", "suc", "chofer", "inicio_foxtrot"))
        seed = hashlib.sha256(("ti-enero-2026-v1|" + str(identity)).encode("utf-8")).digest()
        fraction = int.from_bytes(seed[:8], "big") / (2**64 - 1)
        rec["ti"] = round(25 + 20 * fraction, 2)
        rec["ti_estimado"] = True
        rec.pop("ti_referencia_mensual", None)
    return rutas


def _estimar_ti_20_35_2026(rutas, mes):
    """Simulacion solicitada: TI uniforme 20-35 min, estable por ruta."""
    nombre_mes = {"2026-05": "mayo", "2026-06": "junio", "2026-07": "julio"}[mes]
    for rec in rutas:
        if (rec.get("mes") != mes or not rec.get("usable")
                or str(rec.get("tml_ti_origen") or "").startswith("fichaya")):
            continue
        if rec.get("ti_estimado") and rec.get("ti_estimacion_metodo") == "uniforme_20_35_v1":
            continue
        identity = rec.get("rid") or "|".join(str(rec.get(k) or "") for k in ("fecha", "suc", "chofer", "inicio_foxtrot"))
        seed = hashlib.sha256(("ti-" + nombre_mes + "-2026-v1|" + str(identity)).encode("utf-8")).digest()
        fraction = int.from_bytes(seed[:8], "big") / (2**64 - 1)
        rec["ti"] = round(20 + 15 * fraction, 2)
        rec["ti_estimado"] = True
        rec.pop("ti_referencia_mensual", None)
    return rutas


def _estimar_tml_abril_2026(rutas):
    """Simulacion solicitada: TML uniforme 20-35 min, estable por ruta."""
    mes = "2026-04"
    for rec in rutas:
        if (rec.get("mes") != mes or not rec.get("usable")
                or str(rec.get("tml_ti_origen") or "").startswith("fichaya")):
            continue
        if rec.get("tml_estimado") and rec.get("tml_estimacion_metodo") == "uniforme_20_35_v1":
            continue
        identity = rec.get("rid") or "|".join(str(rec.get(k) or "") for k in ("fecha", "suc", "chofer", "inicio_foxtrot"))
        seed = hashlib.sha256(("tml-abril-2026-v1|" + str(identity)).encode("utf-8")).digest()
        fraction = int.from_bytes(seed[:8], "big") / (2**64 - 1)
        rec["tml"] = round(20 + 15 * fraction, 2)
        rec["tml_estimado"] = True
        rec.pop("tml_referencia_mensual", None)
    return rutas


def _estimar_ti_julio_2026(rutas):
    return _estimar_ti_20_35_2026(rutas, "2026-07")


def aplicar_ventanas_actuales_clientes(rutas, clientes):
    """Current master coverage; keep historical On Time classifications untouched."""
    for ruta in rutas:
        candidates = {}
        for field in ("clientes_con_ventana", "clientes_sin_ventana", "clientes_fuera_ontime"):
            for item in ruta.get(field) or []:
                rec = item if isinstance(item, dict) else {"cliente": item}
                cid = _norm_customer_id_foxtrot(rec.get("cliente"))
                if cid:
                    candidates.setdefault(cid, rec)
        con, sin = [], []
        for cid, previous in candidates.items():
            current = clientes.get(cid)
            if current and current.get("ventanas"):
                con.append(cid)
            else:
                sin.append({"cliente": cid,
                            "nombre": (current or {}).get("nombre") or (current or {}).get("razon_social") or previous.get("nombre") or "",
                            "motivo": "sin ventana cargada" if current is not None else "no encontrado en base de clientes"})
        ruta["clientes_con_ventana_actual"] = con
        ruta["clientes_sin_ventana_actual"] = sin
    return rutas


def _data_desde_base(base):
    rutas = sorted((_dashboard_route(r) for r in base.values()), key=lambda r: (r["fecha"], r["suc"], r["chofer"]))
    _calibrar_tiempos_historicos_casa_central(rutas)
    _estimar_ti_enero_2026(rutas)
    _estimar_tml_abril_2026(rutas)
    for mes in ("2026-05", "2026-06", "2026-07"):
        _estimar_ti_20_35_2026(rutas, mes)
    aplicar_tiempos_fichaya_guardados(rutas)
    aplicar_ventanas_actuales_clientes(rutas, storage.load_clientes())
    rechazos_base = storage.load_rechazos()
    if rutas and not rechazos_base:
        try:
            fechas = sorted(r["fecha"] for r in rutas if r.get("fecha"))
            if fechas:
                importar_rechazos(fechas[0], fechas[-1])
                rechazos_base = storage.load_rechazos()
        except Exception:
            rechazos_base = {}
    rechazos = sorted(rechazos_base.values(), key=lambda r: r["fecha"])
    rechazos_detalle = sorted(storage.load_rechazos_detalle().values(), key=lambda r: (r["fecha"], r.get("chofer", ""), r.get("motivo", "")))
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="dashboard-external") as executor:
        satisfaction_future = executor.submit(cargar_satisfaccion)
        dqi_future = executor.submit(cargar_dqi)
        dpo_future = executor.submit(cargar_dpo_gkpis)
        satisfaction = satisfaction_future.result()
        dqi = dqi_future.result()
        dpo = dpo_future.result()
    return {"rutas": rutas,
            "rechazos": rechazos,
            "rechazos_detalle": rechazos_detalle,
            "satisfaccion": satisfaction,
            "dqi": dqi,
            "dpo": dpo,
            "settings": {"dqi_objetivo_bultos_mes": dqi_objetivo_bultos_mes()},
            "choferes": sorted({r["chofer"] for r in rutas}),
            "sucursales": sorted({r["suc"] for r in rutas}),
            "meses": sorted({r["mes"] for r in rutas}),
            "obj": OBJ}


def actualizar(xls_file, xls_name, csv_files=None, reset=False):
    """Procesa el export y agrega a la base SOLO las rutas nuevas. Devuelve stats."""
    visitas = _leer_csv_visitas(csv_files or [])
    nuevos = procesar_export(xls_file, xls_name, csv_files, visitas=visitas)
    attempts = procesar_attempts(visitas) if not visitas.empty else {}
    if reset and not nuevos:
        raise ValueError("El export no contiene rutas; se cancela el borrado de la base.")
    backup_id = storage.reset() if reset else None
    previas = storage.count_routes()
    actualiza_existentes = True
    agregadas = storage.upsert_all(nuevos)
    attempts_guardados = storage.upsert_attempts(attempts) if attempts else 0
    stats = storage.route_import_stats(OBJ["tml"], OBJ["ti"])
    return {"backup_id": backup_id, "previas": previas, "agregadas": agregadas, "actualiza_existentes": actualiza_existentes, "procesadas": len(nuevos), "attempts_guardados": attempts_guardados, **stats}


@_ttl_cached(DASHBOARD_CACHE_TTL_SECONDS)
def render_dashboard():
    data = _data_desde_base(storage.load_dashboard_routes(DASHBOARD_ROUTE_FIELDS))
    html = open(PLANTILLA, encoding="utf-8").read()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return html.replace("__DATA__", payload)


def clear_dashboard_cache(include_external=False):
    render_dashboard.cache_clear()
    if include_external:
        cargar_satisfaccion.cache_clear()
        cargar_dqi.cache_clear()
        cargar_dpo_gkpis.cache_clear()


def hay_datos():
    return storage.count_routes() > 0
