"""Equipos históricos de Casa Central y asignación conservadora de rutas."""
import copy
import hashlib
import json
import re
from datetime import date, datetime, timezone

import pipeline
import storage

ROLES = (("chofer", "Chofer"), ("ayudante1", "Ayudante 1"), ("ayudante2", "Ayudante 2"))


def validate_range(desde, hasta):
    start, end = date.fromisoformat(desde), date.fromisoformat(hasta)
    if start.isoformat() != desde or end.isoformat() != hasta:
        raise ValueError("Usá fechas con formato YYYY-MM-DD.")
    if start > end or (end - start).days > 366:
        raise ValueError("Seleccioná un rango de hasta 367 días, con inicio anterior al fin.")
    return start, end


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def truck_key(value):
    text = pipeline._norm_persona_key(value)
    # Sólo códigos explícitos: no interpretar como camión el modelo de vehículo.
    number = re.fullmatch(r"\d+", text) or re.match(r"^\((\d+)\)", text)
    if number:
        return "NUM:" + str(int(number.group(1) if number.lastindex else number.group()))
    if text in {"", "SIN CAMION", "SIN CAMIÓN"}:
        return ""
    return "TEXT:" + text


def person(name, mapping, employees):
    ref = pipeline.fichaya_lookup_ref(name, mapping, employees)
    legajo = ref.get("legajo", "")
    employee = employees.get(legajo)
    state = pipeline._norm_persona_key((employee or {}).get("estado", ""))
    issue = ""
    if not legajo:
        issue = "Sin vincular"
    elif not employee:
        issue = "Legajo fuera del catálogo"
    elif state and state not in {"ACTIVO", "ACTIVE", "1"}:
        issue = "Revisar estado: " + str(employee["estado"])
    return {"nombre": name, "legajo": legajo, "nombre_fichaya": (employee or {}).get("nombre", ""), "aviso": issue}


def prepare_days(source, mapping, employees, desde, hasta):
    validate_range(desde, hasta)
    if source.get("error"):
        raise ValueError("No se guardó el histórico porque DPO está incompleto: " + source["error"])
    days = {}
    for raw in source.get("rows", []):
        fecha = raw.get("fecha", "")
        if str(raw.get("sucursal_id")) != "1" or not desde <= fecha <= hasta:
            continue
        date.fromisoformat(fecha)
        if fecha > date.today().isoformat():
            continue
        members = [dict(person(str(raw[field]).strip(), mapping, employees), rol=role)
                   for field, role in ROLES if str(raw.get(field) or "").strip()]
        truck = str(raw.get("nro_camion") or raw.get("camion") or "")
        snapshot = {"fecha": fecha, "camion": str(raw.get("camion") or ""), "numero": truck,
                    "fuente": raw.get("fuente", "dpo"), "recarga": bool(raw.get("recarga")),
                    "personas": raw.get("personas"), "integrantes": members}
        issues = []
        if not any(p["rol"] == "Chofer" for p in members):
            issues.append("Falta chofer")
        if not truck_key(truck):
            issues.append("Falta camión")
        if raw.get("personas") is not None and raw["personas"] != len(members):
            issues.append("Cantidad de personas no coincide")
        if any(p["aviso"] for p in members):
            issues.append("Vinculación pendiente")
        ids = [p["legajo"] or pipeline._norm_persona_key(p["nombre"]) for p in members]
        if len(set(ids)) != len(ids):
            issues.append("Integrante repetido")
        snapshot["avisos"] = issues
        teams = days.setdefault(fecha, [])
        # Una repetición exacta sigue siendo visible y bloquea el cruce automático.
        base = digest(snapshot)[:20]
        occurrence = sum(t["id"].startswith(base + "-") for t in teams)
        snapshot["id"] = base + "-" + str(occurrence + 1)
        teams.append(snapshot)
    return {day: sorted(teams, key=lambda t: t["id"]) for day, teams in days.items()}


def central_routes(routes, fecha):
    return {str(rid): dict(row, rid=str(rid)) for rid, row in routes.items()
            if row.get("fecha") == fecha and row.get("usable")
            and pipeline._norm_logistics_branch(row.get("suc")) == "CASA CENTRAL"}


def route_snapshot(route):
    return {field: route.get(field) for field in ("rid", "fecha", "suc", "camion", "chofer", "usable")}


