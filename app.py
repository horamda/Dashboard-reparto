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
import gzip
import json
import math
import random
import re
import secrets
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta
from html import escape
from io import StringIO
from urllib.parse import urlencode, urlsplit
from flask import Flask, request, redirect, url_for, Response, session, send_from_directory, render_template, jsonify
import pipeline
from logistics_cost_service import LogisticsCostService
from pedidos_blueprint import pedidos_bp
import storage_pedidos
import equipos_service
import fichaya_kpis_service
import kpi_storage

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", ADMIN_TOKEN)
app.secret_key = os.environ.get("SECRET_KEY") or ADMIN_TOKEN or ADMIN_PASSWORD or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("SESSION_COOKIE_SECURE") == "1"),
)
app.register_blueprint(pedidos_bp)
from pdv_distance_api import bp as pdv_distances_bp
app.register_blueprint(pdv_distances_bp)

_GZIP_CACHE = OrderedDict()
_GZIP_CACHE_LOCK = threading.RLock()
_GZIP_CACHE_MAX_ITEMS = max(1, int(os.environ.get("GZIP_CACHE_MAX_ITEMS", "4")))
_GZIP_CACHE_MIN_BYTES = max(1024, int(os.environ.get("GZIP_CACHE_MIN_BYTES", "262144")))


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "favicon-t2-v2.ico",
        mimetype="image/vnd.microsoft.icon",
    )


def _gzip_payload(payload, etag=""):
    if len(payload) < _GZIP_CACHE_MIN_BYTES or not etag:
        return gzip.compress(payload, compresslevel=4)
    with _GZIP_CACHE_LOCK:
        cached = _GZIP_CACHE.get(etag)
        if cached is not None:
            _GZIP_CACHE.move_to_end(etag)
            return cached
    compressed = gzip.compress(payload, compresslevel=4)
    with _GZIP_CACHE_LOCK:
        _GZIP_CACHE[etag] = compressed
        _GZIP_CACHE.move_to_end(etag)
        while len(_GZIP_CACHE) > _GZIP_CACHE_MAX_ITEMS:
            _GZIP_CACHE.popitem(last=False)
    return compressed


def _prewarm_caches():
    jobs = (
        ("pedidos stats", storage_pedidos.stats),
        ("pedidos data", storage_pedidos.fetch_dashboard),
        ("rutas foxtrot", pipeline.storage.load_all),
        ("dashboard operativo", pipeline.render_dashboard),
    )
    for _name, fn in jobs:
        try:
            fn()
        except Exception:
            pass


if os.environ.get("DISABLE_CACHE_PREWARM") != "1":
    threading.Thread(target=_prewarm_caches, daemon=True).start()


@app.after_request
def optimize_response(response):
    # Branding is public and versioned; never apply private page caching to it.
    branding = {'logo-t2-v2.webp', 'favicon-t2-v2.png', 'favicon-t2-v2.ico'}
    if request.method in ('GET', 'HEAD') and response.status_code in (200, 304):
        if request.endpoint == 'static' and (request.view_args or {}).get('filename') in branding:
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
            return response
        if request.endpoint == 'favicon':
            response.headers['Cache-Control'] = 'public, max-age=86400'
            return response
    read_only_posts = {"login", "pedidos.pedidos_ai_analisis", "exportar_clientes_sin_ventana"}
    if (
        request.method not in ("GET", "HEAD")
        and response.status_code < 400
        and request.endpoint not in read_only_posts
    ):
        pipeline.clear_dashboard_cache()
        pipeline.storage.clear_cache("health:")
        pipeline.storage.clear_cache("logistics:")
        pipeline.storage.clear_cache("counts:")

    compressible = {
        "text/html", "text/css", "text/javascript", "application/javascript",
        "application/json", "image/svg+xml",
    }
    cacheable_get = (
        request.method in ("GET", "HEAD")
        and response.status_code == 200
        and not response.direct_passthrough
    )
    etag = ""
    if cacheable_get:
        if not response.headers.get("ETag"):
            response.add_etag()
        etag, _is_weak = response.get_etag()
        if etag:
            # Gzip and plain responses are semantically equivalent representations.
            response.set_etag(etag, weak=True)
        response.headers["Cache-Control"] = "private, no-cache, must-revalidate"
        response.make_conditional(request)
        if response.status_code == 304:
            return response

    can_compress = (
        request.method != "HEAD"
        and response.status_code == 200
        and not response.direct_passthrough
        and response.mimetype in compressible
        and "gzip" in request.headers.get("Accept-Encoding", "").lower()
        and not response.headers.get("Content-Encoding")
    )
    if can_compress:
        payload = response.get_data()
        if len(payload) >= 1024:
            response.vary.add("Accept-Encoding")
            response.set_data(_gzip_payload(payload, etag))
            response.headers["Content-Encoding"] = "gzip"
            response.headers["Content-Length"] = str(len(response.get_data()))

    if not cacheable_get:
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response

ADMIN_HTML = """<!doctype html><html lang=es><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Actualizar dashboard</title><link rel="icon" type="image/png" href="/static/favicon-t2-v2.png" sizes="64x64">
<style>*{{box-sizing:border-box;letter-spacing:0}}body{{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#EEF1F5;color:#15233B;margin:0;line-height:1.45;-webkit-font-smoothing:antialiased}}
.box{{width:min(100% - 28px,620px);margin:6vh auto;background:#fff;border:1px solid #DCE2EA;border-radius:12px;padding:28px 30px;box-shadow:0 8px 22px rgba(21,35,59,.06)}}
h1{{font-size:20px;line-height:1.2;margin:0 0 4px}}p{{color:#657085;font-size:13.5px;margin:0 0 18px}}
label{{display:block;font-size:12px;font-weight:600;text-transform:uppercase;color:#657085;margin:16px 0 6px}}
input[type=file],input[type=password],input[type=date],select{{width:100%;min-height:44px;padding:10px 11px;border:1px solid #DCE2EA;border-radius:8px;font-size:14px;background:#fff}}
input[type=file]{{padding:5px}}input[type=file]::file-selector-button{{min-height:30px;margin-right:10px;border:0;border-radius:6px;padding:6px 10px;background:#E8EDF3;color:#15233B;font-weight:650;cursor:pointer}}
input:focus,select:focus{{outline:2px solid #C77D1A;outline-offset:1px}}
.btn{{margin-top:22px;width:100%;min-height:44px;background:#15233B;color:#fff;border:0;border-radius:9px;padding:12px;font-size:15px;font-weight:650;cursor:pointer}}
.btn:hover{{background:#26334d}}
.btn:disabled{{cursor:wait;opacity:.68}}
.msg{{background:#DCFCE7;border:1px solid #86EFAC;color:#166534;border-radius:9px;padding:10px 12px;font-size:13.5px;margin-bottom:16px}}
.err{{background:#FEE2E2;border:1px solid #FCA5A5;color:#991B1B}}
.integration{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;border:1px solid #DCE2EA;border-radius:8px;padding:9px 11px;margin-bottom:16px;font-size:12.5px}}.integration strong{{margin-right:auto}}.integration span{{border-radius:999px;padding:3px 8px;font-weight:700}}.integration .mode{{background:#E8EDF3;color:#15233B}}.integration .ok{{background:#DCFCE7;color:#166534}}.integration .missing{{background:#FEF3C7;color:#92400E}}
a{{color:#1E3A8A;font-size:13.5px}}hr{{border:0;border-top:1px solid #DCE2EA;margin:24px 0}}.busy{{display:none;position:fixed;right:18px;bottom:18px;z-index:50;background:#15233B;color:#fff;border-radius:8px;padding:11px 14px;box-shadow:0 10px 28px rgba(21,35,59,.24);font-size:13.5px;font-weight:650}}.busy.show{{display:block}}:focus-visible{{outline:2px solid #C77D1A;outline-offset:2px}}@media(max-width:640px){{.box{{width:min(100% - 20px,620px);margin:18px auto;padding:22px 18px}}.busy{{left:10px;right:10px;bottom:10px;text-align:center}}}}@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto!important;transition:none!important}}}}</style></head>
<body><div class=box>
<div id=busy class=busy role=status aria-live=polite>Procesando datos...</div>
<h1>Actualizar dashboard</h1>
<p><a href="/admin/objetivos-pdv">Administrar objetivos de tiempo en PDV</a></p>
<p>Subí el export nuevo de Route Analytics y el Attempt Analytics. Las rutas existentes se actualizan con las columnas nuevas.</p>
{msg}
{fichaya_status}
<form method=post action="/actualizar" enctype="multipart/form-data">
  <label>Export Route Analytics (.xls / .xlsx) *</label>
  <input type=file name=xls accept=".xls,.xlsx" required>
  <label>Archivo de visitas Foxtrot (opcional, para recuperar rutas sin cierre y calcular on time)</label>
  <input type=file name=csv accept=".csv,.xls,.xlsx" multiple>
  <label>CSV de clientes (opcional, actualiza ventanas horarias)</label>
  <input type=file name=clientes accept=".csv">
  {token_field}
  <label style="text-transform:none;font-weight:400;color:#15233B;margin-top:14px">
    <input type=checkbox name=reset value=1 style="width:auto;margin-right:6px">Rehacer la base de cero (crea una copia de seguridad antes de borrar)</label>
  <button class=btn type=submit>Actualizar</button>
</form>
<p style="margin-top:18px"><a href="/inicio">Panel principal</a> · <a href="/dashboard">Dashboard</a> · <a href="/datos">Revisar datos cargados</a> · <a href="/foxtrot-calidad">Calidad Foxtrot</a> · <a href="/reporte-fichaya-foxtrot">Reporte FichaYA/Foxtrot</a> · <a href="/pedidos">Análisis de pedidos</a> · <a href="/costos-distribucion">Costos</a> · <a href="/logout">Cerrar sesión</a></p>
<hr>
<section id="sincronizacion-logistica"><h1>Sincronizar ventas para In Full y OTIF</h1>
<p>Importación manual desde la base externa. Actualiza todas las sucursales del período y cruza las visitas guardadas de Foxtrot. Repetir el período recoge correcciones de rechazos sin duplicar comprobantes.</p>
<form method=post action="/cumplimiento-comprobantes/sincronizar">
<input type=hidden name=csrf value="{logistics_csrf}"><input type=hidden name=volver value=admin>
<label>Desde</label><input type=date name=desde value="{logistics_desde_default}" required>
<label>Hasta</label><input type=date name=hasta value="{hasta_default}" required>
<label>Acción</label><select name=modo><option value=ventas>Actualizar ventas y cruzar Foxtrot</option><option value=foxtrot>Solo recalcular Foxtrot con las ventas guardadas</option></select>
<button class=btn type=submit>Ejecutar actualización</button></form>
<p>Si falla, se conserva la versión anterior. Los filtros y el botón «Recargar resultados guardados» del dashboard no consultan la base externa. Después de modificar visitas o ventanas horarias, recalculá Foxtrot para el período afectado.</p>
<p><a href="/dashboard?tab=otif">Ver OTIF</a> · <a href="/dashboard?tab=infull">Ver In Full</a> · <a href="/cumplimiento-comprobantes">Consultar evidencia y fecha de actualización</a></p></section>
<hr><details><summary>Consulta histórica de pedidos por API</summary>
<p><a href="/datos-logistica">Ventas y entregas: lectura directa de la base</a></p>
<p><a href="/datos-api">Ver todos los datos importados de la API</a></p>
<p>Conserva la consulta API anterior para revisión. No actualiza los comprobantes procesados de las solapas In Full y OTIF.</p>
<form method=post action="/actualizar-otif-pedidos">
<label>Desde</label><input type=date name=desde value="2026-01-01" required>
<label>Hasta</label><input type=date name=hasta value="{hasta_default}" required>
<label>Sucursal (codigo o TODAS)</label><input name=sucursal value="TODAS" required>
<button class=btn type=submit>Actualizar consulta histórica API</button>
</form></details><hr>
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
<h1>Importar volumen entregado</h1>
<p>Actualiza visitas existentes mediante Route ID y Customer ID. Admite Bultos, HL, Pallets y Unidades entregadas.</p>
<form method=post action="/actualizar-volumen-entregas" enctype="multipart/form-data">
  <label>Archivo de entregas (.csv / .xls / .xlsx)</label>
  <input type=file name=entregas accept=".csv,.xls,.xlsx" required>
  <button class=btn type=submit>Importar entregas</button>
</form>
<hr>
<h1>Asignar vehículos</h1>
<p>Actualiza rutas existentes mediante Route ID y Vehículo, Patente o Unidad.</p>
<form method=post action="/actualizar-vehiculos-rutas" enctype="multipart/form-data">
  <label>Archivo de asignación (.csv / .xls / .xlsx)</label>
  <input type=file name=vehiculos accept=".csv,.xls,.xlsx" required>
  <button class=btn type=submit>Importar asignaciones</button>
</form>
<hr>
<h1>Completar datos desde API logística</h1>
<p>Completa únicamente vehículo, bultos, HL, pallets y unidades que estén vacíos. Las coincidencias ambiguas no se modifican.</p>
{logistics_api_status}
<form method=post action="/actualizar-logistica-api">
  <label>Desde</label>
  <input type=date name=desde value="{logistics_desde_default}" required>
  <label>Hasta</label>
  <input type=date name=hasta value="{hasta_default}" required>
  <label>Sucursal</label>
  <select name=sucursal><option value="TODAS">Todas</option><option value="1">Casa Central</option><option value="2">Dolores</option><option value="3">Chascomús</option></select>
  <button class=btn type=submit>Completar desde API</button>
</form>
<hr>
<h1>Configurar DQI</h1>
<p>Define el objetivo mensual de roturas en bultos para DQI y Team Room.</p>
<form method=post action="/configurar-dqi">
  <label>Objetivo mensual DQI (bultos)</label>
  <input name=dqi_objetivo value="{dqi_objetivo}" required>
  <button class=btn type=submit>Guardar objetivo</button>
</form>
</div><script>document.querySelectorAll('form').forEach(form=>form.addEventListener('submit',()=>{{if(!form.checkValidity())return;const button=form.querySelector('button[type="submit"]');if(!button)return;form.setAttribute('aria-busy','true');button.disabled=true;button.textContent='Procesando...';document.getElementById('busy').classList.add('show');}}));</script></body></html>"""

LOGIN_HTML = """<!doctype html><html lang=es><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Ingresar</title><link rel="icon" type="image/png" href="/static/favicon-t2-v2.png" sizes="64x64">
<style>*{{box-sizing:border-box;letter-spacing:0}}body{{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#EEF1F5;color:#15233B;margin:0;line-height:1.45;-webkit-font-smoothing:antialiased}}
.box{{width:min(100% - 28px,420px);margin:12vh auto;background:#fff;border:1px solid #DCE2EA;border-radius:12px;padding:28px 30px;box-shadow:0 8px 22px rgba(21,35,59,.06)}}
h1{{font-size:20px;line-height:1.2;margin:0 0 4px}}p{{color:#657085;font-size:13.5px;margin:0 0 18px}}
label{{display:block;font-size:12px;font-weight:600;text-transform:uppercase;color:#657085;margin:16px 0 6px}}
input{{width:100%;min-height:42px;padding:10px 11px;border:1px solid #DCE2EA;border-radius:8px;font-size:14px}}
input:focus{{outline:2px solid #C77D1A;outline-offset:1px}}
.btn{{margin-top:22px;width:100%;min-height:44px;background:#15233B;color:#fff;border:0;border-radius:9px;padding:12px;font-size:15px;font-weight:650;cursor:pointer}}
.btn:hover{{background:#26334d}}.login-logo{{width:92px;height:92px;object-fit:contain;border-radius:18px;margin:0 auto 14px;display:block}}
.err{{background:#FEE2E2;border:1px solid #FCA5A5;color:#991B1B;border-radius:9px;padding:10px 12px;font-size:13.5px;margin-bottom:16px}}@media(max-width:640px){{.box{{width:min(100% - 20px,420px);margin:18px auto;padding:22px 18px}}}}</style></head>
<body><div class=box>
<img class=login-logo src="/static/logo-t2-v2.webp" width="108" height="108" fetchpriority="high" decoding="async" alt="T2">
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
<meta name=viewport content="width=device-width,initial-scale=1"><title>Dashboard de reparto</title><link rel="icon" type="image/png" href="/static/favicon-t2-v2.png" sizes="64x64">
<style>*{box-sizing:border-box}body{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#EEF1F5;color:#15233B;margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;line-height:1.45;-webkit-font-smoothing:antialiased}
.box{width:min(100%,460px);background:#fff;border:1px solid #DCE2EA;border-radius:12px;padding:28px 30px;text-align:center;box-shadow:0 8px 22px rgba(21,35,59,.06)}.landing-logo{width:108px;height:108px;object-fit:contain;border-radius:20px;margin:0 auto 16px;display:block}
h1{font-size:22px;line-height:1.2;margin:0 0 8px}p{color:#657085;font-size:14px;margin:0}
a{display:inline-block;margin-top:18px;background:#15233B;color:#fff;text-decoration:none;border-radius:9px;padding:12px 22px;font-weight:650}</style></head>
<body><div class=box><img class=landing-logo src="/static/logo-t2-v2.webp" width="108" height="108" fetchpriority="high" decoding="async" alt="T2"><h1>Todavía no hay datos cargados</h1>
<p>Subí el primer export para generar el dashboard.</p>
<a href="/admin">Cargar datos</a></div></body></html>"""

DATOS_CSS = """<style>*{box-sizing:border-box;letter-spacing:0}body{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#EEF1F5;color:#15233B;margin:0;line-height:1.45;-webkit-font-smoothing:antialiased}
.wrap{width:min(100% - 28px,1280px);margin:24px auto 44px}.top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:18px}.brand-title{display:flex;align-items:center;gap:12px;min-width:0}.brand-logo{width:56px;height:56px;border-radius:12px;object-fit:contain;background:#fff;flex:0 0 auto}
h1{font-size:24px;margin:0 0 4px}.muted{color:#657085;font-size:13.5px;margin:0}.nav{display:flex;gap:8px;flex-wrap:wrap}.nav a,.btn{background:#15233B;color:#fff;text-decoration:none;border:0;border-radius:8px;padding:10px 13px;font-size:13.5px;font-weight:650;cursor:pointer}
.nav a.secondary,.btn.secondary{background:#fff;color:#15233B;border:1px solid #DCE2EA}.msg{background:#DCFCE7;border:1px solid #86EFAC;color:#166534;border-radius:9px;padding:10px 12px;font-size:13.5px;margin:12px 0}.err{background:#FEE2E2;border-color:#FCA5A5;color:#991B1B}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}.tabs a{padding:9px 12px;border-radius:8px;border:1px solid #DCE2EA;background:#fff;color:#15233B;text-decoration:none;font-size:13.5px}.tabs a.on{background:#C77D1A;color:#fff;border-color:#C77D1A}
.tools{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0 16px}.tools input{min-height:40px;border:1px solid #DCE2EA;border-radius:8px;padding:9px 10px;font-size:14px;min-width:min(100%,320px)}.pagination{display:flex;justify-content:flex-end;align-items:center;gap:8px;margin-top:12px}.pagination span{color:#657085;font-size:13px;margin-right:auto}
.panel{background:#fff;border:1px solid #DCE2EA;border-radius:10px;overflow:hidden}.table-wrap{overflow:auto;max-height:62vh}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid #E7ECF2;padding:9px 10px;text-align:left;vertical-align:top;white-space:nowrap}th{position:sticky;top:0;background:#F8FAFC;z-index:1;font-size:12px;text-transform:uppercase;color:#657085}td.trunc{max-width:260px;overflow:hidden;text-overflow:ellipsis}
details{border-top:1px solid #E7ECF2;padding:12px 14px}summary{cursor:pointer;font-weight:700}.edit{display:grid;gap:10px;margin-top:10px}.edit-grid{display:grid;grid-template-columns:180px minmax(220px,1fr);border:1px solid #E7ECF2;border-radius:8px;overflow:hidden}.field-name{background:#F8FAFC;color:#657085;font-size:12px;font-weight:700;text-transform:uppercase}.field-name,.field-control{border-bottom:1px solid #E7ECF2;padding:9px 10px}.field-control input,.field-control textarea{width:100%;border:1px solid #DCE2EA;border-radius:7px;padding:8px 9px;font-size:13.5px}.field-control textarea{min-height:84px;font:12.5px ui-monospace,SFMono-Regular,Consolas,monospace}.actions{display:flex;gap:8px;flex-wrap:wrap}.danger{background:#991B1B}.empty{padding:22px;color:#657085}:focus-visible{outline:2px solid #C77D1A;outline-offset:2px}@media(max-width:900px){.top{display:block}.brand-title{align-items:flex-start}.brand-logo{width:48px;height:48px;border-radius:10px}.nav{margin-top:12px}.nav a,.btn,.tools input{min-height:44px}th,td{padding:8px}.wrap{width:min(100% - 18px,1280px);margin-top:14px}.edit-grid{grid-template-columns:1fr}.field-name{border-bottom:0;padding-bottom:2px}.field-control{padding-top:2px}.pagination{justify-content:space-between}.pagination span{display:none}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}</style>"""

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
    "Total Driven Meters": ("Planned Foxtrot Driving Meters", "number"),
    "Total Driven Seconds": ("Planned Foxtrot Driving Seconds", "number"),
    "Total Journey Seconds": ("Planned Foxtrot Journey Seconds", "number"),
    "Actual Route Departure Time": ("Driver Marked Route Start Timestamp", "timestamp"),
    "Actual Route Arrival Time": ("Driver Marked Route End Timestamp", "timestamp"),
}

