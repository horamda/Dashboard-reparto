import os
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pipeline


ROOT = Path(__file__).resolve().parents[1]


class FichayaCredentialTests(unittest.TestCase):
    def test_common_credentials_configure_web_and_api(self):
        with patch.dict(
            os.environ,
            {
                "FICHAYA_USERNAME": "operador",
                "FICHAYA_PASSWORD": "secreto",
                "FICHAYA_API_BASE_URL": "https://fichaya.example/",
            },
            clear=True,
        ), patch.multiple(
            pipeline,
            FICHAYA_API_USERNAME=None,
            FICHAYA_API_PASSWORD=None,
            FICHAYA_WEB_USERNAME=None,
            FICHAYA_WEB_PASSWORD=None,
        ):
            credentials = pipeline._fichaya_credentials()
            status = pipeline.fichaya_credentials_status()

        self.assertEqual(credentials["api_username"], "operador")
        self.assertEqual(credentials["web_username"], "operador")
        self.assertEqual(credentials["base_url"], "https://fichaya.example")
        self.assertEqual(status["mode"], "web")
        self.assertTrue(status["api_configured"])
        self.assertTrue(status["web_configured"])
        self.assertNotIn("api_password", status)
        self.assertNotIn("web_password", status)

    def test_web_credentials_are_reused_by_external_api(self):
        with patch.dict(
            os.environ,
            {
                "FICHAYA_WEB_USER": "usuario-web",
                "FICHAYA_WEB_PASSWORD": "clave-web",
            },
            clear=True,
        ), patch.multiple(
            pipeline,
            FICHAYA_API_USERNAME=None,
            FICHAYA_API_PASSWORD=None,
            FICHAYA_WEB_USERNAME=None,
            FICHAYA_WEB_PASSWORD=None,
        ):
            credentials = pipeline._fichaya_credentials()

        self.assertEqual(credentials["api_username"], "usuario-web")
        self.assertEqual(credentials["api_password"], "clave-web")

    def test_missing_credentials_return_actionable_error_without_live_calls(self):
        status = {"mode": "web", "web_configured": False, "api_configured": False}
        with patch.object(
            pipeline, "cargar_fichadas_cache", return_value={}
        ), patch.object(
            pipeline, "fichaya_credentials_status", return_value=status
        ), patch.object(
            pipeline, "_fichaya_web_csv"
        ) as web, patch.object(
            pipeline, "_fichaya_external_csv"
        ) as api:
            with self.assertRaisesRegex(RuntimeError, "variables del servicio"):
                pipeline.cargar_fichadas("2026-08-01", "2026-08-24")

        web.assert_not_called()
        api.assert_not_called()

    def test_default_web_mode_does_not_fallback_to_external_api(self):
        status = {"mode": "web", "web_configured": True, "api_configured": True}
        with patch.object(
            pipeline, "cargar_fichadas_cache", return_value={}
        ), patch.object(
            pipeline, "fichaya_credentials_status", return_value=status
        ), patch.object(
            pipeline, "_fichaya_web_csv", side_effect=RuntimeError("sin respuesta")
        ) as web, patch.object(
            pipeline, "_fichaya_external_csv"
        ) as api:
            with self.assertRaisesRegex(RuntimeError, "acceso web: sin respuesta"):
                pipeline.cargar_fichadas("2026-08-01", "2026-08-24", force_live=True)

        web.assert_called_once()
        api.assert_not_called()


