# -*- coding: utf-8 -*-
"""
App Flask del dashboard de Tiempos de reparto - Foxtrot (Del Palacio S.A.).

Rutas:
  GET  /            -> muestra el dashboard (o la pantalla de carga si no hay datos)
  GET  /admin       -> formulario para subir el export nuevo
  POST /actualizar  -> recibe el .xls (+ CSV opcionales), actualiza la base y regenera

Persistencia: la base vive en DATA_DIR (por defecto ./data). En Railway,
montar un VOLUMEN en esa ruta para que no se borre en cada deploy.

Protección opcional: si definís la variable de entorno ADMIN_TOKEN, se pide esa
clave para subir datos.
"""

import os
import csv
import json
import secrets
import time
from datetime import date
from html import escape
from io import StringIO
from urllib.parse import urlencode
from flask import Flask, request, redirect, url_for, Response, session
import pipeline

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB
app.secret_key = os.environ.get("SECRET_KEY", os.environ.get("ADMIN_TOKEN") or secrets.token_hex(32))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", ADMIN_TOKEN)


@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

ADMIN_HTML = """<!doctype html><html lang=es><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Actualizar dashboard</title>
<style>*{{box-sizing:border-box}}body{{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#EEF1F5;color:#15233B;margin:0;line-height:1.45;-webkit-font-smoothing:antialiased}}
.box{{width:min(100% - 28px,620px);margin:6vh auto;background:#fff;border:1px solid #DCE2EA;border-radius:12px;padding:28px 30px;box-shadow:0 8px 22px rgba(21,35,59,.06)}}
h1{{font-size:20px;line-height:1.2;margin:0 0 4px}}p{{color:#657085;font-size:13.5px;margin:0 0 18px}}
label{{display:block;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.4px;color:#657085;margin:16px 0 6px}}
input[type=file],input[type=password],input[type=date]{{width:100%;min-height:42px;padding:10px 11px;border:1px solid #DCE2EA;border-radius:8px;font-size:14px;background:#fff}}
input:focus{{outline:2px solid #C77D1A;outline-offset:1px}}
.btn{{margin-top:22px;width:100%;min-height:44px;background:#15233B;color:#fff;border:0;border-radius:9px;padding:12px;font-size:15px;font-weight:650;cursor:pointer}}
.btn:hover{{background:#26334d}}
.msg{{background:#DCFCE7;border:1px solid #86EFAC;color:#166534;border-radius:9px;padding:10px 12px;font-size:13.5px;margin-bottom:16px}}
.err{{background:#FEE2E2;border:1px solid #FCA5A5;color:#991B1B}}
a{{color:#1E3A8A;font-size:13.5px}}hr{{border:0;border-top:1px solid #DCE2EA;margin:24px 0}}@media(max-width:640px){{.box{{width:min(100% - 20px,620px);margin:18px auto;padding:22px 18px}}}}</style></head>
<body><div class=box>
<h1>Actualizar dashboard</h1>
<p>Subí el export nuevo de Route Analytics y el Attempt Analytics. Las rutas existentes se actualizan con las columnas nuevas.</p>
{msg}
<form method=post action="/actualizar" enctype="multipart/form-data">
  <label>Export Route Analytics (.xls / .xlsx) *</label>
  <input type=file name=xls accept=".xls,.xlsx" required>
  <label>Archivo de visitas Foxtrot (opcional, para recuperar rutas sin cierre y calcular on time)</label>
  <input type=file name=csv accept=".csv,.xls,.xlsx" multiple>
  <label>CSV de clientes (opcional, actualiza ventanas horarias)</label>
  <input type=file name=clientes accept=".csv">
  {token_field}
  <label style="text-transform:none;font-weight:400;color:#15233B;margin-top:14px">
    <input type=checkbox name=reset value=1 style="width:auto;margin-right:6px">Rehacer la base de cero (borra lo guardado)</label>
  <button class=btn type=submit>Actualizar</button>
</form>
<p style="margin-top:18px"><a href="/inicio">Panel principal</a> · <a href="/dashboard">Dashboard</a> · <a href="/datos">Revisar datos cargados</a> · <a href="/foxtrot-calidad">Calidad Foxtrot</a> · <a href="/reporte-fichaya-foxtrot">Reporte FichaYA/Foxtrot</a> · <a href="/logout">Cerrar sesión</a></p>
<hr>
<h1>Importar rechazos</h1>
<p>Consume el endpoint CSV de rechazos diarios de Dolores y lo guarda en la base.</p>
<form method=post action="/actualizar-rechazos" enctype="multipart/form-data">
  <label>Desde</label>
  <input type=date name=desde value="2026-01-01" required>
  <label>Hasta</label>
  <input type=date name=hasta value="{hasta_default}" required>
  <label>Archivo de rechazos diarios (opcional .csv / .json / .xls / .xlsx)</label>
  <input type=file name=rechazos_file accept=".csv,.json,.xls,.xlsx,text/csv,application/json">
  <button class=btn type=submit>Importar rechazos</button>
</form>
<hr>
<h1>Importar artículos</h1>
<p>Guarda el maestro de artículos para convertir unidades a bultos en DQI.</p>
<form method=post action="/actualizar-articulos" enctype="multipart/form-data">
  <label>Archivo de artículos (.csv)</label>
  <input type=file name=articulos accept=".csv,text/csv" required>
  <button class=btn type=submit>Importar artículos</button>
</form>
<hr>
<h1>Configurar DQI</h1>
<p>Define el objetivo mensual de roturas en bultos para DQI y Team Room.</p>
<form method=post action="/configurar-dqi">
  <label>Objetivo mensual DQI (bultos)</label>
  <input name=dqi_objetivo value="{dqi_objetivo}" required>
  <button class=btn type=submit>Guardar objetivo</button>
</form>
</div></body></html>"""

LOGIN_HTML = """<!doctype html><html lang=es><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Ingresar</title>
<style>*{{box-sizing:border-box}}body{{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#EEF1F5;color:#15233B;margin:0;line-height:1.45;-webkit-font-smoothing:antialiased}}
.box{{width:min(100% - 28px,420px);margin:12vh auto;background:#fff;border:1px solid #DCE2EA;border-radius:12px;padding:28px 30px;box-shadow:0 8px 22px rgba(21,35,59,.06)}}
h1{{font-size:20px;line-height:1.2;margin:0 0 4px}}p{{color:#657085;font-size:13.5px;margin:0 0 18px}}
label{{display:block;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.4px;color:#657085;margin:16px 0 6px}}
input{{width:100%;min-height:42px;padding:10px 11px;border:1px solid #DCE2EA;border-radius:8px;font-size:14px}}
input:focus{{outline:2px solid #C77D1A;outline-offset:1px}}
.btn{{margin-top:22px;width:100%;min-height:44px;background:#15233B;color:#fff;border:0;border-radius:9px;padding:12px;font-size:15px;font-weight:650;cursor:pointer}}
.btn:hover{{background:#26334d}}
.err{{background:#FEE2E2;border:1px solid #FCA5A5;color:#991B1B;border-radius:9px;padding:10px 12px;font-size:13.5px;margin-bottom:16px}}@media(max-width:640px){{.box{{width:min(100% - 20px,420px);margin:18px auto;padding:22px 18px}}}}</style></head>
<body><div class=box>
<h1>Ingresar</h1>
<p>Acceso para actualizar datos del dashboard.</p>
{msg}
<form method=post action="/login">
  <input type=hidden name=next value="{next_url}">
  <label>Usuario</label>
  <input name=user autocomplete=username required autofocus>
  <label>Clave</label>
  <input type=password name=password autocomplete=current-password required>
  <button class=btn type=submit>Ingresar</button>
</form>
</div></body></html>"""

LANDING = """<!doctype html><html lang=es><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Dashboard de reparto</title>
<style>*{box-sizing:border-box}body{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#EEF1F5;color:#15233B;margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;line-height:1.45;-webkit-font-smoothing:antialiased}
.box{width:min(100%,460px);background:#fff;border:1px solid #DCE2EA;border-radius:12px;padding:28px 30px;text-align:center;box-shadow:0 8px 22px rgba(21,35,59,.06)}
h1{font-size:22px;line-height:1.2;margin:0 0 8px}p{color:#657085;font-size:14px;margin:0}
a{display:inline-block;margin-top:18px;background:#15233B;color:#fff;text-decoration:none;border-radius:9px;padding:12px 22px;font-weight:650}</style></head>
<body><div class=box><h1>Todavía no hay datos cargados</h1>
<p>Subí el primer export para generar el dashboard.</p>
<a href="/admin">Cargar datos</a></div></body></html>"""

