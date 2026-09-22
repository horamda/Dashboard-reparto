import copy
import os
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

os.environ["DISABLE_CACHE_PREWARM"] = "1"
import app
import equipos_service
import fichaya_kpis_service as svc
import kpi_storage
import pipeline
import storage


class KpiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.row = {"fecha": "2026-09-01", "sucursal_id": "1", "nro_camion": "3", "camion": "3", "chofer": "Ana", "ayudante1": "Luis", "personas": 2}
        self.mapping = {"ANA": {"legajo": "001"}, "LUIS": {"legajo": "002"}}
        self.employees = {"001": {"nombre": "Ana", "estado": "activo"}, "002": {"nombre": "Luis", "estado": "activo"}}
        self.routes = {"R1": {"rid": "R1", "fecha": "2026-09-01", "suc": "Casa Central", "camion": "3", "chofer": "Ana", "usable": True,
                              "adhcli": 90, "disp_km_plan": 100, "disp_km_real": 110, "disp_hs_plan": 200, "disp_hs_real": 180,
                              "inicio_foxtrot": "07:30", "fin_foxtrot": "14:00", "tml": 99, "ti": 99, "tml_ti_origen": "estimado"}}
        for target, name, kwargs in (
            (storage, "DATA_DIR", {"new": temp.name}), (storage, "BACKEND", {"new": "json"}),
            (storage, "load_all", {"return_value": self.routes}),
            (pipeline, "fichaya_nombre_map", {"return_value": self.mapping}),
            (pipeline, "fichaya_empleados", {"return_value": self.employees}),
            (pipeline, "fichaya_ajustes_manuales", {"return_value": {}}),
            (pipeline, "cargar_fichadas_cache", {"return_value": {("2026-09-01", "LEGAJO:001"): {"ingreso": pipeline._parse_hora_fichaya("07:00"), "egreso": pipeline._parse_hora_fichaya("14:20")}}}),
            (pipeline, "cargar_dpo_gkpis", {"return_value": {"rows": [self.row]}}),
        ):
            p = patch.object(target, name, **kwargs)
            p.start()
            self.addCleanup(p.stop)
        env = patch.dict(os.environ, {"FICHAYA_API_BASE_URL": "https://fichaya.example", "FICHAYA_API_USERNAME": "technical", "FICHAYA_API_PASSWORD": "test-only"})
        env.start()
        self.addCleanup(env.stop)
        svc._TOKEN.clear()
        equipos_service.sync_history("2026-09-01", "2026-09-01", "test")

    def draft(self):
        return svc.create_draft("2026-09-01", "2026-09-01", "test")

    def unthrottle(self):
        kpi_storage.update("sender", lambda old: {**old, "disponible": 0})

    def test_catalog_uses_codes_from_workbook_not_database_ids(self):
        self.assertEqual(svc.config()["empresa_id"], 1)
        self.assertEqual(svc.config()["metricas"]["click"], "4")
        self.assertEqual(svc.METRICS["rmd"]["codigo"], "RMD")
        self.assertEqual(svc.METRICS["nps"]["acumulacion"], "ultimo")
        with self.assertRaises(ValueError):
            svc.save_config(1, {"dqi": "5"}, "test")

    def test_values_shared_with_helpers_preserve_legajos_and_real_times(self):
        draft = self.draft()
        self.assertEqual(draft["errores"], [])
        values = {(r["legajo"], r["codigo_kpi"]): r["valor"] for r in draft["resultados"]}
        for legajo in ("001", "002"):
            self.assertEqual(values[(legajo, "2")], "-10.0000")
            self.assertEqual(values[(legajo, "3")], "10.0000")
            self.assertEqual(values[(legajo, "4")], "90.0000")
            self.assertEqual(values[(legajo, "6")], "0.0000")
            self.assertEqual(values[(legajo, "7")], "20.0000")

    def test_dispersion_uses_totals_not_average_percentages(self):
        routes = {"a": {"disp_km_plan": 100, "disp_km_real": 120}, "b": {"disp_km_plan": 300, "disp_km_real": 300}}
        self.assertEqual(svc.metric_value("disp_km", routes, {}), Decimal("-5"))

    def test_tml_fixed_start_preserves_actual_attendance(self):
        for arrival in ('07:00', '07:30', '07:45'):
            with self.subTest(arrival=arrival):
                mark = pipeline._parse_hora_fichaya(arrival)
                result = pipeline.calcular_tiempos_fichaya_ruta(
                    {**self.routes['R1'], 'inicio_foxtrot': '07:55'},
                    {('2026-09-01', 'LEGAJO:001'): {'ingreso': mark}},
                    self.mapping, self.employees, {})
                self.assertEqual(result['tml'], 25)
                self.assertEqual(result['effective']['fichada_ingreso'], mark)

    def test_named_marks_resolve_to_unique_catalog_legajo(self):
        marks = {('2026-09-01', 'ANA'): {'ingreso': pipeline._parse_hora_fichaya('07:00'), 'egreso': pipeline._parse_hora_fichaya('14:20')}}
        with patch.object(pipeline, 'cargar_fichadas_cache', return_value=marks):
            draft = self.draft()
        self.assertFalse(draft['errores'])
        self.assertEqual(len(draft['resultados']), 10)

    def test_named_marks_reject_homonyms_and_conflicting_clocks(self):
        marks = {('2026-09-01', 'ANA'): {'ingreso': '07:00', 'egreso': '14:20'}}
        employees = {**self.employees, '003': {'nombre': 'Ana'}}
        self.assertEqual(svc.marks_by_legajo(marks, self.mapping, employees), {})
        marks[('2026-09-01', 'LEGAJO:001')] = {'ingreso': '08:00', 'egreso': '14:20'}
        self.assertEqual(svc.marks_by_legajo(marks, self.mapping, self.employees), {})

    def test_general_rmd_score_not_response_percentage(self):
        cfg = {"empresa_id": 1, "sector_id": 1, "metricas": {"rmd": "RMD"}}
        source = {"rows": [
            {"fecha": "2026-09-01", "tipo": "RMD Puntaje", "resultado": 4.75},
            {"fecha": "2026-09-01", "tipo": "Rate My Delivery % de respuestas", "resultado": 80}
        ], "error": ""}
        with patch.object(pipeline, "cargar_satisfaccion", return_value=source):
            result = svc.calculate("2026-09-01", "2026-09-01", cfg)
        self.assertFalse(result["errores"])
        self.assertEqual(len(result["resultados"]), 2)
        self.assertEqual({r["valor"] for r in result["resultados"]}, {"4.7500"})
        source["rows"][0]["resultado"] = 75
        with self.assertRaises(ValueError):
            svc.general_value("rmd", "2026-09-01", source)

    def test_general_nps_repeats_same_measurement_for_each_member(self):
        cfg = {"empresa_id": 1, "sector_id": 1, "metricas": {"nps": "8"}}
        source = {"rows": [{"fecha": "2026-09-01", "tipo": "NPS GRAL", "resultado": -12}], "error": ""}
        with patch.object(pipeline, "cargar_satisfaccion", return_value=source):
            result = svc.calculate("2026-09-01", "2026-09-01", cfg)
        self.assertFalse(result["errores"])
        self.assertEqual(len(result["resultados"]), 2)
        self.assertEqual({r["valor"] for r in result["resultados"]}, {"-12.0000"})
        self.assertEqual({r["legajo"] for r in result["resultados"]}, {"001", "002"})
        with self.assertRaises(ValueError):
            svc.general_value("nps", "2026-09-02", source)

    def test_multiple_routes_consolidate_to_one_result_per_person_and_code(self):
        self.routes["R2"] = {**self.routes["R1"], "rid": "R2", "adhcli": 80, "disp_km_plan": 300, "disp_km_real": 300}
        def add_route(day):
            day["rutas"]["R2"] = {"equipo_id": day["equipos"][0]["id"], "modo": "manual", "ruta_origen": equipos_service.route_snapshot(self.routes["R2"])}
            return day
        storage.update_equipo_day("2026-09-01", add_route)
        run = self.draft()
        self.assertFalse(run["errores"])
        self.assertEqual(len(run["resultados"]), 10)
        clicks = [r for r in run["resultados"] if r["codigo_kpi"] == "4"]
        self.assertEqual([r["valor"] for r in clicks], ["85.0000", "85.0000"])
        self.assertTrue(all(e["rutas"] == ["R1", "R2"] for e in run["evidencia"]))

    def test_missing_marks_never_export_estimated_times(self):
        with patch.object(pipeline, "cargar_fichadas_cache", return_value={}):
            run = self.draft()
        self.assertEqual(run["estado"], "bloqueado")
        self.assertEqual(run["lotes"], [])
        self.assertTrue(any("estimados" in e for e in run["errores"]))

    def test_missing_metrics_and_changed_routes_block_entire_draft(self):
        self.routes["R1"]["adhcli"] = None
        self.assertEqual(self.draft()["estado"], "bloqueado")
        self.routes["R1"]["camion"] = "9"
        self.assertTrue(any("cambió la ruta" in e for e in self.draft()["errores"]))

    def test_nonfinite_and_extreme_values_are_rejected(self):
        for value in (None, True, "NaN", "Infinity", -1):
            with self.assertRaises(ValueError):
                svc.number(value)
        self.routes["R1"]["disp_km_real"] = 999
        self.assertEqual(self.draft()["estado"], "bloqueado")

    def test_send_success_and_same_run_is_not_resent(self):
        run = self.draft()
        with patch.object(svc, "post_results", return_value={"empresa_id": 1, "recibidos": 10, "guardados": 10}) as post:
            sent = svc.send_next(run["id"], "test")
            self.assertEqual(sent["estado"], "completo")
            svc.send_next(run["id"], "test")
            post.assert_called_once()
        self.assertEqual(kpi_storage.recent()[0]["estado"], "completo")

    def test_changed_input_invalidates_preview_before_network(self):
        run = self.draft()
        self.routes["R1"]["adhcli"] = 80
        with patch.object(svc, "post_results") as post:
            with self.assertRaises(ValueError):
                svc.send_next(run["id"], "test")
            post.assert_not_called()

    def test_network_failure_retries_exact_payload(self):
        run = self.draft()
        with patch.object(svc, "post_results", side_effect=svc.ApiError("Timeout", retryable=True)) as post:
            failed = svc.send_next(run["id"], "test")
            payload = copy.deepcopy(post.call_args.args[0])
            self.assertEqual(failed["estado"], "reintentar")
        self.unthrottle()
        with patch.object(svc, "post_results", return_value={"empresa_id": 1, "recibidos": 10, "guardados": 10}) as post:
            sent = svc.send_next(run["id"], "test")
            self.assertEqual(post.call_args.args[0], payload)
            self.assertEqual(len(sent["lotes"][0]["intentos"]), 2)

    def test_422_does_not_allow_unmodified_retry(self):
        run = self.draft()
        with patch.object(svc, "post_results", side_effect=svc.ApiError("Rechazado", 422, detail=[{"fila": 2, "error": "Sector inválido"}])):
            rejected = svc.send_next(run["id"], "test")
        self.assertEqual(rejected["estado"], "rechazado")
        self.unthrottle()
        with self.assertRaises(ValueError):
            svc.send_next(run["id"], "test")

    def test_newer_attempt_blocks_stale_draft(self):
        old, new = self.draft(), self.draft()
        with patch.object(svc, "post_results", side_effect=svc.ApiError("Timeout", retryable=True)):
            svc.send_next(new["id"], "test")
        self.unthrottle()
        with self.assertRaisesRegex(ValueError, "más reciente"):
            svc.send_next(old["id"], "test")

    def test_token_cache_and_one_refresh_on_401(self):
        response = {"access_token": "token-one", "scope": "external:read kpis:write", "expires_in": 3600}
        with patch.object(svc, "api_post", return_value=response) as post:
            self.assertEqual(svc.token(), "token-one")
            self.assertEqual(svc.token(), "token-one")
            post.assert_called_once()
        with patch.object(svc, "api_post", side_effect=[svc.ApiError("Expired", 401), {**response, "access_token": "token-two"}, {"ok": True}]) as post:
            self.assertEqual(svc.post_results({"test": 1}), {"ok": True})
            self.assertEqual(post.call_count, 3)
            self.assertEqual(post.call_args.args[2], "token-two")

    def test_token_without_write_scope_does_not_send_results(self):
        with patch.object(svc, "api_post", return_value={"access_token": "token", "scope": "external:read"}) as post:
            with self.assertRaises(svc.ApiError):
                svc.post_results({})
            post.assert_called_once()

    def test_large_preview_splits_batches_and_preserves_partial_success(self):
        calc = {"empresa_id": 1, "resultados": [{"legajo": str(i).zfill(4), "fecha": "2026-09-01", "codigo_kpi": "4", "valor": "90.0000"} for i in range(1001)],
                "evidencia": [{} for _ in range(1001)], "errores": [], "huella": "large-test"}
        with patch.object(svc, "calculate", return_value=calc):
            run = self.draft()
            self.assertEqual([len(c["payload"]["resultados"]) for c in run["lotes"]], [1000, 1])
            with patch.object(svc, "post_results", return_value={"empresa_id": 1, "recibidos": 1000, "guardados": 1000}):
                self.assertEqual(svc.send_next(run["id"], "test")["estado"], "parcial")
            self.unthrottle()
            with patch.object(svc, "post_results", side_effect=svc.ApiError("Temporal", 500, retryable=True)):
                failed = svc.send_next(run["id"], "test")
            self.assertEqual(failed["lotes"][0]["estado"], "guardado")
            self.unthrottle()
            with patch.object(svc, "post_results", return_value={"empresa_id": 1, "recibidos": 1, "guardados": 1}) as post:
                self.assertEqual(svc.send_next(run["id"], "test")["estado"], "completo")
                self.assertEqual(len(post.call_args.args[0]["resultados"]), 1)

    def test_rate_limit_pause_and_unconfirmed_response(self):
        run = self.draft()
        with patch.object(svc, "post_results", side_effect=svc.ApiError("Demasiadas solicitudes", 429, retryable=True, wait=120)):
            svc.send_next(run["id"], "test")
        with self.assertRaisesRegex(ValueError, "pausa"):
            svc.send_next(run["id"], "test")
        self.unthrottle()
        with patch.object(svc, "post_results", return_value={"empresa_id": 1, "recibidos": 10, "guardados": 9}):
            self.assertEqual(svc.send_next(run["id"], "test")["estado"], "reintentar")

    def test_mark_lookup_does_not_guess_unlinked_names(self):
        with patch.object(pipeline, "cargar_fichadas_cache", return_value={("2026-09-01", "ANA OTRO APELLIDO"): {"ingreso": pipeline._parse_hora_fichaya("07:00"), "egreso": pipeline._parse_hora_fichaya("14:20")}}):
            self.assertEqual(self.draft()["estado"], "bloqueado")

    def test_imported_sector_mismatch_blocks_export(self):
        self.employees["002"]["sector_id"] = "2"
        self.assertTrue(any("sector" in error for error in self.draft()["errores"]))

    def test_partial_draft_keeps_missing_metrics_pending_and_sends_valid_rows(self):
        self.routes['R1']['adhcli'] = None
        run = svc.create_draft('2026-09-01', '2026-09-01', 'test', allow_partial=True)
        self.assertEqual(run['errores'], [])
        self.assertEqual(len(run['pendientes']), 2)
        self.assertEqual(len(run['resultados']), 8)
        self.assertTrue(all(row['codigo_kpi'] != '4' for row in run['resultados']))
        with patch.object(svc, 'post_results', return_value={'empresa_id': 1, 'recibidos': 8, 'guardados': 8}):
            self.assertEqual(svc.send_next(run['id'], 'test')['estado'], 'completo')

    def test_partial_excludes_person_day_when_another_route_is_unassigned(self):
        self.routes['R2'] = dict(self.routes['R1'], rid='R2')
        run = svc.create_draft('2026-09-01', '2026-09-01', 'test', allow_partial=True)
        self.assertEqual(run['resultados'], [])
        self.assertEqual(run['lotes'], [])
        self.assertTrue(run['pendientes'])

    def test_new_drafts_skip_previously_confirmed_keys_even_if_value_changed(self):
        run = self.draft()
        with patch.object(svc, 'post_results', return_value={'empresa_id': 1, 'recibidos': 10, 'guardados': 10}):
            svc.send_next(run['id'], 'test')
        self.routes['R1']['adhcli'] = 80
        new = svc.create_draft('2026-09-01', '2026-09-01', 'test', allow_partial=True)
        self.assertEqual(new['lotes'], [])
        self.assertEqual(len(new['omitidos_enviados']), 10)
        self.assertEqual(len(new['pendientes']), 2)

    def test_other_draft_confirmed_after_preview_prevents_duplicate_network_call(self):
        old, new = self.draft(), self.draft()
        with patch.object(svc, 'post_results', return_value={'empresa_id': 1, 'recibidos': 10, 'guardados': 10}):
            svc.send_next(new['id'], 'test')
        self.unthrottle()
        with patch.object(svc, 'post_results') as post:
            with self.assertRaises(ValueError):
                svc.send_next(old['id'], 'test')
            post.assert_not_called()

    def test_ui_preview_protected_and_actual_catalog_visible(self):
        app.app.config.update(TESTING=True, SECRET_KEY="test-kpi")
        client = app.app.test_client()
        self.assertEqual(client.get("/kpis-fichaya").status_code, 302)
        with client.session_transaction() as session:
            session["admin_logged_in"] = True
        html = client.get("/kpis-fichaya").get_data(as_text=True)
        self.assertIn("DISPERSION EN KM", html)
        self.assertIn("PPM", html)
        self.assertEqual(client.post("/kpis-fichaya/preparar").status_code, 400)
        with client.session_transaction() as session:
            csrf = session["kpis_csrf"]
        response = client.post("/kpis-fichaya/preparar", data={"csrf": csrf, "desde": "2026-09-01", "hasta": "2026-09-01"})
        self.assertEqual(response.status_code, 302)
        preview = client.get(response.location)
        self.assertEqual(preview.status_code, 200)
        self.assertIn("0.0000", preview.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()


def test_recent_postgres_parameterizes_like_pattern():
    from unittest.mock import MagicMock, patch
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
    with patch.object(storage, 'BACKEND', 'postgres'), patch.object(storage, '_conn', return_value=connection):
        assert kpi_storage.recent(5) == []
    sql, params = cursor.execute.call_args.args
    assert "LIKE %s" in sql
    assert params == ('fichaya_kpis:run_%', 5)
