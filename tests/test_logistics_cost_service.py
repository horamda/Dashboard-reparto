import json
import unittest
from unittest.mock import MagicMock, patch

from logistics_cost_service import LogisticsCostService, RoutingService


class FakeRoutingService:
    provider = "fake"

    def __init__(self, distances=None):
        self.distances = iter(distances or [])

    def segment_distance(self, origin, destination):
        return {
            "distance_km": next(self.distances),
            "duration_seconds": 600,
            "provider": self.provider,
            "fallback": False,
        }


class LogisticsAllocationTests(unittest.TestCase):
    def setUp(self):
        self.service = LogisticsCostService(FakeRoutingService())
        self.config = {
            "weights": {"distance": 0.4, "time": 0.3, "volume": 0.3},
            "volume_criterion": "bultos",
        }

    def test_customer_costs_sum_route_total(self):
        stops = [
            {"cliente": "1", "segment_km": 10, "tiempo_segundos": 600, "bultos": 5, "hl": 0, "pallets": 0},
            {"cliente": "2", "segment_km": 20, "tiempo_segundos": 1200, "bultos": 15, "hl": 0, "pallets": 0},
        ]
        rows, meta = self.service._allocate({"rid": "r1", "bultos": 20}, stops, 1000, self.config, return_km=15)
        self.assertAlmostEqual(sum(row["costo_entrega"] for row in rows), 1000)
        self.assertAlmostEqual(meta["assigned_total"], 1000)

    def test_return_distance_is_fully_distributed(self):
        stops = [
            {"cliente": "1", "segment_km": 10, "tiempo_segundos": 1, "bultos": 1},
            {"cliente": "2", "segment_km": 30, "tiempo_segundos": 1, "bultos": 1},
        ]
        rows, _ = self.service._allocate({"rid": "r1"}, stops, 100, self.config, return_km=20)
        self.assertAlmostEqual(sum(row["km_regreso_asignados"] for row in rows), 20)
        self.assertAlmostEqual(sum(row["km_atribuibles"] for row in rows), 60)

    def test_missing_time_redistributes_weights(self):
        stops = [
            {"cliente": "1", "segment_km": 10, "tiempo_segundos": 0, "bultos": 10},
            {"cliente": "2", "segment_km": 10, "tiempo_segundos": 0, "bultos": 10},
        ]
        _, meta = self.service._allocate({"rid": "r1"}, stops, 100, self.config)
        self.assertAlmostEqual(meta["effective_weights"]["distance"], 4 / 7)
        self.assertAlmostEqual(meta["effective_weights"]["volume"], 3 / 7)
        self.assertEqual(meta["effective_weights"]["time"], 0)

    def test_missing_all_dimensions_uses_equal_allocation(self):
        stops = [{"cliente": str(i), "segment_km": 0, "tiempo_segundos": 0, "bultos": 0} for i in range(3)]
        rows, meta = self.service._allocate({"rid": "r1"}, stops, 90, self.config)
        self.assertTrue(meta["equal_allocation_fallback"])
        self.assertEqual([row["costo_entrega"] for row in rows], [30, 30, 30])

    def test_one_customer_receives_full_cost_and_return(self):
        stops = [{"cliente": "1", "segment_km": 12, "tiempo_segundos": 300, "bultos": 4}]
        rows, _ = self.service._allocate({"rid": "r1"}, stops, 250, self.config, return_km=8)
        self.assertAlmostEqual(rows[0]["costo_entrega"], 250)
        self.assertAlmostEqual(rows[0]["km_atribuibles"], 20)

    def test_zero_km_route_cost_does_not_divide_by_zero(self):
        total, components = self.service._route_cost(
            {"horas": 2},
            {"fuel_price_per_liter": 100, "vehicle_liters_per_100km": 10, "vehicle_cost_per_km": 5,
             "driver_cost_per_hour": 20, "helper_cost_per_hour": 10, "helpers_count": 1, "other_route_cost": 0},
            0,
        )
        self.assertEqual(components["fuel"], 0)
        self.assertEqual(components["vehicle"], 0)
        self.assertEqual(total, 60)

    def test_negative_inputs_do_not_create_negative_cost(self):
        total, components = self.service._route_cost(
            {"horas": -4},
            {"fuel_price_per_liter": -100, "vehicle_liters_per_100km": -10,
             "vehicle_cost_per_km": -5, "driver_cost_per_hour": -20,
             "helper_cost_per_hour": -10, "helpers_count": -1, "other_route_cost": -50},
            -20,
        )
        self.assertEqual(total, 0)
        self.assertTrue(all(value == 0 for value in components.values()))

    def test_unit_metrics_use_actual_delivered_volume(self):
        stops = [{
            "cliente": "1", "segment_km": 10, "tiempo_segundos": 600,
            "bultos": 5, "hl": 2, "pallets": 1, "venta": 2000,
        }]

        rows, _ = self.service._allocate({"rid": "r1"}, stops, 100, self.config)

        self.assertEqual(rows[0]["cost_per_bulto"], 20)
        self.assertEqual(rows[0]["cost_per_hl"], 50)
        self.assertEqual(rows[0]["cost_per_pallet"], 100)
        self.assertEqual(rows[0]["cost_to_sales_pct"], 5)

    def test_total_rejection_does_not_fall_back_to_planned_volume(self):
        stops = [{
            "cliente": "1", "segment_km": 10, "tiempo_segundos": 600,
            "bultos": 0, "bultos_available": True, "estado_entrega": "Rechazado",
        }]

        rows, meta = self.service._allocate({"rid": "r1", "bultos": 100}, stops, 100, self.config)

        self.assertEqual(meta["effective_weights"]["volume"], 0)
        self.assertEqual(rows[0]["cost_per_bulto"], 0)
        self.assertEqual(rows[0]["costo_entrega"], 100)

    def test_partial_delivery_and_invalid_gps_are_preserved_safely(self):
        attempts = [{
            "Customer ID": "1", "Visit Start Timestamp": "2026-08-01 10:00:00",
            "bultos": 3, "hl": 1.5, "estado_entrega": "Parcial",
        }]
        clients = {"1": {"nombre": "Cliente", "latitud": 91, "longitud": -57}}

        stops, warnings = self.service._build_stops(attempts, clients)

        self.assertEqual(stops[0]["bultos"], 3)
        self.assertEqual(stops[0]["estado_entrega"], "Parcial")
        self.assertTrue(stops[0]["bultos_available"])
        self.assertIsNone(stops[0]["latitud"])
        self.assertTrue(any("coordenadas" in warning for warning in warnings))

    def test_route_without_client_gps_still_calculates(self):
        mocks = {
            "ensure_logistics_tables": MagicMock(),
            "get_route": MagicMock(return_value={
                "rid": "r1", "suc": "Dolores", "horas": 1,
                "disp_km_real": 10000, "bultos": 5,
            }),
            "load_logistics_config": MagicMock(return_value={"driver_cost_per_hour": 100}),
            "get_effective_logistics_cost_rate": MagicMock(return_value=None),
            "get_effective_logistics_vehicle_cost": MagicMock(return_value=None),
            "load_logistics_depots": MagicMock(return_value={
                "Dolores": {"latitud": -36.2, "longitud": -57.6},
            }),
            "load_clientes": MagicMock(return_value={"1": {"nombre": "Sin GPS"}}),
            "load_attempts_by_route": MagicMock(return_value={"a1": {"Customer ID": "1"}}),
            "load_route_sales": MagicMock(return_value={}),
            "load_route_cost": MagicMock(return_value={"route": None, "customers": []}),
            "save_route_cost": MagicMock(),
        }
        with patch.multiple("logistics_cost_service.storage", **mocks):
            result = LogisticsCostService(FakeRoutingService()).calculate_route("r1")

        self.assertEqual(result["route"]["km"], 10)
        self.assertEqual(result["route"]["routing_segments"], [])
        self.assertAlmostEqual(sum(row["costo_entrega"] for row in result["customers"]), result["route"]["total_cost"])
        self.assertTrue(any("GPS" in warning for warning in result["route"]["warnings"]))

    @patch("logistics_cost_service.storage.save_route_cost")
    @patch("logistics_cost_service.storage.load_route_cost", return_value={"route": None, "customers": []})
    @patch("logistics_cost_service.storage.load_route_sales", return_value={"1": {"venta": 1000, "pedidos_facturados": 1}})
    @patch("logistics_cost_service.storage.load_attempts_by_route")
    @patch("logistics_cost_service.storage.load_clientes")
    @patch("logistics_cost_service.storage.load_logistics_depots")
    @patch("logistics_cost_service.storage.get_effective_logistics_vehicle_cost", return_value=None)
    @patch("logistics_cost_service.storage.get_effective_logistics_cost_rate", return_value=None)
    @patch("logistics_cost_service.storage.load_logistics_config")
    @patch("logistics_cost_service.storage.get_route")
    @patch("logistics_cost_service.storage.ensure_logistics_tables")
    def test_calculation_saves_auditable_metadata(
        self, _ensure, get_route, load_config, _effective_rate, _vehicle_rate, load_depots, load_clientes,
        load_attempts, _load_sales, _load_current, save_cost,
    ):
        get_route.return_value = {"rid": "r1", "suc": "Dolores", "horas": 1, "disp_km_real": 0}
        load_config.return_value = {"driver_cost_per_hour": 100}
        load_depots.return_value = {"Dolores": {"latitud": -36.2, "longitud": -57.6}}
        load_clientes.return_value = {"1": {"nombre": "Cliente", "latitud": -36.3, "longitud": -57.7}}
        load_attempts.return_value = {"a1": {"Customer ID": "1", "Visit Duration Seconds": 60}}
        service = LogisticsCostService(FakeRoutingService([10, 10]))

        result = service.calculate_route("r1", actor="admin", reason="carga inicial")

        route = result["route"]
        self.assertEqual(route["calculation_type"], "original")
        self.assertEqual(route["calculation_version"], "v1")
        self.assertEqual(route["calculated_by"], "admin")
        self.assertEqual(route["recalculation_reason"], "carga inicial")
        self.assertEqual(route["venta"], 1000)
        self.assertEqual(route["cost_to_sales_pct"], 10)
        self.assertIsNotNone(route["calculation_id"])
        save_cost.assert_called_once()

    @patch("logistics_cost_service.storage.load_logistics_depots", return_value={})
    @patch("logistics_cost_service.storage.get_effective_logistics_vehicle_cost", return_value=None)
    @patch("logistics_cost_service.storage.get_effective_logistics_cost_rate")
    @patch("logistics_cost_service.storage.load_logistics_config", return_value={})
    def test_effective_rate_depends_on_route_date(self, _base, effective_rate, _vehicle, _depots):
        effective_rate.side_effect = lambda route_date: (
            {"valid_from": "2026-01-01", "fuel_price_per_liter": 100}
            if route_date < "2026-07-01"
            else {"valid_from": "2026-07-01", "fuel_price_per_liter": 140}
        )
        old = self.service._config("2026-06-30")
        new = self.service._config("2026-07-01")
        self.assertEqual(old["fuel_price_per_liter"], 100)
        self.assertEqual(old["rate_valid_from"], "2026-01-01")
        self.assertEqual(new["fuel_price_per_liter"], 140)

    @patch("logistics_cost_service.storage.load_logistics_depots", return_value={})
    @patch("logistics_cost_service.storage.get_effective_logistics_vehicle_cost")
    @patch("logistics_cost_service.storage.get_effective_logistics_cost_rate")
    @patch("logistics_cost_service.storage.load_logistics_config", return_value={})
    def test_vehicle_rate_overrides_general_consumption(self, _base, general, vehicle, _depots):
        general.return_value = {"valid_from": "2026-01-01", "vehicle_liters_per_100km": 20}
        vehicle.return_value = {"valid_from": "2026-06-01", "vehicle": "AA123BB", "vehicle_liters_per_100km": 14}
        config = self.service._config("2026-08-01", "AA123BB")
        self.assertEqual(config["vehicle_liters_per_100km"], 14)
        self.assertEqual(config["vehicle_rate_vehicle"], "AA123BB")