DATOS_CSS = """<style>*{box-sizing:border-box}body{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#EEF1F5;color:#15233B;margin:0;line-height:1.45;-webkit-font-smoothing:antialiased}
.wrap{width:min(100% - 28px,1280px);margin:24px auto 44px}.top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:18px}
h1{font-size:24px;margin:0 0 4px}.muted{color:#657085;font-size:13.5px;margin:0}.nav{display:flex;gap:8px;flex-wrap:wrap}.nav a,.btn{background:#15233B;color:#fff;text-decoration:none;border:0;border-radius:8px;padding:10px 13px;font-size:13.5px;font-weight:650;cursor:pointer}
.nav a.secondary,.btn.secondary{background:#fff;color:#15233B;border:1px solid #DCE2EA}.msg{background:#DCFCE7;border:1px solid #86EFAC;color:#166534;border-radius:9px;padding:10px 12px;font-size:13.5px;margin:12px 0}.err{background:#FEE2E2;border-color:#FCA5A5;color:#991B1B}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}.tabs a{padding:9px 12px;border-radius:8px;border:1px solid #DCE2EA;background:#fff;color:#15233B;text-decoration:none;font-size:13.5px}.tabs a.on{background:#C77D1A;color:#fff;border-color:#C77D1A}
.tools{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0 16px}.tools input{min-height:40px;border:1px solid #DCE2EA;border-radius:8px;padding:9px 10px;font-size:14px;min-width:min(100%,320px)}
.panel{background:#fff;border:1px solid #DCE2EA;border-radius:10px;overflow:hidden}.table-wrap{overflow:auto;max-height:62vh}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid #E7ECF2;padding:9px 10px;text-align:left;vertical-align:top;white-space:nowrap}th{position:sticky;top:0;background:#F8FAFC;z-index:1;font-size:12px;text-transform:uppercase;color:#657085;letter-spacing:.2px}td.trunc{max-width:260px;overflow:hidden;text-overflow:ellipsis}
details{border-top:1px solid #E7ECF2;padding:12px 14px}summary{cursor:pointer;font-weight:700}.edit{display:grid;gap:10px;margin-top:10px}.edit-grid{display:grid;grid-template-columns:180px minmax(220px,1fr);border:1px solid #E7ECF2;border-radius:8px;overflow:hidden}.field-name{background:#F8FAFC;color:#657085;font-size:12px;font-weight:700;text-transform:uppercase}.field-name,.field-control{border-bottom:1px solid #E7ECF2;padding:9px 10px}.field-control input,.field-control textarea{width:100%;border:1px solid #DCE2EA;border-radius:7px;padding:8px 9px;font-size:13.5px}.field-control textarea{min-height:84px;font:12.5px ui-monospace,SFMono-Regular,Consolas,monospace}.actions{display:flex;gap:8px;flex-wrap:wrap}.danger{background:#991B1B}.empty{padding:22px;color:#657085}@media(max-width:760px){.top{display:block}.nav{margin-top:12px}th,td{padding:8px}.wrap{width:min(100% - 18px,1280px);margin-top:14px}.edit-grid{grid-template-columns:1fr}.field-name{border-bottom:0;padding-bottom:2px}.field-control{padding-top:2px}}</style>"""

TABLES = {
    "rutas": {"label": "Rutas Foxtrot", "key": "rid", "load": lambda: pipeline.storage.load_all(), "cols": ["rid", "fecha", "mes", "suc", "chofer", "usable", "tml", "ti", "tml_ti_origen", "fichaya_ingreso", "fichaya_egreso", "horas"]},
    "attempts": {"label": "Attempt Analytics", "key": "attempt_key", "load": lambda: pipeline.storage.load_attempts(), "cols": ["attempt_key", "Route ID", "Customer ID", "Customer Name", "Visit Start Timestamp", "Driver Click Timestamp", "Aggregate Visit Status"]},
    "clientes": {"label": "Clientes", "key": "cliente", "load": lambda: pipeline.storage.load_clientes(), "cols": ["cliente", "sucursal", "razon_social", "nombre", "horario_entrega", "ventanas"]},
    "rechazos": {"label": "Rechazos", "key": "key", "load": lambda: pipeline.storage.load_rechazos(), "cols": ["key", "fecha", "sucursal", "rechazos", "rechazo_bultos", "pct_rechazo_bultos", "origen"]},
    "rechazos_detalle": {"label": "Detalle rechazos", "key": "key", "load": lambda: pipeline.storage.load_rechazos_detalle(), "cols": ["fecha", "sucursal", "chofer", "sector", "motivo", "pedidos_rechazo", "bultos_rechazo", "hl_rechazo"]},
    "articulos": {"label": "Artículos", "key": "articulo", "load": lambda: pipeline.storage.load_articulos(), "cols": ["articulo", "descripcion", "unidades_por_bulto"]},
    "settings": {"label": "Configuración", "key": "key", "load": lambda: pipeline.storage.load_settings(), "cols": ["key", "valor"]},
}

FOXTROT_AUDIT_COLUMNS = [
    "Total Driven Meters",
    "Total Journey Seconds",
    "Actual Route Departure Time",
    "Actual Route Arrival Time",
    "Driver Marked Route Start Timestamp",
    "Driver Marked Route End Timestamp",
    "Planned Foxtrot Driving Meters",
    "Planned Foxtrot Driving Seconds",
    "Total Driven Seconds",
    "Planned Foxtrot Journey Seconds",
]

FOXTROT_AUTOFILL_RULES = {
    "Total Driven Meters": ("Planned Foxtrot Driving Meters", 1.10, "number"),
    "Total Driven Seconds": ("Planned Foxtrot Driving Seconds", 1.10, "number"),
    "Total Journey Seconds": ("Planned Foxtrot Journey Seconds", 1.10, "number"),
    "Actual Route Departure Time": ("Driver Marked Route Start Timestamp", 1.0, "timestamp"),
    "Actual Route Arrival Time": ("Driver Marked Route End Timestamp", 1.0, "timestamp"),
}

FOXTROT_COLUMN_LABELS = {
    "Total Driven Meters": "Km real",
    "Total Journey Seconds": "Jornada real",
    "Actual Route Departure Time": "Salida real",
    "Actual Route Arrival Time": "Llegada real",
    "Driver Marked Route Start Timestamp": "Inicio marcado",
    "Driver Marked Route End Timestamp": "Fin marcado",
    "Planned Foxtrot Driving Meters": "Km plan",
    "Planned Foxtrot Driving Seconds": "Manejo plan",
    "Total Driven Seconds": "Manejo real",
    "Planned Foxtrot Journey Seconds": "Jornada plan",
}


def _admin_page(msg="", err=False):
    token_field = ""
    m = f'<div class="msg{" err" if err else ""}">{msg}</div>' if msg else ""
    return ADMIN_HTML.format(
        msg=m,
        token_field=token_field,
        hasta_default=date.today().strftime("%Y-%m-%d"),
        dqi_objetivo=pipeline.dqi_objetivo_bultos_mes(),
    )


def _login_page(msg="", err=False):
    m = f'<div class="err">{msg}</div>' if msg else ""
    next_url = request.args.get("next") or request.form.get("next") or url_for("inicio")
    return LOGIN_HTML.format(msg=m, next_url=next_url)


def _is_logged_in():
    return bool(session.get("admin_logged_in"))


def _require_login():
    if not _is_logged_in():
        return redirect(url_for("login", next=request.path))
    return None


def _dashboard_response():
    if not pipeline.hay_datos():
        return Response(LANDING, mimetype="text/html")
    return Response(pipeline.render_dashboard(), mimetype="text/html")


