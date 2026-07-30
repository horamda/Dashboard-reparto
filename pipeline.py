# -*- coding: utf-8 -*-
"""
Lógica de datos del dashboard de Tiempos de reparto - Foxtrot.
La usa app.py (Flask). La persistencia (Postgres o JSON) está en storage.py.

Base incremental: cada ruta se identifica por Route ID y su TI/TML aleatorio es
determinístico por ID, así una ruta ya cargada nunca cambia de valor al actualizar.
"""

import os, json, hashlib
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

storage.init()


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


def _mapa_correccion(csv_files):
    frames = []
    for f, name in csv_files:
        try:
            c = pd.read_csv(f)
        except Exception:
            continue
        if "Route ID" not in c.columns:
            continue
        c["click"] = pd.to_datetime(c.get("Driver Click Timestamp"), errors="coerce")
        c["vs"] = pd.to_datetime(c.get("Visit Start Timestamp"), errors="coerce")
        c["visend"] = c["vs"] + pd.to_timedelta(c["Visit Duration Seconds"], "s") \
            if "Visit Duration Seconds" in c.columns else c["vs"]
        frames.append(c[["Route ID", "click", "visend"]])
    if not frames:
        return {}
    c = pd.concat(frames, ignore_index=True)
    lv = c.groupby("Route ID").agg(u1=("click", "max"), u2=("visend", "max"))
    lv["f"] = lv["u1"].fillna(lv["u2"])
    return lv["f"].to_dict()


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
    return {"rutas": rutas,
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
    agregadas = storage.add_new(nuevos)
    base = storage.load_all()
    us = [r for r in base.values() if r.get("usable")]
    tml = [r["tml"] for r in us]; ti = [r["ti"] for r in us]
    return {"previas": previas, "agregadas": agregadas, "total": len(base),
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
