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
import json
import secrets
from datetime import date
from html import escape
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
<p style="margin-top:18px"><a href="/dashboard">&larr; Volver al dashboard</a> · <a href="/datos">Revisar datos cargados</a> · <a href="/logout">Cerrar sesión</a></p>
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
    "rutas": {"label": "Rutas Foxtrot", "key": "rid", "load": lambda: pipeline.storage.load_all(), "cols": ["rid", "fecha", "mes", "suc", "chofer", "usable", "tml", "ti", "horas"]},
    "attempts": {"label": "Attempt Analytics", "key": "attempt_key", "load": lambda: pipeline.storage.load_attempts(), "cols": ["attempt_key", "Route ID", "Customer ID", "Customer Name", "Visit Start Timestamp", "Driver Click Timestamp", "Aggregate Visit Status"]},
    "clientes": {"label": "Clientes", "key": "cliente", "load": lambda: pipeline.storage.load_clientes(), "cols": ["cliente", "sucursal", "razon_social", "nombre", "horario_entrega", "ventanas"]},
    "rechazos": {"label": "Rechazos", "key": "key", "load": lambda: pipeline.storage.load_rechazos(), "cols": ["key", "fecha", "sucursal", "rechazos", "rechazo_bultos", "pct_rechazo_bultos", "origen"]},
    "rechazos_detalle": {"label": "Detalle rechazos", "key": "key", "load": lambda: pipeline.storage.load_rechazos_detalle(), "cols": ["fecha", "sucursal", "chofer", "sector", "motivo", "pedidos_rechazo", "bultos_rechazo", "hl_rechazo"]},
    "articulos": {"label": "Artículos", "key": "articulo", "load": lambda: pipeline.storage.load_articulos(), "cols": ["articulo", "descripcion", "unidades_por_bulto"]},
    "settings": {"label": "Configuración", "key": "key", "load": lambda: pipeline.storage.load_settings(), "cols": ["key", "valor"]},
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
    next_url = request.args.get("next") or request.form.get("next") or url_for("admin")
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
<div class=nav><a class=secondary href="/dashboard">Dashboard</a><a class=secondary href="/admin">Admin</a><a href="/logout">Salir</a></div></div>{alert}
<div class=tabs>{tabs}</div><form class=tools method=get action="/datos"><input type=hidden name=tabla value="{escape(table)}"><input name=q value="{escape(q)}" placeholder="Buscar en esta tabla"><button class=btn type=submit>Buscar</button><a class="btn secondary" href="/datos?tabla={escape(table)}">Limpiar</a></form>
<div class=panel><div class=table-wrap><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div></div>
<p class=muted style="margin-top:12px">Se muestran hasta 500 registros por búsqueda. Editar JSON incorrecto puede afectar el dashboard.</p>
</div></body></html>"""


@app.route("/")
def home():
    return _dashboard_response()


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
            return redirect(request.form.get("next") or url_for("admin"))
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
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=True)
