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

    def test_forced_refresh_does_not_hide_failure_behind_stale_cache(self):
        stale = {
            ("2026-08-24", "OPERADOR"): {
                "ingreso": pipeline._parse_hora_fichaya("07:30"),
                "egreso": pipeline._parse_hora_fichaya("14:30"),
            }
        }
        status = {"mode": "web", "web_configured": True, "api_configured": False}
        with patch.object(
            pipeline, "cargar_fichadas_cache", return_value=stale
        ), patch.object(
            pipeline, "fichaya_credentials_status", return_value=status
        ), patch.object(
            pipeline, "_fichaya_web_csv", side_effect=RuntimeError("servicio no disponible")
        ):
            with self.assertRaisesRegex(RuntimeError, "servicio no disponible"):
                pipeline.cargar_fichadas(
                    "2026-08-01", "2026-08-25", force_live=True
                )


class TabletLayoutTests(unittest.TestCase):
    def test_operational_dashboard_has_tablet_breakpoint_and_idle_charts(self):
        html = (ROOT / "plantilla_dashboard.html").read_text(encoding="utf-8")

        self.assertIn("@media(min-width:721px) and (max-width:1024px)", html)
        self.assertIn("requestIdleCallback(loadDashboardCharts", html)
        self.assertIn("min-height:44px", html)

    def test_operational_dashboard_cross_filters_chart_dimensions(self):
        html = (ROOT / "plantilla_dashboard.html").read_text(encoding="utf-8")

        self.assertIn('id="fFecha" type="date"', html)
        self.assertIn('id="filterContext"', html)
        self.assertIn("function applyChartFilter(dimension,value)", html)
        self.assertIn("function chartInteraction(selection)", html)
        self.assertIn("chartFilter('mes',ser.map(s=>s.key))", html)
        self.assertIn("chartFilter('fecha',dd.map(s=>s.key))", html)
        self.assertIn("chartFilter('cho',rc.map(r=>r.key)", html)
        self.assertIn("otChart('chOTZona',byZona,'suc')", html)
        self.assertIn("const ser=aggOperativo('mes',usM)", html)
        self.assertIn("const full=r=>base(r)&&periodOk(r)", html)

    def test_operational_compliance_shows_route_numerator_and_denominator(self):
        html = (ROOT / "plantilla_dashboard.html").read_text(encoding="utf-8")

        self.assertIn('id="oTIcmp"', html)
        self.assertIn('id="oTMLcmp"', html)
        self.assertIn('result.ok+" de "+result.total+" rutas', html)

    def test_orders_loads_chartjs_dynamically_and_supports_tablets(self):
        html = (ROOT / "templates" / "plantilla_pedidos.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("@media (min-width: 769px) and (max-width: 1024px)", html)
        self.assertIn("requestIdleCallback(loadChartLibrary", html)
        self.assertNotIn(
            '<script src="https://cdn.jsdelivr.net/npm/chart.js', html
        )

    def test_dqi_view_separates_physical_breakage_from_quality_indicators(self):
        html = (ROOT / "plantilla_dashboard.html").read_text(encoding="utf-8")

        self.assertIn("dqi-real-section", html)
        self.assertIn("dqi-quality-section", html)
        self.assertIn("Roturas reales", html)
        self.assertIn("Indicadores de calidad", html)
        self.assertIn("BULTOS_REAL", html)
        self.assertIn("ROTURA_HL_REAL", html)
        self.assertIn("DQI_WQI_BULTOS", html)
        self.assertIn("DQI_WQI_HL", html)
        self.assertIn('id="chDqiRealBultos"', html)
        self.assertIn('id="chDqiRealHl"', html)
        self.assertIn('id="chDqiDia"', html)
        self.assertIn('id="chDqiHlDia"', html)
        self.assertIn("rankDqi(realDet,'camion','bultos_real')", html)
        self.assertIn("rankDqi(det,'camion','bultos')", html)

    def test_dqi_view_compares_monthly_and_cumulative_history(self):
        html = (ROOT / "plantilla_dashboard.html").read_text(encoding="utf-8")

        self.assertIn('id="chDqiHistoricoMes"', html)
        self.assertIn('id="chDqiHistoricoAcum"', html)
        self.assertIn('id="tbodyDqiHistorico"', html)
        self.assertIn('data-dqi-history-metric="dqi"', html)
        self.assertIn('data-dqi-history-metric="dqi_hl"', html)
        self.assertIn("function dqiHistoricalSeries(field)", html)
        self.assertIn("function dqiHistoryChart(id,series,cumulative)", html)
        self.assertIn("return String(r.fecha||'').startsWith(DQI_FOCUS_YEAR+'-')", html)
        self.assertIn("...DQI.map(r=>r.mes).filter(Boolean)", html)

    def test_dpo_gkpis_shows_monthly_dqi_plus_wqi_in_hl(self):
        html = (ROOT / "plantilla_dashboard.html").read_text(encoding="utf-8")

        self.assertIn('id="chDpoDqiWqiMes"', html)
        self.assertIn('id="chDpoDqiWqiAcum"', html)
        self.assertIn('id="tbodyDpoDqiWqi"', html)
        self.assertIn("DQIWQI=(DATA.dqi&&DATA.dqi.dqi_wqi_rows)||[]", html)
        self.assertIn("function dpoQualityMonthly()", html)
        self.assertIn("function dpoQualityStackedChart(id,items)", html)
        self.assertIn("function dpoQualityCumulativeChart(id,items)", html)
        self.assertIn("renderDpoQuality();", html)
        self.assertIn("solo TIPOMERC MERCADERIA", html)
        self.assertIn("function sucAliases(raw)", html)
        self.assertIn("DQIWQI.filter(genericDataBase)", html)

    def test_team_room_sums_and_separates_dqi_metrics(self):
        html = (ROOT / "plantilla_dashboard.html").read_text(encoding="utf-8")

        self.assertIn('class="tab active" id="tabTeamRoom"', html)
        self.assertIn("tab:'teamroom'", html)
        self.assertIn("function dqiSum(rows,field)", html)
        self.assertNotIn("function dqiAvg(rows)", html)
        self.assertIn("'real_bultos','Bultos reales'", html)
        self.assertIn("'real_hl','HL reales'", html)
        self.assertIn("'dqi_bultos','DQI en bultos'", html)
        self.assertIn("'dqi_hl','DQI en HL'", html)
        self.assertIn("DQI y roturas: solo MERCADERIA", html)
        self.assertIn("key==='dqi_bultos'&&period==='day'", html)
        self.assertIn("key==='dqi_bultos'&&period==='month'", html)
        self.assertIn("function teamDqiRows()", html)
        self.assertIn("teamDqiRows().filter(genericDataBase)", html)
        self.assertIn("x._realData=true", html)
        self.assertIn("x._dqiData=true", html)
        self.assertIn("tblscroll team-scroll", html)


class DqiSheetTests(unittest.TestCase):
    def tearDown(self):
        pipeline.cargar_dqi.cache_clear()

    def test_current_sheet_uses_weighted_dqi_and_keeps_physical_volume(self):
        csv_data = (
            "Depósito,Tipo,Fecha Mvto,Transporte,ALMACENAMIENTO,Artículo,"
            "Descripción Artículo,Bultos,Unids,UXB,DQI_WQI_BULTOS,BULTOS_REAL,"
            "DQI_WQI_HL,ROTURA_HL_REAL,TIPOMERC,TIPO\n"
            '7,RCS,13/8/2026,1403,(08) IVECO TECTOR (AF071AX),20433,CORONA,0,2,8,"0,25","0,08","0,03","0,01",MERCADERIA,DQI\n'
            '7,RCS,13/8/2026,1403,(08) IVECO TECTOR (AF071AX),20433,CORONA,0,2,8,"0,25","0,08","0,03","0,01",MERCADERIA,DQI\n'
            '7,RCS,13/8/2026,1301,(01) MERCEDES ATEGO (DSB034),20434,ANDES,0,4,24,"1,00","0,17","0,04","0,01",MERCADERIA,DQI\n'
            '7,RCS,13/8/2026,1306,(06) IVECO TECTOR (HPU756),20435,QUILMES,0,8,8,"9,00","1,00","0,90","0,10",MERCADERIA,WQI\n'
            '7,RCS,13/8/2026,1306,(06) IVECO TECTOR (HPU756),20435,ENVASE,0,8,8,"7,00","1,00","0,70","0,10",ENVASE,DQI\n'
            '7,RCS,13/8/2026,1306,(06) IVECO TECTOR (HPU756),20435,ESQUELETO,0,8,8,"6,00","1,00","0,60","0,10",ESQUELETO,DQI\n'
            '2,RCS,13/8/2026,1100,(19) IVECO TECTOR,20436,PATAGONIA,0,4,8,"5,00","0,50","0,50","0,10",MERCADERIA,DQI\n'
        )
        response = MagicMock()
        response.__enter__.return_value.read.return_value = csv_data.encode("utf-8")
        pipeline.cargar_dqi.cache_clear()
        with patch.object(pipeline, "urlopen", return_value=response), patch.object(
            pipeline.storage, "load_articulos", return_value={}
        ):
            result = pipeline.cargar_dqi()

        self.assertEqual(result["error"], "")
        self.assertEqual(result["rows"], [{
            "fecha": "2026-08-13",
            "mes": "2026-08",
            "dqi": 1.25,
            "bultos_real": 0.25,
            "dqi_hl": 0.07,
            "hl_real": 0.02,
        }])
        self.assertEqual(result["dqi_wqi_rows"], [{
            "fecha": "2026-08-13",
            "mes": "2026-08",
            "dqi_bultos": 1.25,
            "wqi_bultos": 9.0,
            "total_bultos": 10.25,
            "dqi_hl": 0.07,
            "wqi_hl": 0.9,
            "total_hl": 0.97,
        }])
        self.assertEqual(len(result["detalles"]), 2)
        self.assertIn("1403", result["detalles"][0]["camion"])
        self.assertIn("IVECO TECTOR", result["detalles"][0]["camion"])
        self.assertTrue(any("MERCEDES ATEGO" in row["camion"] for row in result["detalles"]))
        self.assertEqual(result["detalles"][0]["bultos"], 0.25)
        self.assertEqual(result["detalles"][0]["bultos_real"], 0.08)
        self.assertEqual(result["detalles"][0]["hl"], 0.03)
        self.assertEqual(result["detalles"][0]["hl_real"], 0.01)
        self.assertTrue(all(row["tipo_mercaderia"] == "MERCADERIA" for row in result["detalles"]))
        self.assertEqual(result["quality"]["source_rows"], 7)
        self.assertEqual(result["quality"]["duplicates_removed"], 1)
        self.assertEqual(result["quality"]["included_rows"], 2)
        self.assertEqual(result["quality"]["included_wqi_rows"], 1)
        self.assertEqual(result["quality"]["latest_quality_date"], "2026-08-13")
        self.assertEqual(result["quality"]["merchandise_filter"], "MERCADERIA")

    def test_dqi_keeps_branch_dimension_when_sheet_has_sucursal(self):
        csv_data = (
            "DepÃ³sito,Sucursal,Fecha Mvto,Transporte,ArtÃ­culo,Bultos,Unids,UXB,"
            "DQI_WQI_BULTOS,BULTOS_REAL,DQI_WQI_HL,ROTURA_HL_REAL,TIPOMERC,TIPO\n"
            '7,2,13/8/2026,1100,20433,0,2,8,"1,00","0,10","0,20","0,02",MERCADERIA,DQI\n'
            '2,1,13/8/2026,1200,20434,0,2,8,"2,00","0,20","0,30","0,03",MERCADERIA,WQI\n'
        )
        response = MagicMock()
        response.__enter__.return_value.read.return_value = csv_data.encode("utf-8")
        pipeline.cargar_dqi.cache_clear()
        with patch.object(pipeline, "urlopen", return_value=response), patch.object(
            pipeline.storage, "load_articulos", return_value={}
        ):
            result = pipeline.cargar_dqi()

        self.assertEqual(
            [(row["sucursal"], row["sucursal_id"], row["dqi"], row["dqi_hl"]) for row in result["rows"]],
            [("Dolores", "2", 1.0, 0.2)],
        )
        self.assertEqual(
            [(row["sucursal"], row["sucursal_id"], row["dqi_hl"], row["wqi_hl"], row["total_hl"]) for row in result["dqi_wqi_rows"]],
            [("Dolores", "2", 0.2, 0.0, 0.2), ("Mar de Ajo", "1", 0.0, 0.3, 0.3)],
        )
        self.assertEqual(result["quality"]["metric"], "DQI_WQI_BULTOS")
        self.assertEqual(result["quality"]["quality_hl_metric"], "DQI_WQI_HL")

    def test_dqi_loads_2024_2025_and_2026_for_historical_comparison(self):
        csv_data = (
            "Deposito,Fecha Mvto,DQI_WQI_BULTOS,BULTOS_REAL,"
            "DQI_WQI_HL,ROTURA_HL_REAL,TIPOMERC,TIPO\n"
            '7,15/1/2024,"2,00","1,00","0,20","0,10",MERCADERIA,DQI\n'
            '7,15/1/2025,"3,00","2,00","0,30","0,20",MERCADERIA,DQI\n'
            '7,15/1/2026,"4,00","3,00","0,40","0,30",MERCADERIA,DQI\n'
            '7,15/1/2023,"9,00","9,00","0,90","0,90",MERCADERIA,DQI\n'
        )
        response = MagicMock()
        response.__enter__.return_value.read.return_value = csv_data.encode("utf-8")
        pipeline.cargar_dqi.cache_clear()
        with patch.object(pipeline, "urlopen", return_value=response), patch.object(
            pipeline.storage, "load_articulos", return_value={}
        ):
            result = pipeline.cargar_dqi()

        self.assertEqual(
            [row["fecha"] for row in result["rows"]],
            ["2024-01-15", "2025-01-15", "2026-01-15"],
        )
        self.assertEqual(result["quality"]["comparison_years"], ["2024", "2025", "2026"])
        self.assertEqual(result["quality"]["source_date_from"], "2024-01-15")
        self.assertEqual(result["quality"]["source_date_to"], "2026-01-15")
        self.assertEqual(result["quality"]["latest_dqi_date"], "2026-01-15")

    def test_dqi_rejects_sources_without_merchandise_classification(self):
        csv_data = (
            "Depósito,Fecha Mvto,BULTOS_REAL,TIPO\n"
            '7,13/8/2026,"1,00",DQI\n'
        )
        response = MagicMock()
        response.__enter__.return_value.read.return_value = csv_data.encode("utf-8")
        pipeline.cargar_dqi.cache_clear()

        with patch.object(pipeline, "urlopen", return_value=response):
            result = pipeline.cargar_dqi()

        self.assertEqual(result["rows"], [])
        self.assertIn("TIPOMERC", result["error"])


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
