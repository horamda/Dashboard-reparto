# -*- coding: utf-8 -*-
"""Calculo de costos logisticos por ruta y cliente."""

import hashlib
import json
import math
import os
import uuid
from datetime import date, datetime, timezone
from urllib.error import URLError
from urllib.request import urlopen

import storage


DEFAULT_CONFIG = {
    "fuel_price_per_liter": 0.0,
    "vehicle_liters_per_100km": 0.0,
    "vehicle_cost_per_km": 0.0,
    "driver_cost_per_hour": 0.0,
    "helper_cost_per_hour": 0.0,
    "helpers_count": 0,
    "other_route_cost": 0.0,
    "weights": {"distance": 0.40, "time": 0.30, "volume": 0.30},
    "volume_criterion": "bultos",
    "return_distance_criterion": "proportional_forward_distance",
    "profitability_thresholds": {"green_max_pct": 3.0, "yellow_max_pct": 6.0},
    "depot_by_sucursal": {},
}


def _num(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        value = float(v)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _norm_cliente(v):
    s = str(v or "").strip()
    if len(s) > 8 and s.isdigit():
        return str(int(s[-8:]))
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    return s


def _haversine_km(a, b):
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _valid_point(lat, lon):
    return (
        lat is not None
        and lon is not None
        and math.isfinite(lat)
        and math.isfinite(lon)
        and -90 <= lat <= 90
        and -180 <= lon <= 180
        and not (lat == 0 and lon == 0)
    )


class RoutingService:
    def __init__(self, provider="osrm", base_url=None, timeout_seconds=None):
        self.provider = provider
        self.base_url = (base_url or os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")).rstrip("/")
        self.timeout_seconds = float(timeout_seconds or os.environ.get("ROUTING_TIMEOUT_SECONDS", "8"))

    def segment_distance(self, origin, destination):
        key = self._key(origin, destination)
        cached = storage.routing_cache_get(key)
        if cached:
            return cached
        rec = self._osrm_segment(origin, destination)
        storage.routing_cache_set(key, rec["provider"], rec)
        return rec

    def calculate_segments(self, points):
        if len(points) < 2:
            return []
        keys = [self._key(points[index], points[index + 1]) for index in range(len(points) - 1)]
        if hasattr(storage, "routing_cache_get_many"):
            cached_by_key = storage.routing_cache_get_many(keys)
            cached = [cached_by_key.get(key) for key in keys]
        else:
            cached = [storage.routing_cache_get(key) for key in keys]
        if all(cached):
            results = cached
        else:
            fetched = self._osrm_multi_segments(points)
            results = []
            pending_cache = {}
            for key, cached_item, fetched_item in zip(keys, cached, fetched):
                result = cached_item or fetched_item
                if not cached_item:
                    pending_cache[key] = result
                results.append(result)
            if pending_cache and hasattr(storage, "routing_cache_set_many"):
                storage.routing_cache_set_many(pending_cache)
            else:
                for key, result in pending_cache.items():
                    storage.routing_cache_set(key, result["provider"], result)
        return [
            {
                "origin": {"latitud": points[index][0], "longitud": points[index][1]},
                "destination": {"latitud": points[index + 1][0], "longitud": points[index + 1][1]},
                **result,
            }
            for index, result in enumerate(results)
        ]

    def _fallback_segment(self, origin, destination):
        return {
            "distance_km": _haversine_km(origin, destination),
            "duration_seconds": 0,
            "geometry": [],
            "provider": "haversine_fallback",
            "fallback": True,
        }

    def _osrm_multi_segments(self, points):
        max_coordinates = max(2, int(os.environ.get("ROUTING_MAX_COORDINATES", "100")))
        if len(points) > max_coordinates:
            results = []
            for start in range(0, len(points) - 1, max_coordinates - 1):
                chunk = points[start:min(start + max_coordinates, len(points))]
                if len(chunk) >= 2:
                    results.extend(self._osrm_multi_segments(chunk))
            return results
        fallback = [self._fallback_segment(points[index], points[index + 1]) for index in range(len(points) - 1)]
        coordinates = ";".join(f"{point[1]},{point[0]}" for point in points)
        url = (
            f"{self.base_url}/route/v1/driving/{coordinates}"
            "?overview=false&geometries=geojson&steps=true"
        )
        try:
            with urlopen(url, timeout=self.timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            route = (payload.get("routes") or [])[0]
            legs = route.get("legs") or []
            if len(legs) != len(points) - 1:
                raise ValueError("El proveedor no devolvió todos los tramos.")
            results = []
            for leg in legs:
                geometry = []
                for step in leg.get("steps") or []:
                    coordinates = (step.get("geometry") or {}).get("coordinates") or []
                    if geometry and coordinates and geometry[-1] == coordinates[0]:
                        coordinates = coordinates[1:]
                    geometry.extend(coordinates)
                distance = _num(leg.get("distance"), None)
                duration = _num(leg.get("duration"), None)
                if distance is None or distance < 0 or duration is None or duration < 0:
                    raise ValueError("El proveedor devolvió un tramo inválido.")
                results.append({
                    "distance_km": distance / 1000,
                    "duration_seconds": duration,
                    "geometry": geometry,
                    "provider": self.provider,
                    "fallback": False,
                })
            return results
        except (OSError, URLError, ValueError, KeyError, IndexError):
            return fallback

    def _osrm_segment(self, origin, destination):
        url = (
            f"{self.base_url}/route/v1/driving/"
            f"{origin[1]},{origin[0]};{destination[1]},{destination[0]}"
            "?overview=full&geometries=geojson&steps=false"
        )
        try:
            with urlopen(url, timeout=self.timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            routes = payload.get("routes") or []
            if not routes:
                raise ValueError("El proveedor no devolvió una ruta.")
            route = routes[0]
            meters = _num(route.get("distance"), None)
            seconds = _num(route.get("duration"), None)
            if meters is None or meters < 0 or seconds is None or seconds < 0:
                raise ValueError("El proveedor devolvió una ruta inválida.")
            return {
                "distance_km": meters / 1000,
                "duration_seconds": seconds,
                "geometry": (route.get("geometry") or {}).get("coordinates") or [],
                "provider": self.provider,
                "fallback": False,
            }
        except (OSError, URLError, ValueError, KeyError):
            return self._fallback_segment(origin, destination)

    def _key(self, origin, destination):
        raw = f"v2|{origin[0]:.6f},{origin[1]:.6f}|{destination[0]:.6f},{destination[1]:.6f}|{self.provider}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class LogisticsCostService:
    def __init__(self, routing_service=None):
        self.routing = routing_service or RoutingService()

    def calculate_route(self, rid, overwrite=True, actor="system", reason=""):
        storage.ensure_logistics_tables()
        route = storage.get_route(rid)
        if not route:
            raise ValueError("Ruta no encontrada.")
        config = self._config(route.get("fecha"), route.get("camion"))
        clientes = storage.load_clientes()
        attempts = list(storage.load_attempts_by_route(rid).values())
        stops, warnings = self._build_stops(attempts, clientes)
        if not stops:
            raise ValueError("La ruta no tiene visitas con clientes reconocibles.")
        if str(route.get("camion") or "").strip().lower() in ("", "sin camion", "sin camión"):
            warnings.append("Ruta sin vehículo identificado; se aplicó la tarifa general.")
        if _num(route.get("horas")) <= 0:
            warnings.append("Ruta sin duración válida; el costo de personal puede quedar incompleto.")
        has_stop_volume = any(
            _num(stop.get("bultos")) > 0 or _num(stop.get("hl")) > 0
            or _num(stop.get("pallets")) > 0 or _num(stop.get("unidades")) > 0
            for stop in stops
        )
        volume_rows = sum(
            any(stop.get(f"{key}_available") for key in ("bultos", "hl", "pallets", "unidades"))
            for stop in stops
        )
        if 0 < volume_rows < len(stops):
            warnings.append(f"Volumen entregado disponible para {volume_rows} de {len(stops)} clientes.")
        if volume_rows and not has_stop_volume:
            warnings.append("El volumen entregado informado es cero para toda la ruta.")
        elif not has_stop_volume and _num(route.get("bultos")) <= 0 and _num(route.get("hl")) <= 0 and _num(route.get("pallets")) <= 0:
            warnings.append("Ruta sin volumen entregado; no se calculan costos por bulto, HL o pallet.")
        if _num(config.get("fuel_price_per_liter")) <= 0 or _num(config.get("vehicle_liters_per_100km")) <= 0:
            warnings.append("Combustible o consumo sin configurar; el costo de combustible será cero.")
        sales = storage.load_route_sales(rid)
        for stop in stops:
            sale = sales.get(stop["cliente"]) or {}
            stop["venta"] = _num(sale.get("venta"))
            stop["pedidos_facturados"] = int(_num(sale.get("pedidos_facturados")))

        route_km = _num(route.get("disp_km_real")) / 1000
        if route_km <= 0:
            route_km = _num(route.get("disp_km_plan")) / 1000

        segment_fallback = False
        routing_segments = []
        return_km = 0.0
        routing_total_km = 0.0
        depot = self._depot(config, route)
        if depot:
            valid_stops = []
            points = [depot]
            for stop in stops:
                if stop.get("latitud") is None or stop.get("longitud") is None:
                    warnings.append(f"Cliente {stop['cliente']} sin GPS válido.")
                    stop["segment_km"] = 0.0
                    continue
                valid_stops.append(stop)
                points.append((stop["latitud"], stop["longitud"]))
            if valid_stops:
                points.append(depot)
                if hasattr(self.routing, "calculate_segments"):
                    segments = self.routing.calculate_segments(points)
                else:
                    segments = [
                        self.routing.segment_distance(points[index], points[index + 1])
                        for index in range(len(points) - 1)
                    ]
                for index, stop in enumerate(valid_stops):
                    seg = segments[index]
                    stop["segment_km"] = max(0, _num(seg.get("distance_km")))
                    stop["segment_duration_seconds"] = max(0, _num(seg.get("duration_seconds")))
                    if stop["segment_km"] > 1000:
                        warnings.append(f"Distancia inusual al cliente {stop['cliente']}: {stop['segment_km']:.1f} km.")
                    segment_fallback = segment_fallback or bool(seg.get("fallback"))
                    routing_total_km += stop["segment_km"]
                    routing_segments.append(self._segment_record(points[index], points[index + 1], seg, stop["cliente"]))
                return_segment = segments[-1]
                segment_fallback = segment_fallback or bool(return_segment.get("fallback"))
                return_km = max(0, _num(return_segment.get("distance_km")))
                routing_total_km += return_km
                routing_segments.append(self._segment_record(points[-2], depot, return_segment, "deposito"))
                if return_km > 1000:
                    warnings.append(f"Distancia de regreso inusual: {return_km:.1f} km.")
            if route_km <= 0:
                route_km = routing_total_km
        else:
            warnings.append("Falta configurar coordenada del deposito para la sucursal.")

        total_cost, components = self._route_cost(route, config, route_km)
        customer_costs, allocation = self._allocate(route, stops, total_cost, config, return_km)
        delivered_bultos = (
            sum(_num(item.get("bultos")) for item in customer_costs)
            if any(item.get("bultos_available") for item in customer_costs)
            else max(0, _num(route.get("bultos")))
        )
        delivered_hl = (
            sum(_num(item.get("hl")) for item in customer_costs)
            if any(item.get("hl_available") for item in customer_costs)
            else max(0, _num(route.get("hl")))
        )
        delivered_pallets = (
            sum(_num(item.get("pallets")) for item in customer_costs)
            if any(item.get("pallets_available") for item in customer_costs)
            else max(0, _num(route.get("pallets")))
        )
        delivered_units = (
            sum(_num(item.get("unidades")) for item in customer_costs)
            if any(item.get("unidades_available") for item in customer_costs)
            else max(0, _num(route.get("unidades")))
        )
        delivered_customers = sum(
            1 for item in customer_costs
            if any(_num(item.get(key)) > 0 for key in ("bultos", "hl", "pallets", "unidades"))
            or any(word in str(item.get("estado_entrega") or "").lower() for word in ("success", "entregado", "delivered", "parcial", "partial"))
        )
        total_sales = sum(_num(item.get("venta")) for item in customer_costs)
        customers_with_sales = sum(1 for item in customer_costs if _num(item.get("venta")) > 0)
        if customers_with_sales == 0:
            warnings.append("No se encontraron ventas facturadas con coincidencia única para esta ruta.")
        if segment_fallback:
            warnings.append("Uno o más tramos usan distancia estimada porque el proveedor vial no respondió.")
        existing = storage.load_route_cost(rid).get("route") if overwrite else None
        calculation_id = str(uuid.uuid4())
        calculation_type = "recalculation" if existing else "original"
        calculated_at = datetime.now(timezone.utc).isoformat()
        route_cost = {
            "rid": rid,
            "fecha": route.get("fecha"),
            "sucursal": route.get("suc"),
            "chofer": route.get("chofer"),
            "camion": route.get("camion"),
            "clientes": len(stops),
            "clientes_entregados": delivered_customers,
            "km": route_km,
            "horas": _num(route.get("horas")),
            "inicio": route.get("inicio_foxtrot"),
            "fin": route.get("fin_foxtrot"),
            "bultos": delivered_bultos,
            "hl": delivered_hl,
            "pallets": delivered_pallets,
            "unidades": delivered_units,
            "components": components,
            "total_cost": total_cost,
            "cost_per_customer": total_cost / len(stops) if stops else 0,
            "cost_per_km": total_cost / route_km if route_km else 0,
            "cost_per_bulto": total_cost / delivered_bultos if delivered_bultos else 0,
            "cost_per_hl": total_cost / delivered_hl if delivered_hl else 0,
            "cost_per_pallet": total_cost / delivered_pallets if delivered_pallets else 0,
            "cost_per_unit": total_cost / delivered_units if delivered_units else 0,
            "venta": total_sales,
            "cost_to_sales_pct": total_cost / total_sales * 100 if total_sales else None,
            "sales_customers_covered": customers_with_sales,
            "sales_customers_total": len(customer_costs),
            "sales_matching_criterion": "cliente_fecha_entrega_unica_ruta_facturado_no_anulado",
            "calculation_id": calculation_id,
            "calculation_type": calculation_type,
            "calculated_at": calculated_at,
            "calculated_by": str(actor or "system"),
            "recalculation_reason": str(reason or "").strip(),
            "previous_calculation_id": (existing.get("calculation_id") or f"legacy:{rid}") if existing else None,
            "config_snapshot": config,
            "calculation_version": "v1",
            "routing_provider": self.routing.provider,
            "routing_distance_km": routing_total_km,
            "return_distance_km": return_km,
            "routing_segments": routing_segments,
            "allocation": allocation,
            "warnings": warnings,
            "routing_fallback": segment_fallback,
        }
        for item in customer_costs:
            item["calculation_id"] = calculation_id
        if overwrite:
            storage.save_route_cost(rid, route_cost, customer_costs)
        return {"route": route_cost, "customers": customer_costs}

    def _segment_record(self, origin, destination, result, destination_ref):
        return {
            "origin": {"latitud": origin[0], "longitud": origin[1]},
            "destination": {"latitud": destination[0], "longitud": destination[1]},
            "destination_ref": destination_ref,
            "distance_km": _num(result.get("distance_km")),
            "duration_seconds": _num(result.get("duration_seconds")),
            "geometry": result.get("geometry") or [],
            "provider": result.get("provider") or self.routing.provider,
            "fallback": bool(result.get("fallback")),
        }

    def get_config(self):
        return self._config(date.today().isoformat())

    def _config(self, route_date=None, vehicle=None):
        cfg = DEFAULT_CONFIG.copy()
        saved = storage.load_logistics_config()
        for key, value in (saved or {}).items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                tmp = dict(cfg[key])
                tmp.update(value)
                cfg[key] = tmp
            else:
                cfg[key] = value
        effective_date = str(route_date or date.today().isoformat())
        rate = storage.get_effective_logistics_cost_rate(effective_date)
        if rate:
            cfg.update({key: value for key, value in rate.items() if key != "valid_from"})
            cfg["rate_valid_from"] = rate["valid_from"]
        vehicle_rate = None
        if vehicle and str(vehicle).strip().lower() not in ("", "sin camion", "sin camión"):
            vehicle_rate = storage.get_effective_logistics_vehicle_cost(vehicle, effective_date)
        if vehicle_rate:
            cfg.update({key: value for key, value in vehicle_rate.items() if key not in ("valid_from", "vehicle")})
            cfg["vehicle_rate_valid_from"] = vehicle_rate["valid_from"]
            cfg["vehicle_rate_vehicle"] = str(vehicle)
        depots = storage.load_logistics_depots()
        if depots:
            cfg["depot_by_sucursal"] = {
                sucursal: {
                    "nombre": rec.get("nombre") or sucursal,
                    "latitud": rec.get("latitud"),
                    "longitud": rec.get("longitud"),
                }
                for sucursal, rec in depots.items()
            }
        return cfg

    def _depot(self, config, route):
        depot = (config.get("depot_by_sucursal") or {}).get(str(route.get("suc") or ""))
        if not depot:
            return None
        lat = _num(depot.get("latitud"), None)
        lon = _num(depot.get("longitud"), None)
        return (lat, lon) if _valid_point(lat, lon) else None

    def _build_stops(self, attempts, clientes):
        stops, warnings, seen = [], [], set()
        attempts = sorted(attempts, key=lambda r: (str(r.get("Driver Click Timestamp") or ""), str(r.get("Visit Start Timestamp") or "")))
        for idx, att in enumerate(attempts, start=1):
            cid = _norm_cliente(att.get("cliente") or att.get("Customer ID"))
            if not cid or cid in seen:
                continue
            seen.add(cid)
            cli = clientes.get(cid) or {}
            lat = _num(cli.get("latitud"), None)
            lon = _num(cli.get("longitud"), None)
            if not _valid_point(lat, lon):
                if lat is not None or lon is not None:
                    warnings.append(f"Cliente {cid} con coordenadas inválidas.")
                lat, lon = None, None
            stops.append({
                "cliente": cid,
                "nombre": cli.get("nombre") or att.get("Customer Name") or "",
                "razon_social": cli.get("razon_social") or "",
                "direccion": cli.get("direccion") or "",
                "localidad": cli.get("localidad") or "",
                "orden_visita": idx,
                "latitud": lat,
                "longitud": lon,
                "tiempo_segundos": max(0, _num(att.get("service_duration_seconds") or att.get("Visit Duration Seconds"))),
                "hora_llegada": att.get("Visit Start Timestamp") or att.get("visit_start"),
                "hora_salida": att.get("Driver Click Timestamp") or att.get("driver_click"),
                "estado_entrega": att.get("estado_entrega") or att.get("Aggregate Visit Status") or "",
                "bultos": max(0, _num(att.get("bultos"))),
                "hl": max(0, _num(att.get("hl"))),
                "pallets": max(0, _num(att.get("pallets"))),
                "unidades": max(0, _num(att.get("unidades"))),
                "bultos_available": "bultos" in att and att.get("bultos") not in (None, ""),
                "hl_available": "hl" in att and att.get("hl") not in (None, ""),
                "pallets_available": "pallets" in att and att.get("pallets") not in (None, ""),
                "unidades_available": "unidades" in att and att.get("unidades") not in (None, ""),
            })
        return stops, warnings

    def _route_cost(self, route, config, km):
        km = max(0, _num(km))
        hours = max(0, _num(route.get("horas")))
        fuel = (km / 100) * max(0, _num(config.get("vehicle_liters_per_100km"))) * max(0, _num(config.get("fuel_price_per_liter")))
        vehicle = km * max(0, _num(config.get("vehicle_cost_per_km")))
        team_hour = max(0, _num(config.get("driver_cost_per_hour"))) + max(0, _num(config.get("helpers_count"))) * max(0, _num(config.get("helper_cost_per_hour")))
        personal = hours * team_hour
        other = max(0, _num(config.get("other_route_cost")))
        components = {"fuel": fuel, "vehicle": vehicle, "personal": personal, "other": other}
        return sum(components.values()), components

    def _allocate(self, route, stops, total_cost, config, return_km=0.0):
        total_cost = max(0, _num(total_cost))
        return_km = max(0, _num(return_km))
        volume_criterion = str(config.get("volume_criterion") or "bultos")
        if volume_criterion not in ("bultos", "hl", "pallets", "unidades"):
            volume_criterion = "bultos"
        route_volume = max(0, _num(route.get(volume_criterion)))
        configured_weights = dict(config.get("weights") or {})
        weights = {key: max(0, _num(configured_weights.get(key))) for key in ("distance", "time", "volume")}
        forward_distance = sum(max(0, _num(s.get("segment_km"))) for s in stops)
        if forward_distance <= 0:
            weights["distance"] = 0
        if sum(max(0, _num(s.get("tiempo_segundos"))) for s in stops) <= 0:
            weights["time"] = 0
        customer_volume = sum(max(0, _num(s.get(volume_criterion))) for s in stops)
        if any(s.get(f"{volume_criterion}_available") for s in stops):
            route_volume = 0
        if customer_volume <= 0 and route_volume <= 0:
            weights["volume"] = 0
        total_weight = sum(weights.values())
        equal_allocation = total_weight <= 0
        weights = {k: (_num(v) / total_weight if total_weight > 0 else 0) for k, v in weights.items()}
        total_time = sum(max(0, _num(s.get("tiempo_segundos"))) for s in stops) or 1
        total_volume = customer_volume or route_volume or 1
        distance_values = []
        for stop in stops:
            forward = max(0, _num(stop.get("segment_km")))
            return_share = return_km * (forward / forward_distance) if forward_distance > 0 else 0
            distance_values.append(forward + return_share)
        total_distance = sum(distance_values) or 1
        out, assigned = [], 0.0
        for i, s in enumerate(stops):
            volume = max(0, _num(s.get(volume_criterion))) or (route_volume / len(stops) if route_volume and stops else 0)
            score = 1 / len(stops) if equal_allocation else (
                (distance_values[i] / total_distance) * weights.get("distance", 0)
                + (max(0, _num(s.get("tiempo_segundos"))) / total_time) * weights.get("time", 0)
                + (volume / total_volume) * weights.get("volume", 0)
            )
            cost = total_cost * score
            assigned += cost
            item = dict(s)
            item.update({
                "rid": route.get("rid"),
                "km_atribuibles": distance_values[i],
                "km_regreso_asignados": distance_values[i] - max(0, _num(s.get("segment_km"))),
                "costo_entrega": cost,
                "cost_per_bulto": cost / max(0, _num(s.get("bultos"))) if max(0, _num(s.get("bultos"))) else 0,
                "cost_per_hl": cost / max(0, _num(s.get("hl"))) if max(0, _num(s.get("hl"))) else 0,
                "cost_per_pallet": cost / max(0, _num(s.get("pallets"))) if max(0, _num(s.get("pallets"))) else 0,
                "cost_to_sales_pct": cost / max(0, _num(s.get("venta"))) * 100 if max(0, _num(s.get("venta"))) else None,
                "allocation_score": score,
            })
            out.append(item)
        if out and abs(total_cost - assigned) > 0.01:
            out[-1]["costo_entrega"] += total_cost - assigned
        return out, {
            "effective_weights": weights,
            "volume_criterion": volume_criterion,
            "return_distance_criterion": "proportional_forward_distance",
            "equal_allocation_fallback": equal_allocation,
            "assigned_total": sum(item["costo_entrega"] for item in out),
        }
