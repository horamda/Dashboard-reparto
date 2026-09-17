import copy
import unittest
from unittest.mock import patch
import pipeline


class CurrentWindowTests(unittest.TestCase):
    def test_current_master_reconciles_both_directions_without_changing_history(self):
        route = {"clientes_sin_ventana": [{"cliente": "287", "nombre": "Viejo"}, {"cliente": "999", "nombre": "Sin maestro"}],
                 "clientes_con_ventana": ["100"], "clientes_fuera_ontime": [{"cliente": "100"}],
                 "pdv_sin_ventana": 5, "pdv_ontime": 2, "pdv_fuera_ontime": 3, "ontime_pct": 40}
        original = copy.deepcopy(route)
        master = {"287": {"nombre": "EL BODEGON", "ventanas": [{"ini": 600, "fin": 840}]},
                  "100": {"nombre": "Actual", "ventanas": []}}
        pipeline.aplicar_ventanas_actuales_clientes([route], master)
        self.assertEqual(route["clientes_con_ventana_actual"], ["287"])
        missing = {r["cliente"]: r for r in route["clientes_sin_ventana_actual"]}
        self.assertEqual(set(missing), {"999", "100"})
        self.assertEqual(missing["100"]["nombre"], "Actual")
        self.assertEqual(missing["999"]["motivo"], "no encontrado en base de clientes")
        self.assertEqual({k: route[k] for k in original}, original)
        self.assertNotIn("287", missing)

    def test_empty_current_list_is_explicit_and_latest_master_is_used(self):
        route = {"clientes_sin_ventana": [{"cliente": "287"}]}
        pipeline.aplicar_ventanas_actuales_clientes([route], {"287": {"ventanas": [{"ini": 600, "fin": 840}]}})
        self.assertEqual(route["clientes_sin_ventana_actual"], [])
        pipeline.aplicar_ventanas_actuales_clientes([route], {"287": {"ventanas": []}})
        self.assertEqual(route["clientes_sin_ventana_actual"][0]["cliente"], "287")

    def test_customer_import_invalidates_dashboard(self):
        with patch.object(pipeline, "procesar_clientes", return_value={"287": {}}), patch.object(pipeline.storage, "replace_clientes", return_value=1), patch.object(pipeline, "clear_dashboard_cache") as clear:
            self.assertEqual(pipeline.actualizar_clientes(None), 1)
        clear.assert_called_once_with()
