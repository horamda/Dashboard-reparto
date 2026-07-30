# -*- coding: utf-8 -*-
"""
Lógica de datos del dashboard de Tiempos de reparto - Foxtrot.
La usa app.py (Flask). La persistencia (Postgres o JSON) está en storage.py.

Base incremental: cada ruta se identifica por Route ID y su TI/TML aleatorio es
determinístico por ID, así una ruta ya cargada nunca cambia de valor al actualizar.
"""

import os, json, hashlib, re
from datetime import date
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


def _norm_rechazo(row):
    fecha = str(_pick_value(row, ("fecha", "dia", "date", "Fecha", "Día"), "") or "")[:10]
    cantidad = _to_int(_pick_value(row, ("rechazos", "cantidad", "total", "count", "valor"), 0))
    motivo = str(_pick_value(row, ("motivo", "causa", "tipo", "descripcion", "descripción"), "") or "")
    suc = str(_pick_value(row, ("sucursal", "Sucursal"), RECHAZOS_SUCURSAL) or RECHAZOS_SUCURSAL)
    sid = str(_pick_value(row, ("sucursal_id", "sucursalId", "id_sucursal"), RECHAZOS_SUCURSAL_ID) or RECHAZOS_SUCURSAL_ID)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
        return None
    return {"fecha": fecha, "sucursal": suc, "sucursal_id": sid, "rechazos": cantidad, "motivo": motivo}


def importar_rechazos(desde=None, hasta=None):
    desde = desde or "2026-01-01"
    hasta = hasta or _today()
    url = RECHAZOS_API_URL + "?" + urlencode({"desde": desde, "hasta": hasta})
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=30) as res:
            ctype = res.headers.get("Content-Type", "")
            raw = res.read().decode("utf-8")
    except HTTPError as e:
        raise ValueError(f"El endpoint respondió HTTP {e.code}. URL: {url}") from e
    if "json" not in ctype.lower():
        raise ValueError(f"El endpoint no devolvió JSON. Content-Type: {ctype or 'sin Content-Type'}")
    payload = json.loads(raw)
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
    return {"desde": desde, "hasta": hasta, "url": url, "recibidos": len(recs), "guardados": guardados}


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
    rechazos = sorted(storage.load_rechazos().values(), key=lambda r: r["fecha"])
    return {"rutas": rutas,
            "rechazos": rechazos,
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
