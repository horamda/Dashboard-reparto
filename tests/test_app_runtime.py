import gzip
import os
import threading
import time
import unittest
from unittest.mock import call, patch

os.environ["DISABLE_CACHE_PREWARM"] = "1"

import app as app_module
import pipeline


class AppRuntimeTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = app_module.app.test_client()

    def test_html_response_supports_gzip_and_conditional_etag(self):
        response = self.client.get("/login", headers={"Accept-Encoding": "gzip"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Encoding"), "gzip")
        self.assertIn("Accept-Encoding", response.headers.get("Vary", ""))
        self.assertTrue(response.headers["ETag"].startswith('W/"'))
        self.assertIn(b"Ingresar", gzip.decompress(response.data))

        cached = self.client.get(
            "/login",
            headers={"Accept-Encoding": "gzip", "If-None-Match": response.headers["ETag"]},
        )
        self.assertEqual(cached.status_code, 304)

    def test_dashboard_requires_login(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login?next=/dashboard", response.headers["Location"])

    def test_login_rejects_external_next_url(self):
        with patch.object(app_module, "ADMIN_USER", "admin"), patch.object(app_module, "ADMIN_PASSWORD", "secret"):
            response = self.client.post(
                "/login",
                data={"user": "admin", "password": "secret", "next": "https://example.com/phishing"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/inicio"))

    def test_dashboard_projection_excludes_raw_export(self):
        projected = pipeline._dashboard_route({
            "rid": "route-1",
            "fecha": "2026-08-01",
            "raw_foxtrot": {"large": "payload"},
            "unused": "value",
        })

        self.assertEqual(projected["rid"], "route-1")
        self.assertNotIn("raw_foxtrot", projected)
        self.assertNotIn("unused", projected)

    def test_casa_central_historical_times_match_monthly_references(self):
        routes = []
        expected_tml = {
            "2026-01": 32,
            "2026-02": 28,
            "2026-03": 21,
            "2026-04": 28,
            "2026-05": 27,
            "2026-06": 27,
            "2026-07": 28,
        }
        expected_ti = {
            "2026-01": 42,
            "2026-02": 30,
            "2026-03": 30,
            "2026-04": 30,
            "2026-05": 33,
            "2026-06": 34,
            "2026-07": 30,
        }
        for month in expected_tml:
            routes.extend([
                {"mes": month, "suc": "Mar de Ajo", "usable": True, "tml": 20, "ti": 25},
                {"mes": month, "suc": "Casa Central", "usable": True, "tml": 35, "ti": 45},
            ])
        untouched = [
            {"mes": "2026-01", "suc": "Dolores", "usable": True, "tml": 24, "ti": 31},
            {"mes": "2026-01", "suc": "Mar de Ajo", "usable": False, "tml": 26, "ti": 32},
            {"mes": "2026-08", "suc": "Mar de Ajo", "usable": True, "tml": 19, "ti": 29},
            {"mes": "2026-08", "suc": "Mar de Ajo", "usable": True, "tml": 17, "ti": 27, "tml_ti_origen": "fichaya"},
        ]
        routes.extend(untouched)

        pipeline._calibrar_tiempos_historicos_casa_central(routes)

        self.assertEqual(pipeline.CASA_CENTRAL_TML_REFERENCIA_2026, expected_tml)
        self.assertEqual(pipeline.CASA_CENTRAL_TI_REFERENCIA_2026, expected_ti)
        for field, expected in (("tml", expected_tml), ("ti", expected_ti)):
            for month, target in expected.items():
                month_rows = [
                    row for row in routes
                    if row["mes"] == month
                    and row["usable"]
                    and pipeline._norm_logistics_branch(row["suc"]) == "CASA CENTRAL"
                ]
                values = [row[field] for row in month_rows]
                self.assertAlmostEqual(sum(values) / len(values), target, places=6)
                self.assertTrue(all(
                    row.get(f"{field}_referencia_mensual") == target
                    for row in month_rows
                ))
        self.assertEqual(
            [(row["tml"], row["ti"]) for row in untouched],
            [(24, 31), (26, 32), (19, 29), (17, 27)],
        )

    def test_dashboard_uses_cached_fichaya_with_manual_override_priority(self):
        route = {
            "rid": "route-manual",
            "fecha": "2026-08-25",
            "mes": "2026-08",
            "suc": "Mar de Ajo",
            "chofer": "Gomez Jonatan",
            "inicio_foxtrot": "07:30",
            "fin_foxtrot": "14:00",
            "usable": True,
            "tml": 45,
            "ti": 45,
            "tml_ti_origen": "estimado",
            "horas": 6.5,
        }
        marks = {
            ("2026-08-25", "LEGAJO:818"): {
                "ingreso": pipeline._parse_hora_fichaya("07:00"),
                "egreso": pipeline._parse_hora_fichaya("14:30"),
            }
        }
        mapping = {
            "GOMEZ JONATAN": {"legajo": "818", "nombre": "GOMEZ JONATAN JOSUE"}
        }
        manual = {
            "RID:route-manual": {
                "fichada_ingreso": "07:10",
                "fichada_salida": "14:20",
                "motivo": "Fichadas verificadas",
            }
        }
        with patch.object(
            pipeline, "cargar_fichadas_cache", return_value=marks
        ), patch.object(
            pipeline, "fichaya_nombre_map", return_value=mapping
        ), patch.object(
            pipeline, "fichaya_empleados", return_value={}
        ), patch.object(
            pipeline, "fichaya_ajustes_manuales", return_value=manual
        ):
            pipeline.aplicar_tiempos_fichaya_guardados([route])

        self.assertEqual(route["tml"], 20)
        self.assertEqual(route["ti"], 20)
        self.assertEqual(route["tml_ti_origen"], "fichaya")
        self.assertTrue(route["tml_ti_ajuste_manual"])
        self.assertEqual(route["fichaya_ingreso"], "07:10")
        self.assertEqual(route["fichaya_egreso"], "14:20")

    def test_admin_message_is_escaped(self):
        with app_module.app.test_request_context("/admin"), patch.object(
            app_module.pipeline, "dqi_objetivo_bultos_mes", return_value=10
        ):
            html = app_module._admin_page('<script>alert("x")</script>', err=True)

        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertIn("&lt;script&gt;", html)

    def test_foxtrot_quality_uses_projected_query(self):
        quality = {
            "total": 1,
            "missing": {column: 0 for column in app_module.FOXTROT_AUDIT_COLUMNS},
            "rows": [],
        }
        with app_module.app.test_request_context("/foxtrot-calidad"), patch.object(
            app_module.pipeline.storage, "load_foxtrot_quality", return_value=quality
        ) as projected, patch.object(app_module.pipeline.storage, "load_all") as load_all:
            html = app_module._foxtrot_calidad_page()

        projected.assert_called_once()
        load_all.assert_not_called()
        self.assertIn("Calidad de columnas Foxtrot", html)

    def test_fichaya_empty_report_does_not_fetch_external_marks(self):
        with patch.object(
            app_module.pipeline.storage, "load_fichaya_routes", return_value=[]
        ) as projected, patch.object(app_module.pipeline, "cargar_fichadas") as marks:
            rows, warning = app_module._fichaya_report_rows("2026-08-01", "2026-08-31")

        projected.assert_called_once_with("2026-08-01", "2026-08-31", suc="", chofer="")
        marks.assert_not_called()
        self.assertEqual(rows, [])
        self.assertEqual(warning, "")

    def test_fichaya_failed_refresh_uses_labeled_cache_fallback(self):
        route = {
            "rid": "route-1",
            "fecha": "2026-08-25",
            "suc": "Chascomus",
            "chofer": "Gomez Jonatan",
            "inicio_foxtrot": "07:30",
            "fin_foxtrot": "14:00",
        }
        mark = {
            "ingreso": pipeline._parse_hora_fichaya("07:00"),
            "egreso": pipeline._parse_hora_fichaya("14:30"),
        }
        cached = {("2026-08-25", "GOMEZ JONATAN"): mark}
        with patch.object(
            app_module.pipeline.storage, "load_fichaya_routes", return_value=[route]
        ), patch.object(
            app_module.pipeline,
            "cargar_fichadas",
            side_effect=RuntimeError("faltan credenciales"),
        ), patch.object(
            app_module.pipeline, "cargar_fichadas_cache", return_value=cached
        ), patch.object(
            app_module, "_fichaya_name_map", return_value={}
        ), patch.object(
            app_module, "_fichaya_empleados", return_value={}
        ), patch.object(
            app_module, "_fichaya_manual_overrides", return_value={}
        ):
            rows, warning = app_module._fichaya_report_rows(
                "2026-08-01", "2026-08-25", force_live=True
            )

        self.assertIn("No se pudo actualizar desde FichaYA", warning)
        self.assertIn("guardadas como respaldo", warning)
        self.assertEqual(rows[0]["fichada_ingreso"], "07:00")
        self.assertEqual(rows[0]["estado"], "OK")

    def test_fichaya_refresh_warns_when_live_range_is_incomplete(self):
        routes = [
            {
                "rid": f"route-{day}",
                "fecha": f"2026-08-{day}",
                "suc": "Chascomus",
                "chofer": "Gomez Jonatan",
                "inicio_foxtrot": "07:30",
                "fin_foxtrot": "14:00",
            }
            for day in ("24", "25")
        ]
        marks = {
            ("2026-08-24", "GOMEZ JONATAN"): {
                "ingreso": pipeline._parse_hora_fichaya("07:00"),
                "egreso": pipeline._parse_hora_fichaya("14:30"),
            }
        }
        with patch.object(
            app_module.pipeline.storage, "load_fichaya_routes", return_value=routes
        ), patch.object(
            app_module.pipeline, "cargar_fichadas", return_value=marks
        ), patch.object(
            app_module, "_fichaya_name_map", return_value={}
        ), patch.object(
            app_module, "_fichaya_empleados", return_value={}
        ), patch.object(
            app_module, "_fichaya_manual_overrides", return_value={}
        ):
            _rows, warning = app_module._fichaya_report_rows(
                "2026-08-01", "2026-08-25", force_live=True
            )

        self.assertIn("llegan hasta 2026-08-24", warning)
        self.assertIn("rutas hasta 2026-08-25", warning)

    def test_fichaya_manual_override_takes_precedence_and_recalculates(self):
        route = {
            "rid": "route-manual",
            "fecha": "2026-08-25",
            "suc": "Chascomus",
            "chofer": "Gomez Jonatan",
            "inicio_foxtrot": "07:30",
            "fin_foxtrot": "14:00",
        }
        marks = {
            ("2026-08-25", "GOMEZ JONATAN"): {
                "ingreso": pipeline._parse_hora_fichaya("07:00"),
                "egreso": pipeline._parse_hora_fichaya("14:30"),
            }
        }
        manual = {
            "RID:route-manual": {
                "fichada_ingreso": "07:10",
                "inicio_foxtrot": "07:40",
                "finalizacion_foxtrot": "14:05",
                "fichada_salida": "14:25",
                "motivo": "Correccion validada",
                "actualizado": "2026-08-25T15:00:00-03:00",
                "usuario": "admin",
            }
        }
        with patch.object(
            app_module.pipeline.storage, "load_fichaya_routes", return_value=[route]
        ), patch.object(
            app_module.pipeline, "cargar_fichadas", return_value=marks
        ), patch.object(
            app_module, "_fichaya_name_map", return_value={}
        ), patch.object(
            app_module, "_fichaya_empleados", return_value={}
        ), patch.object(
            app_module, "_fichaya_manual_overrides", return_value=manual
        ):
            rows, warning = app_module._fichaya_report_rows(
                "2026-08-25", "2026-08-25"
            )

        self.assertEqual(warning, "")
        self.assertEqual(rows[0]["fichada_ingreso"], "07:10")
        self.assertEqual(rows[0]["inicio_foxtrot"], "07:40")
        self.assertEqual(rows[0]["tml"], 30)
        self.assertEqual(rows[0]["ti"], 20)
        self.assertTrue(rows[0]["ajuste_manual"])
        self.assertEqual(rows[0]["_source_fichada_ingreso"], "07:00")

    def test_fichaya_manual_save_persists_only_values_changed_from_source(self):
        with self.client.session_transaction() as user_session:
            user_session["admin_logged_in"] = True
            user_session["admin_user"] = "operador"
        row = {
            "manual_key": "RID:route-manual",
            "_source_fichada_ingreso": "07:00",
            "_source_inicio_foxtrot": "07:30",
            "_source_finalizacion_foxtrot": "14:00",
            "_source_fichada_salida": "14:30",
        }
        with patch.object(
            app_module, "_fichaya_manual_row", return_value=(row, "")
        ), patch.object(
            app_module, "_fichaya_manual_overrides", return_value={}
        ), patch.object(
            app_module.pipeline.storage, "save_setting"
        ) as save:
            response = self.client.post(
                "/reporte-fichaya-foxtrot/ajuste/guardar",
                data={
                    "ajuste_key": "RID:route-manual",
                    "desde": "2026-08-25",
                    "hasta": "2026-08-25",
                    "suc": "",
                    "chofer": "",
                    "fichada_ingreso": "07:05",
                    "inicio_foxtrot": "07:30",
                    "finalizacion_foxtrot": "14:00",
                    "fichada_salida": "14:30",
                    "motivo": "Fichada verificada",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("ajuste=guardado", response.headers["Location"])
        payload = save.call_args.args[1]["valor"]["RID:route-manual"]
        self.assertEqual(payload["fichada_ingreso"], "07:05")
        self.assertNotIn("inicio_foxtrot", payload)
        self.assertEqual(payload["motivo"], "Fichada verificada")
        self.assertEqual(payload["usuario"], "operador")

    def test_fichaya_manual_save_rejects_invalid_time(self):
        with self.client.session_transaction() as user_session:
            user_session["admin_logged_in"] = True
        row = {
            "manual_key": "RID:route-manual",
            "fecha": "2026-08-25",
            "sucursal": "Chascomus",
            "empleado": "Gomez Jonatan",
            "legajo_fichaya": "818",
            "fichada_ingreso": "07:00",
            "inicio_foxtrot": "07:30",
            "finalizacion_foxtrot": "14:00",
            "fichada_salida": "14:30",
            "motivo_ajuste": "",
            "ajuste_manual": False,
            "_source_fichada_ingreso": "07:00",
            "_source_inicio_foxtrot": "07:30",
            "_source_finalizacion_foxtrot": "14:00",
            "_source_fichada_salida": "14:30",
        }
        with patch.object(
            app_module, "_fichaya_manual_row", return_value=(row, "")
        ), patch.object(
            app_module.pipeline.storage, "save_setting"
        ) as save:
            response = self.client.post(
                "/reporte-fichaya-foxtrot/ajuste/guardar",
                data={
                    "ajuste_key": "RID:route-manual",
                    "desde": "2026-08-25",
                    "hasta": "2026-08-25",
                    "fichada_ingreso": "25:00",
                    "inicio_foxtrot": "07:30",
                    "finalizacion_foxtrot": "14:00",
                    "fichada_salida": "14:30",
                    "motivo": "Prueba",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"formato HH:MM", response.data)
        save.assert_not_called()

    def test_fichaya_manual_restore_deletes_only_the_override(self):
        with self.client.session_transaction() as user_session:
            user_session["admin_logged_in"] = True
        overrides = {
            "RID:route-manual": {"fichada_ingreso": "07:05"},
            "RID:route-other": {"fichada_ingreso": "08:00"},
        }
        with patch.object(
            app_module, "_fichaya_manual_overrides", return_value=overrides
        ), patch.object(
            app_module.pipeline.storage, "save_setting"
        ) as save:
            response = self.client.post(
                "/reporte-fichaya-foxtrot/ajuste/eliminar",
                data={
                    "ajuste_key": "RID:route-manual",
                    "desde": "2026-08-25",
                    "hasta": "2026-08-25",
                },
            )

        self.assertEqual(response.status_code, 302)
        saved = save.call_args.args[1]["valor"]
        self.assertNotIn("RID:route-manual", saved)
        self.assertIn("RID:route-other", saved)

    def test_fichaya_report_renders_manual_badge_and_edit_action(self):
        row = {
            "fecha": "2026-08-25",
            "sucursal": "Chascomus",
            "empleado": "Gomez Jonatan",
            "legajo_fichaya": "818",
            "empleado_fichaya": "Gomez Jonatan Josue",
            "fichada_ingreso": "07:05",
            "inicio_foxtrot": "07:30",
            "tml": 25,
            "finalizacion_foxtrot": "14:00",
            "fichada_salida": "14:30",
            "ti": 30,
            "estado": "OK",
            "route_id": "route-manual",
            "manual_key": "RID:route-manual",
            "ajuste_manual": True,
            "campos_ajuste": ["fichada_ingreso"],
            "motivo_ajuste": "Fichada verificada",
            "ajuste_actualizado": "2026-08-25T15:00:00-03:00",
            "ajuste_usuario": "operador",
        }
        cache = {
            "total": 1,
            "desde": "2026-08-25",
            "hasta": "2026-08-25",
            "actualizado": "2026-08-25 15:00:00",
        }
        with app_module.app.test_request_context(
            "/reporte-fichaya-foxtrot?desde=2026-08-25&hasta=2026-08-25"
        ), patch.object(
            app_module, "_require_login", return_value=None
        ), patch.object(
            app_module.pipeline.storage,
            "load_fichaya_dimensions",
            return_value={"sucursales": [], "choferes": []},
        ), patch.object(
            app_module, "_fichaya_report_rows", return_value=([row], "")
        ), patch.object(
            app_module.pipeline, "fichaya_cache_info", return_value=cache
        ):
            html = app_module._fichaya_report_page()

        self.assertIn("manual-badge", html)
        self.assertIn("Manual</span>", html)
        self.assertIn("/reporte-fichaya-foxtrot/editar?", html)
        self.assertIn("manual-value", html)
        self.assertIn("Ruta y empleados", html)
        self.assertIn("Empleado FichaYA / legajo", html)
        self.assertIn("identity-date", html)
        self.assertIn("action-cell", html)
        self.assertIn("Ajustes manuales: 1", html)

    def test_fichaya_live_refresh_invalidates_dashboard_immediately(self):
        cache = {"total": 0, "desde": "", "hasta": "", "actualizado": ""}
        with app_module.app.test_request_context(
            "/reporte-fichaya-foxtrot?desde=2026-08-01&hasta=2026-08-25&actualizar=1"
        ), patch.object(
            app_module, "_require_login", return_value=None
        ), patch.object(
            app_module.pipeline.storage,
            "load_fichaya_dimensions",
            return_value={"sucursales": [], "choferes": []},
        ), patch.object(
            app_module, "_fichaya_report_rows", return_value=([], "")
        ), patch.object(
            app_module.pipeline, "fichaya_cache_info", return_value=cache
        ), patch.object(
            app_module.pipeline, "clear_dashboard_cache"
        ) as clear_dashboard:
            app_module._fichaya_report_page()

        clear_dashboard.assert_called_once_with()

    def test_admin_can_complete_missing_logistics_data_from_api(self):
        with self.client.session_transaction() as user_session:
            user_session["admin_logged_in"] = True
        stats = {
            "api_rows": 3,
            "routes_updated": 2,
            "routes_unmatched": 1,
            "routes_ambiguous": 0,
            "fields_updated": {"camion": 1, "bultos": 2},
        }
        with patch.object(
            app_module.pipeline,
            "completar_rutas_desde_api_logistica",
            return_value=stats,
        ) as sync, patch.object(
            app_module.pipeline, "clear_dashboard_cache"
        ) as clear_cache, patch.object(
            app_module, "_admin_page", side_effect=lambda msg, err=False: msg
        ):
            response = self.client.post(
                "/actualizar-logistica-api",
                data={"desde": "2026-08-01", "hasta": "2026-08-13", "sucursal": "2"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Rutas actualizadas: 2", response.data)
        sync.assert_called_once_with("2026-08-01", "2026-08-13", "2")
        clear_cache.assert_has_calls([call(include_external=True)])

    def test_cost_config_rejects_weights_that_do_not_sum_one(self):
        with self.client.session_transaction() as user_session:
            user_session["admin_logged_in"] = True
        response = self.client.post(
            "/costos-distribucion/config",
            json={
                "weights": {"distance": 0.5, "time": 0.5, "volume": 0.5},
                "volume_criterion": "bultos",
                "profitability_thresholds": {"green_max_pct": 3, "yellow_max_pct": 6},
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("deben sumar", response.get_json()["error"])

    def test_cost_config_normalizes_and_whitelists_persisted_values(self):
        with self.client.session_transaction() as user_session:
            user_session["admin_logged_in"] = True
        payload = {
            "fuel_price_per_liter": 1200.5,
            "vehicle_liters_per_100km": 18,
            "vehicle_cost_per_km": 90,
            "driver_cost_per_hour": 5000,
            "helper_cost_per_hour": 3500,
            "helpers_count": 2,
            "other_route_cost": 500,
            "fuel_type": "Gasoil premium",
            "weights": {"distance": 0.4, "time": 0.3, "volume": 0.3},
            "volume_criterion": "bultos",
            "profitability_thresholds": {"green_max_pct": 3, "yellow_max_pct": 6},
            "unexpected": "must not be stored",
        }
        with patch.object(
            app_module.pipeline.storage,
            "save_logistics_config",
            side_effect=lambda config: config,
        ) as save:
            response = self.client.post("/costos-distribucion/config", json=payload)

        self.assertEqual(response.status_code, 200)
        saved = save.call_args.args[0]
        self.assertEqual(saved["helpers_count"], 2)
        self.assertEqual(saved["fuel_type"], "Gasoil premium")
        self.assertEqual(saved["return_distance_criterion"], "proportional_forward_distance")
        self.assertNotIn("unexpected", saved)

    def test_cost_page_allows_selecting_saved_depot_without_route(self):
        setup = {
            "config": {},
            "rates": [{"valid_from": "2026-01-01"}],
            "vehicles": [],
            "depots": {"Depósito independiente": {"nombre": "Base", "latitud": -36, "longitud": -57}},
        }
        with app_module.app.test_request_context("/costos-distribucion"), patch.object(
            app_module, "_require_login", return_value=None
        ), patch.object(
            app_module.pipeline.storage, "load_logistics_setup", return_value=setup
        ), patch.object(
            app_module.pipeline.storage, "list_recent_routes", return_value=[]
        ), patch.object(
            app_module.LogisticsCostService, "get_config", return_value={}
        ):
            html = app_module._costos_distribucion_page()

        self.assertIn('id=depotSucursal', html)
        self.assertIn('value="Depósito independiente"', html)
        self.assertIn("selectedDepotSucursal", html)

    def test_dashboard_json_cannot_close_script_tag(self):
        pipeline.clear_dashboard_cache()
        with patch.object(pipeline.storage, "load_dashboard_routes", return_value={}), patch.object(
            pipeline, "_data_desde_base", return_value={"value": "</script><script>alert(1)</script>"}
        ):
            html = pipeline.render_dashboard()
        pipeline.clear_dashboard_cache()

        self.assertNotIn("</script><script>alert(1)</script>", html)
        self.assertIn("<\\/script>", html)

    def test_expired_ttl_cache_returns_stale_value_while_refreshing(self):
        refreshed = threading.Event()
        calls = []

        @pipeline._ttl_cached(0.01)
        def value():
            calls.append(len(calls) + 1)
            if len(calls) > 1:
                refreshed.set()
            return calls[-1]

        self.assertEqual(value(), 1)
        time.sleep(0.02)
        self.assertEqual(value(), 1)
        self.assertTrue(refreshed.wait(1))
        value.cache_clear()


if __name__ == "__main__":
    unittest.main()
