# -*- coding: utf-8 -*-
"""
Lógica de datos del dashboard de Tiempos de reparto - Foxtrot.
La usa app.py (Flask). La persistencia (Postgres o JSON) está en storage.py.

Base incremental: cada ruta se identifica por Route ID y su TI/TML aleatorio es
determinístico por ID, así una ruta ya cargada nunca cambia de valor al actualizar.
"""

import os, json, hashlib, re
from datetime import date
from io import StringIO
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import numpy as np
import pandas as pd

import storage

# ---------------- Parámetros ajustables ----------------
TI_CENTRO  = 35
TML_CENTRO = 27.5
TI_SD, TML_SD = 7, 6
OBJ = {"tml": 30, "ti": 30, "ruta": 7, "ruta_max": 8, "alerta_h": 12, "adh": 85, "disp": 10}

AQUI = os.path.dirname(os.path.abspath(__file__))
PLANTILLA = os.path.join(AQUI, "plantilla_dashboard.html")
RECHAZOS_API_URL = os.environ.get(
    "RECHAZOS_API_URL",
    "https://web-production-f968ec.up.railway.app/api/picos/rechazos-dolores/diario",
)
RECHAZOS_SUCURSAL = os.environ.get("RECHAZOS_SUCURSAL", "Dolores")
RECHAZOS_SUCURSAL_ID = os.environ.get("RECHAZOS_SUCURSAL_ID", "2")
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

storage.init()


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


def _to_int(v):
    if v is None or v == "":
        return 0
    try:
        return int(float(str(v).replace(",", ".")))
    except Exception:
        return 0


def _to_float(v):
    if v is None or v == "":
        return 0.0
    try:
        return float(str(v).replace(".", "").replace(",", ".")) if "," in str(v) else float(v)
    except Exception:
        return 0.0


def _parse_fecha_ar(v):
    dt = pd.to_datetime(v, dayfirst=True, errors="coerce")
    if pd.isna(dt):
        return ""
    return dt.strftime("%Y-%m-%d")


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
        with urlopen(req, timeout=20) as res:
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

    try:
        rows.extend(leer_csv(SATISFACCION_CSV_URL, "Fecha", "Tipo", "Resultado"))
    except Exception:
        errors.append("No se pudo leer el CSV publicado de RMD.")
    try:
        rows.extend(leer_csv(NPS_CSV_URL, "FECHA", "TIPO", "RESULTADO"))
    except Exception:
        errors.append("No se pudo leer el CSV publicado de NPS.")
    return {"rows": rows, "error": " ".join(errors)}


