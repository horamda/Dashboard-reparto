import copy
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

os.environ["DISABLE_CACHE_PREWARM"] = "1"
import app
import equipos_service as service
import storage


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for field, value in (("DATA_DIR", self.directory.name), ("BACKEND", "json")):
            p = patch.object(storage, field, value)
            p.start()
            self.addCleanup(p.stop)
        self.row = {"fecha": "2026-09-01", "sucursal_id": "1", "nro_camion": "03", "camion": "(03) IVECO", "chofer": "Ana", "ayudante1": "Luis", "personas": 2, "fuente": "principal"}
        self.mapping = {"ANA": {"legajo": "001"}, "LUIS": {"legajo": "002"}}
        self.employees = {"001": {"nombre": "Ana", "estado": "activo"}, "002": {"nombre": "Luis", "estado": "activo"}}
        self.routes = {"R1": {"rid": "R1", "fecha": "2026-09-01", "suc": "Mar de Ajo", "camion": "(03) IVECO", "chofer": "Ana", "usable": True}}
        for target, name, value in ((service.pipeline, "cargar_dpo_gkpis", {"rows": [self.row], "error": ""}),
                                    (service.pipeline, "fichaya_nombre_map", self.mapping),
                                    (service.pipeline, "fichaya_empleados", self.employees),
                                    (storage, "load_all", self.routes)):
            p = patch.object(target, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def saved(self):
        return storage.load_equipo_days("2026-09-01", "2026-09-01")["2026-09-01"]

    def sync(self):
        return service.sync_history("2026-09-01", "2026-09-01", "operador")

    def test_snapshot_is_idempotent_and_survives_source_and_mapping_changes(self):
        self.assertEqual(self.sync()["guardados"], 1)
        original = self.saved()
        self.assertEqual(original["equipos"][0]["integrantes"][1]["legajo"], "002")
        self.assertEqual(original["rutas"]["R1"]["modo"], "automatico")
        self.assertEqual(self.sync()["existentes"], 1)
        self.row["ayudante1"] = "Otro"
        self.assertEqual(self.sync()["cambiados"], 1)
        self.assertEqual(self.saved(), original)

    def test_reordering_source_does_not_create_new_versions(self):
        rows = [self.row, {**self.row, "nro_camion": "5"}]
        with patch.object(service.pipeline, "cargar_dpo_gkpis", return_value={"rows": rows}):
            self.sync()
            rows.reverse()
            self.assertEqual(self.sync()["existentes"], 1)

    def test_ambiguous_reloads_are_not_automatically_assigned(self):
        with patch.object(service.pipeline, "cargar_dpo_gkpis", return_value={"rows": [self.row, {**self.row, "fuente": "extra", "recarga": True}]}):
            self.sync()
        day = self.saved()
        self.assertEqual(len(day["equipos"]), 2)
        self.assertEqual(day["rutas"], {})
        service.assign_route(day["fecha"], 1, "R1", day["equipos"][0]["id"], "operador", "Primera salida")
        self.assertEqual(self.saved()["rutas"]["R1"]["modo"], "manual")

    def test_incomplete_source_and_future_days_are_not_written(self):
        with patch.object(service.pipeline, "cargar_dpo_gkpis", return_value={"rows": [self.row], "error": "Fuente caída"}):
            with self.assertRaises(ValueError):
                self.sync()
        self.assertEqual(storage.load_equipo_days("2026-09-01", "2026-09-01"), {})
        future = {**self.row, "fecha": "2999-01-01"}
        self.assertEqual(service.prepare_days({"rows": [future]}, self.mapping, self.employees, "2999-01-01", "2999-01-01"), {})

    def test_duplicate_people_or_missing_legajos_block_automatic_assignment(self):
        for helper in ("Ana", "Desconocido"):
            teams = service.prepare_days({"rows": [{**self.row, "ayudante1": helper}]}, self.mapping, self.employees, "2026-09-01", "2026-09-01")["2026-09-01"]
            self.assertTrue(teams[0]["avisos"])
            self.assertEqual(service.match_routes(teams, self.routes, self.mapping, self.employees), {})

    def test_refresh_requires_revision_and_keeps_previous_version(self):
        self.sync()
        previous = self.saved()
        self.row["ayudante1"] = "Otro"
        service.revise_day("2026-09-01", 1, "operador", "Cambio de ayudante")
        current = self.saved()
        self.assertEqual(current["revision"], 2)
        self.assertEqual(current["auditoria"][-1]["anterior"]["equipos"], previous["equipos"])
        self.assertEqual(current["rutas"], {})
        with self.assertRaises(ValueError):
            service.revise_day("2026-09-01", 1, "operador", "Formulario viejo")
        self.assertEqual(self.saved(), current)

    def test_wrong_day_route_rejected_and_removal_audited(self):
        self.sync()
        day = self.saved()
        with self.assertRaises(ValueError):
            service.assign_route(day["fecha"], 1, "INEXISTENTE", day["equipos"][0]["id"], "operador", "Corrección")
        service.assign_route(day["fecha"], 1, "R1", "", "operador", "No participó")
        self.assertEqual(self.saved()["rutas"], {})
        self.assertIsNotNone(self.saved()["auditoria"][-1]["anterior"])

    def test_json_updates_are_atomic_and_serialized(self):
        def increment(_):
            return storage.update_equipo_day("2026-09-02", lambda old: {"fecha": "2026-09-02", "count": old.get("count", 0) + 1})
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(increment, range(20)))
        self.assertEqual(storage.load_equipo_days("2026-09-02", "2026-09-02")["2026-09-02"]["count"], 20)
        with self.assertRaises(RuntimeError):
            storage.update_equipo_day("2026-09-02", lambda old: (_ for _ in ()).throw(RuntimeError("error")))
        self.assertEqual(storage.load_equipo_days("2026-09-02", "2026-09-02")["2026-09-02"]["count"], 20)

    def test_history_page_and_protected_form(self):
        app.app.config.update(TESTING=True, SECRET_KEY="history-test")
        client = app.app.test_client()
        self.assertEqual(client.get("/equipos-reparto/historico").status_code, 302)
        with client.session_transaction() as session:
            session["admin_logged_in"] = True
        url = "/equipos-reparto/historico?desde=2026-09-01&hasta=2026-09-01"
        self.assertEqual(client.get(url).status_code, 200)
        self.assertEqual(client.post("/equipos-reparto/historico/guardar", data={}).status_code, 400)
        with client.session_transaction() as session:
            token = session["equipos_csrf"]
        response = client.post("/equipos-reparto/historico/guardar", data={"csrf": token, "desde": "2026-09-01", "hasta": "2026-09-01", "accion": "importar"})
        self.assertEqual(response.status_code, 302)
        html = client.get(url).get_data(as_text=True)
        self.assertIn("R1", html)
        self.assertIn("002", html)
        self.routes["R1"]["camion"] = "5"
        self.assertIn("La ruta cambió", client.get(url).get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