def _main_page():
    blocked = _require_login()
    if blocked:
        return blocked
    cards = [
        ("Dashboard operativo", "Indicadores principales, Team Room, DPO, rechazos, OTIF y calidad.", "/dashboard", "Abrir dashboard"),
        ("Actualizar datos", "Carga de Route Analytics, visitas Foxtrot, clientes, rechazos y artículos.", "/admin", "Ir a admin"),
        ("Datos cargados", "Revisión y edición directa de rutas, clientes, rechazos, artículos y configuración.", "/datos", "Revisar datos"),
        ("Calidad Foxtrot", "Auditoría de columnas vacías y autocompletado de campos Foxtrot.", "/foxtrot-calidad", "Ver calidad"),
        ("Reporte FichaYA / Foxtrot", "Empleado, fichada de ingreso, inicio Foxtrot, TML, fin Foxtrot, salida y TI.", "/reporte-fichaya-foxtrot", "Abrir reporte"),
        ("Asociar nombres", "Mapa entre nombres de choferes Foxtrot y nombres de empleados FichaYA.", "/asociar-fichaya", "Asociar"),
    ]
    items = "".join(
        f"""<a class=hub-card href="{escape(url)}"><span>{escape(title)}</span><p>{escape(desc)}</p><b>{escape(cta)}</b></a>"""
        for title, desc, url, cta in cards
    )
    return f"""<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Panel principal</title>
<style>*{{box-sizing:border-box}}body{{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#EEF1F5;color:#15233B;margin:0;line-height:1.45;-webkit-font-smoothing:antialiased}}.wrap{{width:min(100% - 28px,1180px);margin:28px auto 44px}}.top{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:22px}}h1{{font-size:28px;line-height:1.1;margin:0 0 6px}}.muted{{color:#657085;font-size:14px;margin:0}}.nav{{display:flex;gap:8px;flex-wrap:wrap}}.nav a{{background:#fff;color:#15233B;border:1px solid #DCE2EA;text-decoration:none;border-radius:8px;padding:10px 13px;font-size:13.5px;font-weight:650}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}.hub-card{{display:block;background:#fff;border:1px solid #DCE2EA;border-left:5px solid #C77D1A;border-radius:10px;padding:18px 18px 16px;text-decoration:none;color:#15233B;min-height:154px;box-shadow:0 8px 22px rgba(21,35,59,.045)}}.hub-card:hover{{border-color:#C77D1A;box-shadow:0 10px 24px rgba(21,35,59,.08);transform:translateY(-1px)}}.hub-card span{{display:block;font-size:17px;font-weight:800;margin-bottom:7px}}.hub-card p{{color:#657085;font-size:13.5px;margin:0 0 18px}}.hub-card b{{display:inline-block;background:#15233B;color:#fff;border-radius:8px;padding:9px 12px;font-size:13px}}@media(max-width:720px){{.top{{display:block}}.nav{{margin-top:12px}}h1{{font-size:24px}}.wrap{{width:min(100% - 18px,1180px);margin-top:18px}}}}</style></head>
<body><div class=wrap><div class=top><div><h1>Panel principal</h1><p class=muted>Accesos del sistema de reparto, Foxtrot y FichaYA.</p></div><div class=nav><a href="/logout">Cerrar sesión</a></div></div><div class=grid>{items}</div></div></body></html>"""


def _short_value(value):
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    return str(value)


def _record_matches(rec, q):
    if not q:
        return True
    return q.lower() in json.dumps(rec, ensure_ascii=False).lower()


def _field_input(name, value):
    raw = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else _short_value(value)
    escaped = escape(raw)
    if isinstance(value, bool):
        checked = " checked" if value else ""
        return f'<input type=hidden name="field__{escape(name)}" value="false"><input type=checkbox name="field__{escape(name)}" value="true"{checked}>'
    if isinstance(value, (dict, list)):
        return f'<textarea name="field__{escape(name)}">{escaped}</textarea>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<input name="field__{escape(name)}" value="{escaped}" inputmode="decimal">'
    return f'<input name="field__{escape(name)}" value="{escaped}">'


def _edit_fields(rec):
    fields = "".join(
        f'<div class=field-name>{escape(str(name))}</div><div class=field-control>{_field_input(str(name), value)}</div>'
        for name, value in rec.items()
    )
    return f"<div class=edit-grid>{fields}</div>"


def _coerce_field(value, previous):
    if isinstance(previous, bool):
        return value == "true"
    if isinstance(previous, int) and not isinstance(previous, bool):
        return int(value) if str(value).strip() != "" else None
    if isinstance(previous, float):
        return float(value) if str(value).strip() != "" else None
    if isinstance(previous, (dict, list)):
        return json.loads(value) if str(value).strip() else ([] if isinstance(previous, list) else {})
    if previous is None:
        text = str(value).strip()
        if text == "":
            return None
        try:
            return json.loads(text)
        except Exception:
            return value
    return value


def _raw_value(rec, col):
    return (rec.get("raw_foxtrot") or {}).get(col)


def _is_blank(value):
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or text.lower() in ("nan", "none", "null", "nat")


def _to_float_or_none(value):
    if _is_blank(value):
        return None
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _parse_dt(value):
    if _is_blank(value):
        return None
    try:
        ts = pipeline.pd.to_datetime(value, errors="coerce")
        if pipeline.pd.isna(ts):
            return None
        return ts
    except Exception:
        return None


def _dispersion(plan, real):
    plan = _to_float_or_none(plan)
    real = _to_float_or_none(real)
    if plan is None or real is None or plan <= 0:
        return None
    return round((real - plan) / plan * 100, 1)


def _sync_route_from_raw(rec):
    raw = rec.get("raw_foxtrot") or {}
    ini = _parse_dt(raw.get("Driver Marked Route Start Timestamp"))
    fin = _parse_dt(raw.get("Driver Marked Route End Timestamp"))
    if ini is not None:
        rec["fecha"] = ini.strftime("%Y-%m-%d")
        rec["mes"] = ini.strftime("%Y-%m")
        rec["anio"] = ini.strftime("%Y")
        rec["inicio_foxtrot"] = ini.strftime("%H:%M")
    if fin is not None:
        rec["fin_foxtrot"] = fin.strftime("%H:%M")
    if ini is not None and fin is not None:
        horas = (fin - ini).total_seconds() / 3600
        rec["horas"] = round(horas, 3)
        rec["usable"] = 0 < horas <= 14
        rec["alerta"] = horas > pipeline.OBJ["alerta_h"]
    km_plan = raw.get("Planned Foxtrot Driving Meters")
    km_real = raw.get("Total Driven Meters")
    hs_plan = raw.get("Planned Foxtrot Driving Seconds")
    hs_real = raw.get("Total Driven Seconds")
    rec["disp_km_plan"] = _to_float_or_none(km_plan)
    rec["disp_km_real"] = _to_float_or_none(km_real)
    rec["disp_hs_plan"] = _to_float_or_none(hs_plan)
    rec["disp_hs_real"] = _to_float_or_none(hs_real)
    rec["dispkm"] = _dispersion(km_plan, km_real)
    rec["disphs"] = _dispersion(hs_plan, hs_real)
    return rec


def _format_autofill_number(value, factor):
    num = _to_float_or_none(value)
    if num is None:
        return None
    out = num * factor
    return str(int(round(out))) if abs(out - round(out)) < 0.000001 else str(round(out, 3))


def _format_autofill_timestamp(value):
    ts = _parse_dt(value)
    if ts is None:
        return None
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _autofill_foxtrot_missing():
    started = time.perf_counter()
    if pipeline.storage.backend_name() == "postgres" and hasattr(pipeline.storage, "fast_autofill_foxtrot_missing"):
        st = pipeline.storage.fast_autofill_foxtrot_missing()
        st["segundos"] = time.perf_counter() - started
        return st
    base = pipeline.storage.load_all()
    changed_routes = 0
    changed_cells = 0
    by_col = {col: 0 for col in FOXTROT_AUTOFILL_RULES}
    updates = {}
    for rid, original in base.items():
        rec = dict(original)
        raw = dict(rec.get("raw_foxtrot") or {})
        route_changed = False
        for target, (source, factor, kind) in FOXTROT_AUTOFILL_RULES.items():
            if not _is_blank(raw.get(target)):
                continue
            src = raw.get(source)
            if _is_blank(src):
                continue
            value = _format_autofill_number(src, factor) if kind == "number" else _format_autofill_timestamp(src)
            if value is None:
                continue
            raw[target] = value
            route_changed = True
            changed_cells += 1
            by_col[target] += 1
        if route_changed:
            rec["raw_foxtrot"] = raw
            rec["rid"] = rid
            _sync_route_from_raw(rec)
            updates[rid] = rec
            changed_routes += 1
    if updates:
        pipeline.storage.upsert_all(updates, count_new=False)
    elapsed = time.perf_counter() - started
    return {"rutas": changed_routes, "celdas": changed_cells, "por_columna": by_col, "segundos": elapsed}


