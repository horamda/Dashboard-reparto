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
from flask import Flask, request, redirect, url_for, Response, abort
import pipeline

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

ADMIN_HTML = """<!doctype html><html lang=es><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Actualizar dashboard</title>
<style>body{{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#EEF1F5;color:#15233B;margin:0}}
.box{{max-width:560px;margin:6vh auto;background:#fff;border:1px solid #DCE2EA;border-radius:14px;padding:28px 30px}}
h1{{font-size:20px;margin:0 0 4px}}p{{color:#657085;font-size:13.5px;margin:0 0 18px}}
label{{display:block;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.4px;color:#657085;margin:16px 0 6px}}
input[type=file],input[type=password]{{width:100%;padding:9px 10px;border:1px solid #DCE2EA;border-radius:8px;font-size:14px;background:#fff}}
.btn{{margin-top:22px;width:100%;background:#15233B;color:#fff;border:0;border-radius:9px;padding:12px;font-size:15px;font-weight:600;cursor:pointer}}
.msg{{background:#DCFCE7;border:1px solid #86EFAC;color:#166534;border-radius:9px;padding:10px 12px;font-size:13.5px;margin-bottom:16px}}
.err{{background:#FEE2E2;border:1px solid #FCA5A5;color:#991B1B}}
a{{color:#1E3A8A;font-size:13.5px}}</style></head>
<body><div class=box>
<h1>Actualizar dashboard</h1>
<p>Subí el export nuevo de Route Analytics. Se agregan solo los días que falten; lo ya cargado no cambia.</p>
{msg}
<form method=post action="/actualizar" enctype="multipart/form-data">
  <label>Export Route Analytics (.xls / .xlsx) *</label>
  <input type=file name=xls accept=".xls,.xlsx" required>
  <label>CSV de visitas (opcional, para recuperar rutas sin cierre)</label>
  <input type=file name=csv accept=".csv" multiple>
  <label>CSV de clientes (opcional, actualiza ventanas horarias)</label>
  <input type=file name=clientes accept=".csv">
  {token_field}
  <label style="text-transform:none;font-weight:400;color:#15233B;margin-top:14px">
    <input type=checkbox name=reset value=1 style="width:auto;margin-right:6px">Rehacer la base de cero (borra lo guardado)</label>
  <button class=btn type=submit>Actualizar</button>
</form>
<p style="margin-top:18px"><a href="/dashboard">&larr; Volver al dashboard</a></p>
</div></body></html>"""

LANDING = """<!doctype html><html lang=es><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Dashboard de reparto</title>
<style>body{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#EEF1F5;color:#15233B;text-align:center;padding:12vh 20px}
a{display:inline-block;margin-top:16px;background:#15233B;color:#fff;text-decoration:none;border-radius:9px;padding:12px 22px;font-weight:600}</style></head>
<body><h1>Todavía no hay datos cargados</h1>
<p style="color:#657085">Subí el primer export para generar el dashboard.</p>
<a href="/admin">Cargar datos</a></body></html>"""


def _admin_page(msg="", err=False):
    token_field = "" if not ADMIN_TOKEN else \
        '<label>Clave de administrador *</label><input type=password name=token required>'
    m = f'<div class="msg{" err" if err else ""}">{msg}</div>' if msg else ""
    return ADMIN_HTML.format(msg=m, token_field=token_field)


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


@app.route("/admin")
def admin():
    return Response(_admin_page(), mimetype="text/html")


@app.route("/actualizar", methods=["POST"])
def actualizar():
    if ADMIN_TOKEN and request.form.get("token", "") != ADMIN_TOKEN:
        return Response(_admin_page("Clave incorrecta.", err=True), mimetype="text/html", status=403)
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
    if clientes_importados is not None:
        msg += f" Clientes importados: {clientes_importados}."
    return Response(_admin_page(msg), mimetype="text/html")


@app.route("/salud")
def salud():
    base = pipeline.storage.load_all()
    clientes = pipeline.storage.load_clientes()
    return {
        "ok": True,
        "backend": pipeline.storage.backend_name(),
        "con_datos": len(base) > 0,
        "rutas": len(base),
        "validas": len([r for r in base.values() if r.get("usable")]),
        "clientes": len(clientes),
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=True)
