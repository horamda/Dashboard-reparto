import io
import unittest
from unittest.mock import patch

import pipeline


class LogisticsImportTests(unittest.TestCase):
    @patch("pipeline.storage.update_attempt_deliveries", return_value=1)
    def test_delivery_import_accepts_aliases_and_rejects_negative_values(self, update):
        content = (
            "Route ID,Customer ID,Bultos entregados,HL entregados,Pallets entregados,Estado entrega\n"
            "route-1,000123,10,2.5,1,Entregado\n"
            "route-2,456,-1,1,0,Entregado\n"
        ).encode("utf-8")

        stats = pipeline.importar_volumen_entregas(io.BytesIO(content), "entregas.csv")

        self.assertEqual(stats["filas_validas"], 1)
        self.assertEqual(stats["filas_invalidas"], 1)
        record = update.call_args.args[0][0]
        self.assertEqual(record["cliente"], "123")
        self.assertEqual(record["values"]["bultos"], 10)
        self.assertEqual(record["values"]["hl"], 2.5)

    @patch("pipeline.storage.update_route_vehicles", return_value=1)
    def test_vehicle_import_updates_existing_route_keys(self, update):
        content = "Route ID,Patente\nroute-1,AA123BB\nroute-2,Sin camion\n".encode("utf-8")

        stats = pipeline.importar_asignacion_vehiculos(io.BytesIO(content), "vehiculos.csv")

        self.assertEqual(stats["filas_validas"], 1)
        self.assertEqual(stats["filas_invalidas"], 1)
        self.assertEqual(update.call_args.args[0][0]["camion"], "AA123BB")

    def test_delivery_import_requires_identifiers(self):
        content = "Cliente,Bultos\n123,10\n".encode("utf-8")
        with self.assertRaisesRegex(ValueError, "Route ID"):
            pipeline.importar_volumen_entregas(io.BytesIO(content), "entregas.csv")


if __name__ == "__main__":
    unittest.main()
