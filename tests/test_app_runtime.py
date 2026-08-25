import gzip
import os
import threading
import time
import unittest
from unittest.mock import patch

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
