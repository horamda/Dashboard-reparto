from datetime import datetime
from io import BytesIO

from pipeline import procesar_clientes
from pipeline_pedidos import FRANJAS, _franja, _localidad_de_ruta


def test_order_time_buckets_between_11_and_15_are_30_minutes():
    assert "14:00-14:30" in FRANJAS
    assert "14:15-14:30" not in FRANJAS
    assert _franja(datetime(2026, 8, 31, 14, 0)) == "14:00-14:30"
    assert _franja(datetime(2026, 8, 31, 14, 29)) == "14:00-14:30"
    assert _franja(datetime(2026, 8, 31, 14, 30)) == "14:30-15:00"


def test_order_location_accepts_route_code_on_either_side():
    assert _localidad_de_ruta("523 - DOLORES") == "DOLORES"
    assert _localidad_de_ruta("DOLORES - 523") == "DOLORES"
    assert _localidad_de_ruta("1794") == "SIN RUTA"


def test_customer_import_reads_nombre_de_localidad_column():
    csv = (
        "Cliente;Sucursal;Razon social;Nombre de fantasia;Nombre de localidad;Horario de entrega\n"
        "6205;2;POLIRRUBRO MARIOS;POLIRRUBRO MARIOS;DOLORES;09:30 A 23:00\n"
    ).encode("cp1252")

    clientes = procesar_clientes(BytesIO(csv))

    assert clientes["6205"]["localidad"] == "DOLORES"
