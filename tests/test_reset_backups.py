import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pipeline
import storage


class ResetBackupTests(unittest.TestCase):
    def test_json_backup_round_trips_full_records(self):
        with tempfile.TemporaryDirectory() as directory:
            routes = Path(directory) / "routes.json"
            attempts = Path(directory) / "attempts.json"
            original = {"rutas": [{"rid": "r1", "ti": 27, "raw_foxtrot": {"x": "original"}, "ti_estimado": True}]}
            marks = {"attempts": {"a1": {"route_id": "r1"}}}
            routes.write_text(json.dumps(original), encoding="utf-8")
            attempts.write_text(json.dumps(marks), encoding="utf-8")
            with patch.multiple(storage, DATA_DIR=directory, JSON_PATH=str(routes), ATTEMPTS_JSON_PATH=str(attempts)):
                result = storage._backup_json_before_reset()
            payload = json.loads(Path(result).read_text(encoding="utf-8"))
            self.assertEqual(payload["files"], {"rutas": original, "attempts": marks})
            self.assertEqual(json.loads(routes.read_text()), original)

    def test_corrupt_json_aborts_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            routes = Path(directory) / "routes.json"
            routes.write_text("not-json", encoding="utf-8")
            with patch.multiple(storage, DATA_DIR=directory, JSON_PATH=str(routes), ATTEMPTS_JSON_PATH=str(Path(directory) / "absent.json")):
                with self.assertRaises(json.JSONDecodeError):
                    storage._backup_json_before_reset()
            self.assertEqual(routes.read_text(), "not-json")
            self.assertFalse((Path(directory) / "backups").exists())

    @unittest.skipUnless(storage.backend_name() == "postgres", "Postgres branch")
    def test_postgres_reset_backs_up_before_truncating(self):
        cur = MagicMock()
        cur.fetchone.return_value = ("backup-id",)
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cur
        with patch.object(storage, "_conn") as conn, patch.object(storage.uuid, "uuid4", return_value="backup-id"), patch.object(storage, "clear_cache"):
            conn.return_value.__enter__.return_value = connection
            result = storage.reset()
        sql = [call.args[0] for call in cur.execute.call_args_list]
        self.assertEqual(result, "backup-id")
        self.assertTrue(sql[0].startswith("LOCK TABLE"))
        self.assertIn("INSERT INTO dashboard_reset_backups", sql[2])
        self.assertIn("to_jsonb(r)", sql[2])
        self.assertEqual(sql[3:], ["TRUNCATE rutas_dashboard;", "TRUNCATE attempts_dashboard;"])

    @unittest.skipUnless(storage.backend_name() == "postgres", "Postgres branch")
    def test_failed_backup_prevents_truncate(self):
        cur = MagicMock()
        def execute(sql, *args):
            if "INSERT INTO dashboard_reset_backups" in sql:
                raise RuntimeError("disk full")
        cur.execute.side_effect = execute
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cur
        with patch.object(storage, "_conn") as conn:
            conn.return_value.__enter__.return_value = connection
            with self.assertRaisesRegex(RuntimeError, "disk full"):
                storage.reset()
        self.assertFalse(any("TRUNCATE" in call.args[0] for call in cur.execute.call_args_list))
        self.assertIsNotNone(conn.return_value.__exit__.call_args.args[0])

    def test_invalid_export_does_not_reset(self):
        with patch.object(pipeline, "_leer_csv_visitas"), patch.object(pipeline, "procesar_export", side_effect=ValueError("invalid")), patch.object(storage, "reset") as reset:
            with self.assertRaises(ValueError):
                pipeline.actualizar(None, "invalid.xlsx", reset=True)
        reset.assert_not_called()

    def test_empty_export_does_not_reset(self):
        with patch.object(pipeline, "_leer_csv_visitas"), patch.object(pipeline, "procesar_export", return_value={}), patch.object(storage, "reset") as reset:
            with self.assertRaisesRegex(ValueError, "no contiene rutas"):
                pipeline.actualizar(None, "empty.xlsx", reset=True)
        reset.assert_not_called()

    def test_failed_reset_prevents_import(self):
        with patch.object(pipeline, "_leer_csv_visitas"), patch.object(pipeline, "procesar_export", return_value={"r1": {}}), patch.object(storage, "reset", side_effect=RuntimeError("backup failed")), patch.object(storage, "upsert_all") as save:
            with self.assertRaisesRegex(RuntimeError, "backup failed"):
                pipeline.actualizar(None, "routes.xlsx", reset=True)
        save.assert_not_called()