FOXTROT_AUTOFILL_FACTOR_MIN = 1.05
FOXTROT_AUTOFILL_FACTOR_MAX = 1.08

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
    m = f'<div class="msg{" err" if err else ""}">{escape(str(msg))}</div>' if msg else ""
    try:
        dqi_objetivo = pipeline.dqi_objetivo_bultos_mes()
    except Exception:
        dqi_objetivo = ""
    credentials = pipeline.fichaya_credentials_status()
    mode = credentials["mode"]
    mode_label = {
        "web": "Método: acceso web",
        "api": "Método: API externa",
        "auto": "Método: web + respaldo API",
    }[mode]
    active_configured = (
        credentials["web_configured"] if mode == "web" else
        credentials["api_configured"] if mode == "api" else
        credentials["web_configured"] or credentials["api_configured"]
    )
    active_class = "ok" if active_configured else "missing"
    active_label = "Credenciales presentes" if active_configured else "Faltan credenciales"
    fichaya_status = (
        '<div class="integration" role="status"><strong>FichaYA</strong>'
        f'<span class="mode">{mode_label}</span>'
        f'<span class="{active_class}">{active_label}</span></div>'
    )
    logistics_status = pipeline.logistics_integration_status()
    logistics_class = "ok" if logistics_status["configured"] else "missing"
    logistics_label = "API key configurada" if logistics_status["configured"] else "Falta API key del consumidor"
    logistics_api_status = (
        '<div class="integration" role="status"><strong>API logística v1</strong>'
        f'<span class="{logistics_class}">{logistics_label}</span></div>'
    )
    return ADMIN_HTML.format(
        msg=m,
        fichaya_status=fichaya_status,
        logistics_api_status=logistics_api_status,
        logistics_csrf=escape(session.setdefault('logistics_csrf', secrets.token_urlsafe(32)), quote=True),
        token_field=token_field,
        hasta_default=date.today().strftime("%Y-%m-%d"),
        logistics_desde_default=(date.today() - timedelta(days=30)).strftime("%Y-%m-%d"),
        dqi_objetivo=escape(str(dqi_objetivo), quote=True),
    )


def _login_page(msg="", err=False):
    m = f'<div class="err">{escape(str(msg))}</div>' if msg else ""
    next_url = escape(
        _safe_next_url(request.args.get("next") or request.form.get("next") or url_for("inicio")),
        quote=True,
    )
    return LOGIN_HTML.format(msg=m, next_url=next_url)


def _is_logged_in():
    return bool(session.get("admin_logged_in"))


def _safe_next_url(value):
    value = str(value or "").strip()
    parsed = urlsplit(value)
    if value.startswith("/") and not value.startswith("//") and not parsed.scheme and not parsed.netloc:
        return value
    return url_for("inicio")


def _require_login():
    if not _is_logged_in():
        return redirect(url_for("login", next=request.path))
    return None


def _data_unavailable_page(title, exc):
    error_ref = secrets.token_hex(4)
    app.logger.exception("Error de datos [%s] en %s: %s", error_ref, request.path, exc)
    return f'''<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>{escape(title)}</title><link rel="icon" type="image/png" href="/static/favicon-t2-v2.png" sizes="64x64">
<style>*{{box-sizing:border-box;letter-spacing:0}}body{{font-family:"Segoe UI",system-ui,Arial,sans-serif;background:#EEF1F5;color:#15233B;margin:0;line-height:1.45}}.wrap{{width:min(100% - 28px,920px);margin:32px auto}}.top{{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;margin-bottom:20px}}.nav{{display:flex;gap:8px;flex-wrap:wrap}}.nav a,.btn{{background:#15233B;color:#fff;text-decoration:none;border:0;border-radius:8px;padding:10px 13px;font-size:13px;font-weight:750}}.nav a.secondary,.btn.secondary{{background:#fff;color:#15233B;border:1px solid #DCE2EA}}.panel{{background:#fff;border:1px solid #DCE2EA;border-left:5px solid #C77D1A;border-radius:10px;padding:20px;box-shadow:0 8px 22px rgba(21,35,59,.045)}}h1{{font-size:24px;margin:0 0 6px}}p{{margin:0 0 12px;color:#657085}}code{{display:block;white-space:normal;background:#F8FAFC;border:1px solid #E5EBF2;border-radius:8px;padding:10px;color:#334155;font-size:12px}}:focus-visible{{outline:2px solid #C77D1A;outline-offset:2px}}@media(max-width:640px){{.wrap{{width:min(100% - 18px,920px);margin-top:18px}}.top{{display:block}}.nav{{margin-top:12px}}.panel{{padding:17px}}}}@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto!important;transition:none!important}}}}</style></head>
<body><div class=wrap><div class=top><div><h1>{escape(title)}</h1><p>No se pudieron cargar los datos en este momento.</p></div><div class=nav><a class=secondary href="/inicio">Inicio</a><a class=secondary href="/pedidos">Pedidos</a><a href="{escape(request.path)}">Reintentar</a></div></div>
<div class=panel><p>La página existe y el acceso funciona, pero la consulta a la base de datos no respondió.</p><code>Referencia: {error_ref}</code></div></div></body></html>'''


def _dashboard_response():
    try:
        if not pipeline.hay_datos():
            return Response(LANDING, mimetype="text/html")
        return Response(pipeline.render_dashboard(), mimetype="text/html")
    except Exception as exc:
        return Response(_data_unavailable_page("Dashboard operativo", exc), mimetype="text/html", status=503)


def _main_page():
    blocked = _require_login()
    if blocked:
        return blocked
    groups = [
        ("Operación", [
            ("Dashboard operativo", "Indicadores principales, Team Room, DPO, On Time, In Full, OTIF y calidad.", "/dashboard", "Abrir"),
            ("Costos de distribución", "Costo por ruta, kilómetro, entrega y cliente con asignación configurable.", "/costos-distribucion", "Calcular"),
            ("Dashboard de costos", "Histórico vigente, comparaciones y rankings logísticos.", "/costos-distribucion/dashboard", "Analizar"),
            ("Análisis de pedidos", "Importación y análisis por franja horaria, corte, canal, vendedor y bultos estimados.", "/pedidos", "Abrir"),
            ("Reporte FichaYA / Foxtrot", "Empleado, fichada de ingreso, inicio Foxtrot, TML, fin Foxtrot, salida y TI.", "/reporte-fichaya-foxtrot", "Abrir"),
            ("Equipos de Casa Central", "Choferes y ayudantes por fecha y camión, con su vinculación a FichaYA.", "/equipos-reparto", "Ver equipos"),
            ("KPIs a FichaYA", "Resultados diarios por integrante, vista previa y seguimiento de envíos.", "/kpis-fichaya", "Preparar"),
        ]),
        ("Datos y calidad", [
            ("Evidencia por comprobante", "Documentos procesados, visitas vinculadas y exportaciones para revisar el OTIF.", "/cumplimiento-comprobantes", "Consultar"),
            ("Ventas en origen", "Todas las columnas de ventas externas en modo consulta.", "/datos-logistica", "Consultar"),
            ("Calidad Foxtrot", "Auditoría de columnas vacías y autocompletado de campos Foxtrot.", "/foxtrot-calidad", "Ver"),
            ("Datos cargados", "Revisión y edición directa de rutas, clientes, rechazos, artículos y configuración.", "/datos", "Revisar"),
            ("Asociar nombres", "Vinculación de choferes y ayudantes de reparto con legajos FichaYA.", "/asociar-fichaya", "Asociar"),
        ]),
        ("Administración", [
            ("Objetivos de tiempo en PDV", "Estándares del SOP y asignación a tipos de negocio y clientes.", "/admin/objetivos-pdv", "Administrar"),
            ("Actualizar datos", "Carga de Route Analytics, visitas Foxtrot, clientes, rechazos y artículos.", "/admin", "Ir a admin"),
        ]),
    ]
    sections = []
    for group, cards in groups:
        items = "".join(
            f'''<a class=hub-card href="{escape(url)}"><span>{escape(title)}</span><p>{escape(desc)}</p><b>{escape(cta)}</b></a>'''
            for title, desc, url, cta in cards
        )
        sections.append(f'''<section><h2>{escape(group)}</h2><div class=grid>{items}</div></section>''')
    content = "".join(sections)
    return f'''<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Panel principal</title><link rel="icon" type="image/png" href="/static/favicon-t2-v2.png" sizes="64x64">
<style>*{{box-sizing:border-box;letter-spacing:0}}body{{font-family:"Segoe UI",system-ui,Arial,sans-serif;background:#EEF1F5;color:#15233B;margin:0;line-height:1.5;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}}.wrap{{width:min(100% - 28px,1180px);margin:28px auto 44px}}.top{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;margin-bottom:22px}}.brand-title{{display:flex;align-items:center;gap:13px;min-width:0}}.brand-logo{{width:64px;height:64px;border-radius:14px;object-fit:contain;background:#fff;flex:0 0 auto}}h1{{font-size:27px;line-height:1.15;margin:0 0 7px;font-weight:800}}h2{{font-size:12px;text-transform:uppercase;color:#657085;margin:24px 0 10px}}.muted{{color:#657085;font-size:14px;margin:0;max-width:680px}}.nav{{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}}.nav a{{background:#fff;color:#15233B;border:1px solid #DCE2EA;text-decoration:none;border-radius:8px;padding:10px 13px;font-size:13px;font-weight:700;white-space:nowrap}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(285px,1fr));gap:14px}}.hub-card{{display:flex;flex-direction:column;background:#fff;border:1px solid #DCE2EA;border-left:5px solid #C77D1A;border-radius:9px;padding:19px 18px 16px;text-decoration:none;color:#15233B;min-height:132px;box-shadow:0 8px 22px rgba(21,35,59,.045)}}.hub-card:hover{{border-color:#C77D1A;box-shadow:0 10px 24px rgba(21,35,59,.08);transform:translateY(-1px)}}.hub-card span{{display:block;font-size:16px;line-height:1.25;font-weight:800;margin-bottom:8px}}.hub-card p{{color:#657085;font-size:13.5px;line-height:1.45;margin:0 0 18px;flex:1}}.hub-card b{{display:inline-block;align-self:flex-start;background:#15233B;color:#fff;border-radius:7px;padding:8px 12px;font-size:12.5px;line-height:1.2}}:focus-visible{{outline:2px solid #C77D1A;outline-offset:2px}}@media(max-width:720px){{.top{{display:block}}.brand-logo{{width:54px;height:54px;border-radius:12px}}.nav{{margin-top:14px;justify-content:flex-start}}h1{{font-size:24px}}.wrap{{width:min(100% - 18px,1180px);margin-top:18px}}.grid{{grid-template-columns:1fr}}}}@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto!important;transition:none!important}}}}</style></head>
<body><div class=wrap><div class=top><div class=brand-title><img class=brand-logo src="/static/logo-t2-v2.webp" width="108" height="108" fetchpriority="high" decoding="async" alt="T2"><div><h1>Panel principal</h1><p class=muted>Del Palacio S.A. - módulos de reparto, Foxtrot, FichaYA y pedidos.</p></div></div><div class=nav><a href="/dashboard">Dashboard</a><a href="/pedidos">Pedidos</a><a href="/logout">Cerrar sesión</a></div></div>{content}</div></body></html>'''


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
        for target, (source, kind) in FOXTROT_AUTOFILL_RULES.items():
            if not _is_blank(raw.get(target)):
                continue
            src = raw.get(source)
            if _is_blank(src):
                continue
            factor = random.uniform(FOXTROT_AUTOFILL_FACTOR_MIN, FOXTROT_AUTOFILL_FACTOR_MAX)
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


def _foxtrot_calidad_page(q="", fecha="", msg="", err=False):
    filters = {col: request.args.get(f"raw__{col}", "") for col in FOXTROT_AUDIT_COLUMNS}
    filters["fecha"] = str(fecha or "").strip()
    quality = pipeline.storage.load_foxtrot_quality(
        FOXTROT_AUDIT_COLUMNS,
        filters=filters,
        q=q,
        limit=300,
    )
    rows = quality["rows"]
    stats = []
    total = quality["total"] or 1
    for col in FOXTROT_AUDIT_COLUMNS:
        missing = quality["missing"].get(col, 0)
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
    return f"""<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Calidad Foxtrot</title><link rel="icon" type="image/png" href="/static/favicon-t2-v2.png" sizes="64x64">{DATOS_CSS}
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
.tools.raw label{{font-size:11px;font-weight:800;color:#657085;text-transform:uppercase}}.tools.raw select,.tools.raw input[type=date]{{width:100%;min-height:38px;border:1px solid #DCE2EA;border-radius:8px;padding:8px;background:#fff}}
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
@media(max-width:900px){{.table-wrap.foxtrot th:nth-child(2),.table-wrap.foxtrot td:nth-child(2),.table-wrap.foxtrot th:nth-child(3),.table-wrap.foxtrot td:nth-child(3){{position:static;min-width:118px;box-shadow:none}}.table-wrap.foxtrot th:nth-child(1),.table-wrap.foxtrot td:nth-child(1){{left:0;min-width:78px}}.tools.raw select{{min-height:44px}}.autofill-box form,.autofill-box .btn{{width:100%}}}}
</style></head>
<body><div class=wrap><div class=top><div class=brand-title><img class=brand-logo src="/static/logo-t2-v2.webp" width="108" height="108" fetchpriority="high" decoding="async" alt="T2"><div><h1>Calidad de columnas Foxtrot</h1><p class=muted>Filtrá campos vacíos/con dato y completá valores faltantes por ruta. Al guardar se recalculan inicio, fin, horas y dispersiones si aplica.</p></div></div>
<div class=nav><a class=secondary href="/inicio">Inicio</a><a class=secondary href="/dashboard">Dashboard</a><a class=secondary href="/pedidos">Pedidos</a><a class=secondary href="/costos-distribucion/dashboard">Costos</a><a class=secondary href="/datos">Datos</a><a class=secondary href="/admin">Admin</a><a href="/logout">Salir</a></div></div>{alert}
<div class=quality-shell>
<div class=stats-grid>{"".join(stats)}</div>
<div class=autofill-box><div><b>Autocompletar campos vacíos</b><p>Usa planificado x un factor aleatorio entre 1,05 y 1,08, y timestamps marcados. No pisa datos existentes.</p></div>
 <form method=post action="/foxtrot-calidad/autocompletar" onsubmit="if(!confirm('Esto completará solo campos vacíos usando datos planificados o timestamps disponibles. No pisa datos existentes. ¿Continuar?'))return false;this.querySelector('button').textContent='Procesando...';this.querySelector('button').disabled=true;return true">
  <button class="btn autofill" type=submit>Autocompletar vacíos Foxtrot</button>
 </form></div>
<details class=filter-panel open><summary><b>Filtros</b><span>Buscar rutas y elegir campos vacíos o con dato</span></summary>
<form class="tools raw" method=get action="/foxtrot-calidad"><label>Buscar<input name=q value="{escape(q)}" placeholder="Chofer, fecha, ruta..."></label><label>Fecha<input type=date name=fecha value="{escape(filters['fecha'])}"></label>{filter_controls}<div class=filter-actions><button class=btn type=submit>Filtrar</button><a class="btn secondary" href="/foxtrot-calidad">Limpiar</a></div></form></details>
<div class=panel><div class="table-wrap foxtrot"><table><thead><tr><th>Fecha</th><th>Sucursal</th><th>Chofer</th><th>Route ID</th>{header_inputs}<th>Acción</th></tr></thead><tbody>{body}</tbody></table></div></div>
<p class=muted style="margin-top:12px">Se muestran hasta 300 rutas. Para tiempos usá el formato que viene de Foxtrot o un timestamp reconocible, por ejemplo 2026-01-12 14:36:00.</p>
</div></div></body></html>"""


