# -*- coding: utf-8 -*-
"""
pedidos_blueprint.py
--------------------
Página independiente "Ingreso de pedidos" para sumar al proyecto dashboard-reparto.

Se registra como Blueprint en tu app.py existente:

    from pedidos_blueprint import pedidos_bp
    app.register_blueprint(pedidos_bp)

Rutas:
    GET  /pedidos          -> la página (dibuja desde la base)
    GET  /pedidos/data     -> JSON con todos los registros (lo consume el front)
    POST /pedidos/upload   -> recibe el .xlsx, normaliza y persiste incremental

La agregación (por hora, franja, localidad, vendedor, canal, día) se hace en el
navegador con Chart.js, así los filtros responden al instante. El server solo
sirve los registros ya normalizados desde Postgres.
"""

import io
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, session

from pipeline_pedidos import parse_pedidos, FRANJAS, DIAS
import storage_pedidos as store

pedidos_bp = Blueprint("pedidos", __name__)


def _extract_openai_text(payload):
    if payload.get("output_text"):
        return payload["output_text"]
    parts = []
    for item in payload.get("output", []) or []:
        for content in item.get("content", []) or []:
            if isinstance(content, dict) and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts).strip()


def _analizar_con_openai(resumen):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Falta configurar OPENAI_API_KEY en las variables de entorno.")
    model = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
    body = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": (
                    "Sos un analista operativo de Del Palacio S.A. "
                    "Analiza ingresos de pedidos por horario de corte. "
                    "Respondé en español, breve, con hallazgos accionables. "
                    "No inventes datos: usá solo el JSON recibido."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Analizá este resumen filtrado del tablero de pedidos. "
                    "Enfocate en la ventana 14:00 a 14:30, fuera de corte, "
                    "egreso posterior, sucursal/canal/vendedor si aparecen, "
                    "riesgos y acciones sugeridas.\n\n"
                    + json.dumps(resumen, ensure_ascii=False)
                ),
            },
        ],
    }
    req = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI respondió HTTP {exc.code}: {detail[:400]}") from exc
    except URLError as exc:
        raise RuntimeError(f"No se pudo conectar con OpenAI: {exc}") from exc
    text = _extract_openai_text(data)
    if not text:
        raise RuntimeError("OpenAI no devolvió texto de análisis.")
    return text


@pedidos_bp.before_request
def require_dashboard_login():
    if not session.get("admin_logged_in"):
        return redirect(url_for("login", next=request.path))
    return None


@pedidos_bp.route("/pedidos")
@pedidos_bp.route("/pedidos/")
def pedidos_home():
    try:
        resumen = store.stats()
    except Exception:
        resumen = {"total": 0, "desde": None, "hasta": None, "error": "No se pudo consultar pedidos en este momento."}
    franjas = FRANJAS
    return render_template("plantilla_pedidos.html",
                           resumen=resumen, franjas=franjas, dias=DIAS)


@pedidos_bp.route("/pedidos/data")
def pedidos_data():
    try:
        return jsonify(store.fetch_all())
    except Exception as exc:
        resp = jsonify([])
        resp.status_code = 503
        msg = re.sub(r"\s+", " ", str(exc)).strip()[:300]
        resp.headers["X-Data-Error"] = f"No se pudo consultar pedidos: {msg}"
        return resp


@pedidos_bp.route("/pedidos/ai-analisis", methods=["POST"])
def pedidos_ai_analisis():
    try:
        payload = request.get_json(silent=True) or {}
        resumen = payload.get("resumen") or {}
        if not resumen:
            return jsonify({"ok": False, "error": "No se recibió resumen para analizar."}), 400
        analisis = _analizar_con_openai(resumen)
        return jsonify({"ok": True, "analisis": analisis})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@pedidos_bp.route("/pedidos/upload", methods=["POST"])
def pedidos_upload():
    f = request.files.get("archivo")
    if not f or f.filename == "":
        flash("No se seleccionó ningún archivo.", "warning")
        return redirect(url_for("pedidos.pedidos_home"))
    try:
        data = io.BytesIO(f.read())
        records, meta = parse_pedidos(data)
        insertadas = store.upsert_records(records)
        duplicadas = max(meta["leidas"] - insertadas, 0)
        flash(f"Importado: {meta['leidas']} filas leídas, "
              f"{insertadas} pedidos nuevos, {duplicadas} duplicados ignorados.", "success")
    except Exception as e:  # noqa: BLE001
        flash(f"Error al importar: {e}", "danger")
    return redirect(url_for("pedidos.pedidos_home"))