def cargar_dqi():
    req = Request(DQI_CSV_URL, headers={"Accept": "text/csv"})
    try:
        with urlopen(req, timeout=30) as res:
            raw = res.read().decode("utf-8-sig", errors="replace")
        df = pd.read_csv(StringIO(raw), dtype=str).fillna("")
    except Exception:
        return {"rows": [], "error": "No se pudo leer el CSV publicado de DQI."}
    fecha_col = _pick_col(df, ["Fecha Mvto", "Fecha Movimiento", "Fecha"])
    unids_col = _pick_col(df, ["Unids", "Unidades"])
    bultos_col = _pick_col(df, ["Bultos"])
    deposito_col = _pick_col(df, ["Depósito", "Deposito"])
    articulo_col = _pick_col(df, ["Artículo", "Articulo"])
    transporte_cols = [
        c for c in [
            _pick_col(df, ["Transporte"]),
            _pick_col(df, ["Descripción Transporte", "Descripcion Transporte"]),
            _pick_col(df, ["Descripción Movimiento", "Descripcion Movimiento"]),
        ] if c is not None
    ]
    if fecha_col is None or unids_col is None:
        return {"rows": [], "error": "El CSV de DQI no trae Fecha Mvto y Unids."}
    articulos = storage.load_articulos()

    def es_entrega(row):
        if not transporte_cols:
            return True
        txt = " ".join(str(row.get(c, "")) for c in transporte_cols).lower()
        if "camion" in txt or "camión" in txt:
            return True
        if any(x in txt for x in ("roturas acarreo", "iveco", "entrega")):
            return True
        return False

    def bultos_equivalentes(row):
        bultos = _to_float(row.get(bultos_col)) if bultos_col is not None else 0.0
        unids = _to_float(row.get(unids_col))
        articulo = _norm_id(row.get(articulo_col)) if articulo_col is not None else ""
        upb = (articulos.get(articulo) or {}).get("unidades_por_bulto")
        extra = (unids / upb) if upb and upb > 0 else 0.0
        return bultos + extra

    daily = {}
    detalles = []
    for _, r in df.iterrows():
        if deposito_col is not None and _norm_id(r.get(deposito_col)) != "7":
            continue
        if not es_entrega(r):
            continue
        fecha = _parse_fecha_ar(r.get(fecha_col, ""))
        if not fecha:
            continue
        if fecha[:4] != "2026":
            continue
        bultos_eq = bultos_equivalentes(r)
        if bultos_eq <= 0:
            continue
        articulo = _norm_id(r.get(articulo_col)) if articulo_col is not None else ""
        art = articulos.get(articulo) or {}
        camion = " ".join(str(r.get(c, "")).strip() for c in transporte_cols if str(r.get(c, "")).strip())
        if not camion:
            camion = "Sin camion"
        daily[fecha] = daily.get(fecha, 0.0) + bultos_eq
        detalles.append({
            "fecha": fecha,
            "mes": fecha[:7],
            "camion": camion,
            "articulo": articulo,
            "descripcion": art.get("descripcion") or str(r.get(_pick_col(df, ["Descripción Artículo", "Descripcion Articulo"]) or "", "")).strip(),
            "bultos": round(bultos_eq, 2),
        })
    rows = [
        {"fecha": fecha, "mes": fecha[:7], "dqi": round(valor, 1)}
        for fecha, valor in sorted(daily.items())
    ]
    return {"rows": rows, "detalles": detalles, "error": ""}


def dqi_objetivo_bultos_mes():
    cfg = storage.load_settings().get("dqi_objetivo_bultos_mes") or {}
    val = _to_float(cfg.get("valor"))
    return val if val > 0 else 1


def guardar_dqi_objetivo(valor):
    val = _to_float(valor)
    if val <= 0:
        raise ValueError("El objetivo mensual DQI debe ser mayor a cero.")
    return storage.save_setting("dqi_objetivo_bultos_mes", {"valor": val})


def _norm_rechazo(row):
    fecha = str(_pick_value(row, ("fecha", "dia", "date", "Fecha", "Día"), "") or "")[:10]
    cantidad = _to_int(_pick_value(row, ("rechazo_pedidos", "rechazos", "cantidad", "total", "count", "valor"), 0))
    motivo = str(_pick_value(row, ("motivo", "causa", "tipo", "descripcion", "descripción", "evento", "feriado"), "") or "")
    suc = str(_pick_value(row, ("sucursal", "Sucursal"), RECHAZOS_SUCURSAL) or RECHAZOS_SUCURSAL)
    sid = str(_pick_value(row, ("sucursal_id", "sucursalId", "id_sucursal"), RECHAZOS_SUCURSAL_ID) or RECHAZOS_SUCURSAL_ID)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
        return None
    return {
        "fecha": fecha,
        "sucursal": suc,
        "sucursal_id": sid,
        "rechazos": cantidad,
        "motivo": motivo,
        "pedidos_pdv_atendidos": _to_int(_pick_value(row, ("pedidos_pdv_atendidos", "pedidos", "pdv_unicos"), 0)),
        "pdv_unicos": _to_int(_pick_value(row, ("pdv_unicos",), 0)),
        "nds": _to_float(_pick_value(row, ("nds",), 0)),
        "bultos": _to_float(_pick_value(row, ("bultos",), 0)),
        "rechazo_bultos": _to_float(_pick_value(row, ("rechazo_bultos",), 0)),
        "rechazo_bultos_total": _to_float(_pick_value(row, ("rechazo_bultos_total",), 0)),
        "pct_rechazo_bultos": _to_float(_pick_value(row, ("pct_rechazo_bultos",), 0)),
        "hl": _to_float(_pick_value(row, ("hl",), 0)),
        "rechazo_hl": _to_float(_pick_value(row, ("rechazo_hl",), 0)),
        "rechazo_hl_total": _to_float(_pick_value(row, ("rechazo_hl_total",), 0)),
        "pct_rechazo_hl": _to_float(_pick_value(row, ("pct_rechazo_hl",), 0)),
        "salidas": _to_int(_pick_value(row, ("salidas",), 0)),
        "pct_rechazo_pedidos": _to_float(_pick_value(row, ("pct_rechazo_pedidos", "pct_rechazo", "porcentaje"), 0)),
        "pico": str(_pick_value(row, ("pico",), "")).lower() == "true",
        "feriado": str(_pick_value(row, ("feriado",), "") or ""),
        "evento": str(_pick_value(row, ("evento",), "") or ""),
    }