def _datos_page(table="rutas", q="", msg="", err=False, edit_key="", page=1):
    if table not in TABLES:
        table = "rutas"
    spec = TABLES[table]
    page = max(1, int(page or 1))
    page_size = 100
    rows = pipeline.storage.list_records(table, q=q, limit=page_size, offset=(page - 1) * page_size)
    counts = pipeline.storage.count_data_tables()
    tabs = "".join(
        f'<a class="{"on" if name == table else ""}" href="/datos?tabla={name}">{escape(cfg["label"])} ({counts.get(name, 0)})</a>'
        for name, cfg in TABLES.items()
    )
    alert = f'<div class="msg{" err" if err else ""}">{escape(msg)}</div>' if msg else ""
    header = "".join(f"<th>{escape(col)}</th>" for col in spec["cols"]) + "<th>Acciones</th>"
    body = ""
    for key, rec in rows:
        cells = "".join(f'<td class="trunc">{escape(_short_value(rec.get(col)))}</td>' for col in spec["cols"])
        edit_url = "/datos?" + urlencode({"tabla": table, "q": q, "pagina": page, "editar": str(key)})
        editor = f"""<details open><summary>Editar {escape(str(key))}</summary>
<form class=edit method=post action="/datos/guardar">
  <input type=hidden name=tabla value="{escape(table)}"><input type=hidden name=clave value="{escape(str(key))}"><input type=hidden name=pagina value="{page}"><input type=hidden name=q value="{escape(q)}">
  {_edit_fields(rec)}
  <div class=actions><button class=btn type=submit>Guardar</button></div>
</form></details>"""
        delete_form = f"""<form method=post action="/datos/borrar" onsubmit="return confirm('¿Borrar este registro?')">
<input type=hidden name=tabla value="{escape(table)}"><input type=hidden name=clave value="{escape(str(key))}"><input type=hidden name=pagina value="{page}"><input type=hidden name=q value="{escape(q)}">
<button class="btn danger" type=submit>Borrar</button></form>"""
        body += f'<tr>{cells}<td><div class=actions><a class="btn secondary" href="{escape(edit_url)}">Editar</a>{delete_form}</div></td></tr>'
        if edit_key == str(key):
            body += f'<tr><td colspan="{len(spec["cols"]) + 1}">{editor}</td></tr>'
    if not body:
        body = f'<tr><td class=empty colspan="{len(spec["cols"]) + 1}">No hay registros para mostrar.</td></tr>'
    query_args = {"tabla": table, "q": q}
    previous_link = ""
    next_link = ""
    if page > 1:
        previous_link = f'<a class="btn secondary" href="/datos?{urlencode({**query_args, "pagina": page - 1})}">Anterior</a>'
    if len(rows) == page_size:
        next_link = f'<a class="btn secondary" href="/datos?{urlencode({**query_args, "pagina": page + 1})}">Siguiente</a>'
    pagination = f'<div class="pagination"><span>Página {page}</span>{previous_link}{next_link}</div>'
    return f"""<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Datos cargados</title><link rel="icon" type="image/png" href="/static/favicon-t2-v2.png" sizes="64x64">{DATOS_CSS}</head>
<body><div class=wrap><div class=top><div class=brand-title><img class=brand-logo src="/static/logo-t2-v2.webp" width="108" height="108" fetchpriority="high" decoding="async" alt="T2"><div><h1>Datos cargados</h1><p class=muted>Revisión y edición directa de las tablas usadas por el dashboard.</p></div></div>
<div class=nav><a class=secondary href="/inicio">Inicio</a><a class=secondary href="/dashboard">Dashboard</a><a class=secondary href="/pedidos">Pedidos</a><a class=secondary href="/costos-distribucion">Costos</a><a class=secondary href="/foxtrot-calidad">Calidad Foxtrot</a><a class=secondary href="/reporte-fichaya-foxtrot">Reporte FichaYA/Foxtrot</a><a class=secondary href="/admin">Admin</a><a href="/logout">Salir</a></div></div>{alert}
<p><a href="/cumplimiento-comprobantes">Evidencia por comprobante</a> &middot; <a href="/datos-logistica">Ventas en origen</a> &middot; <a href="/datos-api">Archivo API</a></p><div class=tabs>{tabs}</div><form class=tools method=get action="/datos"><input type=hidden name=tabla value="{escape(table)}"><input name=q value="{escape(q)}" placeholder="Buscar en esta tabla"><button class=btn type=submit>Buscar</button><a class="btn secondary" href="/datos?tabla={escape(table)}">Limpiar</a></form>
<div class=panel><div class=table-wrap><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div></div>
{pagination}<p class=muted style="margin-top:12px">Se muestran 100 registros por página. Editar JSON incorrecto puede afectar el dashboard.</p>
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
    return pipeline.fichaya_nombre_map()


def _fichaya_empleados():
    return pipeline.fichaya_empleados()


FICHAYA_MANUAL_FIELDS = pipeline.FICHAYA_MANUAL_FIELDS


def _fichaya_manual_overrides():
    return pipeline.fichaya_ajustes_manuales()


def _fichaya_override_key(rec):
    return pipeline.fichaya_override_key(rec)


def _normalize_fichaya_manual_time(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = pipeline._parse_hora_fichaya(raw)
    if parsed is None or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", raw):
        raise ValueError("Los horarios deben tener formato HH:MM.")
    return parsed.strftime("%H:%M")


def _import_fichaya_empleados(file_obj):
    if hasattr(file_obj, "seek"):
        file_obj.seek(0)
    wb = pipeline.load_workbook(file_obj, read_only=True, data_only=True)
    try:
        ws = wb["Empleados"] if "Empleados" in wb.sheetnames else wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)
        header = None
        for row in rows:
            values = [str(value or "").strip().lower() for value in row]
            if "legajo" in values and "apellido" in values and "nombre" in values:
                header = values
                break
        if header is None:
            raise ValueError("No encontré columnas legajo, apellido y nombre.")
        idx = {name: index for index, name in enumerate(header) if name}

        def cell(row, name):
            index = idx.get(name)
            return row[index] if index is not None and index < len(row) else None

        empleados = {}
        for row in rows:
            legajo = pipeline.fichaya_legajo(cell(row, "legajo"))
            if not legajo:
                continue
            apellido = str(cell(row, "apellido") or "").strip()
            nombre = str(cell(row, "nombre") or "").strip()
            empleados[legajo] = {
                "legajo": legajo,
                "nombre": " ".join(value for value in (apellido, nombre) if value).strip(),
                "sucursal": str(cell(row, "sucursal_nombre") or "").strip(),
                "puesto": str(cell(row, "puesto_nombre") or "").strip(),
                "estado": str(cell(row, "estado") or "").strip(),
                "empresa_id": pipeline._norm_id(cell(row, "empresa_id")),
                "sector_id": pipeline._norm_id(cell(row, "sector_id")),
            }
    finally:
        wb.close()
    pipeline.storage.save_setting("fichaya_empleados", {"valor": empleados})
    return empleados


def _fichaya_lookup_ref(foxtrot_name, mapping=None, empleados=None):
    return pipeline.fichaya_lookup_ref(foxtrot_name, mapping, empleados)


def _fichaya_report_rows(desde="2026-08-01", hasta=None, suc="", chofer="", force_live=False):
    hasta = hasta or date.today().strftime("%Y-%m-%d")
    rows = pipeline.storage.load_fichaya_routes(desde, hasta, suc=suc, chofer=chofer)

    fichadas, warning = {}, ""
    refresh_failed = False
    if rows:
        fechas = [r.get("fecha") for r in rows if r.get("fecha")]
        fichadas_desde = min(fechas) if fechas else desde
        fichadas_hasta = max(fechas) if fechas else hasta
        try:
            fichadas = pipeline.cargar_fichadas(
                fichadas_desde,
                fichadas_hasta,
                force_live=force_live,
            ) if fechas else {}
            if not fichadas:
                warning = "FichaYA no devolvió marcas para las rutas del rango seleccionado."
            elif force_live:
                fechas_marcas = sorted({
                    key[0]
                    for key in fichadas
                    if not key[1].startswith("LEGAJO:")
                })
                if fechas_marcas and fechas_marcas[-1] < fichadas_hasta:
                    warning = (
                        f"FichaYA respondió, pero las marcas recibidas llegan hasta "
                        f"{fechas_marcas[-1]}; hay rutas hasta {fichadas_hasta}."
                    )
        except Exception as exc:
            refresh_failed = True
            fichadas = pipeline.cargar_fichadas_cache(fichadas_desde, fichadas_hasta)
            respaldo = (
                " Se muestran las fichadas guardadas como respaldo."
                if fichadas else
                " No hay fichadas guardadas para este rango."
            )
            warning = f"No se pudo actualizar desde FichaYA ({exc}).{respaldo}"

    if not rows:
        return [], warning

    mapping = _fichaya_name_map()
    empleados = _fichaya_empleados()
    manual_overrides = _fichaya_manual_overrides()
    out = []
    for rec in rows:
        fecha = rec.get("fecha") or ""
        nombre = rec.get("chofer") or ""
        calc = pipeline.calcular_tiempos_fichaya_ruta(
            rec,
            fichadas,
            mapping=mapping,
            empleados=empleados,
            manual_overrides=manual_overrides,
        )
        legajo_fichaya = calc["legajo"]
        nombre_fichaya = calc["nombre"]
        manual_key = calc["manual_key"]
        manual = calc["manual"]
        source_ingreso = calc["sources"]["fichada_ingreso"]
        source_ini = calc["sources"]["inicio_foxtrot"]
        source_fin = calc["sources"]["finalizacion_foxtrot"]
        source_egreso = calc["sources"]["fichada_salida"]
        ingreso = calc["effective"]["fichada_ingreso"]
        ini = calc["effective"]["inicio_foxtrot"]
        fin = calc["effective"]["finalizacion_foxtrot"]
        egreso = calc["effective"]["fichada_salida"]
        tml, ti = calc["tml"], calc["ti"]
        tml_ok, ti_ok = calc["tml_ok"], calc["ti_ok"]
        estado = "OK" if tml_ok and ti_ok else "Faltan fichadas FichaYA"
        if not calc["item_encontrado"] and refresh_failed:
            estado = "Sin actualizar FichaYA"
        if (
            calc["tml_raw"] is not None and not tml_ok
        ) or (
            calc["ti_raw"] is not None and not ti_ok
        ):
            estado = "Revisar"
        out.append({
            "fecha": fecha,
            "sucursal": rec.get("suc") or "",
            "empleado": nombre,
            "legajo_fichaya": legajo_fichaya,
            "empleado_fichaya": nombre_fichaya,
            "fichada_ingreso": _time_to_label(ingreso),
            "inicio_foxtrot": _time_to_label(ini),
            "tml": tml if tml_ok else "",
            "finalizacion_foxtrot": _time_to_label(fin),
            "fichada_salida": _time_to_label(egreso),
            "ti": ti if ti_ok else "",
            "route_id": rec.get("rid") or "",
            "estado": estado,
            "ajuste_manual": any(field in manual for field in FICHAYA_MANUAL_FIELDS),
            "campos_ajuste": [field for field in FICHAYA_MANUAL_FIELDS if field in manual],
            "motivo_ajuste": str(manual.get("motivo") or ""),
            "ajuste_actualizado": str(manual.get("actualizado") or ""),
            "ajuste_usuario": str(manual.get("usuario") or ""),
            "manual_key": manual_key,
            "_source_fichada_ingreso": _time_to_label(source_ingreso),
            "_source_inicio_foxtrot": _time_to_label(source_ini),
            "_source_finalizacion_foxtrot": _time_to_label(source_fin),
            "_source_fichada_salida": _time_to_label(source_egreso),
        })
    return out, warning


def _fichaya_report_page():
    blocked = _require_login()
    if blocked:
        return blocked
    desde = request.args.get("desde") or "2026-08-01"
    hasta = request.args.get("hasta") or date.today().strftime("%Y-%m-%d")
    try:
        desde_date = date.fromisoformat(desde)
        hasta_date = date.fromisoformat(hasta)
    except ValueError:
        desde_date = date.fromisoformat("2026-08-01")
        hasta_date = date.today()
    if desde_date > hasta_date:
        desde_date, hasta_date = hasta_date, desde_date
    desde, hasta = desde_date.isoformat(), hasta_date.isoformat()
    suc = request.args.get("suc") or ""
    chofer = request.args.get("chofer") or ""
    force_live = request.args.get("actualizar") == "1"
    manual_action = request.args.get("ajuste") or ""
    dimensions = pipeline.storage.load_fichaya_dimensions("2026-08-01")
    sucs = dimensions["sucursales"]
    choferes = dimensions["choferes"]
    rows, warning = _fichaya_report_rows(desde, hasta, suc, chofer, force_live=force_live)
    if force_live:
        pipeline.clear_dashboard_cache()
    cache = pipeline.fichaya_cache_info()
    fechas_reporte = sorted({r["fecha"] for r in rows if r.get("fecha")})
    rango_reporte = f" · rutas mostradas {fechas_reporte[0]} a {fechas_reporte[-1]}" if fechas_reporte else ""
    csv_qs = urlencode({"desde": desde, "hasta": hasta, "suc": suc, "chofer": chofer})
    refresh_qs = urlencode({"desde": desde, "hasta": hasta, "suc": suc, "chofer": chofer, "actualizar": "1"})
    opts_suc = '<option value="">Todas</option>' + ''.join(f'<option value="{escape(x)}"{" selected" if x == suc else ""}>{escape(x)}</option>' for x in sucs)
    opts_cho = '<option value="">Todos</option>' + ''.join(f'<option value="{escape(x)}"{" selected" if x == chofer else ""}>{escape(x)}</option>' for x in choferes)
    body_parts = []
    for row in rows:
        fields = set(row.get("campos_ajuste") or [])
        edit_qs = urlencode({
            "ajuste_key": row["manual_key"],
            "desde": desde,
            "hasta": hasta,
            "suc": suc,
            "chofer": chofer,
        })
        if row["ajuste_manual"]:
            title = " · ".join(
                value for value in (
                    row.get("motivo_ajuste"),
                    row.get("ajuste_actualizado"),
                    row.get("ajuste_usuario"),
                ) if value
            )
            adjustment = f'<span class=manual-badge title="{escape(title, quote=True)}">Manual</span>'
        else:
            adjustment = '<span class=source-badge>Origen</span>'

        try:
            date_label = date.fromisoformat(row["fecha"]).strftime("%d/%m/%Y")
        except (TypeError, ValueError):
            date_label = row["fecha"]
        status_key = pipeline._norm_persona_key(row["estado"])
        status_class = (
            "status-ok" if status_key == "OK" else
            "status-review" if status_key == "REVISAR" else
            "status-missing"
        )
        route_id = str(row.get("route_id") or "")
        route_label = (route_id[:8] + "…") if len(route_id) > 8 else route_id
        fichaya_name = row.get("empleado_fichaya") or "Sin nombre asociado"
        legajo = row.get("legajo_fichaya") or "Sin legajo"

        def time_cell(field):
            css_class = "time-cell manual-value" if field in fields else "time-cell"
            return f'<td class="{css_class}">{escape(row[field])}</td>'

        body_parts.append(
            "<tr>"
            f'<td class=identity-date><time datetime="{escape(row["fecha"], quote=True)}">{escape(date_label)}</time></td>'
            f'<td class=identity-branch>{escape(row["sucursal"])}</td>'
            f'<td class="identity-foxtrot person-cell">{escape(row["empleado"])}</td>'
            f'<td class="person-cell fichaya-person"><span>{escape(fichaya_name)}</span><small>{escape(legajo)}</small></td>'
            f"{time_cell('fichada_ingreso')}{time_cell('inicio_foxtrot')}<td class=metric>{escape(str(row['tml']))}</td>"
            f"{time_cell('finalizacion_foxtrot')}{time_cell('fichada_salida')}<td class=metric>{escape(str(row['ti']))}</td>"
            f'<td><span class="status-badge {status_class}">{escape(row["estado"])}</span></td><td class=adjustment-cell>{adjustment}</td>'
            f'<td><code class=route-code title="{escape(route_id, quote=True)}">{escape(route_label)}</code></td>'
            f'<td class=action-cell><a class="row-edit" href="/reporte-fichaya-foxtrot/editar?{edit_qs}">Editar</a></td></tr>'
        )
    body = "".join(body_parts) or '<tr><td class=empty colspan=14>No hay rutas desde agosto con esos filtros.</td></tr>'
    manual_count = sum(1 for row in rows if row.get("ajuste_manual"))
    if warning:
        alert_class = "err" if warning.startswith("No se pudo actualizar") else "warn"
        alert = f'<div class="msg {alert_class}" role="alert">{escape(warning)}</div>'
    elif manual_action in {"guardado", "restaurado"}:
        action_label = (
            "Ajuste manual guardado."
            if manual_action == "guardado" else
            "Se restablecieron los valores de origen."
        )
        alert = f'<div class="msg" role="status">{action_label}</div>'
    elif force_live:
        rango_cache = (
            f" Rango guardado: {cache['desde']} a {cache['hasta']}."
            if cache.get("desde") else ""
        )
        alert = (
            '<div class="msg" role="status">'
            f"FichaYA se actualizó correctamente.{escape(rango_cache)}"
            "</div>"
        )
    else:
        alert = ""
    return f"""<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Reporte FichaYA Foxtrot</title><link rel="icon" type="image/png" href="/static/favicon-t2-v2.png" sizes="64x64">{DATOS_CSS}