def _raw_filter_select(col, value):
    opts = [("", "Todos"), ("empty", "Vacíos"), ("present", "Con dato")]
    label = FOXTROT_COLUMN_LABELS.get(col, col)
    return (
        f'<label title="{escape(col)}">{escape(label)}<select name="raw__{escape(col)}">'
        + "".join(f'<option value="{v}"{" selected" if value == v else ""}>{label}</option>' for v, label in opts)
        + "</select></label>"
    )


def _foxtrot_calidad_page(q="", msg="", err=False):
    base = pipeline.storage.load_all()
    filters = {col: request.args.get(f"raw__{col}", "") for col in FOXTROT_AUDIT_COLUMNS}
    rows = list(base.values())
    if q:
        rows = [r for r in rows if _record_matches(r, q)]
    for col, mode in filters.items():
        if mode == "empty":
            rows = [r for r in rows if _is_blank(_raw_value(r, col))]
        elif mode == "present":
            rows = [r for r in rows if not _is_blank(_raw_value(r, col))]
    rows = sorted(rows, key=lambda r: (r.get("fecha") or "", r.get("suc") or "", r.get("chofer") or ""))[:300]
    stats = []
    total = len(base) or 1
    for col in FOXTROT_AUDIT_COLUMNS:
        missing = sum(1 for r in base.values() if _is_blank(_raw_value(r, col)))
        pct = round(missing / total * 100, 1)
        severity = "bad" if pct >= 50 else ("warn" if pct > 0 else "ok")
        stats.append(
            f'<div class="stat {severity}" title="{escape(col)}"><span>{escape(FOXTROT_COLUMN_LABELS.get(col, col))}</span>'
            f'<b>{missing}</b><small>{pct}% vacíos</small></div>'
        )
    filter_controls = "".join(_raw_filter_select(col, filters[col]) for col in FOXTROT_AUDIT_COLUMNS)
    body = ""
    for rec in rows:
        raw = rec.get("raw_foxtrot") or {}
        rid = rec.get("rid") or raw.get("Route ID") or ""
        form_id = "f_" + "".join(ch if ch.isalnum() else "_" for ch in str(rid))
        inputs = "".join(
            f'<td><input form="{escape(form_id)}" name="raw__{escape(col)}" value="{escape(_short_value(raw.get(col)))}"></td>'
            for col in FOXTROT_AUDIT_COLUMNS
        )
        body += f"""<tr>
<td>{escape(rec.get("fecha") or "")}</td><td>{escape(rec.get("suc") or "")}</td><td>{escape(rec.get("chofer") or "")}</td><td class=route-id title="{escape(str(rid))}">{escape(str(rid))}</td>
{inputs}
<td><form id="{escape(form_id)}" method=post action="/foxtrot-calidad/guardar"><input type=hidden name=rid value="{escape(str(rid))}"><button class=btn type=submit>Guardar</button></form></td>
</tr>"""
    if not body:
        body = f'<tr><td class=empty colspan="{len(FOXTROT_AUDIT_COLUMNS) + 5}">No hay rutas con esos filtros.</td></tr>'
    alert = f'<div class="msg{" err" if err else ""}">{escape(msg)}</div>' if msg else ""
    header_inputs = "".join(f'<th title="{escape(col)}">{escape(FOXTROT_COLUMN_LABELS.get(col, col))}</th>' for col in FOXTROT_AUDIT_COLUMNS)
    return f"""<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Calidad Foxtrot</title>{DATOS_CSS}
<style>
.quality-shell{{display:grid;gap:16px}}
.stats-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:16px}}
.stat{{background:#fff;border:1px solid #DCE2EA;border-left:4px solid #94A3B8;border-radius:9px;padding:10px 11px;min-height:88px}}
.stat span{{display:block;color:#657085;font-size:11px;font-weight:800;text-transform:uppercase;line-height:1.2;min-height:28px}}
.stat b{{display:block;font-size:24px;line-height:1;margin-top:6px}}.stat small{{display:block;color:#657085;margin-top:4px}}
.stat.bad{{border-left-color:#DC2626}}.stat.warn{{border-left-color:#C77D1A}}.stat.ok{{border-left-color:#16A34A}}
.autofill-box{{background:#FFF7ED;border:1px solid #FDBA74;border-left:5px solid #C77D1A;border-radius:10px;padding:15px 16px;display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap}}
.autofill-box b{{display:block;margin-bottom:3px;font-size:15px}}.autofill-box p{{margin:0;color:#657085;font-size:13px;max-width:760px}}
.btn.autofill{{background:#C77D1A}}.btn:disabled{{opacity:.65;cursor:wait}}
.filter-panel{{background:#fff;border:1px solid #DCE2EA;border-radius:10px;margin-bottom:16px;overflow:hidden}}
.filter-panel summary{{padding:12px 14px;border:0;list-style:none;display:flex;justify-content:space-between;gap:12px;align-items:center}}
.filter-panel summary::-webkit-details-marker{{display:none}}.filter-panel summary b{{font-size:13px}}.filter-panel summary span{{color:#657085;font-size:12.5px}}
.tools.raw{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));align-items:end;padding:0 14px 14px;margin:0}}
.tools.raw label{{font-size:11px;font-weight:800;color:#657085;text-transform:uppercase}}.tools.raw select{{width:100%;min-height:38px;border:1px solid #DCE2EA;border-radius:8px;padding:8px;background:#fff}}
.tools.raw .filter-actions{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.table-wrap.foxtrot{{max-height:68vh}}.table-wrap.foxtrot table{{font-size:12px}}
.table-wrap.foxtrot th,.table-wrap.foxtrot td{{padding:7px 8px}}
.table-wrap.foxtrot th{{white-space:normal;line-height:1.15;min-width:112px}}
.table-wrap.foxtrot th:nth-child(1),.table-wrap.foxtrot td:nth-child(1){{position:sticky;left:0;background:#fff;z-index:3;min-width:82px}}
.table-wrap.foxtrot th:nth-child(2),.table-wrap.foxtrot td:nth-child(2){{position:sticky;left:82px;background:#fff;z-index:3;min-width:118px}}
.table-wrap.foxtrot th:nth-child(3),.table-wrap.foxtrot td:nth-child(3){{position:sticky;left:200px;background:#fff;z-index:3;min-width:170px;box-shadow:1px 0 0 #E7ECF2}}
.table-wrap.foxtrot thead th:nth-child(-n+3){{background:#F8FAFC;z-index:4}}
td input{{width:150px;border:1px solid #DCE2EA;border-radius:7px;padding:7px 8px;font-size:12px}}td input:focus{{outline:2px solid #C77D1A;outline-offset:1px}}
.route-id{{max-width:120px;overflow:hidden;text-overflow:ellipsis;color:#657085}}
</style></head>
<body><div class=wrap><div class=top><div><h1>Calidad de columnas Foxtrot</h1><p class=muted>Filtrá campos vacíos/con dato y completá valores faltantes por ruta. Al guardar se recalculan inicio, fin, horas y dispersiones si aplica.</p></div>
<div class=nav><a class=secondary href="/dashboard">Dashboard</a><a class=secondary href="/datos">Datos</a><a href="/admin">Admin</a></div></div>{alert}
<div class=quality-shell>
<div class=stats-grid>{"".join(stats)}</div>
<div class=autofill-box><div><b>Autocompletar campos vacíos</b><p>Usa planificado x 1,10 y timestamps marcados. No pisa datos existentes.</p></div>
 <form method=post action="/foxtrot-calidad/autocompletar" onsubmit="if(!confirm('Esto completará solo campos vacíos usando datos planificados o timestamps disponibles. No pisa datos existentes. ¿Continuar?'))return false;this.querySelector('button').textContent='Procesando...';this.querySelector('button').disabled=true;return true">
  <button class="btn autofill" type=submit>Autocompletar vacíos Foxtrot</button>
 </form></div>
<details class=filter-panel open><summary><b>Filtros</b><span>Buscar rutas y elegir campos vacíos o con dato</span></summary>
<form class="tools raw" method=get action="/foxtrot-calidad"><label>Buscar<input name=q value="{escape(q)}" placeholder="Chofer, fecha, ruta..."></label>{filter_controls}<div class=filter-actions><button class=btn type=submit>Filtrar</button><a class="btn secondary" href="/foxtrot-calidad">Limpiar</a></div></form></details>
<div class=panel><div class="table-wrap foxtrot"><table><thead><tr><th>Fecha</th><th>Sucursal</th><th>Chofer</th><th>Route ID</th>{header_inputs}<th>Acción</th></tr></thead><tbody>{body}</tbody></table></div></div>
<p class=muted style="margin-top:12px">Se muestran hasta 300 rutas. Para tiempos usá el formato que viene de Foxtrot o un timestamp reconocible, por ejemplo 2026-01-12 14:36:00.</p>
</div></div></body></html>"""