class RoutingFallbackTests(unittest.TestCase):
    @patch.dict("os.environ", {"OSRM_BASE_URL": "https://routing.example.test/"})
    def test_provider_base_url_can_be_configured(self):
        self.assertEqual(RoutingService().base_url, "https://routing.example.test")

    @patch("logistics_cost_service.urlopen")
    def test_osrm_geometry_is_preserved(self, urlopen_mock):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "routes": [{
                "distance": 1250,
                "duration": 180,
                "geometry": {"coordinates": [[-57.6, -36.2], [-57.7, -36.3]]},
            }]
        }).encode("utf-8")
        urlopen_mock.return_value.__enter__.return_value = response

        result = RoutingService()._osrm_segment((-36.2, -57.6), (-36.3, -57.7))

        self.assertEqual(result["geometry"], [[-57.6, -36.2], [-57.7, -36.3]])
        self.assertEqual(result["distance_km"], 1.25)
        self.assertFalse(result["fallback"])

    @patch("logistics_cost_service.storage.routing_cache_set")
    @patch("logistics_cost_service.storage.routing_cache_get", return_value=None)
    @patch("logistics_cost_service.urlopen", side_effect=OSError("offline"))
    def test_provider_failure_returns_fallback(self, _urlopen, _cache_get, cache_set):
        result = RoutingService().segment_distance((-36.7, -56.6), (-36.8, -56.7))
        self.assertTrue(result["fallback"])
        self.assertGreater(result["distance_km"], 0)
        cache_set.assert_called_once()

    @patch("logistics_cost_service.storage.routing_cache_get")
    def test_cache_avoids_provider_call(self, cache_get):
        cached = {"distance_km": 5, "duration_seconds": 300, "provider": "osrm", "fallback": False}
        cache_get.return_value = cached
        service = RoutingService()
        with patch.object(service, "_osrm_segment") as provider:
            self.assertEqual(service.segment_distance((1, 1), (2, 2)), cached)
            provider.assert_not_called()

    @patch("logistics_cost_service.storage.routing_cache_set_many")
    @patch("logistics_cost_service.storage.routing_cache_get_many", return_value={})
    @patch("logistics_cost_service.urlopen")
    def test_multi_point_route_uses_one_provider_call(self, urlopen_mock, _cache_get, cache_set):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "routes": [{
                "legs": [
                    {
                        "distance": 1000,
                        "duration": 120,
                        "steps": [{"geometry": {"coordinates": [[-57.6, -36.2], [-57.7, -36.3]]}}],
                    },
                    {
                        "distance": 2000,
                        "duration": 240,
                        "steps": [{"geometry": {"coordinates": [[-57.7, -36.3], [-57.8, -36.4]]}}],
                    },
                ]
            }]
        }).encode("utf-8")
        urlopen_mock.return_value.__enter__.return_value = response

        result = RoutingService().calculate_segments([
            (-36.2, -57.6), (-36.3, -57.7), (-36.4, -57.8),
        ])

        self.assertEqual(urlopen_mock.call_count, 1)
        self.assertEqual([segment["distance_km"] for segment in result], [1, 2])
        self.assertEqual(len(cache_set.call_args.args[0]), 2)
        self.assertTrue(all(segment["geometry"] for segment in result))

    @patch.dict("os.environ", {"ROUTING_MAX_COORDINATES": "3"})
    @patch("logistics_cost_service.storage.routing_cache_set_many")
    @patch("logistics_cost_service.storage.routing_cache_get_many", return_value={})
    @patch("logistics_cost_service.urlopen")
    def test_large_route_is_split_into_coordinate_batches(self, urlopen_mock, _cache_get, cache_set):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "routes": [{
                "legs": [
                    {"distance": 1000, "duration": 120, "steps": []},
                    {"distance": 1000, "duration": 120, "steps": []},
                ]
            }]
        }).encode("utf-8")
        urlopen_mock.return_value.__enter__.return_value = response

        result = RoutingService().calculate_segments([
            (-36.1, -57.1), (-36.2, -57.2), (-36.3, -57.3),
            (-36.4, -57.4), (-36.5, -57.5),
        ])

        self.assertEqual(urlopen_mock.call_count, 2)
        self.assertEqual(len(result), 4)
        self.assertEqual(len(cache_set.call_args.args[0]), 4)

    @patch("logistics_cost_service.storage.routing_cache_set_many")
    @patch("logistics_cost_service.storage.routing_cache_get_many", return_value={})
    @patch("logistics_cost_service.urlopen", side_effect=OSError("offline"))
    def test_multi_point_provider_failure_does_not_create_n_plus_one_calls(self, urlopen_mock, _cache_get, cache_set):
        result = RoutingService().calculate_segments([
            (-36.2, -57.6), (-36.3, -57.7), (-36.4, -57.8),
        ])

        self.assertEqual(urlopen_mock.call_count, 1)
        self.assertEqual(len(cache_set.call_args.args[0]), 2)
        self.assertTrue(all(segment["fallback"] for segment in result))


if __name__ == "__main__":
    unittest.main()