def importar_rechazos(desde=None, hasta=None):
    desde = desde or "2026-01-01"
    hasta = hasta or _today()
    url = RECHAZOS_API_URL + "?" + urlencode({"desde": desde, "hasta": hasta, "formato": "csv"})
    req = Request(url, headers={"Accept": "text/csv, application/json"})
    try:
        with urlopen(req, timeout=30) as res:
            ctype = res.headers.get("Content-Type", "")
            raw = res.read().decode("utf-8")
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise ValueError(f"El endpoint respondió HTTP {e.code}. URL: {url}. Respuesta: {body}") from e
    if "csv" in ctype.lower() or url.endswith("formato=csv"):
        return guardar_rechazos_csv(raw, desde, hasta, url)
    if "json" in ctype.lower():
        payload = json.loads(raw)
        return guardar_rechazos_payload(payload, desde, hasta, url)
    raise ValueError(f"El endpoint no devolvió CSV ni JSON. Content-Type: {ctype or 'sin Content-Type'}")


def guardar_rechazos_csv(raw, desde="", hasta="", origen="archivo"):
    df = pd.read_csv(StringIO(raw))
    payload = df.fillna("").to_dict(orient="records")
    return guardar_rechazos_payload(payload, desde, hasta, origen)


def guardar_rechazos_payload(payload, desde="", hasta="", origen="archivo"):
    recs = {}
    for row in _json_items(payload):
        rec = _norm_rechazo(row)
        if not rec:
            continue
        key = rec["fecha"]
        if key not in recs:
            recs[key] = rec
        else:
            recs[key]["rechazos"] += rec["rechazos"]
            if rec["motivo"] and not recs[key].get("motivo"):
                recs[key]["motivo"] = rec["motivo"]
    guardados = storage.upsert_rechazos(recs)
    return {"desde": desde, "hasta": hasta, "url": origen, "recibidos": len(recs), "guardados": guardados}


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
            "horario_entrega": "" if pd.isna(horario) else str(horario).strip(),
            "ventanas": ventanas,
        }
    return out


def actualizar_clientes(clientes_file):
    clientes = procesar_clientes(clientes_file)
    return storage.replace_clientes(clientes)


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
        }
    return out


def actualizar_articulos(articulos_file):
    articulos = procesar_articulos(articulos_file)
    return storage.replace_articulos(articulos)


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


def _mapa_correccion(csv_files):
    c = _leer_csv_visitas(csv_files)
    if c.empty:
        return {}
    lv = c.groupby("Route ID").agg(u1=("click", "max"), u2=("visend", "max"))
    lv["f"] = lv["u1"].fillna(lv["u2"])
    return lv["f"].to_dict()


def _mapa_ontime(csv_files):
    visitas = _leer_csv_visitas(csv_files)
    clientes = storage.load_clientes()
    if visitas.empty:
        return {}
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
            cliente_ref = {"cliente": cid, "nombre": nombre}
            if ventanas:
                clientes_con_ventana[cid] = cliente_ref
            else:
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