<style>
.report-wrap{{width:min(100% - 24px,1560px)}}.tools{{align-items:end}}.tools label{{display:grid;gap:4px;color:#657085;font-size:11px;font-weight:750;text-transform:uppercase}}.tools select,.tools input{{width:100%;min-width:0;min-height:40px;border:1px solid #DCE2EA;border-radius:8px;padding:9px 10px;background:#fff;color:#15233B;font-size:13.5px}}
.msg.warn{{background:#FEF3C7;border-color:#FCD34D;color:#92400E}}.report-panel{{overflow:hidden}}.report-table-wrap{{max-height:calc(100vh - 300px);scrollbar-gutter:stable both-edges}}.report-table{{min-width:1248px;table-layout:fixed;font-size:12px}}.report-table th,.report-table td{{padding:8px 9px;vertical-align:middle;overflow:hidden;text-overflow:ellipsis}}.report-table tbody tr:hover td{{background:#F8FAFC}}.report-table tbody tr:hover td.manual-value{{background:#FEF3C7}}
.report-table .c-date{{width:90px}}.report-table .c-branch{{width:92px}}.report-table .c-foxtrot{{width:164px}}.report-table .c-fichaya{{width:185px}}.report-table .c-time{{width:74px}}.report-table .c-metric{{width:52px}}.report-table .c-status{{width:86px}}.report-table .c-adjustment{{width:68px}}.report-table .c-route{{width:96px}}.report-table .c-action{{width:75px}}
.column-groups th{{top:0;height:27px;padding:5px 9px;background:#E8EDF3;color:#4C5B70;font-size:10.5px;text-align:center;border-right:1px solid #DCE2EA}}.column-labels th{{top:27px;height:34px;background:#F8FAFC;font-size:10.5px;line-height:1.1;white-space:normal}}.person-cell{{white-space:normal;line-height:1.2;font-weight:650}}.fichaya-person small{{display:block;margin-top:3px;color:#657085;font-size:10.5px;font-weight:600}}.time-cell{{font-variant-numeric:tabular-nums;text-align:center}}.metric{{font-weight:800;text-align:center;font-variant-numeric:tabular-nums}}
.manual-badge,.source-badge,.status-badge{{display:inline-block;border-radius:999px;padding:3px 7px;font-size:10.5px;font-weight:750;white-space:nowrap}}.manual-badge{{background:#FEF3C7;color:#92400E}}.source-badge{{background:#E8EDF3;color:#657085}}.status-ok{{background:#DCFCE7;color:#166534}}.status-review{{background:#FEF3C7;color:#92400E}}.status-missing{{background:#FEE2E2;color:#991B1B}}td.manual-value{{background:#FFFBEB;font-weight:800;color:#92400E}}
.identity-date,.action-cell{{position:sticky;z-index:2;background:#fff}}.identity-date{{left:0}}.action-cell{{right:0;box-shadow:-1px 0 0 #E1E7EE}}.column-labels .identity-date,.column-labels .action-cell{{z-index:6;background:#F8FAFC}}.route-code{{display:block;color:#4C5B70;font:11px ui-monospace,SFMono-Regular,Consolas,monospace;white-space:nowrap}}.row-edit{{display:inline-flex;align-items:center;justify-content:center;min-height:32px;border:1px solid #CAD3DF;border-radius:7px;padding:5px 9px;background:#fff;color:#15233B;text-decoration:none;font-weight:700}}.row-edit:hover{{background:#F1F5F9}}.table-summary{{display:flex;gap:8px 18px;flex-wrap:wrap;margin-top:10px}}
@media(min-width:1100px){{.identity-branch,.identity-foxtrot{{position:sticky;z-index:2;background:#fff}}.identity-branch{{left:90px}}.identity-foxtrot{{left:182px;box-shadow:1px 0 0 #E1E7EE}}.column-labels .identity-branch,.column-labels .identity-foxtrot{{z-index:6;background:#F8FAFC}}}}
@media(max-width:900px){{.report-wrap{{width:min(100% - 16px,1560px)}}.tools>*{{width:100%}}.tools select,.tools input{{min-height:44px}}.report-table-wrap{{max-height:calc(100vh - 330px)}}.row-edit{{min-height:40px}}}}
</style></head>
<body><div class="wrap report-wrap"><div class=top><div class=brand-title><img class=brand-logo src="/static/logo-t2-v2.webp" width="108" height="108" fetchpriority="high" decoding="async" alt="T2"><div><h1>Reporte FichaYA + Foxtrot</h1><p class=muted>Desde agosto 2026. TML = inicio Foxtrot - 07:30 (base fija). TI = fichada salida - finalización Foxtrot.</p></div></div>
<div class=nav><a class=secondary href="/inicio">Inicio</a><a class=secondary href="/dashboard">Dashboard</a><a class=secondary href="/pedidos">Pedidos</a><a class=secondary href="/costos-distribucion/dashboard">Costos</a><a class=secondary href="/datos">Datos</a><a class=secondary href="/admin">Admin</a><a href="/logout">Salir</a></div></div>{alert}
<form class=tools method=get action="/reporte-fichaya-foxtrot">
<label>Desde <input type=date name=desde value="{escape(desde)}"></label>
<label>Hasta <input type=date name=hasta value="{escape(hasta)}"></label>
<label>Sucursal <select name=suc>{opts_suc}</select></label>
<label>Chofer <select name=chofer>{opts_cho}</select></label>
<button class=btn type=submit>Filtrar</button><a class=btn href="/reporte-fichaya-foxtrot?{refresh_qs}">Actualizar desde FichaYA</a><a class="btn secondary" href="/reporte-fichaya-foxtrot">Limpiar</a><a class="btn secondary" href="/asociar-fichaya">Asociar nombres</a><a class=btn href="/reporte-fichaya-foxtrot.csv?{csv_qs}">Descargar CSV</a>
</form>
<div class="panel report-panel"><div class="table-wrap report-table-wrap"><table class=report-table><colgroup><col class=c-date><col class=c-branch><col class=c-foxtrot><col class=c-fichaya><col class=c-time><col class=c-time><col class=c-metric><col class=c-time><col class=c-time><col class=c-metric><col class=c-status><col class=c-adjustment><col class=c-route><col class=c-action></colgroup><thead>
<tr class=column-groups><th colspan=4>Ruta y empleados</th><th colspan=3>Inicio</th><th colspan=3>Cierre</th><th colspan=3>Control</th><th>Acción</th></tr>
<tr class=column-labels><th class=identity-date>Fecha</th><th class=identity-branch>Sucursal</th><th class=identity-foxtrot>Empleado Foxtrot</th><th>Empleado FichaYA / legajo</th><th>Ingreso</th><th>Inicio</th><th>TML</th><th>Fin</th><th>Salida</th><th>TI</th><th>Estado</th><th>Ajuste</th><th>Route ID</th><th class=action-cell>Acción</th></tr></thead><tbody>{body}</tbody></table></div></div>
<p class="muted table-summary"><span>Filas: {len(rows)}{rango_reporte}</span><span>Ajustes manuales: {manual_count}</span><span>Fichadas guardadas: {cache['total']} registros{f" · rango {cache['desde']} a {cache['hasta']}" if cache['desde'] else ""}{f" · actualizado {cache['actualizado']}" if cache['actualizado'] else ""}</span></p>
</div></body></html>"""


def _fichaya_manual_params(values):
    desde = values.get("desde") or "2026-08-01"
    hasta = values.get("hasta") or date.today().strftime("%Y-%m-%d")
    try:
        desde_date = date.fromisoformat(desde)
        hasta_date = date.fromisoformat(hasta)
    except ValueError:
        desde_date = date.fromisoformat("2026-08-01")
        hasta_date = date.today()
    if desde_date > hasta_date:
        desde_date, hasta_date = hasta_date, desde_date
    return {
        "desde": desde_date.isoformat(),
        "hasta": hasta_date.isoformat(),
        "suc": values.get("suc") or "",
        "chofer": values.get("chofer") or "",
    }


def _fichaya_manual_row(ajuste_key, params):
    rows, warning = _fichaya_report_rows(
        params["desde"],
        params["hasta"],
        params["suc"],
        params["chofer"],
    )
    row = next((item for item in rows if item.get("manual_key") == ajuste_key), None)
    return row, warning


def _fichaya_manual_edit_page(row, params, error="", source_warning="", form_values=None):
    form_values = form_values or {}
    values = {
        field: str(form_values.get(field, row.get(field) or ""))
        for field in FICHAYA_MANUAL_FIELDS
    }
    motivo = str(form_values.get("motivo", row.get("motivo_ajuste") or ""))
    labels = {
        "fichada_ingreso": "Fichada ingreso",
        "inicio_foxtrot": "Inicio Foxtrot",
        "finalizacion_foxtrot": "Finalización Foxtrot",
        "fichada_salida": "Fichada salida",
    }
    fields_html = ""
    for field in FICHAYA_MANUAL_FIELDS:
        source = row.get("_source_" + field) or ""
        source_label = source or "Sin dato"
        fields_html += f"""<label>{labels[field]}
<input type=time name="{field}" value="{escape(values[field], quote=True)}" step=60>
<small>Origen: {escape(source_label)}</small></label>"""

    hidden = "".join(
        f'<input type=hidden name="{escape(key)}" value="{escape(value, quote=True)}">'
        for key, value in params.items()
    ) + f'<input type=hidden name=ajuste_key value="{escape(row["manual_key"], quote=True)}">'
    back_url = "/reporte-fichaya-foxtrot?" + urlencode(params)
    alert = ""
    if error:
        alert = f'<div class="msg err" role=alert>{escape(error)}</div>'
    elif source_warning:
        alert = f'<div class="msg warn" role=alert>{escape(source_warning)}</div>'

    audit = ""
    if row.get("ajuste_manual"):
        audit_parts = [
            value for value in (
                row.get("ajuste_actualizado"),
                row.get("ajuste_usuario"),
            ) if value
        ]
        audit = f'<p class=audit>Último ajuste: {escape(" · ".join(audit_parts) or "sin detalle")}</p>'
    restore_form = ""
    if row.get("ajuste_manual"):
        restore_form = f"""<form method=post action="/reporte-fichaya-foxtrot/ajuste/eliminar" onsubmit="return confirm('¿Restablecer todos los horarios a los valores de origen?')">
{hidden}<button class="btn danger" type=submit>Restablecer origen</button></form>"""

    return f"""<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Editar registro FichaYA</title><link rel="icon" type="image/png" href="/static/favicon-t2-v2.png" sizes="64x64">{DATOS_CSS}
<style>.editor{{max-width:780px;padding:20px;overflow:visible}}.record-meta{{display:flex;gap:8px 18px;flex-wrap:wrap;margin-bottom:18px;color:#657085;font-size:13px}}.record-meta strong{{color:#15233B}}.field-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.field-grid label{{display:grid;gap:6px;color:#657085;font-size:11.5px;font-weight:750;text-transform:uppercase}}.field-grid input,.field-grid textarea{{width:100%;min-height:44px;border:1px solid #CAD3DF;border-radius:8px;padding:9px 10px;background:#fff;color:#15233B;font-size:15px}}.field-grid textarea{{min-height:88px;resize:vertical;font-family:inherit}}.field-grid small{{font-size:12px;font-weight:500;text-transform:none;color:#657085}}.full{{grid-column:1/-1}}.editor-actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px}}.editor-actions form{{margin:0}}.audit{{margin:14px 0 0;color:#657085;font-size:12.5px}}.msg.warn{{background:#FEF3C7;border-color:#FCD34D;color:#92400E}}@media(max-width:700px){{.editor{{padding:16px}}.field-grid{{grid-template-columns:1fr}}.full{{grid-column:auto}}.editor-actions,.editor-actions form,.editor-actions .btn{{width:100%}}}}</style></head>
<body><div class=wrap><div class=top><div class=brand-title><img class=brand-logo src="/static/logo-t2-v2.webp" width="108" height="108" fetchpriority="high" decoding="async" alt="T2"><div><h1>Editar registro</h1><p class=muted>Reporte FichaYA + Foxtrot</p></div></div><div class=nav><a class=secondary href="{escape(back_url, quote=True)}">Volver al reporte</a><a href="/logout">Salir</a></div></div>{alert}
<div class="panel editor"><div class=record-meta><span><strong>{escape(row['fecha'])}</strong></span><span>{escape(row['sucursal'])}</span><span>{escape(row['empleado'])}</span><span>Legajo {escape(row['legajo_fichaya'] or 'sin asociar')}</span></div>
<form method=post action="/reporte-fichaya-foxtrot/ajuste/guardar">{hidden}<div class=field-grid>{fields_html}
<label class=full>Motivo del ajuste<textarea name=motivo maxlength=300 required>{escape(motivo)}</textarea></label></div>
<div class=editor-actions><button class=btn type=submit>Guardar ajuste</button><a class="btn secondary" href="{escape(back_url, quote=True)}">Cancelar</a></div></form>
{audit}<div class=editor-actions>{restore_form}</div></div>
</div></body></html>"""


def _fichaya_mapping_page(msg="", err=False):
    blocked = _require_login()
    if blocked:
        return blocked
    mapping = _fichaya_name_map()
    empleados = _fichaya_empleados()
    dimensions = pipeline.storage.load_fichaya_dimensions("2026-08-01")
    dpo = pipeline.cargar_dpo_gkpis()
    personas = {pipeline._norm_persona_key(n): n for n in dimensions["choferes"] if n}
    for row in dpo.get("rows", []):
        for field in ("chofer", "ayudante1", "ayudante2"):
            name = str(row.get(field) or "").strip()
            if name:
                personas.setdefault(pipeline._norm_persona_key(name), name)
    # Mantener editables asociaciones anteriores aunque una fuente esté incompleta.
    for name in mapping:
        personas.setdefault(name, name)
    choferes = sorted(personas.values(), key=pipeline._norm_persona_key)
    if dpo.get("error"):
        msg = " ".join(filter(None, [msg, "DPO incompleto: " + dpo["error"]]))
        err = True
    emp_options = "".join(
        f'<option value="{escape(leg)}">{escape(leg)} · {escape(emp.get("nombre", ""))} · {escape(emp.get("sucursal", ""))}</option>'
        for leg, emp in sorted(empleados.items(), key=lambda kv: kv[1].get("nombre", ""))
    )
    body = ""
    for name in choferes:
        norm = pipeline._norm_persona_key(name)
        mapped = mapping.get(norm, {})
        mapped_legajo = pipeline.fichaya_legajo(mapped.get("legajo") if isinstance(mapped, dict) else mapped)
        mapped_name = (empleados.get(mapped_legajo) or {}).get("nombre", "")
        missing_option = (f'<option value="{escape(mapped_legajo)}" selected>{escape(mapped_legajo)} · Fuera del catálogo: revisar</option>'
                          if mapped_legajo and mapped_legajo not in empleados else "")
        body += f"""<tr><td>{escape(name)}</td><td><select name="map__{escape(norm)}"><option value="">Sin vincular</option>{missing_option}{emp_options.replace('value="' + escape(mapped_legajo) + '"', 'value="' + escape(mapped_legajo) + '" selected', 1) if mapped_legajo else emp_options}</select><small>{escape(mapped_name)}</small></td></tr>"""
    if not body:
        body = '<tr><td class=empty colspan=2>No hay personas en las fuentes consultadas.</td></tr>'
    alert = f'<div class="msg{" err" if err else ""}">{escape(msg)}</div>' if msg else ""
    return f"""<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Asociar nombres FichaYA</title><link rel="icon" type="image/png" href="/static/favicon-t2-v2.png" sizes="64x64">{DATOS_CSS}
<style>td select{{width:100%;min-width:300px;border:1px solid #DCE2EA;border-radius:8px;padding:8px 10px;background:#fff}}td small{{display:block;color:#657085;margin-top:4px}}.panel{{max-width:960px}}.upload{{max-width:960px;background:#fff;border:1px solid #DCE2EA;border-radius:10px;padding:14px;margin-bottom:16px}}.upload input[type=file]{{max-width:100%;margin:10px 8px 0 0}}@media(max-width:900px){{td select{{min-width:240px;min-height:44px}}.upload input[type=file],.upload .btn{{width:100%;min-height:44px;margin-right:0}}}}</style></head>
<body><div class=wrap><div class=top><div class=brand-title><img class=brand-logo src="/static/logo-t2-v2.webp" width="108" height="108" fetchpriority="high" decoding="async" alt="T2"><div><h1>Asociar empleados de reparto / FichaYA</h1><p class=muted>Relacioná cada chofer de Foxtrot contra el legajo de FichaYA. El reporte busca fichadas por legajo primero.</p></div></div>
<div class=nav><a class=secondary href="/inicio">Inicio</a><a class=secondary href="/reporte-fichaya-foxtrot">Reporte</a><a class=secondary href="/dashboard">Dashboard</a><a class=secondary href="/pedidos">Pedidos</a><a class=secondary href="/costos-distribucion/dashboard">Costos</a><a class=secondary href="/admin">Admin</a><a href="/logout">Salir</a></div></div>{alert}
<form class=upload method=post action="/asociar-fichaya/importar" enctype="multipart/form-data"><b>Importar empleados FichaYA</b><p class=muted>Subí el Excel exportado desde FichaYA para cargar legajos y nombres.</p><input type=file name=empleados accept=".xlsx,.xls" required> <button class=btn type=submit>Importar empleados</button></form>
<form method=post action="/asociar-fichaya/guardar"><div class=panel><div class=table-wrap><table><thead><tr><th>Nombre en Foxtrot / DPO</th><th>Legajo / empleado FichaYA</th></tr></thead><tbody>{body}</tbody></table></div></div>
<button class=btn type=submit style="margin-top:16px">Guardar asociaciones</button></form>
<p><a href="/equipos-reparto">Ver equipos de Casa Central</a></p><p class=muted style="margin-top:12px">Empleados FichaYA cargados: {len(empleados)}. El reporte usa esta asociación al calcular TML/TI.</p>
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
    blocked = _require_login()
    if blocked:
        return blocked
    return _dashboard_response()


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET" and _is_logged_in():
        return redirect(_safe_next_url(request.args.get("next")))
    if request.method == "POST":
        if not ADMIN_PASSWORD:
            return Response(_login_page("Falta configurar ADMIN_PASSWORD en Railway.", err=True), mimetype="text/html", status=500)
        user = request.form.get("user", "")
        password = request.form.get("password", "")
        if secrets.compare_digest(user, ADMIN_USER) and secrets.compare_digest(password, ADMIN_PASSWORD):
            session["admin_logged_in"] = True
            session["admin_user"] = user
            return redirect(_safe_next_url(request.form.get("next")))
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
    return Response(_admin_page(session.pop('logistics_notice', '')), mimetype="text/html")


from api_import_view import register_api_import_view
register_api_import_view(app, _require_login, DATOS_CSS)
from logistics_db_view import register_logistics_db_view
register_logistics_db_view(app, _require_login, DATOS_CSS)


@app.route("/datos")
def datos():
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        return Response(
            _datos_page(
                table=request.args.get("tabla", "rutas"),
                q=request.args.get("q", ""),
                msg=request.args.get("msg", ""),
                err=request.args.get("err") == "1",
                edit_key=request.args.get("editar", ""),
                page=request.args.get("pagina", 1),
            ),
            mimetype="text/html",
        )
    except Exception as exc:
        return Response(_data_unavailable_page("Datos cargados", exc), mimetype="text/html", status=503)


@app.route("/foxtrot")
@app.route("/foxtrot_calidad")
@app.route("/calidad-foxtrot")
@app.route("/foxtrot-calidad/")
@app.route("/foxtrot-calidad")
def foxtrot_calidad():
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        return Response(
            _foxtrot_calidad_page(
                q=request.args.get("q", ""),
                fecha=request.args.get("fecha", ""),
                msg=request.args.get("msg", ""),
                err=request.args.get("err") == "1",
            ),
            mimetype="text/html",
        )
    except Exception as exc:
        return Response(_data_unavailable_page("Calidad Foxtrot", exc), mimetype="text/html", status=503)


@app.route("/reporte-fichaya-foxtrot")
def reporte_fichaya_foxtrot():
    try:
        return _fichaya_report_page()
    except Exception as exc:
        return Response(_data_unavailable_page("Reporte FichaYA / Foxtrot", exc), mimetype="text/html", status=503)


@app.route("/reporte-fichaya-foxtrot/editar")
def reporte_fichaya_foxtrot_editar():
    blocked = _require_login()
    if blocked:
        return blocked
    params = _fichaya_manual_params(request.args)
    ajuste_key = request.args.get("ajuste_key") or ""
    row, warning = _fichaya_manual_row(ajuste_key, params)
    if row is None:
        return Response(
            _data_unavailable_page(
                "Editar registro FichaYA / Foxtrot",
                "La fila seleccionada ya no existe dentro del rango del reporte.",
            ),
            mimetype="text/html",
            status=404,
        )
    return Response(
        _fichaya_manual_edit_page(row, params, source_warning=warning),
        mimetype="text/html",
    )


@app.route("/reporte-fichaya-foxtrot/ajuste/guardar", methods=["POST"])
def reporte_fichaya_foxtrot_ajuste_guardar():
    blocked = _require_login()
    if blocked:
        return blocked
    params = _fichaya_manual_params(request.form)
    ajuste_key = request.form.get("ajuste_key") or ""
    row, warning = _fichaya_manual_row(ajuste_key, params)
    if row is None:
        return Response(
            _data_unavailable_page(
                "Editar registro FichaYA / Foxtrot",
                "No se encontró la fila que se intentó modificar.",
            ),
            mimetype="text/html",
            status=404,
        )

    form_values = {
        field: str(request.form.get(field) or "").strip()
        for field in FICHAYA_MANUAL_FIELDS
    }
    motivo = str(request.form.get("motivo") or "").strip()
    form_values["motivo"] = motivo
    try:
        normalized = {
            field: _normalize_fichaya_manual_time(form_values[field])
            for field in FICHAYA_MANUAL_FIELDS
        }
    except ValueError as exc:
        return Response(
            _fichaya_manual_edit_page(
                row,
                params,
                error=str(exc),
                source_warning=warning,
                form_values=form_values,
            ),
            mimetype="text/html",
            status=400,
        )

    changes = {
        field: value
        for field, value in normalized.items()
        if value != str(row.get("_source_" + field) or "")
    }
    if changes and not motivo:
        return Response(
            _fichaya_manual_edit_page(
                row,
                params,
                error="Ingresá un motivo para guardar el ajuste manual.",
                source_warning=warning,
                form_values=form_values,
            ),
            mimetype="text/html",
            status=400,
        )

    overrides = dict(_fichaya_manual_overrides())
    if changes:
        overrides[ajuste_key] = {
            **changes,
            "motivo": motivo[:300],
            "actualizado": datetime.now().astimezone().isoformat(timespec="seconds"),
            "usuario": str(session.get("admin_user") or ADMIN_USER),
        }
        action = "guardado"
    else:
        overrides.pop(ajuste_key, None)
        action = "restaurado"
    pipeline.storage.save_setting("fichaya_ajustes_manuales", {"valor": overrides})
    return redirect("/reporte-fichaya-foxtrot?" + urlencode({**params, "ajuste": action}))


@app.route("/reporte-fichaya-foxtrot/ajuste/eliminar", methods=["POST"])
def reporte_fichaya_foxtrot_ajuste_eliminar():
    blocked = _require_login()
    if blocked:
        return blocked
    params = _fichaya_manual_params(request.form)
    ajuste_key = request.form.get("ajuste_key") or ""
    overrides = dict(_fichaya_manual_overrides())
    overrides.pop(ajuste_key, None)
    pipeline.storage.save_setting("fichaya_ajustes_manuales", {"valor": overrides})
    return redirect("/reporte-fichaya-foxtrot?" + urlencode({**params, "ajuste": "restaurado"}))


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
        force_live=request.args.get("actualizar") == "1",
    )
    buf = StringIO()
    fields = ["fecha", "sucursal", "empleado", "legajo_fichaya", "empleado_fichaya", "fichada_ingreso", "inicio_foxtrot", "tml", "finalizacion_foxtrot", "fichada_salida", "ti", "estado", "ajuste_manual", "motivo_ajuste", "ajuste_actualizado", "ajuste_usuario", "route_id"]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    resp = Response(buf.getvalue(), mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = "attachment; filename=reporte_fichaya_foxtrot.csv"
    if warning:
        resp.headers["X-Report-Warning"] = warning[:500]
    return resp


@app.route("/asociar-fichaya")
def asociar_fichaya():
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        return _fichaya_mapping_page(
            msg=request.args.get("msg", ""),
            err=request.args.get("err") == "1",
        )
    except Exception as exc:
        return Response(_data_unavailable_page("Asociar nombres", exc), mimetype="text/html", status=503)


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
    mapping = dict(_fichaya_name_map())
    for key, value in request.form.items():
        if not key.startswith("map__"):
            continue
        norm = key[5:]
        legajo = pipeline.fichaya_legajo(value)
        if legajo:
            emp = empleados.get(legajo)
            if not emp:
                return redirect(url_for("asociar_fichaya", msg="El legajo seleccionado no existe en el catálogo. No se guardaron cambios.", err=1))
            mapping[norm] = {"legajo": legajo, "nombre": emp.get("nombre", "")}
        else:
            mapping.pop(norm, None)
    pipeline.storage.save_setting("fichaya_nombre_map", {"valor": mapping})
    return redirect(url_for("asociar_fichaya", msg=f"Asociaciones guardadas: {len(mapping)}."))


def _equipo_persona(name, mapping, empleados):
    name = str(name or "").strip()
    ref = pipeline.fichaya_lookup_ref(name, mapping, empleados)
    legajo = ref.get("legajo", "")
    emp = empleados.get(legajo)
    estado = pipeline._norm_persona_key((emp or {}).get("estado", ""))
    status = "Sin vincular"
    if legajo:
        status = "Vinculado" if emp else "Legajo fuera del catálogo"
    if emp and estado and estado not in {"ACTIVO", "ACTIVE", "1"}:
        status = "Revisar estado: " + str(emp["estado"])
    return {"nombre": name, "legajo": legajo, "estado": status, "ok": status == "Vinculado"}


@app.route("/equipos-reparto")
def equipos_reparto():
    blocked = _require_login()
    if blocked:
        return blocked
    today = date.today().isoformat()
    desde = request.args.get("desde", today[:8] + "01")
    hasta = request.args.get("hasta", today)
    camion = request.args.get("camion", "").strip()
    pendientes = request.args.get("pendientes") == "1"
    try:
        if date.fromisoformat(desde) > date.fromisoformat(hasta):
            raise ValueError()
    except ValueError:
        return Response("Rango de fechas inválido.", status=400)
    try:
        source = pipeline.cargar_dpo_gkpis()
        mapping, empleados = _fichaya_name_map(), _fichaya_empleados()
        central = [r for r in source.get("rows", []) if str(r.get("sucursal_id")) == "1"]
        camiones = sorted({str(r.get("nro_camion") or r.get("camion") or "") for r in central})
        rows = []
        for raw in central:
            truck = str(raw.get("nro_camion") or raw.get("camion") or "")
            if not (desde <= raw["fecha"] <= hasta) or (camion and camion != truck):
                continue
            members = [dict(_equipo_persona(raw.get(field), mapping, empleados), rol=role)
                       for field, role in (("chofer", "Chofer"), ("ayudante1", "Ayudante 1"), ("ayudante2", "Ayudante 2"))
                       if str(raw.get(field) or "").strip()]
            issues = []
            if not str(raw.get("chofer") or "").strip():
                issues.append("Falta chofer")
            if any(not p["ok"] for p in members):
                issues.append("Vinculación pendiente")
            if raw.get("personas") is not None and raw["personas"] != len(members):
                issues.append("Cantidad de personas no coincide")
            ids = [p["legajo"] or pipeline._norm_persona_key(p["nombre"]) for p in members]
            if len(set(ids)) != len(ids):
                issues.append("Integrante repetido")
            if raw["fecha"] > today:
                issues.append("Fecha futura")
            rows.append({**raw, "integrantes": members, "avisos": issues, "numero": truck})
        counts = {}
        for row in rows:
            key = (row["fecha"], row["numero"])
            counts[key] = counts.get(key, 0) + 1
        for row in rows:
            if counts[(row["fecha"], row["numero"])] > 1:
                row["avisos"].append("Varias filas para el día/camión: revisar salidas o duplicados")
        if pendientes:
            rows = [r for r in rows if r["avisos"]]
        rows.sort(key=lambda r: (r["fecha"], r["numero"]), reverse=True)
        return render_template("equipos_reparto.html", rows=rows, camiones=camiones,
                               desde=desde, hasta=hasta, camion=camion, pendientes=pendientes,
                               warning=source.get("error", ""), css=DATOS_CSS)
    except Exception as exc:
        return Response(_data_unavailable_page("Equipos de Casa Central", exc), status=503)


@app.route("/equipos-reparto/historico")
def equipos_historico():
    blocked = _require_login()
    if blocked:
        return blocked
    today = date.today().isoformat()
    desde, hasta = request.args.get("desde", today[:8] + "01"), request.args.get("hasta", today)
    try:
        equipos_service.validate_range(desde, hasta)
    except ValueError as exc:
        return Response(escape(str(exc)), status=400)
    try:
        days = pipeline.storage.load_equipo_days(desde, hasta)
        routes = pipeline.storage.load_all() if days else {}
        for fecha, day in days.items():
            available = equipos_service.central_routes(routes, fecha)
            # Mostrar también rutas eliminadas o modificadas después de asignarlas.
            day["rutas_actuales"] = available
            day["rutas_visibles"] = sorted(set(available) | set(day["rutas"]))
            day["rutas_cambiadas"] = [rid for rid, saved in day["rutas"].items()
                                      if equipos_service.route_snapshot(available.get(rid, {})) != saved["ruta_origen"]]
        token = session.setdefault("equipos_csrf", secrets.token_urlsafe(32))
        return render_template("equipos_historico.html", days=sorted(days.values(), key=lambda d: d["fecha"], reverse=True),
                               desde=desde, hasta=hasta, token=token, css=DATOS_CSS,
                               msg=request.args.get("msg", ""), error=request.args.get("err") == "1")
    except Exception as exc:
        return Response(_data_unavailable_page("Histórico de equipos", exc), status=503)


def _kpis_csrf_valid():
    expected = session.get("kpis_csrf", "")
    return bool(expected) and secrets.compare_digest(expected, request.form.get("csrf", ""))


@app.route("/kpis-fichaya")
def kpis_fichaya():
    blocked = _require_login()
    if blocked:
        return blocked
    today = date.today().isoformat()
    try:
        token = session.setdefault("kpis_csrf", secrets.token_urlsafe(32))
        return render_template("kpis_fichaya.html", css=DATOS_CSS, token=token,
                               cfg=fichaya_kpis_service.config(), metrics=fichaya_kpis_service.METRICS,
                               runs=kpi_storage.recent(), desde=today[:8] + "01", hasta=today,
                               msg=request.args.get("msg", ""))
    except Exception as exc:
        return Response(_data_unavailable_page("KPIs a FichaYA", exc), status=503)


@app.route("/kpis-fichaya/configurar", methods=["POST"])
def kpis_fichaya_configurar():
    blocked = _require_login()
    if blocked:
        return blocked
    if not _kpis_csrf_valid():
        return Response("Recargá el formulario antes de guardar.", status=400)
    try:
        selected = {key: request.form.get("codigo_" + key, "") for key in fichaya_kpis_service.METRICS
                    if request.form.get("usar_" + key) == "1"}
        fichaya_kpis_service.save_config(request.form.get("empresa_id"), selected, session.get("admin_user") or "admin")
        msg = "Configuración guardada. Los indicadores y objetivos deben estar creados en FichaYA para los sectores de choferes y ayudantes."
    except ValueError as exc:
        msg = str(exc)
    return redirect(url_for("kpis_fichaya", msg=msg))


@app.route("/kpis-fichaya/preparar", methods=["POST"])
def kpis_fichaya_preparar():
    blocked = _require_login()
    if blocked:
        return blocked
    if not _kpis_csrf_valid():
        return Response("Recargá el formulario antes de preparar.", status=400)
    try:
        run = fichaya_kpis_service.create_draft(request.form.get("desde", ""), request.form.get("hasta", ""), session.get("admin_user") or "admin", allow_partial=request.form.get("solo_validos") == "1")
        return redirect(url_for("kpis_fichaya_envio", identifier=run["id"]))
    except ValueError as exc:
        return redirect(url_for("kpis_fichaya", msg=str(exc)))
    except Exception as exc:
        return Response(_data_unavailable_page("Preparar KPIs", exc), status=503)


@app.route("/kpis-fichaya/envios/<identifier>")
def kpis_fichaya_envio(identifier):
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        run = kpi_storage.load(identifier)
    except ValueError:
        run = {}
    if not run or not identifier.startswith("run_"):
        return Response("Envío no encontrado.", status=404)
    try:
        page = max(1, int(request.args.get("pagina", "1")))
    except ValueError:
        page = 1
    pages = max(1, math.ceil(len(run["resultados"]) / 100))
    page = min(page, pages)
    start = (page - 1) * 100
    preview = [(i + 1, row, run["evidencia"][i]) for i, row in enumerate(run["resultados"]) if start <= i < start + 100]
    token = session.setdefault("kpis_csrf", secrets.token_urlsafe(32))
    return render_template("kpis_envio.html", css=DATOS_CSS, run=run, preview=preview, page=page, pages=pages,
                           token=token, msg=request.args.get("msg", ""))


@app.route("/kpis-fichaya/envios/<identifier>/enviar", methods=["POST"])
def kpis_fichaya_enviar(identifier):
    blocked = _require_login()
    if blocked:
        return blocked
    if not _kpis_csrf_valid():
        return jsonify(error="Recargá el formulario antes de enviar."), 400
    try:
        run = fichaya_kpis_service.send_next(identifier, session.get("admin_user") or "admin")
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(estado=run["estado"], guardados=sum(len(c["payload"]["resultados"]) for c in run["lotes"] if c["estado"] == "guardado"))
        return redirect(url_for("kpis_fichaya_envio", identifier=identifier))
    except ValueError as exc:
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(error=str(exc)), 409
        return redirect(url_for("kpis_fichaya_envio", identifier=identifier, msg=str(exc)))
    except Exception:
        # El lote persistido permite recuperar una respuesta incierta. No registrar
        # excepciones de transporte que puedan contener detalles de credenciales.
        return jsonify(error="No se pudo confirmar el envío. Revisá su historial antes de reintentar."), 503


@app.route("/equipos-reparto/historico/guardar", methods=["POST"])
def equipos_historico_guardar():
    blocked = _require_login()
    if blocked:
        return blocked
    expected = session.get("equipos_csrf", "")
    if not expected or not secrets.compare_digest(expected, request.form.get("csrf", "")):
        return Response("La sesión del formulario venció. Recargá la página.", status=400)
    desde, hasta = request.form.get("desde", ""), request.form.get("hasta", "")
    try:
        equipos_service.validate_range(desde, hasta)
    except ValueError as exc:
        return Response(escape(str(exc)), status=400)
    try:
        action = request.form.get("accion", "")
        user = session.get("admin_user") or "admin"
        if action == "importar":
            result = equipos_service.sync_history(desde, hasta, user)
            msg = (f'Días guardados: {result["guardados"]}. Sin cambios: {result["existentes"]}. '
                   f'Días con cambios pendientes de revisión: {result["cambiados"]}. '
                   'Los días ya guardados se conservaron. No se importan fechas futuras.')
            if result["fechas_cambiadas"]:
                msg += " Revisar: " + ", ".join(sorted(result["fechas_cambiadas"])) + "."
        elif action in {"actualizar", "asignar"}:
            fecha = request.form.get("fecha", "")
            date.fromisoformat(fecha)
            if not desde <= fecha <= hasta:
                raise ValueError("El día debe estar dentro del rango seleccionado.")
            revision = int(request.form.get("revision", ""))
            reason = request.form.get("motivo", "").strip()
            if action == "actualizar":
                equipos_service.revise_day(fecha, revision, user, reason)
                msg = "Equipo actualizado desde DPO. La versión anterior quedó en el historial y las rutas se volvieron a evaluar."
            else:
                equipos_service.assign_route(fecha, revision, request.form.get("ruta", ""),
                                             request.form.get("equipo", ""), user, reason)
                msg = "Asignación guardada en el historial."
        else:
            raise ValueError("Acción desconocida.")
        return redirect(url_for("equipos_historico", desde=desde, hasta=hasta, msg=msg))
    except ValueError as exc:
        return redirect(url_for("equipos_historico", desde=desde, hasta=hasta, msg=str(exc), err=1))
    except Exception as exc:
        return Response(_data_unavailable_page("Guardar histórico de equipos; revisá los días guardados antes de reintentar", exc), status=503)


@app.route("/foxtrot-calidad/guardar", methods=["POST"])
def foxtrot_calidad_guardar():
    blocked = _require_login()
    if blocked:
        return blocked
    rid = request.form.get("rid", "")
    try:
        current = pipeline.storage.get_record("rutas", rid)
        if current is None:
            raise ValueError("No se encontró la ruta.")
        rec = dict(current)
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
    page = request.form.get("pagina", "1")
    q = request.form.get("q", "")
    try:
        if table not in TABLES:
            raise ValueError("Tabla no permitida.")
        current = pipeline.storage.get_record(table, key)
        if current is None:
            raise ValueError("El registro ya no existe.")
        rec = dict(current)
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
        return redirect(url_for("datos", tabla=table or "rutas", q=q, pagina=page, editar=key, msg=f"Error guardando: {e}", err=1))
    return redirect(url_for("datos", tabla=table, q=q, pagina=page, editar=key, msg="Registro guardado."))


@app.route("/datos/borrar", methods=["POST"])
def datos_borrar():
    blocked = _require_login()
    if blocked:
        return blocked
    table = request.form.get("tabla", "")
    key = request.form.get("clave", "")
    page = request.form.get("pagina", "1")
    q = request.form.get("q", "")
    try:
        pipeline.storage.delete_record(table, key)
    except Exception as e:
        return redirect(url_for("datos", tabla=table or "rutas", q=q, pagina=page, msg=f"Error borrando: {e}", err=1))
    return redirect(url_for("datos", tabla=table, q=q, pagina=page, msg="Registro borrado."))


@app.route("/clientes-sin-ventana.xlsx", methods=["POST"])
def exportar_clientes_sin_ventana():
    blocked = _require_login()
    if blocked:
        return blocked
    from clientes_export import clientes_sin_ventana_xlsx
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Solicitud invalida."), 400
    try:
        content = clientes_sin_ventana_xlsx(payload.get("clientes"))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    filename = "clientes_sin_ventana_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".xlsx"
    return Response(content, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="' + filename + '"', "Cache-Control": "no-store"})


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
    if st.get("backup_id"):
        msg += f" Copia de seguridad previa al borrado: {st['backup_id']}."
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
    pipeline.clear_dashboard_cache(include_external=True)
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
    pipeline.clear_dashboard_cache(include_external=True)
    return Response(_admin_page(f"Listo. Artículos importados: {total}."), mimetype="text/html")


@app.route("/actualizar-volumen-entregas", methods=["POST"])
def actualizar_volumen_entregas():
    blocked = _require_login()
    if blocked:
        return blocked
    archivo = request.files.get("entregas")
    if not archivo or not archivo.filename:
        return Response(_admin_page("Falta el archivo de entregas.", err=True), mimetype="text/html", status=400)
    try:
        stats = pipeline.importar_volumen_entregas(archivo.stream, archivo.filename)
    except Exception as e:
        return Response(_admin_page(f"Error importando entregas: {e}", err=True), mimetype="text/html", status=400)
    msg = f"Entregas válidas: {stats['filas_validas']}. Intentos actualizados: {stats['intentos_actualizados']}. Filas inválidas: {stats['filas_invalidas']}."
    return Response(_admin_page(msg), mimetype="text/html")


@app.route("/actualizar-vehiculos-rutas", methods=["POST"])
def actualizar_vehiculos_rutas():
    blocked = _require_login()
    if blocked:
        return blocked
    archivo = request.files.get("vehiculos")
    if not archivo or not archivo.filename:
        return Response(_admin_page("Falta el archivo de asignación de vehículos.", err=True), mimetype="text/html", status=400)
    try:
        stats = pipeline.importar_asignacion_vehiculos(archivo.stream, archivo.filename)
    except Exception as e:
        return Response(_admin_page(f"Error importando vehículos: {e}", err=True), mimetype="text/html", status=400)
    msg = f"Asignaciones válidas: {stats['filas_validas']}. Rutas actualizadas: {stats['rutas_actualizadas']}. Filas inválidas: {stats['filas_invalidas']}."
    return Response(_admin_page(msg), mimetype="text/html")


@app.route("/actualizar-otif-pedidos", methods=["POST"])
def actualizar_otif_pedidos():
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        result = pipeline.sincronizar_pedidos_otif(request.form.get("desde", ""), request.form.get("hasta", ""), request.form.get("sucursal") or "TODAS")
    except Exception as exc:
        return Response(_admin_page(f"No se pudo consultar pedidos OTIF: {exc}", err=True), mimetype="text/html", status=400)
    message = (f"Pedidos guardados: {result['pedidos']}. Clientes/dia: {result['clientes_dia']}; "
               f"cumplen: {result['cumplen']}; no cumplen: {result['no_cumplen']}; "
               f"pendientes: {result['pendientes']}. Ver detalle en Consulta histórica API. Esta consulta no actualiza el OTIF por comprobante.")
    return Response(_admin_page(message), mimetype="text/html")


@app.route("/actualizar-logistica-api", methods=["POST"])
def actualizar_logistica_api():
    blocked = _require_login()
    if blocked:
        return blocked
    desde = request.form.get("desde") or (date.today() - timedelta(days=30)).isoformat()
    hasta = request.form.get("hasta") or date.today().isoformat()
    sucursal = request.form.get("sucursal") or "TODAS"
    try:
        stats = pipeline.completar_rutas_desde_api_logistica(desde, hasta, sucursal)
        pipeline.clear_dashboard_cache(include_external=True)
    except Exception as exc:
        return Response(
            _admin_page(f"Error consultando la API logística: {exc}", err=True),
            mimetype="text/html",
            status=400,
        )
    fields = ", ".join(
        f"{field}: {count}" for field, count in stats["fields_updated"].items() if count
    ) or "sin campos nuevos"
    msg = (
        f"API logística: {stats['api_rows']} registros recibidos. "
        f"Rutas actualizadas: {stats['routes_updated']}; "
        f"sin coincidencia: {stats['routes_unmatched']}; "
        f"ambiguas: {stats['routes_ambiguous']}. Campos: {fields}."
    )
    return Response(_admin_page(msg), mimetype="text/html")


def _costos_distribucion_page():
    blocked = _require_login()
    if blocked:
        return blocked
    selected_rid = request.args.get("rid", "").strip()
    try:
        setup = pipeline.storage.load_logistics_setup()
        saved_config = dict(setup.get("config") or {})
        routes = pipeline.storage.list_recent_routes(250)
        rates = list(setup.get("rates") or [])
        if not rates:
            rate_keys = (
                "fuel_price_per_liter", "vehicle_liters_per_100km", "vehicle_cost_per_km",
                "driver_cost_per_hour", "helper_cost_per_hour", "helpers_count", "other_route_cost",
            )
            base_rate = {key: float(saved_config.get(key, 0) or 0) for key in rate_keys}
            base_rate["fuel_type"] = str(saved_config.get("fuel_type") or "Gasoil")
            pipeline.storage.save_logistics_cost_rate("1900-01-01", base_rate)
            rates = pipeline.storage.load_logistics_cost_rates()
        vehicle_rates = list(setup.get("vehicles") or [])
        if selected_rid and not any(str(rec.get("rid") or "") == selected_rid for rec in routes):
            selected_route = pipeline.storage.get_route(selected_rid)
            if selected_route:
                routes.insert(0, {"rid": selected_rid, **selected_route})
        depots = dict(setup.get("depots") or {})
        if not depots:
            for sucursal, rec in (saved_config.get("depot_by_sucursal") or {}).items():
                pipeline.storage.save_logistics_depot(sucursal, rec)
            depots = pipeline.storage.load_logistics_depots()
        if "depot_by_sucursal" in saved_config:
            saved_config.pop("depot_by_sucursal", None)
            pipeline.storage.save_logistics_config(saved_config)
        config = LogisticsCostService().get_config()
    except Exception as exc:
        return Response(_data_unavailable_page("Costos de distribución", exc), mimetype="text/html", status=503)
    route_options = "".join(
        f'<option value="{escape(str(r.get("rid") or ""))}" data-suc="{escape(str(r.get("suc") or ""))}"{" selected" if str(r.get("rid") or "") == selected_rid else ""}>'
        f'{escape(str(r.get("fecha") or "Sin fecha"))} · {escape(str(r.get("suc") or "Sin sucursal"))} · '
        f'{escape(str(r.get("chofer") or "Sin chofer"))}</option>'
        for r in routes
    )
    depot_branches = sorted(
        {str(name) for name in depots if name}
        | {str(route.get("suc") or "") for route in routes if route.get("suc")}
    )
    depot_options = "".join(
        f'<option value="{escape(branch, quote=True)}">{escape(branch)}</option>'
        for branch in depot_branches
    ) or '<option value="">Sin sucursales disponibles</option>'
    config_json = json.dumps(config, ensure_ascii=False).replace("</", "<\\/")
    depots_json = json.dumps(depots, ensure_ascii=False).replace("</", "<\\/")
    rates_json = json.dumps(rates, ensure_ascii=False).replace("</", "<\\/")
    vehicle_rates_json = json.dumps(vehicle_rates, ensure_ascii=False).replace("</", "<\\/")
    return f'''<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Costos de distribución</title><link rel="icon" type="image/png" href="/static/favicon-t2-v2.png" sizes="64x64"><link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin=""><style>
*{{box-sizing:border-box}}body{{font-family:"Segoe UI",system-ui,Arial,sans-serif;background:#EEF1F5;color:#15233B;margin:0;line-height:1.45}}.wrap{{width:min(100% - 28px,1180px);margin:24px auto 48px}}.top{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;margin-bottom:20px}}h1{{font-size:25px;margin:0 0 4px}}h2{{font-size:17px;margin:0 0 14px}}.muted{{color:#657085;font-size:13.5px;margin:0}}.nav{{display:flex;gap:8px;flex-wrap:wrap}}.nav a,.btn{{background:#15233B;color:#fff;text-decoration:none;border:0;border-radius:8px;padding:10px 13px;font-size:13.5px;font-weight:650;cursor:pointer}}.nav a.secondary,.btn.secondary{{background:#fff;color:#15233B;border:1px solid #DCE2EA}}.layout{{display:grid;grid-template-columns:minmax(300px,390px) 1fr;gap:16px;align-items:start}}.panel{{background:#fff;border:1px solid #DCE2EA;border-radius:8px;padding:18px}}.field-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}label{{display:block;color:#657085;font-size:11.5px;font-weight:700;text-transform:uppercase;margin-bottom:5px}}input,select{{width:100%;min-height:40px;border:1px solid #CAD3DF;border-radius:7px;padding:8px 9px;background:#fff;color:#15233B;font-size:14px}}input:focus,select:focus{{outline:2px solid #C77D1A;outline-offset:1px}}.full{{grid-column:1/-1}}.actions{{display:flex;gap:8px;margin-top:16px}}.actions .btn{{flex:1}}.msg{{display:none;margin:0 0 14px;padding:10px 12px;border-radius:7px;font-size:13px;background:#DCFCE7;border:1px solid #86EFAC;color:#166534}}.msg.err{{display:block;background:#FEE2E2;border-color:#FCA5A5;color:#991B1B}}.route-meta{{display:flex;gap:12px;flex-wrap:wrap;color:#657085;font-size:12.5px;margin:-6px 0 12px}}.kpis{{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:0;margin-bottom:14px;border:1px solid #E1E7EE;border-radius:8px;overflow:hidden;background:#FAFBFC}}.kpi{{border:0;border-right:1px solid #E1E7EE;padding:12px;background:transparent}}.kpi:last-child{{border-right:0}}.kpi span{{display:block;color:#657085;font-size:11px;font-weight:700;text-transform:uppercase}}.kpi strong{{display:block;font-size:20px;margin-top:4px}}.map{{height:420px;width:100%;border:1px solid #DCE2EA;border-radius:8px;margin-bottom:14px;background:#E8EDF2}}.number-marker,.depot-marker{{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:#15233B;color:#fff;border:2px solid #fff;box-shadow:0 1px 5px rgba(0,0,0,.45);font-weight:800;font-size:12px}}.depot-marker{{width:34px;height:34px;background:#C77D1A;font-size:9px}}.components{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:16px}}.component{{border-left:3px solid #C77D1A;padding:7px 9px;background:#F8FAFC}}.component span{{display:block;font-size:11px;color:#657085}}.component b{{font-size:14px}}.table-wrap{{overflow:auto;max-height:440px;border:1px solid #E1E7EE;border-radius:8px}}table{{border-collapse:collapse;width:100%;font-size:12.5px}}th,td{{padding:9px 10px;border-bottom:1px solid #E7ECF2;text-align:left;white-space:nowrap}}th{{position:sticky;top:0;background:#F8FAFC;color:#657085;text-transform:uppercase;font-size:11px}}td.num,th.num{{text-align:right}}.empty{{padding:44px 20px;text-align:center;color:#657085}}.warnings{{color:#92400E;background:#FEF3C7;border:1px solid #FCD34D;border-radius:7px;padding:9px 11px;font-size:12.5px;margin-bottom:12px;display:none}}@media(max-width:900px){{.wrap{{width:min(100% - 22px,1180px);margin-top:16px}}.top{{display:block}}.nav{{margin-top:12px}}.nav a,.btn,input,select{{min-height:44px}}.layout{{grid-template-columns:1fr}}.kpis{{grid-template-columns:1fr 1fr}}.kpi:nth-child(2n){{border-right:0}}.map{{height:360px}}}}@media(max-width:560px){{.wrap{{width:min(100% - 18px,1180px);margin-top:14px}}.field-grid,.kpis,.components{{grid-template-columns:1fr 1fr}}.map{{height:340px}}}}
</style><style>*{{letter-spacing:0}}.brand-title{{display:flex;align-items:center;gap:12px;min-width:0}}.brand-logo{{width:56px;height:56px;border-radius:12px;object-fit:contain;background:#fff;flex:0 0 auto}}.layout{{grid-template-columns:minmax(300px,390px) minmax(0,1fr)}}.panel{{min-width:0}}.actions{{flex-wrap:wrap}}.actions .btn{{min-width:130px}}.btn:disabled{{cursor:wait;opacity:.65}}:focus-visible{{outline:2px solid #C77D1A;outline-offset:2px}}#result>div[style*="display:flex"]{{flex-wrap:wrap}}#customerFilter{{min-width:180px;flex:1}}#customerSort{{flex:1}}@media(max-width:560px){{.brand-logo{{width:48px;height:48px;border-radius:10px}}.field-grid{{grid-template-columns:1fr}}.kpis,.components{{grid-template-columns:1fr 1fr}}.actions .btn{{flex-basis:100%}}#result>div[style*="display:flex"]{{justify-content:flex-start!important}}#customerFilter,#customerSort,#mapMode{{width:100%!important;max-width:none!important}}}}@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto!important;transition:none!important}}}}</style></head><body><div class=wrap><div class=top><div class=brand-title><img class=brand-logo src="/static/logo-t2-v2.webp" width="108" height="108" fetchpriority="high" decoding="async" alt="T2"><div><h1>Costos de distribución</h1><p class=muted>Configuración general y cálculo trazable por ruta y cliente.</p></div></div><div class=nav><a class=secondary href="/inicio">Inicio</a><a class=secondary href="/dashboard">Dashboard</a><a class=secondary href="/pedidos">Pedidos</a><a class=secondary href="/admin">Actualizar datos</a><a href="/logout">Salir</a></div></div>
<div id=message class=msg></div><div class=layout><section class=panel><h2>Parámetros de costo</h2><p style="margin:-8px 0 12px"><a href="/costos-distribucion/dashboard">Ver dashboard histórico</a></p><form id=configForm><div class=field-grid>
<div><label for=fuel>Combustible $/litro</label><input id=fuel name=fuel_price_per_liter type=number min=0 step=0.01></div><div><label for=consumption>Consumo litros/100 km</label><input id=consumption name=vehicle_liters_per_100km type=number min=0 step=0.01></div>
<div><label for=rateDate>Vigente desde</label><input id=rateDate type=date value="{date.today().isoformat()}"></div><div><label for=fuelType>Tipo combustible</label><input id=fuelType value="Gasoil"></div>
<div><label for=vehicle>Costo vehículo $/km</label><input id=vehicle name=vehicle_cost_per_km type=number min=0 step=0.01></div><div><label for=driver>Chofer $/hora</label><input id=driver name=driver_cost_per_hour type=number min=0 step=0.01></div>
<div><label for=helper>Ayudante $/hora</label><input id=helper name=helper_cost_per_hour type=number min=0 step=0.01></div><div><label for=helpers>Cantidad ayudantes</label><input id=helpers name=helpers_count type=number min=0 step=1></div>
<div class=full><label for=other>Otros costos por ruta</label><input id=other name=other_route_cost type=number min=0 step=0.01></div>
<div><label for=wDistance>Peso distancia</label><input id=wDistance type=number min=0 step=0.01></div><div><label for=wTime>Peso tiempo</label><input id=wTime type=number min=0 step=0.01></div><div><label for=wVolume>Peso volumen</label><input id=wVolume type=number min=0 step=0.01></div>
<div><label for=volumeCriterion>Criterio de volumen</label><select id=volumeCriterion><option value=bultos>Bultos entregados</option><option value=hl>HL entregados</option><option value=pallets>Pallets entregados</option><option value=unidades>Unidades entregadas</option></select></div><div><label for=greenMax>Rentabilidad verde hasta %</label><input id=greenMax type=number min=0 step=0.1></div><div><label for=yellowMax>Rentabilidad amarilla hasta %</label><input id=yellowMax type=number min=0 step=0.1></div>
<div class=full><label for=routeSelect>Ruta a calcular</label><select id=routeSelect><option value="">Seleccionar ruta</option>{route_options}</select></div>
<div class=full><label for=depotSucursal>Sucursal del depósito</label><select id=depotSucursal>{depot_options}</select></div>
<div class=full><label for=depotName>Nombre del depósito</label><input id=depotName placeholder="Ej. Casa Central"></div><div><label for=depotLat>Latitud depósito</label><input id=depotLat type=number min=-90 max=90 step=0.000001></div><div><label for=depotLon>Longitud depósito</label><input id=depotLon type=number min=-180 max=180 step=0.000001></div>
</div><div class=actions><button class="btn secondary" type=submit>Guardar base</button><button class="btn secondary" id=saveRateBtn type=button>Guardar vigencia</button></div><div id=ratesList style="margin-top:12px"></div><hr style="border:0;border-top:1px solid #E1E7EE;margin:18px 0"><h2>Perfil por vehículo</h2><div class=field-grid><div class=full><label for=vehicleId>Identificador real</label><input id=vehicleId placeholder="Patente o código de unidad"></div><div><label for=vehicleDate>Vigente desde</label><input id=vehicleDate type=date value="{date.today().isoformat()}"></div><div><label for=vehicleConsumption>Litros/100 km</label><input id=vehicleConsumption type=number min=0 step=.01></div><div class=full><label for=vehicleKmCost>Costo vehículo $/km</label><input id=vehicleKmCost type=number min=0 step=.01></div></div><div class=actions><button class="btn secondary" id=saveVehicleBtn type=button>Guardar perfil</button></div><div id=vehiclesList style="margin-top:12px"></div><hr style="border:0;border-top:1px solid #E1E7EE;margin:18px 0"><div class=actions><button class="btn secondary" id=saveDepotBtn type=button>Guardar depósito</button><button class="btn secondary" id=deleteDepotBtn type=button>Eliminar depósito</button><button class=btn id=calculateBtn type=button>Calcular ruta</button></div></form></section>
<section class=panel><h2>Resultado de la ruta</h2><div id=warnings class=warnings></div><div id=result><div class=empty>Seleccioná una ruta y ejecutá el cálculo.</div></div><div id=history></div></section></div></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script><script>const initialConfig={config_json};let config=initialConfig;let depots={depots_json};let rates={rates_json};let vehicleRates={vehicle_rates_json};let routeMap=null,lastRoute=null,lastCustomers=[];const numericFields=['fuel_price_per_liter','vehicle_liters_per_100km','vehicle_cost_per_km','driver_cost_per_hour','helper_cost_per_hour','helpers_count','other_route_cost'];
const $=id=>document.getElementById(id);const esc=v=>String(v??'').replace(/[&<>"']/g,ch=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));const money=v=>new Intl.NumberFormat('es-AR',{{style:'currency',currency:'ARS',maximumFractionDigits:2}}).format(Number(v||0));const number=(v,d=1)=>new Intl.NumberFormat('es-AR',{{maximumFractionDigits:d}}).format(Number(v||0));
function fillConfig(){{numericFields.forEach(k=>document.querySelector(`[name="${{k}}"]`).value=config[k]??0);$('fuelType').value=config.fuel_type??'Gasoil';$('wDistance').value=config.weights?.distance??.4;$('wTime').value=config.weights?.time??.3;$('wVolume').value=config.weights?.volume??.3;$('volumeCriterion').value=config.volume_criterion??'bultos';$('greenMax').value=config.profitability_thresholds?.green_max_pct??3;$('yellowMax').value=config.profitability_thresholds?.yellow_max_pct??6;loadDepot();renderRates();renderVehicleRates();}}
function routeSucursal(){{const o=$('routeSelect').selectedOptions[0];return o?.dataset?.suc||''}}function selectedDepotSucursal(){{return $('depotSucursal').value||''}}function loadDepot(){{const d=depots[selectedDepotSucursal()]||{{}};$('depotName').value=d.nombre??'';$('depotLat').value=d.latitud??'';$('depotLon').value=d.longitud??'';$('deleteDepotBtn').disabled=!depots[selectedDepotSucursal()]}}function syncDepotFromRoute(){{const sucursal=routeSucursal();if(sucursal&&[...$('depotSucursal').options].some(option=>option.value===sucursal))$('depotSucursal').value=sucursal;loadDepot()}}
function collectConfig(){{const next={{...config,fuel_type:$('fuelType').value.trim()||'Gasoil',volume_criterion:$('volumeCriterion').value,weights:{{distance:Number($('wDistance').value||0),time:Number($('wTime').value||0),volume:Number($('wVolume').value||0)}},profitability_thresholds:{{green_max_pct:Number($('greenMax').value||0),yellow_max_pct:Number($('yellowMax').value||0)}}}};numericFields.forEach(k=>next[k]=Number(document.querySelector(`[name="${{k}}"]`).value||0));delete next.depot_by_sucursal;return next}}
function showMessage(text,error=false){{const el=$('message');el.textContent=text;el.className='msg'+(error?' err':'');el.style.display='block'}}async function saveConfig(show=true){{const res=await fetch('/costos-distribucion/config',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(collectConfig())}});const data=await res.json();if(!res.ok||!data.ok)throw new Error(data.error||'No se pudo guardar');config=data.config;if(show)showMessage('Parámetros guardados.');}}
function currentRatePayload(){{const payload=collectConfig();delete payload.weights;delete payload.profitability_thresholds;payload.valid_from=$('rateDate').value;return payload}}function renderRates(){{const target=$('ratesList');target.innerHTML=rates.length?`<label>Vigencias guardadas</label>${{rates.slice(0,6).map(r=>`<div style="display:flex;justify-content:space-between;gap:8px;font-size:12px;padding:6px 0;border-bottom:1px solid #E7ECF2"><span><b>${{esc(r.valid_from)}}</b> · ${{esc(r.fuel_type||'')}} · ${{money(r.fuel_price_per_liter)}}/l</span><button type=button class="btn secondary" style="padding:4px 7px" data-delete-rate="${{esc(r.valid_from)}}">Eliminar</button></div>`).join('')}}`:'';target.querySelectorAll('[data-delete-rate]').forEach(button=>button.addEventListener('click',()=>deleteRate(button.dataset.deleteRate)))}}async function saveRate(){{const res=await fetch('/costos-distribucion/tarifas',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(currentRatePayload())}});const data=await res.json();if(!res.ok||!data.ok)throw new Error(data.error||'No se pudo guardar la vigencia');rates=data.rates;renderRates();showMessage('Vigencia de costos guardada.')}}async function deleteRate(validFrom){{if(!confirm(`¿Eliminar la vigencia ${{validFrom}}?`))return;const res=await fetch(`/costos-distribucion/tarifas/${{encodeURIComponent(validFrom)}}`,{{method:'DELETE'}});const data=await res.json();if(!res.ok||!data.ok)throw new Error(data.error||'No se pudo eliminar');rates=data.rates;renderRates()}}
function renderVehicleRates(){{const target=$('vehiclesList');target.innerHTML=vehicleRates.length?`<label>Perfiles guardados</label>${{vehicleRates.slice(0,8).map((r,index)=>`<div style="display:flex;justify-content:space-between;gap:8px;font-size:12px;padding:6px 0;border-bottom:1px solid #E7ECF2"><span><b>${{esc(r.vehicle)}}</b> · ${{esc(r.valid_from)}} · ${{number(r.vehicle_liters_per_100km,2)}} l/100km</span><button type=button class="btn secondary" style="padding:4px 7px" data-delete-vehicle="${{index}}">Eliminar</button></div>`).join('')}}`:'';target.querySelectorAll('[data-delete-vehicle]').forEach(button=>button.addEventListener('click',()=>{{const record=vehicleRates[Number(button.dataset.deleteVehicle)];if(record)deleteVehicle(record.vehicle,record.valid_from)}}))}}async function saveVehicle(){{const payload={{vehicle:$('vehicleId').value.trim(),valid_from:$('vehicleDate').value,vehicle_liters_per_100km:Number($('vehicleConsumption').value||0),vehicle_cost_per_km:Number($('vehicleKmCost').value||0)}};const res=await fetch('/costos-distribucion/vehiculos',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});const data=await res.json();if(!res.ok||!data.ok)throw new Error(data.error||'No se pudo guardar el perfil');vehicleRates=data.vehicles;renderVehicleRates();showMessage('Perfil de vehículo guardado.')}}async function deleteVehicle(vehicle,dateValue){{if(!confirm('¿Eliminar este perfil de vehículo?'))return;const res=await fetch(`/costos-distribucion/vehiculos/${{encodeURIComponent(vehicle)}}/${{encodeURIComponent(dateValue)}}`,{{method:'DELETE'}});const data=await res.json();if(!res.ok||!data.ok)throw new Error(data.error||'No se pudo eliminar');vehicleRates=data.vehicles;renderVehicleRates()}}
async function saveDepot(){{const sucursal=selectedDepotSucursal();if(!sucursal)throw new Error('Seleccioná la sucursal del depósito.');const payload={{sucursal,nombre:$('depotName').value.trim()||sucursal,latitud:Number($('depotLat').value),longitud:Number($('depotLon').value)}};const res=await fetch('/costos-distribucion/depositos',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});const data=await res.json();if(!res.ok||!data.ok)throw new Error(data.error||'No se pudo guardar el depósito');depots=data.depots;loadDepot();showMessage('Depósito guardado.')}}
function renderSegmentTable(route){{const segments=route.routing_segments||[];if(!segments.length)return'';const rows=segments.map((seg,index)=>{{const from=index===0?'Depósito':`Cliente ${{esc(segments[index-1].destination_ref||index)}}`;const to=seg.destination_ref==='deposito'?'Depósito':`Cliente ${{esc(seg.destination_ref||index+1)}}`;return`<tr><td>${{index+1}}</td><td>${{from}}</td><td>${{to}}</td><td class=num>${{number(seg.distance_km,2)}} km</td><td class=num>${{number((seg.duration_seconds||0)/60,1)}} min</td><td>${{esc(seg.provider||'')}}</td><td>${{seg.fallback?'Estimado':'Vial'}}</td></tr>`}}).join('');return`<h2 style="margin-top:16px">Segmentos del recorrido</h2><div class=table-wrap style="max-height:260px;margin-bottom:14px"><table><thead><tr><th>#</th><th>Origen</th><th>Destino</th><th class=num>Km</th><th class=num>Minutos</th><th>Proveedor</th><th>Precisión</th></tr></thead><tbody>${{rows}}</tbody></table></div>`}}
function render(data){{const r=data.route||{{}},cs=data.customers||[],c=r.components||{{}};lastRoute=r;lastCustomers=cs;$('warnings').style.display=r.warnings?.length?'block':'none';$('warnings').textContent=(r.warnings||[]).join(' ');$('result').innerHTML=`<div class=route-meta><b>${{esc(r.fecha||'')}}</b><span>${{esc(r.sucursal||'')}}</span><span>${{esc(r.camion||'Sin vehículo')}}</span><span>${{esc(r.chofer||'Sin chofer')}}</span><span>${{cs.length}} clientes</span></div><div class=kpis><div class=kpi><span>Costo total</span><strong>${{money(r.total_cost)}}</strong></div><div class=kpi><span>Distancia</span><strong>${{number(r.km)}} km</strong></div><div class=kpi><span>Duración</span><strong>${{number(r.horas,2)}} h</strong></div><div class=kpi><span>Clientes</span><strong>${{cs.length}}</strong></div><div class=kpi><span>Bultos</span><strong>${{number(r.bultos,1)}}</strong></div><div class=kpi><span>HL</span><strong>${{number(r.hl,2)}}</strong></div><div class=kpi><span>Por entrega</span><strong>${{money(r.cost_per_customer)}}</strong></div><div class=kpi><span>Por km</span><strong>${{money(r.cost_per_km)}}</strong></div><div class=kpi><span>Costo / venta</span><strong>${{r.cost_to_sales_pct==null?'—':number(r.cost_to_sales_pct,2)+'%'}}</strong></div></div><div style="display:flex;justify-content:flex-end;margin:0 0 7px"><label style="margin:0 7px 0 0;align-self:center">Visualizar por</label><select id=mapMode style="width:auto;min-width:160px" onchange="renderRouteMap(lastRoute,lastCustomers)"><option value=sequence>Secuencia</option><option value=cost>Costo entrega</option><option value=cost_per_bulto>$/Bulto</option><option value=cost_per_hl>$/HL</option><option value=cost_to_sales_pct>Costo/Venta %</option></select></div><div id=routeMap class=map></div>${{renderSegmentTable(r)}}<div class=components><div class=component><span>Combustible</span><b>${{money(c.fuel)}}</b></div><div class=component><span>Vehículo</span><b>${{money(c.vehicle)}}</b></div><div class=component><span>Personal</span><b>${{money(c.personal)}}</b></div><div class=component><span>Otros</span><b>${{money(c.other)}}</b></div></div><div style="display:flex;gap:8px;margin:0 0 9px"><input id=customerFilter placeholder="Filtrar clientes" oninput="renderCustomerRows()"><select id=customerSort style="max-width:190px" onchange="renderCustomerRows()"><option value=order>Orden de visita</option><option value=cost_desc>Mayor costo</option><option value=pct_desc>Mayor costo/venta</option><option value=bulto_desc>Mayor $/bulto</option><option value=hl_desc>Mayor $/HL</option></select></div><div class=table-wrap><table><thead><tr><th>#</th><th>Cliente</th><th>Nombre</th><th>Localidad</th><th>Estado</th><th class=num>Km</th><th class=num>Tiempo</th><th class=num>Bultos</th><th class=num>HL</th><th class=num>Pallets</th><th class=num>Venta</th><th class=num>Costo</th><th class=num>$/Bulto</th><th class=num>$/HL</th><th class=num>$/Pallet</th><th class=num>Costo/venta</th></tr></thead><tbody id=customerRows></tbody></table></div>`;renderRouteMap(r,cs);renderCustomerRows()}}
function renderCustomerRows(){{const body=$('customerRows');if(!body)return;const query=($('customerFilter')?.value||'').trim().toLowerCase();const mode=$('customerSort')?.value||'order';const rows=lastCustomers.filter(x=>!query||[x.cliente,x.nombre,x.localidad,x.estado_entrega].some(v=>String(v||'').toLowerCase().includes(query)));const metric={{cost_desc:'costo_entrega',pct_desc:'cost_to_sales_pct',bulto_desc:'cost_per_bulto',hl_desc:'cost_per_hl'}}[mode];rows.sort((a,b)=>metric?Number(b[metric]||0)-Number(a[metric]||0):Number(a.orden_visita||0)-Number(b.orden_visita||0));body.innerHTML=rows.map(x=>`<tr><td>${{esc(x.orden_visita??'')}}</td><td>${{esc(x.cliente)}}</td><td>${{esc(x.nombre)}}</td><td>${{esc(x.localidad)}}</td><td>${{esc(x.estado_entrega)}}</td><td class=num>${{number(x.km_atribuibles,2)}}</td><td class=num>${{number((x.tiempo_segundos||0)/60)}} min</td><td class=num>${{number(x.bultos,1)}}</td><td class=num>${{number(x.hl,2)}}</td><td class=num>${{number(x.pallets,1)}}</td><td class=num>${{x.venta?money(x.venta):'—'}}</td><td class=num><b>${{money(x.costo_entrega)}}</b></td><td class=num>${{x.bultos?money(x.cost_per_bulto):'—'}}</td><td class=num>${{x.hl?money(x.cost_per_hl):'—'}}</td><td class=num>${{x.pallets?money(x.cost_per_pallet):'—'}}</td><td class=num>${{x.cost_to_sales_pct==null?'—':number(x.cost_to_sales_pct,2)+'%'}}</td></tr>`).join('')||'<tr><td colspan=16 class=empty>Sin clientes para el filtro.</td></tr>'}}
function markerColor(customer,customers){{const mode=$('mapMode')?.value||'sequence';if(mode==='sequence')return '#15233B';const values=customers.map(x=>Number(mode==='cost'?x.costo_entrega:x[mode])).filter(v=>Number.isFinite(v)&&v>0).sort((a,b)=>a-b);const value=Number(mode==='cost'?customer.costo_entrega:customer[mode]);if(!Number.isFinite(value)||value<=0)return '#64748B';if(mode==='cost_to_sales_pct'){{const t=config.profitability_thresholds||{{green_max_pct:3,yellow_max_pct:6}};return value<t.green_max_pct?'#15803D':value<=t.yellow_max_pct?'#CA8A04':'#B91C1C'}}const low=values[Math.floor((values.length-1)*.33)]||0,high=values[Math.floor((values.length-1)*.66)]||0;return value<=low?'#15803D':value<=high?'#CA8A04':'#B91C1C'}}
function renderRouteMap(route,customers){{const target=$('routeMap');if(routeMap){{routeMap.remove();routeMap=null}}if(!window.L){{target.innerHTML='<div class=empty>No se pudo cargar el mapa.</div>';return}}const segments=route.routing_segments||[];if(!segments.length){{target.innerHTML='<div class=empty>Recalculá esta ruta para generar la geometría vial.</div>';return}}routeMap=L.map(target,{{zoomControl:true}});L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'&copy; OpenStreetMap'}}).addTo(routeMap);const bounds=[];segments.forEach(seg=>{{let points=(seg.geometry||[]).map(p=>[p[1],p[0]]);if(!points.length)points=[[seg.origin.latitud,seg.origin.longitud],[seg.destination.latitud,seg.destination.longitud]];L.polyline(points,{{color:seg.fallback?'#B91C1C':'#1E3A8A',weight:4,opacity:.82,dashArray:seg.fallback?'7 7':null}}).addTo(routeMap).bindPopup(`${{number(seg.distance_km,2)}} km · ${{number((seg.duration_seconds||0)/60)}} min${{seg.fallback?' · estimado':''}}`);points.forEach(p=>bounds.push(p))}});const depot=segments[0]?.origin;if(depot){{L.marker([depot.latitud,depot.longitud],{{icon:L.divIcon({{className:'',html:'<div class=depot-marker>DEP</div>',iconSize:[34,34],iconAnchor:[17,17]}})}}).addTo(routeMap).bindPopup(`<b>${{esc((depots[route.sucursal]||{{}}).nombre||'Depósito')}}</b><br>${{esc(route.sucursal||'')}}`)}}customers.forEach(x=>{{if(x.latitud==null||x.longitud==null)return;const fields=[`<b>${{x.orden_visita}}. ${{esc(x.nombre||x.razon_social||x.cliente)}}</b>`,`Cliente: ${{esc(x.cliente)}}`,x.direccion?esc(x.direccion):'',x.localidad?esc(x.localidad):'',x.hora_llegada?`Llegada: ${{esc(x.hora_llegada)}}`:'',x.hora_salida?`Salida: ${{esc(x.hora_salida)}}`:'',x.tiempo_segundos?`Atención: ${{number(x.tiempo_segundos/60)}} min`:'',`Km atribuibles: ${{number(x.km_atribuibles,2)}}`,x.bultos?`Bultos: ${{number(x.bultos,1)}}`:'',x.hl?`HL: ${{number(x.hl,2)}}`:'',x.pallets?`Pallets: ${{number(x.pallets,1)}}`:'',x.venta?`Venta: ${{money(x.venta)}}`:'',`Costo: ${{money(x.costo_entrega)}}`,x.bultos?`$/Bulto: ${{money(x.cost_per_bulto)}}`:'',x.hl?`$/HL: ${{money(x.cost_per_hl)}}`:'',x.cost_to_sales_pct==null?'':`Costo/venta: ${{number(x.cost_to_sales_pct,2)}}%`].filter(Boolean);const color=markerColor(x,customers);L.marker([x.latitud,x.longitud],{{icon:L.divIcon({{className:'',html:`<div class=number-marker style="background:${{color}}">${{x.orden_visita}}</div>`,iconSize:[28,28],iconAnchor:[14,14]}})}}).addTo(routeMap).bindPopup(fields.join('<br>'))}});if(bounds.length)routeMap.fitBounds(bounds,{{padding:[24,24]}});setTimeout(()=>routeMap.invalidateSize(),0)}}
async function loadHistory(rid){{const res=await fetch(`/costos-distribucion/ruta/${{encodeURIComponent(rid)}}/historial`);const data=await res.json();const rows=data.history||[];$('history').innerHTML=rows.length?`<h2 style="margin-top:18px">Historial de cálculos</h2><div class=table-wrap><table><thead><tr><th>Fecha</th><th>Tipo</th><th>Versión</th><th>Usuario</th><th>Motivo</th><th class=num>Total</th></tr></thead><tbody>${{rows.map(x=>`<tr><td>${{esc(new Date(x.calculated_at).toLocaleString('es-AR'))}}</td><td>${{esc(x.calculation_type||'')}}</td><td>${{esc(x.calculation_version||'')}}</td><td>${{esc(x.calculated_by||'')}}</td><td>${{esc(x.recalculation_reason||'')}}</td><td class=num>${{money(x.total_cost)}}</td></tr>`).join('')}}</tbody></table></div>`:''}}
$('depotSucursal').addEventListener('change',loadDepot);$('routeSelect').addEventListener('change',async()=>{{syncDepotFromRoute();const rid=$('routeSelect').value;if(!rid)return;try{{const res=await fetch(`/costos-distribucion/ruta/${{encodeURIComponent(rid)}}`);const data=await res.json();if(data.route)render(data);else $('result').innerHTML='<div class=empty>Esta ruta todavía no fue calculada.</div>';await loadHistory(rid)}}catch(e){{showMessage(e.message,true)}}}});$('configForm').addEventListener('submit',async e=>{{e.preventDefault();try{{await saveConfig(true)}}catch(err){{showMessage(err.message,true)}}}});$('saveRateBtn').addEventListener('click',async()=>{{try{{await saveRate()}}catch(err){{showMessage(err.message,true)}}}});$('saveVehicleBtn').addEventListener('click',async()=>{{try{{await saveVehicle()}}catch(err){{showMessage(err.message,true)}}}});$('saveDepotBtn').addEventListener('click',async()=>{{try{{await saveDepot()}}catch(err){{showMessage(err.message,true)}}}});$('deleteDepotBtn').addEventListener('click',async()=>{{const sucursal=selectedDepotSucursal();if(!sucursal||!depots[sucursal])return;if(!confirm(`¿Eliminar el depósito de ${{sucursal}}?`))return;try{{const res=await fetch(`/costos-distribucion/depositos/${{encodeURIComponent(sucursal)}}`,{{method:'DELETE'}});const data=await res.json();if(!res.ok||!data.ok)throw new Error(data.error||'No se pudo eliminar');depots=data.depots;loadDepot();showMessage('Depósito eliminado.')}}catch(err){{showMessage(err.message,true)}}}});async function calculateRoute(confirmRecalculation=false,reason=''){{const rid=$('routeSelect').value;const res=await fetch(`/costos-distribucion/ruta/${{encodeURIComponent(rid)}}/calcular`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{confirm:confirmRecalculation,reason}})}});const data=await res.json();if(res.status===409&&data.requires_confirmation){{if(!confirm('Esta ruta ya tiene un cálculo. ¿Crear un nuevo cálculo histórico?'))return null;const why=prompt('Motivo del recálculo:','Actualización de costos')||'';return calculateRoute(true,why)}}if(!res.ok||!data.ok)throw new Error(data.error||'No se pudo calcular');return data}}$('calculateBtn').addEventListener('click',async()=>{{const rid=$('routeSelect').value;if(!rid){{showMessage('Seleccioná una ruta.',true);return}}const btn=$('calculateBtn');btn.disabled=true;btn.textContent='Calculando...';try{{await saveConfig(false);const data=await calculateRoute();if(!data)return;render(data);await loadHistory(rid);showMessage(data.route.calculation_type==='recalculation'?'Recálculo guardado sin modificar el histórico anterior.':'Cálculo original guardado.')}}catch(err){{showMessage(err.message,true)}}finally{{btn.disabled=false;btn.textContent='Calcular ruta'}}}});fillConfig();if($('routeSelect').value)$('routeSelect').dispatchEvent(new Event('change'));</script></body></html>'''


def _logistics_dashboard_page():
    blocked = _require_login()
    if blocked:
        return blocked
    filters = {
        key: request.args.get(key, "").strip()
        for key in ("desde", "hasta", "sucursal", "rid", "camion", "chofer", "cliente", "localidad")
    }
    try:
        storage_pedidos.init_db()
        data = pipeline.storage.load_logistics_dashboard(filters, 1000)
        coverage = pipeline.storage.logistics_data_coverage()
    except Exception as exc:
        return Response(_data_unavailable_page("Dashboard de costos", exc), mimetype="text/html", status=503)
    routes, customers, options = data["routes"], data["customers"], data["options"]

    def num(rec, key):
        try:
            return float(rec.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    def ars(value):
        return f"$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def dec(value, digits=1):
        return f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")

    customer_scope = bool(filters["cliente"] or filters["localidad"])
    scoped_routes = {}
    route_delivery_counts = {}
    for customer in customers:
        rid = str(customer.get("rid") or "")
        route_delivery_counts[rid] = route_delivery_counts.get(rid, 0) + 1
        agg = scoped_routes.setdefault(rid, {"total_cost": 0.0, "km": 0.0, "bultos": 0.0, "hl": 0.0, "pallets": 0.0, "venta": 0.0})
        agg["total_cost"] += num(customer, "costo_entrega")
        agg["km"] += num(customer, "km_atribuibles")
        for key in ("bultos", "hl", "pallets", "venta"):
            agg[key] += num(customer, key)

    def route_value(route, key):
        if customer_scope:
            return scoped_routes.get(str(route.get("rid") or ""), {}).get(key, 0.0)
        return num(route, key)

    total_cost = sum(route_value(r, "total_cost") for r in routes)
    total_km = sum(route_value(r, "km") for r in routes)
    total_bultos = sum(route_value(r, "bultos") for r in routes)
    total_hl = sum(route_value(r, "hl") for r in routes)
    total_pallets = sum(route_value(r, "pallets") for r in routes)
    total_sales = sum(route_value(r, "venta") for r in routes)
    deliveries = len(customers)
    route_count = len(routes)
    kpis = [
        ("Costo total", ars(total_cost)),
        ("Promedio por ruta", ars(total_cost / route_count if route_count else 0)),
        ("Promedio por entrega", ars(total_cost / deliveries if deliveries else 0)),
        ("Costo por km", ars(total_cost / total_km if total_km else 0)),
        ("Kilómetros", f"{dec(total_km)} km"),
        ("Rutas", str(route_count)),
        ("Entregas", str(deliveries)),
    ]
    if total_bultos:
        kpis.extend((("Bultos", dec(total_bultos)), ("Costo por bulto", ars(total_cost / total_bultos))))
    if total_hl:
        kpis.extend((("HL", dec(total_hl, 2)), ("Costo por HL", ars(total_cost / total_hl))))
    if total_pallets:
        kpis.extend((("Pallets", dec(total_pallets)), ("Costo por pallet", ars(total_cost / total_pallets))))
    if total_sales:
        kpis.extend((("Venta vinculada", ars(total_sales)), ("Costo / venta", f"{dec(total_cost / total_sales * 100, 2)}%")))

    customer_groups = {}
    for row in customers:
        key = str(row.get("cliente") or "Sin código")
        agg = customer_groups.setdefault(key, {"cliente": key, "nombre": row.get("nombre") or "", "localidad": row.get("localidad") or "", "costo": 0.0, "venta": 0.0, "km": 0.0, "entregas": 0, "bultos": 0.0, "hl": 0.0})
        agg["costo"] += num(row, "costo_entrega")
        agg["venta"] += num(row, "venta")
        agg["km"] += num(row, "km_atribuibles")
        agg["bultos"] += num(row, "bultos")
        agg["hl"] += num(row, "hl")
        agg["entregas"] += 1
    customer_rank = sorted(customer_groups.values(), key=lambda row: row["costo"], reverse=True)[:10]
    profitability_rank = sorted(
        (row for row in customer_groups.values() if row["venta"] > 0),
        key=lambda row: row["costo"] / row["venta"], reverse=True,
    )[:10]
    bulto_rank = sorted(
        (row for row in customer_groups.values() if row["bultos"] > 0),
        key=lambda row: row["costo"] / row["bultos"], reverse=True,
    )[:10]
    hl_rank = sorted(
        (row for row in customer_groups.values() if row["hl"] > 0),
        key=lambda row: row["costo"] / row["hl"], reverse=True,
    )[:10]
    route_rank = sorted(routes, key=lambda row: route_value(row, "total_cost"), reverse=True)[:10]
    locality_groups = {}
    for row in customer_groups.values():
        locality = row["localidad"] or "Sin localidad"
        agg = locality_groups.setdefault(locality, {"label": locality, "costo": 0.0})
        agg["costo"] += row["costo"]
    driver_groups = {}
    vehicle_groups = {}
    for row in routes:
        driver = str(row.get("chofer") or "Sin chofer")
        driver_agg = driver_groups.setdefault(driver, {"label": driver, "costo": 0.0, "km": 0.0, "rutas": 0})
        driver_agg["costo"] += route_value(row, "total_cost")
        driver_agg["km"] += route_value(row, "km")
        driver_agg["rutas"] += 1
        vehicle = str(row.get("camion") or "")
        if vehicle.lower() not in ("", "sin camion", "sin camión"):
            vehicle_agg = vehicle_groups.setdefault(vehicle, {"label": vehicle, "costo": 0.0, "km": 0.0, "rutas": 0})
            vehicle_agg["costo"] += route_value(row, "total_cost")
            vehicle_agg["km"] += route_value(row, "km")
            vehicle_agg["rutas"] += 1

    def bars(rows, label_fn, value_fn, formatter=ars):
        max_value = max((value_fn(row) for row in rows), default=0) or 1
        return "".join(
            f'''<div class=bar-row><div class=bar-label>{escape(label_fn(row))}</div><div class=bar-track><span style="width:{max(2, value_fn(row) / max_value * 100):.1f}%"></span></div><b>{escape(formatter(value_fn(row)))}</b></div>'''
            for row in rows
        ) or '<div class=empty>Sin cálculos para los filtros seleccionados.</div>'

    route_bars = bars(route_rank, lambda r: f"{r.get('fecha') or ''} · {r.get('sucursal') or ''} · {r.get('chofer') or ''}", lambda r: route_value(r, "total_cost"))
    customer_bars = bars(customer_rank, lambda r: f"{r['cliente']} · {r['nombre']}", lambda r: r["costo"])
    profitability_bars = bars(profitability_rank, lambda r: f"{r['cliente']} · {r['nombre']}", lambda r: r["costo"] / r["venta"] * 100, lambda value: f"{dec(value,2)}%")
    bulto_bars = bars(bulto_rank, lambda r: f"{r['cliente']} · {r['nombre']}", lambda r: r["costo"] / r["bultos"])
    hl_bars = bars(hl_rank, lambda r: f"{r['cliente']} · {r['nombre']}", lambda r: r["costo"] / r["hl"])
    locality_bars = bars(sorted(locality_groups.values(), key=lambda row: row["costo"], reverse=True)[:10], lambda r: r["label"], lambda r: r["costo"])
    driver_bars = bars(sorted(driver_groups.values(), key=lambda row: row["costo"] / row["rutas"], reverse=True)[:10], lambda r: r["label"], lambda r: r["costo"] / r["rutas"])
    vehicle_bars = bars(sorted(vehicle_groups.values(), key=lambda row: row["costo"] / row["km"] if row["km"] else 0, reverse=True)[:10], lambda r: r["label"], lambda r: r["costo"] / r["km"] if r["km"] else 0)
    route_rows = "".join(
        f'''<tr><td>{escape(str(r.get("fecha") or ""))}</td><td>{escape(str(r.get("sucursal") or ""))}</td><td>{escape(str(r.get("chofer") or ""))}</td><td>{escape(str(r.get("camion") or ""))}</td><td class=num>{dec(route_value(r,"km"),2)}</td><td class=num>{route_delivery_counts.get(str(r.get("rid") or ""), 0)}</td><td class=num>{ars(route_value(r,"venta")) if route_value(r,"venta") else "—"}</td><td class=num>{ars(route_value(r,"total_cost"))}</td><td class=num>{dec(route_value(r,"total_cost") / route_value(r,"venta") * 100,2)+"%" if route_value(r,"venta") else "—"}</td><td><a href="/costos-distribucion?rid={escape(str(r.get('rid') or ''))}">Ver</a></td></tr>'''
        for r in sorted(routes, key=lambda row: str(row.get("fecha") or ""), reverse=True)
    ) or '<tr><td colspan=10 class=empty>Sin rutas calculadas.</td></tr>'

    def opts(name, values):
        selected = filters.get(name, "")
        rows = []
        for item in values:
            value = item.get("value") if isinstance(item, dict) else item
            label = item.get("label") if isinstance(item, dict) else item
            rows.append(
                f'<option value="{escape(str(value))}"{" selected" if str(value) == selected else ""}>{escape(str(label))}</option>'
            )
        return '<option value="">Todos</option>' + "".join(rows)

    cards = "".join(f'<div class=kpi><span>{escape(label)}</span><strong>{escape(value)}</strong></div>' for label, value in kpis)
    caveat = ""
    if routes and not total_bultos and not total_hl:
        caveat = '<div class=notice>Las rutas calculadas todavía no tienen bultos ni HL vinculados. Los indicadores de volumen se habilitarán cuando esa relación esté disponible.</div>'
    def pct(part, total):
        return f"{(100 * part / total):.1f}%" if total else "0%"
    coverage_cards = "".join(
        f'<div class=coverage-metric><span>{escape(label)}</span><strong>{escape(value)}</strong></div>'
        for label, value in (
            ("Rutas calculadas", f"{coverage['routes_calculated']} / {coverage['routes_total']}"),
            ("Clientes con GPS", pct(coverage["clients_with_gps"], coverage["clients_total"])),
            ("Rutas con volumen", pct(coverage["routes_with_volume"], coverage["routes_total"])),
            ("Pedidos con volumen", pct(coverage["orders_with_volume"], coverage["orders_total"])),
            ("Rutas con vehículo", pct(coverage["routes_with_vehicle"], coverage["routes_total"])),
        )
    )
    return f'''<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Dashboard de costos</title><link rel="icon" type="image/png" href="/static/favicon-t2-v2.png" sizes="64x64"><style>
*{{box-sizing:border-box}}body{{font-family:"Segoe UI",system-ui,Arial,sans-serif;background:#EEF1F5;color:#15233B;margin:0;line-height:1.45}}.wrap{{width:min(100% - 28px,1320px);margin:24px auto 48px}}.top{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;margin-bottom:18px}}h1{{font-size:25px;margin:0 0 4px}}h2{{font-size:17px;margin:0 0 13px}}.muted{{color:#657085;font-size:13px;margin:0}}.nav{{display:flex;gap:8px;flex-wrap:wrap}}.nav a,.btn{{background:#15233B;color:#fff;text-decoration:none;border:0;border-radius:7px;padding:10px 13px;font-size:13px;font-weight:700;cursor:pointer}}.nav a.secondary{{background:#fff;color:#15233B;border:1px solid #DCE2EA}}.filters{{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:10px;align-items:end;background:#fff;border:1px solid #DCE2EA;border-radius:8px;padding:14px;margin-bottom:14px}}.filter-actions{{display:flex;gap:8px;align-items:center}}.filter-actions .btn{{flex:1;text-align:center}}label{{display:block;font-size:11px;text-transform:uppercase;font-weight:700;color:#657085;margin-bottom:4px}}input,select{{width:100%;height:39px;border:1px solid #CAD3DF;border-radius:6px;padding:7px 8px;background:#fff}}.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:14px}}.kpi,.panel{{background:#fff;border:1px solid #DCE2EA;border-radius:8px}}.kpi{{padding:13px}}.kpi span,.coverage-metric span{{display:block;font-size:11px;text-transform:uppercase;color:#657085;font-weight:700}}.kpi strong,.coverage-metric strong{{display:block;font-size:20px;margin-top:5px}}.coverage-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));border:1px solid #E1E7EE;border-radius:7px;overflow:hidden;background:#FAFBFC}}.coverage-metric{{padding:12px;border-right:1px solid #E1E7EE}}.coverage-metric:last-child{{border-right:0}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}}.panel{{padding:16px}}.bar-row{{display:grid;grid-template-columns:minmax(150px,1.4fr) 2fr 105px;gap:10px;align-items:center;margin:9px 0;font-size:12px}}.bar-label{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.bar-track{{height:10px;background:#E7ECF2;border-radius:3px;overflow:hidden}}.bar-track span{{display:block;height:100%;background:#C77D1A}}.bar-row b{{text-align:right}}.table-wrap{{overflow:auto;max-height:460px;border:1px solid #E1E7EE;border-radius:7px}}table{{border-collapse:collapse;width:100%;font-size:12.5px}}th,td{{padding:9px 10px;border-bottom:1px solid #E7ECF2;text-align:left;white-space:nowrap}}th{{position:sticky;top:0;background:#F8FAFC;color:#657085;text-transform:uppercase;font-size:11px}}td.num{{text-align:right}}td a{{color:#1E3A8A;font-weight:700}}.notice{{background:#FEF3C7;border:1px solid #FCD34D;color:#92400E;border-radius:7px;padding:10px 12px;font-size:12.5px;margin-bottom:14px}}.empty{{padding:24px;color:#657085;text-align:center}}@media(max-width:900px){{.wrap{{width:min(100% - 22px,1320px);margin-top:16px}}.top{{display:block}}.nav{{margin-top:12px}}.nav a,.btn,input,select{{min-height:44px}}.filters{{grid-template-columns:1fr 1fr}}.grid{{grid-template-columns:1fr}}}}@media(max-width:560px){{.wrap{{width:min(100% - 18px,1320px);margin-top:14px}}.filters{{grid-template-columns:1fr}}.bar-row{{grid-template-columns:1fr 90px}}.bar-track{{grid-column:1/-1;grid-row:2}}.coverage-grid{{grid-template-columns:1fr 1fr}}.coverage-metric{{border-bottom:1px solid #E1E7EE}}}}
</style><style>*{{letter-spacing:0}}.brand-title{{display:flex;align-items:center;gap:12px;min-width:0}}.brand-logo{{width:56px;height:56px;border-radius:12px;object-fit:contain;background:#fff;flex:0 0 auto}}.nav a.secondary,.btn.secondary{{background:#fff;color:#15233B;border:1px solid #DCE2EA}}.panel{{min-width:0}}:focus-visible{{outline:2px solid #C77D1A;outline-offset:2px}}@media(max-width:900px){{.filter-actions{{grid-column:1/-1}}}}@media(max-width:560px){{.brand-logo{{width:48px;height:48px;border-radius:10px}}.filter-actions{{grid-column:auto}}.filter-actions .btn{{min-width:0}}}}@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto!important;transition:none!important}}}}</style></head><body><div class=wrap><div class=top><div class=brand-title><img class=brand-logo src="/static/logo-t2-v2.webp" width="108" height="108" fetchpriority="high" decoding="async" alt="T2"><div><h1>Dashboard de costos</h1><p class=muted>Cálculo vigente por ruta. Los recálculos históricos no se duplican en los totales.</p></div></div><div class=nav><a class=secondary href="/inicio">Inicio</a><a class=secondary href="/costos-distribucion">Calcular ruta</a><a class=secondary href="/pedidos">Pedidos</a><a class=secondary href="/dashboard">Dashboard operativo</a><a href="/logout">Salir</a></div></div>
<form class=filters method=get><div><label>Desde</label><input type=date name=desde value="{escape(filters['desde'])}"></div><div><label>Hasta</label><input type=date name=hasta value="{escape(filters['hasta'])}"></div><div><label>Sucursal</label><select name=sucursal>{opts('sucursal',options['sucursales'])}</select></div><div><label>Ruta</label><select name=rid>{opts('rid',options['rutas'])}</select></div><div><label>Vehículo</label><select name=camion>{opts('camion',options['camiones'])}</select></div><div><label>Chofer</label><select name=chofer>{opts('chofer',options['choferes'])}</select></div><div><label>Cliente</label><select name=cliente>{opts('cliente',options['clientes'])}</select></div><div><label>Localidad</label><select name=localidad>{opts('localidad',options['localidades'])}</select></div><div class=filter-actions><button class=btn type=submit>Aplicar</button><a class="btn secondary" href="/costos-distribucion/dashboard">Limpiar</a></div></form>{caveat}<div class=kpis>{cards}</div><div class=grid><section class=panel><h2>Rutas más costosas</h2>{route_bars}</section><section class=panel><h2>Clientes más costosos</h2>{customer_bars}</section><section class=panel><h2>Mayor $/bulto</h2>{bulto_bars}</section><section class=panel><h2>Mayor $/HL</h2>{hl_bars}</section><section class=panel><h2>Mayor costo sobre venta</h2>{profitability_bars}</section><section class=panel><h2>Localidades más costosas</h2>{locality_bars}</section><section class=panel><h2>Costo promedio por chofer</h2>{driver_bars}</section><section class=panel><h2>Costo por km del vehículo</h2>{vehicle_bars}</section></div><section class=panel style="margin-bottom:14px"><h2>Cobertura de datos</h2><div class=coverage-grid>{coverage_cards}</div></section><section class=panel><h2>Detalle de rutas</h2><div class=table-wrap><table><thead><tr><th>Fecha</th><th>Sucursal</th><th>Chofer</th><th>Vehículo</th><th>Km</th><th>Entregas</th><th>Venta</th><th>Costo</th><th>Costo/venta</th><th></th></tr></thead><tbody>{route_rows}</tbody></table></div></section></div></body></html>'''


@app.route("/costos-distribucion/dashboard")
def costos_distribucion_dashboard():
    return Response(_logistics_dashboard_page(), mimetype="text/html")


@app.get('/api/combustible/precios')
def fuel_current_prices():
    if not _is_logged_in():
        return jsonify(error='Iniciá sesión para consultar precios'), 401
    from fuel_prices import current_prices, argly_gasoil
    from urllib.error import HTTPError
    try:
        if request.args.get('source') == 'argly':
            return jsonify(argly_gasoil())
        return jsonify(current_prices(request.args.get('city', '')))
    except ValueError:
        return jsonify(error='Ciudad o respuesta de precios inválida'), 400
    except HTTPError as exc:
        return jsonify(error=f'El proveedor respondió HTTP {exc.code}. Podés ingresar el precio manualmente.'), 502
    except Exception:
        return jsonify(error='No se pudo consultar el proveedor. Podés ingresar el precio manualmente.'), 502


@app.get('/api/combustible/historico')
def fuel_stored_history():
    if not _is_logged_in():
        return jsonify(error='Iniciá sesión para consultar precios'), 401
    from fuel_history import get_history
    try:
        return jsonify(get_history())
    except Exception:
        return jsonify(error='No se pudieron leer los precios guardados'), 503


@app.get('/api/clientes/tiempos-visita')
def customer_visit_times():
    if not _is_logged_in():
        return jsonify(error='Iniciá sesión para consultar visitas'), 401
    from visit_metrics import dashboard_rows
    try:
        from pdv_targets import config, group_names
        return jsonify(rows=dashboard_rows(), targets=config(), groups=group_names())
    except Exception:
        return jsonify(error='No se pudieron cargar los tiempos de visita'), 503


@app.route('/clientes/objetivos-tiempo', methods=['GET', 'POST'])
@app.route('/admin/objetivos-pdv', methods=['GET', 'POST'])
def customer_time_targets():
    blocked = _require_login()
    if blocked:
        return blocked
    import pdv_targets
    token = session.setdefault('pdv_targets_csrf', secrets.token_urlsafe(32))
    message = request.args.get('msg', '')
    cfg = pdv_targets.config()
    status = 200
    if request.method == 'POST':
        if not secrets.compare_digest(token, request.form.get('csrf', '')):
            return Response('Recargá el formulario antes de guardar.', status=400)
        fields = ['name', 'minutes', 'agrupacion', 'subcanal', 'cliente']
        values = {field: request.form.getlist(field) for field in fields}
        if len({len(v) for v in values.values()}) != 1:
            return Response('Formulario incompleto.', status=400)
        rules = [{field: values[field][i] if field in ('name', 'minutes') else values[field][i].splitlines()
                  for field in fields} for i in range(len(values['name']))]
        try:
            pdv_targets.save(rules, session.get('admin_user') or 'admin')
            return redirect(url_for('customer_time_targets', msg='Objetivos guardados. Volvé a Clientes / PDV para ver la comparación.'))
        except ValueError as exc:
            cfg = {'rules': rules}
            message, status = str(exc), 400
    return render_template('pdv_targets.html', cfg=cfg, token=token, message=message, catalog=pdv_targets.catalog()), status


@app.route("/costos-distribucion")
def costos_distribucion():
    return _costos_distribucion_page()


@app.route("/costos-distribucion/config", methods=["GET", "POST"])
def costos_distribucion_config():
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            numeric_keys = (
                "fuel_price_per_liter", "vehicle_liters_per_100km", "vehicle_cost_per_km",
                "driver_cost_per_hour", "helper_cost_per_hour", "helpers_count", "other_route_cost",
            )
            numeric_values = [float(payload.get(key, 0) or 0) for key in numeric_keys]
            if any(not math.isfinite(value) or value < 0 for value in numeric_values):
                raise ValueError("Los costos y cantidades no pueden ser negativos.")
            if not numeric_values[numeric_keys.index("helpers_count")].is_integer():
                raise ValueError("La cantidad de ayudantes debe ser un número entero.")
            weights = payload.get("weights") or {}
            weight_values = [float(weights.get(key, 0) or 0) for key in ("distance", "time", "volume")]
            if any(not math.isfinite(value) or value < 0 for value in weight_values) or abs(sum(weight_values) - 1) > 0.001:
                raise ValueError("Los pesos de distancia, tiempo y volumen deben sumar 1,00.")
            if payload.get("volume_criterion") not in ("bultos", "hl", "pallets", "unidades"):
                raise ValueError("El criterio de volumen no es válido.")
            thresholds = payload.get("profitability_thresholds") or {}
            green = float(thresholds.get("green_max_pct", 0) or 0)
            yellow = float(thresholds.get("yellow_max_pct", 0) or 0)
            if not all(math.isfinite(value) for value in (green, yellow)) or green < 0 or yellow < green:
                raise ValueError("Los umbrales de rentabilidad no son válidos.")
            cfg = dict(zip(numeric_keys, numeric_values))
            cfg["helpers_count"] = int(cfg["helpers_count"])
            cfg.update({
                "fuel_type": str(payload.get("fuel_type") or "Gasoil").strip()[:100],
                "weights": dict(zip(("distance", "time", "volume"), weight_values)),
                "volume_criterion": payload["volume_criterion"],
                "return_distance_criterion": "proportional_forward_distance",
                "profitability_thresholds": {"green_max_pct": green, "yellow_max_pct": yellow},
            })
            cfg = pipeline.storage.save_logistics_config(cfg)
            return Response(json.dumps({"ok": True, "config": cfg}, ensure_ascii=False), mimetype="application/json")
        cfg = LogisticsCostService().get_config()
        return Response(json.dumps({"config": cfg}, ensure_ascii=False), mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), mimetype="application/json", status=400)


@app.route("/costos-distribucion/tarifas", methods=["GET", "POST"])
def costos_distribucion_tarifas():
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            valid_from = str(payload.pop("valid_from", "")).strip()
            date.fromisoformat(valid_from)
            numeric_keys = (
                "fuel_price_per_liter", "vehicle_liters_per_100km", "vehicle_cost_per_km",
                "driver_cost_per_hour", "helper_cost_per_hour", "helpers_count", "other_route_cost",
            )
            rec = {key: float(payload.get(key, 0) or 0) for key in numeric_keys}
            if any(not math.isfinite(value) or value < 0 for value in rec.values()):
                raise ValueError("Los importes, consumos y cantidades no pueden ser negativos.")
            if not rec["helpers_count"].is_integer():
                raise ValueError("La cantidad de ayudantes debe ser un número entero.")
            rec["helpers_count"] = int(rec["helpers_count"])
            rec["fuel_type"] = str(payload.get("fuel_type") or "Gasoil").strip()[:100]
            pipeline.storage.save_logistics_cost_rate(valid_from, rec)
        return Response(json.dumps({"ok": True, "rates": pipeline.storage.load_logistics_cost_rates()}, ensure_ascii=False), mimetype="application/json")
    except (TypeError, ValueError) as e:
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), mimetype="application/json", status=400)
    except Exception as e:
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), mimetype="application/json", status=500)


@app.route("/costos-distribucion/tarifas/<valid_from>", methods=["DELETE"])
def costos_distribucion_tarifa_eliminar(valid_from):
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        if valid_from == "1900-01-01":
            raise ValueError("La vigencia base no se puede eliminar; podés editar sus valores.")
        deleted = pipeline.storage.delete_logistics_cost_rate(valid_from)
        return Response(json.dumps({"ok": True, "deleted": deleted, "rates": pipeline.storage.load_logistics_cost_rates()}, ensure_ascii=False), mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), mimetype="application/json", status=400)


@app.route("/costos-distribucion/vehiculos", methods=["GET", "POST"])
def costos_distribucion_vehiculos():
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            vehicle = str(payload.get("vehicle") or "").strip()
            valid_from = str(payload.get("valid_from") or "").strip()
            date.fromisoformat(valid_from)
            if not vehicle or len(vehicle) > 100 or vehicle.lower() in ("sin camion", "sin camión"):
                raise ValueError("Ingresá un identificador real de vehículo.")
            rec = {
                "vehicle_liters_per_100km": float(payload.get("vehicle_liters_per_100km", 0) or 0),
                "vehicle_cost_per_km": float(payload.get("vehicle_cost_per_km", 0) or 0),
            }
            if any(not math.isfinite(value) or value < 0 for value in rec.values()):
                raise ValueError("El consumo y el costo no pueden ser negativos.")
            pipeline.storage.save_logistics_vehicle_cost(vehicle, valid_from, rec)
        return Response(json.dumps({"ok": True, "vehicles": pipeline.storage.load_logistics_vehicle_costs()}, ensure_ascii=False), mimetype="application/json")
    except (TypeError, ValueError) as e:
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), mimetype="application/json", status=400)
    except Exception as e:
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), mimetype="application/json", status=500)


@app.route("/costos-distribucion/vehiculos/<path:vehicle>/<valid_from>", methods=["DELETE"])
def costos_distribucion_vehiculo_eliminar(vehicle, valid_from):
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        deleted = pipeline.storage.delete_logistics_vehicle_cost(vehicle, valid_from)
        return Response(json.dumps({"ok": True, "deleted": deleted, "vehicles": pipeline.storage.load_logistics_vehicle_costs()}, ensure_ascii=False), mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), mimetype="application/json", status=400)


@app.route("/costos-distribucion/depositos", methods=["GET", "POST"])
def costos_distribucion_depositos():
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            sucursal = str(payload.get("sucursal") or "").strip()
            if not sucursal or len(sucursal) > 120:
                raise ValueError("Falta la sucursal del depósito.")
            latitud = float(payload.get("latitud"))
            longitud = float(payload.get("longitud"))
            if (
                not math.isfinite(latitud) or not math.isfinite(longitud)
                or not -90 <= latitud <= 90 or not -180 <= longitud <= 180
                or (latitud == 0 and longitud == 0)
            ):
                raise ValueError("Las coordenadas del depósito no son válidas.")
            pipeline.storage.save_logistics_depot(sucursal, {
                "nombre": str(payload.get("nombre") or sucursal).strip()[:160],
                "latitud": latitud,
                "longitud": longitud,
            })
        depots = pipeline.storage.load_logistics_depots()
        return Response(json.dumps({"ok": True, "depots": depots}, ensure_ascii=False), mimetype="application/json")
    except (TypeError, ValueError) as e:
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), mimetype="application/json", status=400)
    except Exception as e:
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), mimetype="application/json", status=500)


@app.route("/costos-distribucion/depositos/<path:sucursal>", methods=["DELETE"])
def costos_distribucion_deposito_eliminar(sucursal):
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        deleted = pipeline.storage.delete_logistics_depot(sucursal)
        depots = pipeline.storage.load_logistics_depots()
        return Response(json.dumps({"ok": True, "deleted": deleted, "depots": depots}, ensure_ascii=False), mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), mimetype="application/json", status=500)


@app.route("/costos-distribucion/ruta/<rid>")
def costos_distribucion_ruta(rid):
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        data = pipeline.storage.load_route_cost(rid)
        return Response(json.dumps(data, ensure_ascii=False), mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"error": str(e)}, ensure_ascii=False), mimetype="application/json", status=400)


@app.route("/costos-distribucion/ruta/<rid>/historial")
def costos_distribucion_historial(rid):
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        history = pipeline.storage.load_route_cost_history(rid)
        return Response(json.dumps({"ok": True, "history": history}, ensure_ascii=False), mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), mimetype="application/json", status=400)


@app.route("/costos-distribucion/calculo/<calculation_id>")
def costos_distribucion_calculo(calculation_id):
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        data = pipeline.storage.load_route_cost_calculation(calculation_id)
        if not data.get("route"):
            return Response(json.dumps({"ok": False, "error": "Cálculo no encontrado."}, ensure_ascii=False), mimetype="application/json", status=404)
        return Response(json.dumps({"ok": True, **data}, ensure_ascii=False), mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), mimetype="application/json", status=400)


