"""Resultados diarios a partir del histórico, con vista previa y envío por lotes."""
import json
import os
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

import equipos_service as teams
import kpi_storage
import pipeline
import storage

METRICS = {
    # Códigos de la exportación kpis_sectoriales (1).xlsx, empresa 1 / sector 1.
    "rechazos": {"codigo": "1", "nombre": "RECHAZOS", "unidad": "%", "acumulacion": "promedio", "pendiente": "Confirmar atribución de rechazos y volumen en HL por equipo.", "formula": "HL rechazados / HL de referencia × 100. Pendiente de confirmar la fuente y el alcance."},
    "disp_km": {"codigo": "2", "nombre": "DISPERSION EN KM", "unidad": "%", "acumulacion": "promedio", "formula": "100 × (suma km planificados − suma km reales) / suma km planificados. Mismo signo que el dashboard."},
    "disp_tiempo": {"codigo": "3", "nombre": "DISPERSION EN TIEMPO", "unidad": "%", "acumulacion": "promedio", "formula": "100 × (suma tiempo planificado − suma tiempo real) / suma tiempo planificado."},
    "click": {"codigo": "4", "nombre": "ADHERENCIA AL CLICK", "unidad": "%", "acumulacion": "promedio", "formula": "Promedio de adherencia al click entre las rutas del día en las que participó el empleado, igual que el dashboard."},
    "dqi": {"codigo": "5", "nombre": "DQI", "unidad": "PPM", "acumulacion": "promedio", "pendiente": "Confirmar fórmula PPM y denominador; el dashboard conserva bultos/HL.", "formula": "Pendiente de definir la conversión de roturas a PPM."},
    "tml": {"codigo": "6", "nombre": "TML", "unidad": "MIN", "acumulacion": "promedio", "formula": "Promedio del TML medido por ruta (ingreso del chofer a inicio Foxtrot), compartido con su equipo. Sólo fichadas/ajustes guardados; sin estimaciones."},
    "ti": {"codigo": "7", "nombre": "TI", "unidad": "MIN", "acumulacion": "promedio", "formula": "Promedio del TI medido por ruta (fin Foxtrot a salida del chofer), compartido con su equipo. Sólo fichadas/ajustes guardados; sin estimaciones."},
    "nps": {"codigo": "8", "nombre": "NPS GRAL", "unidad": "%", "acumulacion": "ultimo", "pendiente": "Confirmar si el dato general debe compartirse con todos los equipos.", "formula": "Última medición del indicador. Pendiente de confirmar alcance y fecha de atribución."},
    "nps_delivery": {"codigo": "9", "nombre": "NPS SUBDRIVER DELIVERY", "unidad": "%", "acumulacion": "ultimo", "pendiente": "Confirmar si el dato general debe compartirse con todos los equipos.", "formula": "Última medición NPS de entrega. Pendiente de confirmar alcance y fecha de atribución."},
    "rmd": {"codigo": "RMD", "nombre": "RMD", "unidad": "%", "acumulacion": "promedio", "pendiente": "Confirmar si es porcentaje de respuestas o puntaje 0–5.", "formula": "La unidad del catálogo es %. El dashboard tiene puntaje y porcentaje de respuestas por separado."},
}
_TOKEN = {}

# General indicators are repeated unchanged for the day's participating employees.
for _key in ('dqi', 'nps', 'nps_delivery', 'rmd'):
    METRICS[_key]['alcance'] = 'General: mismo valor para todos los integrantes'
for _key in ('nps', 'nps_delivery'):
    METRICS[_key].pop('pendiente', None)
    METRICS[_key]['formula'] = 'Valor general publicado en la fecha del resultado, repetido por legajo. Sin recalcular por rutas ni arrastrar valores de otras fechas.'
METRICS['dqi']['pendiente'] = 'PPM confirmado. Falta identificar la fuente de PPM o el numerador y denominador compatibles para calcularlo.'
METRICS['rmd'].pop('pendiente', None)
METRICS['rmd'].update(unidad='PTS (0–5)', acumulacion='ultimo', formula='Puntaje RMD general publicado en la fecha, repetido sin cambios por legajo. El código de destino debe estar configurado en puntos 0–5, no porcentaje.')


