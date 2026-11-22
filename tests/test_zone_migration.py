import importlib.util
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestZoneMigration(unittest.TestCase):
    def test_assigns_only_unassigned_records_and_preserves_history_on_rerun(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        tables = (
            "Internet Package Profile", "Captive Portal Code",
            "Portal Checkout", "Import Captive Code",
        )
        for table in tables:
            connection.execute(
                "create table `tab{}` (name text, zone text, status text)".format(table)
            )
            connection.executemany(
                "insert into `tab{}` values (?, ?, ?)".format(table),
                [("old", None, "Inactive"), ("blank", "", "SUCCESS"), ("assigned", "north", "Issued")],
            )
        frappe = MagicMock()
        frappe.db.exists.return_value = False
        frappe.db.sql.side_effect = lambda query, values: connection.execute(
            query.replace("%s", "?"), values
        )
        path = Path(__file__).parents[1] / "network_billing_system/patches/assign_legacy_portal_zone.py"
        spec = importlib.util.spec_from_file_location("_zone_migration", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frappe": frappe}):
            spec.loader.exec_module(module)
        module.execute()
        frappe.db.exists.return_value = True
        module.execute()
        self.assertEqual(frappe.get_doc.call_count, 1)
        for table in tables:
            self.assertEqual(
                connection.execute("select * from `tab{}` order by name".format(table)).fetchall(),
                [("assigned", "north", "Issued"), ("blank", "default", "SUCCESS"), ("old", "default", "Inactive")],
            )