@app.route("/costos-distribucion/ruta/<rid>/calcular", methods=["POST"])
def costos_distribucion_calcular(rid):
    blocked = _require_login()
    if blocked:
        return blocked
    try:
        payload = request.get_json(silent=True) or {}
        existing = pipeline.storage.load_route_cost(rid).get("route")
        if existing and not payload.get("confirm"):
            return Response(
                json.dumps({"ok": False, "requires_confirmation": True, "error": "La ruta ya tiene un cálculo guardado."}, ensure_ascii=False),
                mimetype="application/json",
                status=409,
            )
        reason = str(payload.get("reason") or "").strip()
        if existing and not reason:
            return Response(json.dumps({"ok": False, "error": "El motivo del recálculo es obligatorio."}, ensure_ascii=False), mimetype="application/json", status=400)
        data = LogisticsCostService().calculate_route(
            rid,
            overwrite=True,
            actor=session.get("admin_user") or ADMIN_USER,
            reason=reason,
        )
        return Response(json.dumps({"ok": True, **data}, ensure_ascii=False), mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), mimetype="application/json", status=400)


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
    stats = pipeline.storage.health_stats()
    return {
        "ok": True,
        "backend": pipeline.storage.backend_name(),
        "con_datos": stats["rutas"] > 0,
        **stats,
    }


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0").lower() in ("1", "true", "yes", "on")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=debug, use_reloader=debug)