def procesar_export(xls_file, xls_name="", csv_files=None):
    """Devuelve dict rid -> registro, calculado desde el export."""
    csv_files = csv_files or []
    x = _leer_excel(xls_file, xls_name)
    x["fox_ini"] = pd.to_datetime(x["Driver Marked Route Start Timestamp"], errors="coerce")
    x["fox_fin"] = pd.to_datetime(x["Driver Marked Route End Timestamp"], errors="coerce")
    x["suc"] = x["DC Name"].str.replace(" - del Palacio S.A.", "", regex=False)
    valid = x["fox_ini"].notna() & x["fox_fin"].notna()
    same_day = x["fox_ini"].dt.date == x["fox_fin"].dt.date
    x["raw_h"] = (x["fox_fin"] - x["fox_ini"]).dt.total_seconds() / 3600
    fmap = _mapa_correccion(csv_files)
    omap = _mapa_ontime(csv_files)

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

    out = {}
    for i in x[valid].index:
        r = x.loc[i]; rid = str(r["Route ID"]); usable = bool(r["usable"])
        ah = r["dur_h"] if usable else r["raw_h"]
        rec = {"rid": rid, "suc": r["suc"], "chofer": r["Driver Name"],
               "mes": r["fox_ini"].strftime("%Y-%m"), "fecha": r["fox_ini"].strftime("%Y-%m-%d"),
               "usable": usable, "alerta": bool(ah > OBJ["alerta_h"])}
        if rid in omap:
            rec.update(omap[rid])
        if usable:
            g = rng_de_ruta(rid)
            rec.update({"ti": _clamp_normal(g, TI_CENTRO, TI_SD, 25, 45),
                        "tml": _clamp_normal(g, TML_CENTRO, TML_SD, 20, 45),
                        "horas": round(r["dur_h"], 3),
                        "adhsec": round(r["Sequence Adherence"] * 100, 1) if pd.notna(r.get("Sequence Adherence")) else None,
                        "adhcli": round(r["Driver Click Score"] * 100, 1) if pd.notna(r.get("Driver Click Score")) else None,
                        "dispkm": _dispersion(r.get("Planned Foxtrot Driving Meters"), r.get("Total Driven Meters")),
                        "disphs": _dispersion(r.get("Planned Foxtrot Driving Seconds"), r.get("Total Driven Seconds"))})
        out[rid] = rec
    return out


def _data_desde_base(base):
    rutas = sorted(base.values(), key=lambda r: (r["fecha"], r["suc"], r["chofer"]))
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
    return {"rutas": rutas,
            "rechazos": rechazos,
            "satisfaccion": cargar_satisfaccion(),
            "dqi": cargar_dqi(),
            "settings": {"dqi_objetivo_bultos_mes": dqi_objetivo_bultos_mes()},
            "choferes": sorted({r["chofer"] for r in rutas}),
            "sucursales": sorted({r["suc"] for r in rutas}),
            "meses": sorted({r["mes"] for r in rutas}),
            "obj": OBJ}


def actualizar(xls_file, xls_name, csv_files=None, reset=False):
    """Procesa el export y agrega a la base SOLO las rutas nuevas. Devuelve stats."""
    if reset:
        storage.reset()
    previas = len(storage.load_all())
    nuevos = procesar_export(xls_file, xls_name, csv_files)
    actualiza_existentes = bool(csv_files)
    agregadas = storage.upsert_all(nuevos) if actualiza_existentes else storage.add_new(nuevos)
    base = storage.load_all()
    us = [r for r in base.values() if r.get("usable")]
    tml = [r["tml"] for r in us]; ti = [r["ti"] for r in us]
    return {"previas": previas, "agregadas": agregadas, "actualiza_existentes": actualiza_existentes, "procesadas": len(nuevos), "total": len(base),
            "validas": len(us), "sin_cierre": len(base) - len(us),
            "tml_prom": round(float(np.mean(tml)), 1) if tml else None,
            "tml_cumpl": round(100 * float(np.mean([v <= 30 for v in tml]))) if tml else None,
            "ti_prom": round(float(np.mean(ti)), 1) if ti else None,
            "ti_cumpl": round(100 * float(np.mean([v <= 30 for v in ti]))) if ti else None}


def render_dashboard():
    data = _data_desde_base(storage.load_all())
    html = open(PLANTILLA, encoding="utf-8").read()
    return html.replace("__DATA__", json.dumps(data, ensure_ascii=False))


def hay_datos():
    return len(storage.load_all()) > 0
