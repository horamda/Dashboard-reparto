"""Read-only orders API client and conservative Foxtrot link diagnostics."""
import json
from datetime import date, timedelta, datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def fetch_orders(config, desde, hasta, sucursal="TODAS", opener=urlopen):
    if not config.get("api_key"):
        raise ValueError("Falta configurar LOGISTICS_INTEGRATION_API_KEY en este servidor.")
    start, end = date.fromisoformat(desde), date.fromisoformat(hasta)
    if start > end or (end-start).days > 366:
        raise ValueError("Rango invalido: maximo 367 dias.")
    rows, seen, coverage = [], set(), []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=30), end)
        offset, expected = 0, None
        while True:
            params = {"desde": cursor.isoformat(), "hasta": stop.isoformat(), "sucursal": sucursal, "limit": 1000, "offset": offset}
            # This endpoint explicitly does not support empresa_id.
            request = Request(config["base_url"].rstrip("/") + "/api/v1/integracion/logistica/pedidos?" + urlencode(params), headers={"X-API-Key": config["api_key"], "Accept": "application/json"})
            try:
                with opener(request, timeout=config.get("timeout", 30)) as response:
                    payload = json.load(response)
            except HTTPError as exc:
                raise RuntimeError(f"API pedidos HTTP {exc.code}. Revisar disponibilidad y clave de integracion.") from None
            except (OSError, ValueError):
                raise RuntimeError("La API de pedidos no devolvio una respuesta JSON valida.") from None
            if not isinstance(payload, dict) or payload.get("api_version") != "v1" or not isinstance(payload.get("datos"), list):
                raise ValueError("Respuesta incompatible con el contrato de pedidos v1.")
            page = payload["datos"]
            pagination = payload.get("paginacion", {})
            total = pagination.get("total")
            if type(total) is not int or total < 0 or pagination.get("offset") != offset or pagination.get("devueltos") != len(page) or type(pagination.get("hay_mas")) is not bool:
                raise ValueError("Paginacion de pedidos invalida; no se guardaron datos parciales.")
            if expected is not None and total != expected:
                raise ValueError("La fuente cambio durante la consulta; repetir con importaciones estables.")
            expected = total
            for row in page:
                if not isinstance(row, dict) or not row.get("id_integracion"):
                    raise ValueError("Pedido sin id_integracion.")
                identity = str(row["id_integracion"])
                if identity in seen:
                    raise ValueError("Pedido duplicado entre paginas; revisar la fuente.")
                if not cursor.isoformat() <= str(row.get("fecha_entrega") or "") <= stop.isoformat():
                    raise ValueError("Pedido fuera del rango solicitado.")
                seen.add(identity)
                rows.append(row)
            offset += len(page)
            if offset > total or len(rows) > 200000:
                raise ValueError("Cantidad de pedidos incompatible con la paginacion.")
            if not pagination["hay_mas"]:
                if offset != total:
                    raise ValueError("Consulta incompleta; no se guardaron datos parciales.")
                coverage.append({"desde": cursor.isoformat(), "hasta": stop.isoformat(), "cobertura": payload.get("cobertura", {}), "total": total})
                break
            if not page or offset >= total:
                raise ValueError("Paginacion sin avance.")
        cursor = stop + timedelta(days=1)
    return {"datos": rows, "desde": desde, "hasta": hasta, "sucursal": sucursal,
            "coverage": coverage, "generado_en": datetime.now(timezone.utc).isoformat()}


def diagnose_links(orders, attempts, routes):
    # Route/date/customer alone cannot identify an order when a customer has several.
    by_id = {}
    for attempt in attempts:
        rid = str(attempt.get("route_id") or attempt.get("Route ID") or "")
        route = routes.get(rid)
        if not route:
            continue
        references = {str(attempt[k]).strip() for k in ("numero_pedido", "nro_pedido", "pedido_id", "Order ID", "Order Number") if attempt.get(k) not in (None, "")}
        for reference in references:
            by_id.setdefault(reference, []).append((attempt, route))
    counts = {"pedidos": len(orders), "vinculos_candidatos": 0, "sin_vinculo": 0, "ambiguos": 0, "sin_identificador": 0, "estado_desconocido": 0}
    for order in orders:
        if order.get("estado_entrega") not in ("completa", "parcial", "rechazada"):
            counts["estado_desconocido"] += 1
        reference = str(order.get("numero_pedido") or "").strip()
        if not reference or order.get("tipo_identificador") == "fila":
            counts["sin_identificador"] += 1
            continue
        candidates = []
        for attempt, route in by_id.get(reference, []):
            # Preserve leading zeros and require an explicit branch code; names are not codes.
            branch = str(attempt.get("sucursal_id") or route.get("sucursal_id") or "")
            client = str(attempt.get("cliente") or attempt.get("Customer ID") or "")
            if branch and branch == str(order.get("sucursal_id")) and client == str(order.get("cliente_id")) and route.get("fecha") == order.get("fecha_entrega"):
                candidates.append(attempt)
        if len(candidates) == 1:
            counts["vinculos_candidatos"] += 1
        elif candidates:
            counts["ambiguos"] += 1
        else:
            counts["sin_vinculo"] += 1
    return counts
