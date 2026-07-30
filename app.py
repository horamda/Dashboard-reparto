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
<p>Subí el export nuevo de Route Analytics. Se agregan solo los días que falten; lo ya cargado no cambia.</p>
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
<p style="margin-top:18px"><a href="/dashboard">&larr; Volver al dashboard</a> · <a href="/logout">Cerrar sesión</a></p>
<hr>
<h1>Importar rechazos</h1>
<p>Consume el endpoint CSV de rechazos diarios de Dolores y lo guarda en la base.</p>
<form method=post action="/actualizar-rechazos" enctype="multipart/form-data">
  <label>Desde</label>
  <input type=date name=desde value="2026-01-01" required>
  <label>Hasta</label>
  <input type=date name=hasta value="{hasta_default}" required>
  <label>Archivo de rechazos diarios (opcional .csv / .json)</label>
  <input type=file name=rechazos_file accept=".csv,.json,text/csv,application/json">
  <button class=btn type=submit>Importar rechazos</button>
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


def _admin_page(msg="", err=False):
    token_field = ""
    m = f'<div class="msg{" err" if err else ""}">{msg}</div>' if msg else ""
    return ADMIN_HTML.format(msg=m, token_field=token_field, hasta_default=date.today().strftime("%Y-%m-%d"))


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
    msg = (f"Listo. Rutas nuevas agregadas: {st['agregadas']} · total en base: {st['total']} "
           f"({st['validas']} válidas, {st['sin_cierre']} sin cierre). "
           f"TML {st['tml_prom']} min ({st['tml_cumpl']}% cumple) · TI {st['ti_prom']} min ({st['ti_cumpl']}% cumple).")
    if st.get("actualiza_existentes"):
        msg += f" On time recalculado para {st['procesadas']} rutas del export."
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
            if archivo.filename.lower().endswith(".csv"):
                raw = archivo.stream.read().decode("utf-8-sig")
                st = pipeline.guardar_rechazos_csv(raw, desde, hasta, archivo.filename)
            else:
                payload = json.load(archivo.stream)
                st = pipeline.guardar_rechazos_payload(payload, desde, hasta, archivo.filename)
        else:
            st = pipeline.importar_rechazos(desde, hasta)
    except Exception as e:
        return Response(_admin_page(f"Error importando rechazos: {e}", err=True), mimetype="text/html", status=400)
    msg = f"Listo. Rechazos importados: {st['guardados']} días ({st['desde']} a {st['hasta']})."
    return Response(_admin_page(msg), mimetype="text/html")


@app.route("/salud")
def salud():
    base = pipeline.storage.load_all()
    clientes = pipeline.storage.load_clientes()
    rechazos = pipeline.storage.load_rechazos()
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
        "rechazos_total": sum(r.get("rechazos", 0) for r in rechazos.values()),
        "ontime_rutas": len(ontime_rutas),
        "ontime_pdv_total": sum(r.get("pdv_total", 0) for r in rutas),
        "ontime_pdv_evaluables": sum((r.get("pdv_ontime", 0) + r.get("pdv_fuera_ontime", 0)) for r in rutas),
        "ontime_pdv_ok": sum(r.get("pdv_ontime", 0) for r in rutas),
        "ontime_pdv_fuera": sum(r.get("pdv_fuera_ontime", 0) for r in rutas),
        "ontime_pdv_sin_ventana": sum(r.get("pdv_sin_ventana", 0) for r in rutas),
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=True)
