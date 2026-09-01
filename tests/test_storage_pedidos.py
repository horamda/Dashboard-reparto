import storage_pedidos


def test_fetch_dashboard_enriches_orders_with_client_table(monkeypatch):
    storage_pedidos.clear_cache()
    monkeypatch.setattr(storage_pedidos, "init_db", lambda: None)
    monkeypatch.setattr(storage_pedidos.storage, "backend_name", lambda: "json")
    monkeypatch.setattr(
        storage_pedidos,
        "_load_json",
        lambda: {
            "1": {
                "nro_pedido": 1,
                "fecha_alta": "2026-08-31T10:00:00",
                "cliente": "000123 - CLIENTE EXPORT",
                "raw_pedido": {"payload": "pesado"},
            }
        },
    )
    monkeypatch.setattr(
        storage_pedidos.storage,
        "load_clientes",
        lambda: {
            "123": {
                "cliente": "123",
                "sucursal": "1",
                "razon_social": "RAZON SOCIAL SA",
                "nombre": "Nombre comercial",
                "localidad": "Dolores",
                "horario_entrega": "09:00 A 13:00",
                "ventanas": [{"ini": 540, "fin": 780}],
                "latitud": -36.1,
                "longitud": -57.2,
                "direccion": "Calle 1",
            }
        },
    )

    rows = storage_pedidos.fetch_dashboard()

    assert len(rows) == 1
    assert "raw_pedido" not in rows[0]
    assert rows[0]["cliente_codigo"] == "123"
    assert rows[0]["cliente_razon_social"] == "RAZON SOCIAL SA"
    assert rows[0]["cliente_nombre"] == "Nombre comercial"
    assert rows[0]["cliente_localidad"] == "Dolores"
    assert rows[0]["cliente_horario_entrega"] == "09:00 A 13:00"
    assert rows[0]["cliente_ventanas"] == [{"ini": 540, "fin": 780}]
    assert rows[0]["cliente_latitud"] == -36.1
    assert rows[0]["cliente_longitud"] == -57.2


def test_cliente_projection_discards_numeric_location_codes():
    projected = storage_pedidos._cliente_projection({"cliente": "123", "localidad": "1794"})

    assert projected["cliente_localidad"] is None