def _datos_page(table="rutas", q="", msg="", err=False, edit_key=""):
    if table not in TABLES:
        table = "rutas"
    spec = TABLES[table]
    base = spec["load"]()
    rows = sorted(base.items(), key=lambda item: str(item[0]))
    if q:
        rows = [(k, v) for k, v in rows if _record_matches(v, q)]
    rows = rows[:500]
    tabs = "".join(
        f'<a class="{"on" if name == table else ""}" href="/datos?tabla={name}">{escape(cfg["label"])} ({len(cfg["load"]())})</a>'
        for name, cfg in TABLES.items()
    )
    alert = f'<div class="msg{" err" if err else ""}">{escape(msg)}</div>' if msg else ""
    header = "".join(f"<th>{escape(col)}</th>" for col in spec["cols"]) + "<th>Acciones</th>"
    body = ""
    for key, rec in rows:
        cells = "".join(f'<td class="trunc">{escape(_short_value(rec.get(col)))}</td>' for col in spec["cols"])
        opened = " open" if edit_key == str(key) else ""
        editor = f"""<details{opened}><summary>Editar {escape(str(key))}</summary>
<form class=edit method=post action="/datos/guardar">
  <input type=hidden name=tabla value="{escape(table)}"><input type=hidden name=clave value="{escape(str(key))}">
  {_edit_fields(rec)}
  <div class=actions><button class=btn type=submit>Guardar</button></div>
</form></details>"""
        delete_form = f"""<form method=post action="/datos/borrar" onsubmit="return confirm('¿Borrar este registro?')">
<input type=hidden name=tabla value="{escape(table)}"><input type=hidden name=clave value="{escape(str(key))}">
<button class="btn danger" type=submit>Borrar</button></form>"""
        body += f"<tr>{cells}<td><div class=actions><a class=\"btn secondary\" href=\"/datos?tabla={escape(table)}&editar={escape(str(key))}\">Editar</a>{delete_form}</div></td></tr><tr><td colspan=\"{len(spec['cols']) + 1}\">{editor}</td></tr>"
    if not body:
        body = f'<tr><td class=empty colspan="{len(spec["cols"]) + 1}">No hay registros para mostrar.</td></tr>'
    return f"""<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Datos cargados</title>{DATOS_CSS}</head>
<body><div class=wrap><div class=top><div><h1>Datos cargados</h1><p class=muted>Revisión y edición directa de las tablas usadas por el dashboard.</p></div>
<div class=nav><a class=secondary href="/dashboard">Dashboard</a><a class=secondary href="/foxtrot-calidad">Calidad Foxtrot</a><a class=secondary href="/reporte-fichaya-foxtrot">Reporte FichaYA/Foxtrot</a><a class=secondary href="/admin">Admin</a><a href="/logout">Salir</a></div></div>{alert}
<div class=tabs>{tabs}</div><form class=tools method=get action="/datos"><input type=hidden name=tabla value="{escape(table)}"><input name=q value="{escape(q)}" placeholder="Buscar en esta tabla"><button class=btn type=submit>Buscar</button><a class="btn secondary" href="/datos?tabla={escape(table)}">Limpiar</a></form>
<div class=panel><div class=table-wrap><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div></div>
<p class=muted style="margin-top:12px">Se muestran hasta 500 registros por búsqueda. Editar JSON incorrecto puede afectar el dashboard.</p>
</div></body></html>"""


def _time_to_label(value):
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return str(value or "")[:5]


def _route_time(value):
    return pipeline._parse_hora_fichaya(value)


def _fichaya_name_map():
    rec = (pipeline.storage.load_settings().get("fichaya_nombre_map") or {})
    raw = rec.get("valor") if isinstance(rec, dict) else rec
    return raw if isinstance(raw, dict) else {}


def _fichaya_empleados():
    rec = (pipeline.storage.load_settings().get("fichaya_empleados") or {})
    raw = rec.get("valor") if isinstance(rec, dict) else rec
    return raw if isinstance(raw, dict) else {}


def _import_fichaya_empleados(file_obj):
    if hasattr(file_obj, "seek"):
        file_obj.seek(0)
    wb = pipeline.load_workbook(file_obj, read_only=True, data_only=True)
    ws = wb["Empleados"] if "Empleados" in wb.sheetnames else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    header_idx = None
    for i, row in enumerate(rows):
        vals = [str(v or "").strip().lower() for v in row]
        if "legajo" in vals and "apellido" in vals and "nombre" in vals:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("No encontré columnas legajo, apellido y nombre.")
    header = [str(v or "").strip().lower() for v in rows[header_idx]]
    idx = {name: i for i, name in enumerate(header) if name}
    empleados = {}
    for row in rows[header_idx + 1:]:
        legajo = pipeline._norm_id(row[idx["legajo"]] if idx.get("legajo") is not None and idx["legajo"] < len(row) else "")
        if not legajo or not legajo.isdigit():
            continue
        apellido = str(row[idx["apellido"]] if idx.get("apellido") is not None and idx["apellido"] < len(row) and row[idx["apellido"]] is not None else "").strip()
        nombre = str(row[idx["nombre"]] if idx.get("nombre") is not None and idx["nombre"] < len(row) and row[idx["nombre"]] is not None else "").strip()
        nombre_completo = " ".join(x for x in [apellido, nombre] if x).strip()
        empleados[legajo] = {
            "legajo": legajo,
            "nombre": nombre_completo,
            "sucursal": str(row[idx["sucursal_nombre"]] if idx.get("sucursal_nombre") is not None and idx["sucursal_nombre"] < len(row) and row[idx["sucursal_nombre"]] is not None else "").strip(),
            "puesto": str(row[idx["puesto_nombre"]] if idx.get("puesto_nombre") is not None and idx["puesto_nombre"] < len(row) and row[idx["puesto_nombre"]] is not None else "").strip(),
            "estado": str(row[idx["estado"]] if idx.get("estado") is not None and idx["estado"] < len(row) and row[idx["estado"]] is not None else "").strip(),
        }
    pipeline.storage.save_setting("fichaya_empleados", {"valor": empleados})
    return empleados


def _fichaya_lookup_ref(foxtrot_name, mapping=None, empleados=None):
    mapping = mapping if mapping is not None else _fichaya_name_map()
    empleados = empleados if empleados is not None else _fichaya_empleados()
    entry = mapping.get(pipeline._norm_persona_key(foxtrot_name))
    if isinstance(entry, dict):
        legajo = pipeline._norm_id(entry.get("legajo"))
        nombre = entry.get("nombre") or (empleados.get(legajo) or {}).get("nombre") or ""
        return {"legajo": legajo, "nombre": nombre or foxtrot_name or ""}
    if isinstance(entry, str) and entry:
        emp = empleados.get(pipeline._norm_id(entry))
        if emp:
            return {"legajo": emp["legajo"], "nombre": emp.get("nombre") or foxtrot_name or ""}
        return {"legajo": "", "nombre": entry}
    return {"legajo": "", "nombre": foxtrot_name or ""}


