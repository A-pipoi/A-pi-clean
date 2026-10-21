import sqlite3
import tempfile
import unittest
from pathlib import Path

from quant.operations import backup_sqlite_databases, latest_backup_summary


class OperationsTests(unittest.TestCase):
    def test_sqlite_backup_preserves_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "research.sqlite3"
            backup_dir = root / "backups"
            connection = sqlite3.connect(source)
            try:
                connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
                connection.execute("INSERT INTO sample(value) VALUES ('real')")
                connection.commit()
            finally:
                connection.close()
            report = backup_sqlite_databases([source], backup_dir)
            self.assertTrue(report["ok"])
            backups = list(backup_dir.glob("research-*.sqlite3"))
            self.assertEqual(len(backups), 1)
            connection = sqlite3.connect(backups[0])
            try:
                row = connection.execute("SELECT value FROM sample").fetchone()
            finally:
                connection.close()
            self.assertEqual(row[0], "real")
            summary = latest_backup_summary(backup_dir)
            self.assertTrue(summary["available"])
            self.assertEqual(summary["file_count"], 1)