class TabletLayoutTests(unittest.TestCase):
    def test_operational_dashboard_has_tablet_breakpoint_and_idle_charts(self):
        html = (ROOT / "plantilla_dashboard.html").read_text(encoding="utf-8")

        self.assertIn("@media(min-width:721px) and (max-width:1024px)", html)
        self.assertIn("requestIdleCallback(loadDashboardCharts", html)
        self.assertIn("min-height:44px", html)

    def test_orders_loads_chartjs_dynamically_and_supports_tablets(self):
        html = (ROOT / "templates" / "plantilla_pedidos.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("@media (min-width: 769px) and (max-width: 1024px)", html)
        self.assertIn("requestIdleCallback(loadChartLibrary", html)
        self.assertNotIn(
            '<script src="https://cdn.jsdelivr.net/npm/chart.js', html
        )


class DqiSheetTests(unittest.TestCase):
    def tearDown(self):
        pipeline.cargar_dqi.cache_clear()

    def test_current_sheet_columns_load_real_breakage_volume(self):
        csv_data = (
            "Depósito,Fecha Mvto,Transporte,ALMACENAMIENTO,Artículo,"
            "Descripción Artículo,Bultos,Unids,BULTOS_REAL,ROTURA_HL_REAL,SECTOR\n"
            '7,13/8/2026,1403,(08) IVECO TECTOR (AF071AX),20433,CORONA,0,2,"0,25","0,05",REPARTO\n'
            '7,13/8/2026,16,ALMACENAMIENTO,20433,CORONA,0,8,"1,00","0,20",ALMACEN\n'
            '2,13/8/2026,1100,(19) IVECO TECTOR,20433,CORONA,0,4,"0,50","0,10",REPARTO\n'
        )
        response = MagicMock()
        response.__enter__.return_value.read.return_value = csv_data.encode("utf-8")
        pipeline.cargar_dqi.cache_clear()
        with patch.object(pipeline, "urlopen", return_value=response), patch.object(
            pipeline.storage, "load_articulos", return_value={}
        ):
            result = pipeline.cargar_dqi()

        self.assertEqual(result["error"], "")
        self.assertEqual(result["rows"], [{"fecha": "2026-08-13", "mes": "2026-08", "dqi": 0.2}])
        self.assertEqual(len(result["detalles"]), 1)
        self.assertIn("1403", result["detalles"][0]["camion"])
        self.assertIn("IVECO TECTOR", result["detalles"][0]["camion"])
        self.assertEqual(result["detalles"][0]["hl"], 0.05)


class LogisticsIntegrationTests(unittest.TestCase):
    def test_api_client_uses_v1_contract_and_api_key(self):
        payload = {
            "api_version": "v1",
            "paginacion": {"total": 1, "hay_mas": False},
            "datos": [{"fecha": "2026-08-13", "camion_codigo": "1100"}],
        }
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with patch.dict(
            os.environ,
            {
                "LOGISTICS_INTEGRATION_API_BASE_URL": "https://api.example",
                "LOGISTICS_INTEGRATION_API_KEY": "secreto",
                "LOGISTICS_INTEGRATION_EMPRESA_ID": "1",
            },
            clear=False,
        ), patch.object(pipeline, "urlopen", return_value=response) as opener:
            result = pipeline.consultar_logistica_api("2026-08-13", "2026-08-13")

        request = opener.call_args.args[0]
        self.assertIn("/api/v1/integracion/logistica/diaria?", request.full_url)
        self.assertIn("fecha", result["datos"][0])
        self.assertEqual(request.get_header("X-api-key"), "secreto")
        self.assertEqual(result["paginas"], 1)

    def test_api_client_follows_contract_pagination(self):
        payloads = [
            {
                "api_version": "v1",
                "paginacion": {"total": 2, "hay_mas": True},
                "datos": [{"fecha": "2026-08-13", "camion_codigo": "1100"}],
            },
            {
                "api_version": "v1",
                "paginacion": {"total": 2, "hay_mas": False},
                "datos": [{"fecha": "2026-08-13", "camion_codigo": "1200"}],
            },
        ]
        responses = []
        for payload in payloads:
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            responses.append(response)

        with patch.dict(
            os.environ,
            {
                "LOGISTICS_INTEGRATION_API_BASE_URL": "https://api.example",
                "LOGISTICS_INTEGRATION_API_KEY": "secreto",
            },
            clear=False,
        ), patch.object(pipeline, "urlopen", side_effect=responses) as opener:
            result = pipeline.consultar_logistica_api("2026-08-13", "2026-08-13")

        urls = [call.args[0].full_url for call in opener.call_args_list]
        self.assertEqual([row["camion_codigo"] for row in result["datos"]], ["1100", "1200"])
        self.assertIn("offset=0", urls[0])
        self.assertIn("offset=1", urls[1])
        self.assertEqual(result["paginas"], 2)

    def test_sync_fills_only_missing_route_fields(self):
        api_row = {
            "fecha": "2026-08-13",
            "sucursal": "Dolores",
            "sucursal_id": "2",
            "chofer": "Pérez Juan",
            "chofer_codigo": "15",
            "camion_codigo": "1100",
            "patente": "KTO613",
            "bultos": 520.5,
            "hl": 48.7,
            "pallets_estimados": 6.3,
            "up": 680,
            "calidad": {"camion_identificado": True},
        }
        route = {
            "rid": "route-1",
            "fecha": "2026-08-13",
            "suc": "Sucursal Dolores",
            "chofer": "Juan Perez",
            "chofer_codigo": "15",
            "camion": "Sin camion",
            "bultos": 0,
            "hl": None,
            "pallets": 0,
            "unidades": 0,
        }
        with patch.object(
            pipeline,
            "consultar_logistica_api",
            return_value={"datos": [api_row], "paginas": 1, "rangos": 1},
        ), patch.object(
            pipeline.storage, "load_routes_for_logistics_sync", return_value=[route]
        ), patch.object(
            pipeline.storage,
            "update_routes_from_logistics_api",
            side_effect=lambda records: len(records),
        ) as update:
            stats = pipeline.completar_rutas_desde_api_logistica(
                "2026-08-13", "2026-08-13"
            )

        record = update.call_args.args[0][0]
        self.assertEqual(record["values"]["camion"], "KTO613")
        self.assertEqual(record["values"]["bultos"], 520.5)
        self.assertEqual(record["values"]["unidades"], 680.0)
        self.assertEqual(record["payload"]["logistics_api"]["api_version"], "v1")
        self.assertEqual(stats["routes_updated"], 1)

    def test_sync_preserves_existing_values(self):
        api_row = {
            "fecha": "2026-08-13",
            "sucursal": "Casa Central",
            "chofer": "Ana Lopez",
            "camion_codigo": "2000",
            "patente": "NUEVA",
            "bultos": 999,
            "hl": 99,
            "pallets_estimados": 9,
            "up": 300,
        }
        route = {
            "rid": "route-2", "fecha": "2026-08-13", "suc": "Casa Central",
            "chofer": "Ana Lopez", "camion": "ACTUAL", "bultos": 100,
            "hl": 10, "pallets": 2, "unidades": 0,
        }
        with patch.object(
            pipeline,
            "consultar_logistica_api",
            return_value={"datos": [api_row], "paginas": 1, "rangos": 1},
        ), patch.object(
            pipeline.storage, "load_routes_for_logistics_sync", return_value=[route]
        ), patch.object(
            pipeline.storage,
            "update_routes_from_logistics_api",
            side_effect=lambda records: len(records),
        ) as update:
            pipeline.completar_rutas_desde_api_logistica("2026-08-13", "2026-08-13")

        values = update.call_args.args[0][0]["values"]
        self.assertEqual(values, {"unidades": 300.0})

    def test_sync_rejects_api_row_shared_by_multiple_routes(self):
        api_row = {
            "fecha": "2026-08-13", "sucursal": "Dolores", "chofer": "Juan Perez",
            "camion_codigo": "1100", "bultos": 100,
        }
        routes = [
            {"rid": rid, "fecha": "2026-08-13", "suc": "Dolores", "chofer": "Juan Perez", "camion": "", "bultos": 0}
            for rid in ("route-a", "route-b")
        ]
        with patch.object(
            pipeline,
            "consultar_logistica_api",
            return_value={"datos": [api_row], "paginas": 1, "rangos": 1},
        ), patch.object(
            pipeline.storage, "load_routes_for_logistics_sync", return_value=routes
        ), patch.object(
            pipeline.storage, "update_routes_from_logistics_api", return_value=0
        ) as update:
            stats = pipeline.completar_rutas_desde_api_logistica(
                "2026-08-13", "2026-08-13"
            )

        update.assert_called_once_with([])
        self.assertEqual(stats["routes_ambiguous"], 2)
        self.assertEqual(stats["routes_updated"], 0)


if __name__ == "__main__":
    unittest.main()