def general_value(key, fecha, source):
    if source.get('error'):
        raise ValueError('No se pudo verificar la fuente del indicador general.')
    label = {'nps': 'NPS GRAL', 'nps_delivery': 'NPS DELIVERY (ENTREGA)', 'rmd': 'RMD Puntaje'}[key]
    rows = [r for r in source.get('rows', []) if r.get('fecha') == fecha and r.get('tipo') == label]
    if not rows:
        raise ValueError('No hay una medición general publicada para esta fecha; no se arrastran valores de otro día.')
    if any(r.get('resultado') is None for r in rows):
        raise ValueError('Falta el valor general publicado.')
    try:
        values = {Decimal(str(r['resultado'])) for r in rows if r.get('resultado') is not None}
    except (InvalidOperation, KeyError):
        raise ValueError('Medición general inválida.')
    if len(values) != 1:
        raise ValueError('La fuente general contiene valores faltantes o contradictorios para esta fecha.')
    value = values.pop()
    low, high = (Decimal(0), Decimal(5)) if key == 'rmd' else (Decimal(-100), Decimal(100))
    if not value.is_finite() or not low <= value <= high:
        raise ValueError('RMD fuera del rango 0–5.' if key == 'rmd' else 'NPS general fuera del rango -100 a 100.')
    return value


def now():
    return datetime.now(timezone.utc).isoformat()


def config():
    return kpi_storage.load("config") or {"empresa_id": 1, "sector_id": 1,
        "metricas": {key: item["codigo"] for key, item in METRICS.items() if not item.get("pendiente") and not item.get("alcance")}}


def save_config(empresa_id, metrics, user):
    try:
        company = int(str(empresa_id))
    except (ValueError, TypeError):
        raise ValueError("Ingresá el ID numérico de la empresa en FichaYA.")
    if not 1 <= company <= 2147483647:
        raise ValueError("El ID de empresa debe ser un entero positivo.")
    normalized = {}
    for key, code in metrics.items():
        if key not in METRICS:
            raise ValueError("Indicador desconocido.")
        if METRICS[key].get("pendiente"):
            raise ValueError(METRICS[key]["pendiente"])
        value = str(code).strip().upper()
        if not value or len(value) > 100:
            raise ValueError("Cada indicador habilitado necesita el código exacto de FichaYA.")
        if value in normalized.values():
            raise ValueError("Dos indicadores no pueden usar el mismo código de FichaYA.")
        normalized[key] = value
    rec = {"empresa_id": company, "sector_id": 1, "metricas": normalized, "usuario": user, "actualizado": now()}
    return kpi_storage.update("config", lambda old: rec)


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Falta un valor medido.")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Valor numérico inválido.")
    if not result.is_finite() or result < 0:
        raise ValueError("Se necesita un valor finito y no negativo.")
    return result


def metric_value(key, routes, time_values):
    if key == "click":
        values = [number(r.get("adhcli")) for r in routes.values()]
        if any(v > 100 for v in values):
            raise ValueError("Adherencia al click fuera de 0–100%.")
        return sum(values) / len(values)
    if key in {"disp_km", "disp_tiempo"}:
        prefix = "disp_km" if key == "disp_km" else "disp_hs"
        planned, actual = Decimal(0), Decimal(0)
        for route in routes.values():
            plan, real = number(route.get(prefix + "_plan")), number(route.get(prefix + "_real"))
            if not plan or route.get("disp_descartada") or abs(100 * (plan - real) / plan) > pipeline.OBJ["disp_error"]:
                raise ValueError("Planificación ausente o dispersión anómala; revisá Foxtrot.")
            planned += plan
            actual += real
        return 100 * (planned - actual) / planned
    if key in {"tml", "ti"}:
        values = []
        for rid in routes:
            calc = time_values.get(rid, {})
            if not calc.get(key + "_ok"):
                raise ValueError("Faltan fichadas/horarios válidos. Actualizá el reporte FichaYA/Foxtrot; no se usan tiempos estimados.")
            values.append(number(calc[key]))
        return sum(values) / len(values)
    raise ValueError(METRICS[key].get("pendiente", "Indicador sin cálculo configurado."))