def _fichaya_report_rows(desde="2026-08-01", hasta=None, suc="", chofer=""):
    hasta = hasta or date.today().strftime("%Y-%m-%d")
    base = list(pipeline.storage.load_all().values())
    rows = [
        r for r in base
        if r.get("usable") and (r.get("fecha") or "") >= desde and (r.get("fecha") or "") <= hasta
    ]
    if suc:
        rows = [r for r in rows if (r.get("suc") or "") == suc]
    if chofer:
        rows = [r for r in rows if pipeline._norm_persona_key(r.get("chofer")) == pipeline._norm_persona_key(chofer)]
    rows = sorted(rows, key=lambda r: (r.get("fecha") or "", r.get("suc") or "", r.get("chofer") or "", r.get("inicio_foxtrot") or ""))

    fichadas, warning = {}, ""
    fichaya_live_ok = False
    if rows:
        try:
            fechas = [r.get("fecha") for r in rows if r.get("fecha")]
            fichadas = pipeline.cargar_fichadas(min(fechas), max(fechas)) if fechas else {}
            fichaya_live_ok = bool(fichadas)
            if not fichaya_live_ok:
                warning = "No se recibieron fichadas desde FichaYA en vivo. Revisar credenciales, disponibilidad del servicio o si el rango tiene marcas cargadas."
        except Exception as exc:
            warning = f"No se pudo consultar FichaYA en vivo ({exc}). El reporte requiere conexión activa a FichaYA para calcular TML/TI."

    mapping = _fichaya_name_map()
    empleados = _fichaya_empleados()
    out = []
    for rec in rows:
        fecha = rec.get("fecha") or ""
        nombre = rec.get("chofer") or ""
        ref = _fichaya_lookup_ref(nombre, mapping, empleados)
        legajo_fichaya = ref.get("legajo", "")
        nombre_fichaya = ref.get("nombre", "")
        item = None
        if fichadas and legajo_fichaya:
            item = fichadas.get((fecha, "LEGAJO:" + legajo_fichaya))
        if fichadas and item is None:
            item = fichadas.get((fecha, pipeline._norm_persona_key(nombre_fichaya)))
        ingreso = item.get("ingreso") if item else None
        egreso = item.get("egreso") if item else None
        ini = _route_time(rec.get("inicio_foxtrot"))
        fin = _route_time(rec.get("fin_foxtrot"))
        tml = pipeline._minutos_entre(ingreso, ini)
        ti = pipeline._minutos_entre(fin, egreso)
        tml_ok = tml is not None and 0 <= tml <= 240
        ti_ok = ti is not None and 0 <= ti <= 240
        estado = "OK" if tml_ok and ti_ok else "Faltan fichadas FichaYA"
        if rows and not fichaya_live_ok:
            estado = "Sin conexión FichaYA"
        if (tml is not None and not tml_ok) or (ti is not None and not ti_ok):
            estado = "Revisar"
        out.append({
            "fecha": fecha,
            "sucursal": rec.get("suc") or "",
            "empleado": nombre,
            "legajo_fichaya": legajo_fichaya,
            "empleado_fichaya": nombre_fichaya if nombre_fichaya != nombre else "",
            "fichada_ingreso": _time_to_label(ingreso),
            "inicio_foxtrot": rec.get("inicio_foxtrot") or "",
            "tml": tml if tml_ok else "",
            "finalizacion_foxtrot": rec.get("fin_foxtrot") or "",
            "fichada_salida": _time_to_label(egreso),
            "ti": ti if ti_ok else "",
            "route_id": rec.get("rid") or "",
            "estado": estado,
        })
    return out, warning


def _fichaya_report_page():
    blocked = _require_login()
    if blocked:
        return blocked
    desde = request.args.get("desde") or "2026-08-01"
    hasta = request.args.get("hasta") or date.today().strftime("%Y-%m-%d")
    suc = request.args.get("suc") or ""
    chofer = request.args.get("chofer") or ""
    all_rows = [r for r in pipeline.storage.load_all().values() if r.get("usable") and (r.get("fecha") or "") >= "2026-08-01"]
    sucs = sorted({r.get("suc") for r in all_rows if r.get("suc")})
    choferes = sorted({r.get("chofer") for r in all_rows if r.get("chofer")})
    rows, warning = _fichaya_report_rows(desde, hasta, suc, chofer)
    csv_qs = urlencode({"desde": desde, "hasta": hasta, "suc": suc, "chofer": chofer})
    opts_suc = '<option value="">Todas</option>' + ''.join(f'<option value="{escape(x)}"{" selected" if x == suc else ""}>{escape(x)}</option>' for x in sucs)
    opts_cho = '<option value="">Todos</option>' + ''.join(f'<option value="{escape(x)}"{" selected" if x == chofer else ""}>{escape(x)}</option>' for x in choferes)
    body = "".join(
        "<tr>"
        f"<td>{escape(r['fecha'])}</td><td>{escape(r['sucursal'])}</td><td>{escape(r['empleado'])}</td><td>{escape(r['legajo_fichaya'])}</td><td>{escape(r['empleado_fichaya'])}</td>"
        f"<td>{escape(r['fichada_ingreso'])}</td><td>{escape(r['inicio_foxtrot'])}</td><td class=r>{escape(str(r['tml']))}</td>"
        f"<td>{escape(r['finalizacion_foxtrot'])}</td><td>{escape(r['fichada_salida'])}</td><td class=r>{escape(str(r['ti']))}</td>"
        f"<td>{escape(r['estado'])}</td><td class=trunc>{escape(r['route_id'])}</td></tr>"
        for r in rows
    ) or '<tr><td class=empty colspan=13>No hay rutas desde agosto con esos filtros.</td></tr>'
    alert = f'<div class="msg err">{escape(warning)}</div>' if warning else ""
    return f"""<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Reporte FichaYA Foxtrot</title>{DATOS_CSS}
<style>.tools select,.tools input{{min-height:40px;border:1px solid #DCE2EA;border-radius:8px;padding:9px 10px;background:#fff}}.r{{font-weight:750}}.ok{{color:#166534}}.bad{{color:#991B1B}}</style></head>
<body><div class=wrap><div class=top><div><h1>Reporte FichaYA + Foxtrot</h1><p class=muted>Desde agosto 2026. TML = inicio Foxtrot - fichada ingreso. TI = fichada salida - finalización Foxtrot.</p></div>
<div class=nav><a class=secondary href="/dashboard">Dashboard</a><a class=secondary href="/datos">Datos</a><a href="/admin">Admin</a></div></div>{alert}
<form class=tools method=get action="/reporte-fichaya-foxtrot">
<label>Desde <input type=date name=desde value="{escape(desde)}"></label>
<label>Hasta <input type=date name=hasta value="{escape(hasta)}"></label>
<label>Sucursal <select name=suc>{opts_suc}</select></label>
<label>Chofer <select name=chofer>{opts_cho}</select></label>
<button class=btn type=submit>Filtrar</button><a class="btn secondary" href="/reporte-fichaya-foxtrot">Limpiar</a><a class="btn secondary" href="/asociar-fichaya">Asociar nombres</a><a class=btn href="/reporte-fichaya-foxtrot.csv?{csv_qs}">Descargar CSV</a>
</form>
<div class=panel><div class=table-wrap><table><thead><tr><th>Fecha</th><th>Sucursal</th><th>Empleado Foxtrot</th><th>Legajo FichaYA</th><th>Empleado FichaYA</th><th>Fichada ingreso</th><th>Inicio Foxtrot</th><th>TML</th><th>Finalización Foxtrot</th><th>Fichada salida</th><th>TI</th><th>Estado</th><th>Route ID</th></tr></thead><tbody>{body}</tbody></table></div></div>
<p class=muted style="margin-top:12px">Filas: {len(rows)}. Si faltan credenciales FichaYA o marcas del empleado, las fichadas quedan vacías.</p>
</div></body></html>"""


