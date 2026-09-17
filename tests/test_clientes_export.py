import unittest
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from clientes_export import clientes_sin_ventana_xlsx
import app as app_module


class ClientesExportTests(unittest.TestCase):
    def test_complete_list_and_text_safety(self):
        rows = [{"cliente": "000" + str(i), "nombre": "=1+1 & local", "pedidos": i+1, "motivo": "sin ventana"} for i in range(42)]
        with ZipFile(BytesIO(clientes_sin_ventana_xlsx(rows))) as book:
            for name in book.namelist():
                ET.fromstring(book.read(name))
            root = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        self.assertEqual(len(root.findall("s:sheetData/s:row", ns)), 43)
        self.assertEqual(root.find(".//s:c[@r='A2']/s:is/s:t", ns).text, "0000")
        self.assertEqual(root.find(".//s:c[@r='B2']/s:is/s:t", ns).text, "=1+1 & local")
        self.assertEqual(root.find(".//s:c[@r='C43']/s:v", ns).text, "42")
        self.assertFalse(root.findall(".//s:f", ns))
        self.assertEqual(root.find("s:autoFilter", ns).attrib["ref"], "A1:D43")

    def test_invalid_counts_and_empty_export(self):
        for rows in ([], [{"cliente": "1", "nombre": "x", "motivo": "x", "pedidos": -1}]):
            with self.assertRaises(ValueError):
                clientes_sin_ventana_xlsx(rows)

    def test_route_requires_login_and_returns_xlsx(self):
        app_module.app.config.update(TESTING=True, SECRET_KEY="test-export")
        client = app_module.app.test_client()
        self.assertEqual(client.post("/clientes-sin-ventana.xlsx", json={}).status_code, 302)
        with client.session_transaction() as session:
            session["admin_logged_in"] = True
        response = client.post("/clientes-sin-ventana.xlsx", json={"clientes": [{"cliente": "001", "nombre": "Cliente", "pedidos": 2, "motivo": "sin ventana"}]})
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertTrue(response.data.startswith(b"PK"))
        self.assertEqual(client.post("/clientes-sin-ventana.xlsx", json={}).status_code, 400)