def calculate(desde, hasta, cfg=None):
    teams.validate_range(desde, hasta)
    cfg = cfg or config()
    if not cfg.get("empresa_id") or not cfg.get("metricas"):
        raise ValueError("Configurá la empresa y al menos un indicador antes de preparar el envío.")
    if any(key not in METRICS or METRICS[key].get("pendiente") for key in cfg["metricas"]):
        raise ValueError("La configuración incluye indicadores pendientes de definir. Revisá los indicadores habilitados.")
    if hasta > date.today().isoformat():
        raise ValueError("No se pueden preparar resultados de fechas futuras.")
    days = storage.load_equipo_days(desde, hasta)
    all_routes = storage.load_all()
    employees = pipeline.fichaya_empleados()
    time_values = {}
    routes = {str(rid): dict(r, rid=str(rid)) for rid, r in all_routes.items()
              if desde <= str(r.get("fecha", "")) <= hasta and pipeline._norm_logistics_branch(r.get("suc")) == "CASA CENTRAL"}
    if {"tml", "ti"} & cfg["metricas"].keys():
        # La exportación exige identidad por legajo; no usar el fallback por nombre
        # del reporte visual para atribuir una fichada a un equipo.
        marks = {key: value for key, value in pipeline.cargar_fichadas_cache(desde, hasta).items()
                 if isinstance(key, tuple) and len(key) == 2 and str(key[1]).startswith("LEGAJO:")}
        mapping = pipeline.fichaya_nombre_map()
        overrides = pipeline.fichaya_ajustes_manuales()
        time_values = {rid: pipeline.calcular_tiempos_fichaya_ruta(route, marks, mapping, employees, overrides)
                       for rid, route in routes.items()}
    errors, results, evidence = [], [], []
    general_source = pipeline.cargar_satisfaccion() if {'nps', 'nps_delivery', 'rmd'} & cfg['metricas'].keys() else {}
    fechas = sorted(set(days) | {r["fecha"] for r in routes.values()})
    if not fechas:
        errors.append("No hay equipos históricos ni rutas para este período.")
    for fecha in fechas:
        day = days.get(fecha)
        current = {rid: r for rid, r in routes.items() if r["fecha"] == fecha}
        if not day:
            errors.append(f"{fecha}: falta guardar el histórico de equipos.")
            continue
        indexed = {team["id"]: team for team in day["equipos"]}
        persons = {}
        used = set()
        for rid, route in current.items():
            assignment = day["rutas"].get(rid)
            if not route.get("usable"):
                errors.append(f"{fecha}, ruta {rid}: datos de ruta no válidos.")
                continue
            if not assignment:
                errors.append(f"{fecha}, ruta {rid}: falta asignar el equipo.")
                continue
            if teams.route_snapshot(route) != assignment["ruta_origen"]:
                errors.append(f"{fecha}, ruta {rid}: cambió la ruta; revisá su asignación.")
                continue
            team = indexed.get(assignment["equipo_id"])
            if not team or team["avisos"] or not team["integrantes"]:
                errors.append(f"{fecha}, ruta {rid}: el equipo tiene integrantes pendientes de revisión.")
                continue
            used.add(team["id"])
            if {"tml", "ti"} & cfg["metricas"].keys():
                driver_ids = {m["legajo"] for m in team["integrantes"] if m["rol"] == "Chofer"}
                if time_values.get(rid, {}).get("legajo") not in driver_ids:
                    errors.append(f"{fecha}, ruta {rid}: el legajo del chofer en las fichadas no coincide con el equipo histórico.")
            for member in team["integrantes"]:
                legajo = member["legajo"]
                employee = employees.get(legajo)
                state = pipeline._norm_persona_key((employee or {}).get("estado", ""))
                if not employee or state not in {"", "ACTIVO", "ACTIVE", "1"}:
                    errors.append(f"{fecha}, legajo {legajo}: no está disponible como empleado activo en el catálogo.")
                    continue
                if employee.get("empresa_id") and str(employee["empresa_id"]) != str(cfg["empresa_id"]):
                    errors.append(f"Legajo {legajo}: pertenece a otra empresa.")
                    continue
                if employee.get("sector_id") and str(employee["sector_id"]) != str(cfg.get("sector_id", 1)):
                    errors.append(f"Legajo {legajo}: el sector no coincide con el catálogo de Operaciones.")
                    continue
                item = persons.setdefault(legajo, {"nombre": employee.get("nombre", member["nombre"]), "rutas": {}})
                item["rutas"][rid] = route
        for rid in day["rutas"]:
            if rid not in current:
                errors.append(f"{fecha}, ruta {rid}: ya no está disponible en la sucursal/día guardados.")
        for team in day["equipos"]:
            if team["id"] not in used:
                errors.append(f"{fecha}, camión {team['numero']}: hay un equipo sin rutas válidas asignadas.")
        for legajo, item in sorted(persons.items()):
            for key, code in cfg["metricas"].items():
                try:
                    value = (general_value(key, fecha, general_source) if key in {'nps', 'nps_delivery', 'rmd'} else metric_value(key, item["rutas"], time_values)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                except (ValueError, InvalidOperation) as exc:
                    errors.append(f"{fecha}, legajo {legajo}, {METRICS[key]['nombre']}: {exc}")
                    continue
                if abs(value) > Decimal("9999999999.9999"):
                    errors.append(f"{fecha}, legajo {legajo}: resultado fuera del rango admitido.")
                    continue
                results.append({"legajo": legajo, "fecha": fecha, "codigo_kpi": code, "valor": format(value, "f")})
                evidence.append({"nombre": item["nombre"], "indicador": METRICS[key]["nombre"], "unidad": METRICS[key]["unidad"],
                                 "rutas": sorted(item["rutas"]), "revision_equipo": day["revision"],
                                 "origen": ("Fichadas y ajustes manuales" if any(time_values.get(rid, {}).get("manual") for rid in item["rutas"]) else "Fichadas por legajo y Foxtrot") if key in {"tml", "ti"} else "Foxtrot"})
                if key in {'nps', 'nps_delivery', 'rmd'}:
                    evidence[-1]['origen'] = 'Medición general publicada del ' + fecha + '; mismo valor para todos los integrantes'
    if len(results) > 10000:
        errors.append("El período supera 10.000 resultados. Seleccioná un rango menor.")
    if not results and not errors:
        errors.append("No hay resultados para preparar.")
    errors = sorted(set(errors))
    fingerprint = teams.digest({"config": cfg, "dias": days, "resultados": results, "evidencia": evidence, "errores": errors})
    return {"empresa_id": cfg["empresa_id"], "resultados": results, "evidencia": evidence, "errores": errors, "huella": fingerprint}


def create_draft(desde, hasta, user):
    calc = calculate(desde, hasta)
    identifier = "run_" + uuid.uuid4().hex
    chunks = [{"estado": "pendiente", "intentos": [], "payload": {"empresa_id": calc["empresa_id"], "resultados": calc["resultados"][i:i + 1000]}}
              for i in range(0, len(calc["resultados"]), 1000)] if not calc["errores"] else []
    rec = {"id": identifier, "creado": now(), "usuario": user, "desde": desde, "hasta": hasta,
           "estado": "bloqueado" if calc["errores"] else "preparado", "config": config(), **calc, "lotes": chunks}
    return kpi_storage.update(identifier, lambda old: rec)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ApiError(Exception):
    def __init__(self, message, status=0, retryable=False, detail=None, wait=0):
        super().__init__(message)
        self.status, self.retryable, self.detail, self.wait = status, retryable, detail or [], wait


def credentials():
    base = (os.environ.get("FICHAYA_API_BASE_URL") or pipeline.FICHAYA_API_BASE_URL).rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("La URL de FichaYA no es válida.")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Usá HTTPS para enviar resultados a FichaYA.")
    username = os.environ.get("FICHAYA_API_USERNAME") or os.environ.get("FICHAYA_API_USER") or os.environ.get("EXTERNAL_API_USERNAME")
    password = os.environ.get("FICHAYA_API_PASSWORD") or os.environ.get("EXTERNAL_API_PASSWORD")
    if not username or not password:
        raise ValueError("Configurá las credenciales técnicas FICHAYA_API_USERNAME y FICHAYA_API_PASSWORD en el servidor.")
    return base, username, password


def api_post(path, body, token=None):
    base, _, _ = credentials()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = Request(base + path, data=json.dumps(body, allow_nan=False).encode("utf-8"), headers=headers, method="POST")
    try:
        with build_opener(NoRedirect()).open(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except (ValueError, UnicodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        try:
            wait = max(3, min(3600, int(exc.headers.get("Retry-After", "3"))))
        except (ValueError, TypeError, AttributeError):
            wait = 3
        # Conservar sólo los errores de filas, nunca cuerpos arbitrarios ni credenciales.
        detail = [{"fila": d.get("fila"), "error": str(d.get("error", ""))[:500]}
                  for d in data.get("detalle_errores", []) if isinstance(d, dict)] if isinstance(data.get("detalle_errores"), list) else []
        messages = {400: "Solicitud rechazada; revisá los datos.", 401: "Credenciales o token no válidos.",
                    403: "FichaYA no habilitó kpis:write para estas credenciales.", 415: "Formato rechazado.",
                    422: "FichaYA rechazó el lote. Corregí las filas indicadas y prepará una nueva vista previa.",
                    429: "Límite de solicitudes alcanzado. Reintentá más tarde.", 503: "La autenticación externa no está configurada en FichaYA."}
        raise ApiError(messages.get(exc.code, "FichaYA devolvió un error; no se pudo confirmar el guardado."),
                       exc.code, exc.code >= 500 or exc.code == 429, detail, wait if exc.code == 429 else 0) from None
    except (URLError, TimeoutError, OSError, ValueError, UnicodeError):
        raise ApiError("No se pudo confirmar la respuesta de FichaYA. Se puede reenviar exactamente el mismo lote.", retryable=True) from None


def token(force=False):
    base, username, password = credentials()
    key = teams.digest([base, username, password])
    if not force and _TOKEN.get("key") == key and _TOKEN.get("expires", 0) > time.time():
        return _TOKEN["value"]
    response = api_post("/api/v1/external/auth/token", {"username": username, "password": password})
    if not isinstance(response, dict) or not isinstance(response.get("access_token"), str) or not response["access_token"]:
        raise ApiError("FichaYA no devolvió un token válido.")
    if "kpis:write" not in str(response.get("scope", "")).split():
        raise ApiError("El token no tiene permiso kpis:write. Habilitá la escritura en FichaYA y solicitá un token nuevo.", 403)
    try:
        ttl = max(0, min(86400, float(response.get("expires_in", 0)) - 30))
    except (ValueError, TypeError):
        ttl = 0
    _TOKEN.update(key=key, value=response["access_token"], expires=time.time() + ttl)
    return response["access_token"]


def post_results(payload):
    access = token()
    try:
        return api_post("/api/v1/external/kpis/resultados", payload, access)
    except ApiError as exc:
        if exc.status != 401:
            raise
    return api_post("/api/v1/external/kpis/resultados", payload, token(force=True))


def send_next(identifier, user):
    if not identifier.startswith("run_"):
        raise ValueError("Envío no encontrado.")
    with kpi_storage.sender_lock():
        run = kpi_storage.load(identifier)
        if not run or run["errores"]:
            raise ValueError("El borrador no existe o tiene errores que deben corregirse.")
        if run["estado"] == "completo":
            return run
        calc = calculate(run["desde"], run["hasta"])
        if calc["huella"] != run["huella"] or calc["errores"]:
            raise ValueError("Los datos, equipos o configuración cambiaron. Prepará una nueva vista previa antes de enviar.")
        index = next((i for i, chunk in enumerate(run["lotes"]) if chunk["estado"] != "guardado"), None)
        if index is None:
            return run
        chunk = run["lotes"][index]
        if chunk["estado"] == "rechazado":
            raise ValueError("Este lote fue rechazado. Corregí los datos y prepará un nuevo borrador.")
        credentials()
        gate = kpi_storage.load("sender")
        if gate.get("disponible", 0) > time.time():
            raise ValueError("Esperá unos segundos antes de reenviar; todavía está vigente la pausa entre solicitudes.")
        # Evitar que un borrador antiguo revierta una corrección más reciente, incluso
        # cuando una respuesta anterior fue incierta. Reservar las claves antes de llamar.
        keys = [f'{run["empresa_id"]}|{r["legajo"]}|{r["fecha"]}|{r["codigo_kpi"]}' for r in chunk["payload"]["resultados"]]
        watermark = gate.get("ultimos", {})
        if any(watermark.get(key, "") > run["creado"] for key in keys):
            raise ValueError("Existe un envío más reciente para estos resultados. Prepará un nuevo borrador.")
        watermark.update({key: run["creado"] for key in keys})
        kpi_storage.update("sender", lambda old: {"ultimos": watermark, "disponible": time.time() + 2.1})
        attempt = {"inicio": now(), "usuario": user}
        chunk["intentos"].append(attempt)
        chunk["estado"], run["estado"] = "enviando", "enviando"
        kpi_storage.update(identifier, lambda old: run)
        try:
            response = post_results(chunk["payload"])
            expected = len(chunk["payload"]["resultados"])
            if (not isinstance(response, dict) or any(type(response.get(k)) is not int for k in ("empresa_id", "recibidos", "guardados"))
                    or response.get("empresa_id") != run["empresa_id"] or response.get("recibidos") != expected or response.get("guardados") != expected):
                raise ApiError("La respuesta no confirma todas las filas. Reintentá el mismo lote.", retryable=True)
            attempt.update(fin=now(), estado="guardado", guardados=expected)
            chunk["estado"] = "guardado"
            run["estado"] = "completo" if all(c["estado"] == "guardado" for c in run["lotes"]) else "parcial"
        except ApiError as exc:
            attempt.update(fin=now(), estado="error", mensaje=str(exc), http=exc.status, detalle=exc.detail)
            chunk["estado"] = "reintentar" if exc.retryable else "rechazado"
            run["estado"] = chunk["estado"]
            if exc.wait:
                kpi_storage.update("sender", lambda old: {**old, "disponible": time.time() + exc.wait})
        return kpi_storage.update(identifier, lambda old: run)