def _fichaya_mapping_page(msg="", err=False):
    blocked = _require_login()
    if blocked:
        return blocked
    mapping = _fichaya_name_map()
    empleados = _fichaya_empleados()
    rutas = [
        r for r in pipeline.storage.load_all().values()
        if r.get("usable") and (r.get("fecha") or "") >= "2026-08-01" and r.get("chofer")
    ]
    choferes = sorted({r.get("chofer") for r in rutas})
    candidates = []
    try:
        if rutas:
            fechas = [r.get("fecha") for r in rutas if r.get("fecha")]
            fichadas = pipeline.cargar_fichadas(min(fechas), max(fechas))
            candidates = sorted({name for _, name in fichadas.keys()})
    except Exception:
        candidates = []
    emp_options = "".join(
        f'<option value="{escape(leg)}">{escape(leg)} · {escape(emp.get("nombre", ""))} · {escape(emp.get("sucursal", ""))}</option>'
        for leg, emp in sorted(empleados.items(), key=lambda kv: kv[1].get("nombre", ""))
    )
    datalist = '<datalist id="fichayaNames">' + ''.join(f'<option value="{escape(x)}"></option>' for x in candidates) + '</datalist>'
    body = ""
    for name in choferes:
        norm = pipeline._norm_persona_key(name)
        mapped = mapping.get(norm, {})
        mapped_legajo = pipeline._norm_id(mapped.get("legajo") if isinstance(mapped, dict) else mapped)
        mapped_name = (empleados.get(mapped_legajo) or {}).get("nombre", "")
        body += f"""<tr><td>{escape(name)}</td><td><select name="map__{escape(norm)}"><option value="">Usar nombre Foxtrot</option>{emp_options.replace('value="' + escape(mapped_legajo) + '"', 'value="' + escape(mapped_legajo) + '" selected', 1) if mapped_legajo else emp_options}</select><small>{escape(mapped_name)}</small></td></tr>"""
    if not body:
        body = '<tr><td class=empty colspan=2>No hay choferes Foxtrot desde agosto.</td></tr>'
    alert = f'<div class="msg{" err" if err else ""}">{escape(msg)}</div>' if msg else ""
    return f"""<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Asociar nombres FichaYA</title>{DATOS_CSS}
<style>td select{{width:100%;min-width:300px;border:1px solid #DCE2EA;border-radius:8px;padding:8px 10px;background:#fff}}td small{{display:block;color:#657085;margin-top:4px}}.panel{{max-width:960px}}.upload{{max-width:960px;background:#fff;border:1px solid #DCE2EA;border-radius:10px;padding:14px;margin-bottom:16px}}</style></head>
<body><div class=wrap><div class=top><div><h1>Asociar nombres Foxtrot / FichaYA</h1><p class=muted>Relacioná cada chofer de Foxtrot contra el legajo de FichaYA. El reporte busca fichadas por legajo primero.</p></div>
<div class=nav><a class=secondary href="/reporte-fichaya-foxtrot">Reporte</a><a class=secondary href="/dashboard">Dashboard</a><a href="/admin">Admin</a></div></div>{alert}
<form class=upload method=post action="/asociar-fichaya/importar" enctype="multipart/form-data"><b>Importar empleados FichaYA</b><p class=muted>Subí el Excel exportado desde FichaYA para cargar legajos y nombres.</p><input type=file name=empleados accept=".xlsx,.xls" required> <button class=btn type=submit>Importar empleados</button></form>
<form method=post action="/asociar-fichaya/guardar">{datalist}<div class=panel><div class=table-wrap><table><thead><tr><th>Nombre Foxtrot</th><th>Legajo / empleado FichaYA</th></tr></thead><tbody>{body}</tbody></table></div></div>
<button class=btn type=submit style="margin-top:16px">Guardar asociaciones</button></form>
<p class=muted style="margin-top:12px">Empleados FichaYA cargados: {len(empleados)}. Candidatos por fichadas disponibles: {len(candidates)}. El reporte usa esta asociación al calcular TML/TI.</p>
</div></body></html>"""


@app.route("/")
def home():
    if _is_logged_in():
        return redirect(url_for("inicio"))
    return redirect(url_for("login"))


@app.route("/inicio")
def inicio():
    return _main_page()


@app.route("/dashboard")
def dashboard():
    return _dashboard_response()


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not ADMIN_PASSWORD:
            return Response(_login_page("Falta configurar ADMIN_PASSWORD en Railway.", err=True), mimetype="text/html", status=500)
        user = request.form.get("user", "")
        password = request.form.get("password", "")
        if secrets.compare_digest(user, ADMIN_USER) and secrets.compare_digest(password, ADMIN_PASSWORD):
            session["admin_logged_in"] = True
            return redirect(request.form.get("next") or url_for("inicio"))
        return Response(_login_page("Usuario o clave incorrectos.", err=True), mimetype="text/html", status=403)
    return Response(_login_page(), mimetype="text/html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin")
def admin():
    blocked = _require_login()
    if blocked:
        return blocked
    return Response(_admin_page(), mimetype="text/html")


@app.route("/datos")
def datos():
    blocked = _require_login()
    if blocked:
        return blocked
    return Response(
        _datos_page(
            table=request.args.get("tabla", "rutas"),
            q=request.args.get("q", ""),
            msg=request.args.get("msg", ""),
            err=request.args.get("err") == "1",
            edit_key=request.args.get("editar", ""),
        ),
        mimetype="text/html",
    )


@app.route("/foxtrot")
@app.route("/foxtrot_calidad")
@app.route("/calidad-foxtrot")
@app.route("/foxtrot-calidad/")
@app.route("/foxtrot-calidad")
def foxtrot_calidad():
    blocked = _require_login()
    if blocked:
        return blocked
    return Response(
        _foxtrot_calidad_page(
            q=request.args.get("q", ""),
            msg=request.args.get("msg", ""),
            err=request.args.get("err") == "1",
        ),
        mimetype="text/html",
    )


@app.route("/reporte-fichaya-foxtrot")
def reporte_fichaya_foxtrot():
    return _fichaya_report_page()