def match_routes(teams, routes, mapping, employees):
    matches = {}
    for rid, route in routes.items():
        key = truck_key(route.get("camion"))
        driver = pipeline.fichaya_lookup_ref(route.get("chofer", ""), mapping, employees).get("legajo")
        if not key or not driver:
            continue
        candidates = [team for team in teams if truck_key(team["numero"]) == key
                      and any(p["rol"] == "Chofer" and p["legajo"] == driver for p in team["integrantes"])]
        if len(candidates) == 1 and not candidates[0]["avisos"]:
            matches[rid] = {"equipo_id": candidates[0]["id"], "modo": "automatico",
                            "criterio": "Fecha, Casa Central, camión y legajo del chofer",
                            "ruta_origen": route_snapshot(route)}
    return matches


def event(action, user, reason="", **extra):
    return {"accion": action, "usuario": user, "motivo": reason,
            "fecha_hora": datetime.now(timezone.utc).isoformat(), **extra}


def sync_history(desde, hasta, user):
    source = pipeline.cargar_dpo_gkpis()
    mapping, employees = pipeline.fichaya_nombre_map(), pipeline.fichaya_empleados()
    proposals = prepare_days(source, mapping, employees, desde, hasta)
    routes = storage.load_all()
    summary = {"guardados": 0, "existentes": 0, "cambiados": 0, "fechas_cambiadas": []}
    for fecha, teams in proposals.items():
        def transform(current):
            if current:
                changed = current["huella"] != digest(teams)
                summary["cambiados" if changed else "existentes"] += 1
                if changed:
                    summary["fechas_cambiadas"].append(fecha)
                return current
            summary["guardados"] += 1
            return {"fecha": fecha, "revision": 1, "equipos": teams, "huella": digest(teams),
                    "rutas": match_routes(teams, central_routes(routes, fecha), mapping, employees),
                    "auditoria": [event("importacion", user)]}
        storage.update_equipo_day(fecha, transform)
    return summary


def revise_day(fecha, revision, user, reason):
    if not reason.strip():
        raise ValueError("Indicá el motivo de la actualización del equipo.")
    mapping, employees = pipeline.fichaya_nombre_map(), pipeline.fichaya_empleados()
    proposal = prepare_days(pipeline.cargar_dpo_gkpis(), mapping, employees, fecha, fecha).get(fecha)
    if not proposal:
        raise ValueError("No hay equipos en DPO para ese día. Se conserva el histórico.")
    routes = central_routes(storage.load_all(), fecha)
    def transform(current):
        require_revision(current, revision)
        previous = {key: copy.deepcopy(current[key]) for key in ("equipos", "rutas", "huella")}
        current.update(equipos=proposal, huella=digest(proposal), revision=revision + 1,
                       rutas=match_routes(proposal, routes, mapping, employees))
        current["auditoria"].append(event("actualizacion_desde_dpo", user, reason, anterior=previous))
        return current
    return storage.update_equipo_day(fecha, transform)


def require_revision(current, revision):
    if not current or current.get("revision") != revision:
        raise ValueError("El histórico cambió. Recargá la página antes de guardar.")


def assign_route(fecha, revision, rid, team_id, user, reason):
    if not reason.strip():
        raise ValueError("Indicá el motivo de la asignación o desvinculación.")
    routes = central_routes(storage.load_all(), fecha)
    def transform(current):
        require_revision(current, revision)
        previous = copy.deepcopy(current.get("rutas", {}).get(rid))
        if team_id:
            if rid not in routes:
                raise ValueError("La ruta debe ser válida, de Casa Central y del mismo día.")
            team = next((t for t in current["equipos"] if t["id"] == team_id), None)
            if not team or team["avisos"]:
                raise ValueError("El equipo debe tener todos sus integrantes vinculados y sin inconsistencias.")
            current["rutas"][rid] = {"equipo_id": team_id, "modo": "manual", "ruta_origen": route_snapshot(routes[rid])}
        else:
            if not previous:
                raise ValueError("La ruta no tiene una asignación guardada.")
            current["rutas"].pop(rid)
        current["revision"] += 1
        current["auditoria"].append(event("asignacion_ruta", user, reason, ruta=rid, anterior=previous,
                                          nuevo=copy.deepcopy(current["rutas"].get(rid))))
        return current
    return storage.update_equipo_day(fecha, transform)
