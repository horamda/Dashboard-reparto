import os
import unittest
from unittest.mock import patch

os.environ["DISABLE_CACHE_PREWARM"] = "1"
import app as module
import pipeline


class EquiposTests(unittest.TestCase):
    def setUp(self):
        module.app.config.update(TESTING=True, SECRET_KEY="test")
        self.client = module.app.test_client()
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True

    def test_legajo_preserves_leading_zeroes(self):
        self.assertEqual(pipeline.fichaya_legajo(" 001 "), "001")
        self.assertEqual(pipeline.fichaya_lookup_ref("Ana", {"ANA": {"legajo": "001"}}, {})["legajo"], "001")

    def test_save_keeps_unsubmitted_mapping_and_validates_catalog(self):
        with patch.object(module, "_fichaya_empleados", return_value={"001": {"nombre": "Ana"}}), patch.object(module, "_fichaya_name_map", return_value={"OTRO": {"legajo": "9"}}), patch.object(pipeline.storage, "save_setting") as save:
            self.client.post("/asociar-fichaya/guardar", data={"map__ANA": "001"})
            data = save.call_args.args[1]["valor"]
            self.assertEqual(data["ANA"]["legajo"], "001")
            self.assertIn("OTRO", data)
            save.reset_mock()
            self.client.post("/asociar-fichaya/guardar", data={"map__ANA": "999"})
            save.assert_not_called()

    def test_equipment_filters_and_flags_unlinked_helpers_and_reloads(self):
        row = {"fecha": "2026-09-01", "sucursal_id": "1", "camion": "IVECO", "nro_camion": "3", "chofer": "Ana", "ayudante1": "Luis", "personas": 3}
        with patch.object(pipeline, "cargar_dpo_gkpis", return_value={"rows": [row, dict(row), {**row, "sucursal_id": "2", "chofer": "Dolores"}], "error": "Fuente parcial"}), patch.object(module, "_fichaya_name_map", return_value={"ANA": {"legajo": "001"}}), patch.object(module, "_fichaya_empleados", return_value={"001": {"nombre": "Ana", "estado": "activo"}}):
            response = self.client.get("/equipos-reparto?desde=2026-09-01&hasta=2026-09-02")
            html = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            for expected in ("Luis", "001", "Vinculación pendiente", "Cantidad de personas no coincide", "Varias filas", "Fuente parcial"):
                self.assertIn(expected, html)
            self.assertNotIn("Dolores", html)

    def test_mapping_includes_helpers_and_escapes_names(self):
        with patch.object(pipeline.storage, "load_fichaya_dimensions", return_value={"choferes": ["Ana"]}), patch.object(pipeline, "cargar_dpo_gkpis", return_value={"rows": [{"ayudante1": "Luis <test>"}], "error": ""}), patch.object(module, "_fichaya_name_map", return_value={}), patch.object(module, "_fichaya_empleados", return_value={}):
            html = self.client.get("/asociar-fichaya").get_data(as_text=True)
            self.assertIn("Luis &lt;test&gt;", html)
            self.assertIn("map__LUIS", html)

    def test_requires_login_and_valid_dates(self):
        self.assertEqual(self.client.get("/equipos-reparto?desde=bad").status_code, 400)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get("/equipos-reparto").status_code, 302)


if __name__ == "__main__":
    unittest.main()