@app.route("/reporte-fichaya-foxtrot.csv")
def reporte_fichaya_foxtrot_csv():
    blocked = _require_login()
    if blocked:
        return blocked
    rows, warning = _fichaya_report_rows(
        request.args.get("desde") or "2026-08-01",
        request.args.get("hasta") or date.today().strftime("%Y-%m-%d"),
        request.args.get("suc") or "",
        request.args.get("chofer") or "",
    )
    buf = StringIO()
    fields = ["fecha", "sucursal", "empleado", "legajo_fichaya", "empleado_fichaya", "fichada_ingreso", "inicio_foxtrot", "tml", "finalizacion_foxtrot", "fichada_salida", "ti", "estado", "route_id"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    resp = Response(buf.getvalue(), mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = "attachment; filename=reporte_fichaya_foxtrot.csv"
    if warning:
        resp.headers["X-Report-Warning"] = warning[:500]
    return resp


@app.route("/asociar-fichaya")
def asociar_fichaya():
    return _fichaya_mapping_page(
        msg=request.args.get("msg", ""),
        err=request.args.get("err") == "1",
    )


@app.route("/asociar-fichaya/importar", methods=["POST"])
def asociar_fichaya_importar():
    blocked = _require_login()
    if blocked:
        return blocked
    archivo = request.files.get("empleados")
    if not archivo or not archivo.filename:
        return redirect(url_for("asociar_fichaya", msg="Falta el archivo de empleados.", err=1))
    try:
        empleados = _import_fichaya_empleados(archivo.stream)
    except Exception as e:
        return redirect(url_for("asociar_fichaya", msg=f"Error importando empleados: {e}", err=1))
    return redirect(url_for("asociar_fichaya", msg=f"Empleados FichaYA importados: {len(empleados)}."))


@app.route("/asociar-fichaya/guardar", methods=["POST"])
def asociar_fichaya_guardar():
    blocked = _require_login()
    if blocked:
        return blocked
    empleados = _fichaya_empleados()
    mapping = {}
    for key, value in request.form.items():
        if not key.startswith("map__"):
            continue
        norm = key[5:]
        legajo = pipeline._norm_id(value)
        if legajo:
            emp = empleados.get(legajo) or {}
            mapping[norm] = {"legajo": legajo, "nombre": emp.get("nombre", "")}
    pipeline.storage.save_setting("fichaya_nombre_map", {"valor": mapping})
    return redirect(url_for("asociar_fichaya", msg=f"Asociaciones guardadas: {len(mapping)}."))


@app.route("/foxtrot-calidad/guardar", methods=["POST"])
def foxtrot_calidad_guardar():
    blocked = _require_login()
    if blocked:
        return blocked
    rid = request.form.get("rid", "")
    try:
        base = pipeline.storage.load_all()
        if rid not in base:
            raise ValueError("No se encontró la ruta.")
        rec = dict(base[rid])
        raw = dict(rec.get("raw_foxtrot") or {})
        for col in FOXTROT_AUDIT_COLUMNS:
            form_key = f"raw__{col}"
            if form_key in request.form:
                value = request.form.get(form_key, "").strip()
                raw[col] = value if value else None
        rec["raw_foxtrot"] = raw
        rec["rid"] = rid
        _sync_route_from_raw(rec)
        pipeline.storage.save_record("rutas", rid, rec)
    except Exception as e:
        return redirect(url_for("foxtrot_calidad", msg=f"Error guardando: {e}", err=1))
    return redirect(url_for("foxtrot_calidad", msg="Ruta actualizada."))


@app.route("/foxtrot-calidad/autocompletar", methods=["POST"])
def foxtrot_calidad_autocompletar():
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        st = _autofill_foxtrot_missing()
        detalle = ", ".join(f"{col}: {n}" for col, n in st["por_columna"].items() if n)
        rate = st["celdas"] / st["segundos"] if st["segundos"] > 0 else 0
        msg = (
            f"Autocompletado listo en {st['segundos']:.1f} segundos. "
            f"Rutas modificadas: {st['rutas']}. Celdas completadas: {st['celdas']}. "
            f"Velocidad: {rate:.1f} celdas/seg."
        )
        if detalle:
            msg += " " + detalle
    except Exception as e:
        return redirect(url_for("foxtrot_calidad", msg=f"Error autocompletando: {e}", err=1))
    return redirect(url_for("foxtrot_calidad", msg=msg))


@app.route("/datos/guardar", methods=["POST"])
def datos_guardar():
    blocked = _require_login()
    if blocked:
        return blocked
    table = request.form.get("tabla", "")
    key = request.form.get("clave", "")
    try:
        if table not in TABLES:
            raise ValueError("Tabla no permitida.")
        base = TABLES[table]["load"]()
        rec = dict(base.get(key) or {})
        for name in rec.keys():
            form_key = f"field__{name}"
            if form_key in request.form:
                rec[name] = _coerce_field(request.form.get(form_key), rec.get(name))
        key_field = TABLES[table]["key"]
        rec[key_field] = key
        if table == "clientes":
            rec["ventanas"] = pipeline.parse_horario_entrega(rec.get("horario_entrega", ""))
        pipeline.storage.save_record(table, key, rec)
    except Exception as e:
        return redirect(url_for("datos", tabla=table or "rutas", editar=key, msg=f"Error guardando: {e}", err=1))
    return redirect(url_for("datos", tabla=table, editar=key, msg="Registro guardado."))


@app.route("/datos/borrar", methods=["POST"])
def datos_borrar():
    blocked = _require_login()
    if blocked:
        return blocked
    table = request.form.get("tabla", "")
    key = request.form.get("clave", "")
    try:
        pipeline.storage.delete_record(table, key)
    except Exception as e:
        return redirect(url_for("datos", tabla=table or "rutas", msg=f"Error borrando: {e}", err=1))
    return redirect(url_for("datos", tabla=table, msg="Registro borrado."))


@app.route("/actualizar", methods=["POST"])
def actualizar():
    blocked = _require_login()
    if blocked:
        return blocked
    xls = request.files.get("xls")
    if not xls or xls.filename == "":
        return Response(_admin_page("Falta el archivo de export.", err=True), mimetype="text/html", status=400)
    clientes = request.files.get("clientes")
    csvs = [(f.stream, f.filename) for f in request.files.getlist("csv") if f and f.filename]
    reset = request.form.get("reset") == "1"
    try:
        clientes_importados = None
        if clientes and clientes.filename:
            clientes_importados = pipeline.actualizar_clientes(clientes.stream)
        st = pipeline.actualizar(xls.stream, xls.filename, csvs, reset=reset)
    except Exception as e:
        return Response(_admin_page(f"Error procesando el export: {e}", err=True), mimetype="text/html", status=400)
    msg = (f"Listo. Rutas procesadas: {st['procesadas']} · nuevas agregadas: {st['agregadas']} · total en base: {st['total']} "
           f"({st['validas']} válidas, {st['sin_cierre']} sin cierre). "
           f"TML {st['tml_prom']} min ({st['tml_cumpl']}% cumple) · TI {st['ti_prom']} min ({st['ti_cumpl']}% cumple).")
    if st.get("actualiza_existentes"):
        msg += " Las rutas existentes del export fueron actualizadas."
    if st.get("attempts_guardados"):
        msg += f" Attempts guardados/actualizados: {st['attempts_guardados']}."
    if clientes_importados is not None:
        msg += f" Clientes importados: {clientes_importados}."
    return Response(_admin_page(msg), mimetype="text/html")


@app.route("/actualizar-rechazos", methods=["POST"])
def actualizar_rechazos():
    blocked = _require_login()
    if blocked:
        return blocked
    desde = request.form.get("desde") or "2026-01-01"
    hasta = request.form.get("hasta") or date.today().strftime("%Y-%m-%d")
    archivo = request.files.get("rechazos_file")
    try:
        if archivo and archivo.filename:
            nombre = archivo.filename.lower()
            if nombre.endswith(".csv"):
                raw = archivo.stream.read().decode("utf-8-sig")
                st = pipeline.guardar_rechazos_csv(raw, desde, hasta, archivo.filename)
            elif nombre.endswith((".xls", ".xlsx")):
                st = pipeline.guardar_rechazos_excel(archivo.stream, desde, hasta, archivo.filename)
            else:
                payload = json.load(archivo.stream)
                st = pipeline.guardar_rechazos_payload(payload, desde, hasta, archivo.filename)
        else:
            st = pipeline.importar_rechazos(desde, hasta)
    except Exception as e:
        return Response(_admin_page(f"Error importando rechazos: {e}", err=True), mimetype="text/html", status=400)
    msg = f"Listo. Rechazos importados: {st['guardados']} días ({st['desde']} a {st['hasta']})."
    if st.get("detalle_guardados"):
        msg += f" Detalle importado: {st['detalle_guardados']} filas."
    return Response(_admin_page(msg), mimetype="text/html")


@app.route("/actualizar-articulos", methods=["POST"])
def actualizar_articulos():
    blocked = _require_login()
    if blocked:
        return blocked
    archivo = request.files.get("articulos")
    if not archivo or not archivo.filename:
        return Response(_admin_page("Falta el archivo de artículos.", err=True), mimetype="text/html", status=400)
    try:
        total = pipeline.actualizar_articulos(archivo.stream)
    except Exception as e:
        return Response(_admin_page(f"Error importando artículos: {e}", err=True), mimetype="text/html", status=400)
    return Response(_admin_page(f"Listo. Artículos importados: {total}."), mimetype="text/html")


@app.route("/configurar-dqi", methods=["POST"])
def configurar_dqi():
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        cfg = pipeline.guardar_dqi_objetivo(request.form.get("dqi_objetivo"))
    except Exception as e:
        return Response(_admin_page(f"Error guardando objetivo DQI: {e}", err=True), mimetype="text/html", status=400)
    return Response(_admin_page(f"Listo. Objetivo mensual DQI: {cfg['valor']} bultos."), mimetype="text/html")


@app.route("/salud")
def salud():
    base = pipeline.storage.load_all()
    clientes = pipeline.storage.load_clientes()
    rechazos = pipeline.storage.load_rechazos()
    rechazos_detalle = pipeline.storage.load_rechazos_detalle()
    articulos = pipeline.storage.load_articulos()
    rutas = list(base.values())
    ontime_rutas = [r for r in rutas if "pdv_total" in r]
    clientes_foxtrot_con_ventana = {
        c.get("cliente")
        for r in rutas
        for c in r.get("clientes_con_ventana", [])
        if c.get("cliente")
    }
    clientes_foxtrot_sin_ventana = {
        c.get("cliente")
        for r in rutas
        for c in r.get("clientes_sin_ventana", [])
        if c.get("cliente")
    }
    return {
        "ok": True,
        "backend": pipeline.storage.backend_name(),
        "con_datos": len(base) > 0,
        "rutas": len(base),
        "validas": len([r for r in rutas if r.get("usable")]),
        "clientes": len(clientes),
        "clientes_con_ventana": len([c for c in clientes.values() if c.get("ventanas")]),
        "clientes_foxtrot_unicos": len(clientes_foxtrot_con_ventana | clientes_foxtrot_sin_ventana),
        "clientes_foxtrot_con_ventana": len(clientes_foxtrot_con_ventana),
        "clientes_foxtrot_sin_ventana": len(clientes_foxtrot_sin_ventana),
        "rechazos_dias": len(rechazos),
        "rechazos_detalle": len(rechazos_detalle),
        "rechazos_total": sum(r.get("rechazos", 0) for r in rechazos.values()),
        "articulos": len(articulos),
        "ontime_rutas": len(ontime_rutas),
        "ontime_pdv_total": sum(r.get("pdv_total", 0) for r in rutas),
        "ontime_pdv_evaluables": sum((r.get("pdv_ontime", 0) + r.get("pdv_fuera_ontime", 0)) for r in rutas),
        "ontime_pdv_ok": sum(r.get("pdv_ontime", 0) for r in rutas),
        "ontime_pdv_fuera": sum(r.get("pdv_fuera_ontime", 0) for r in rutas),
        "ontime_pdv_sin_ventana": sum(r.get("pdv_sin_ventana", 0) for r in rutas),
    }


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0").lower() in ("1", "true", "yes", "on")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=debug, use_reloader=debug)
