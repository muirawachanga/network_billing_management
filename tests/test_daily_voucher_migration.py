import configparser
import importlib.util
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestDailyVoucherMigration(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.execute(
            """create table `tabCaptive Portal Code` (
                name text, code text, expiry_duration text, package text,
                duration_minutes integer, portal_checkout text, status text, sms_sent integer
            )"""
        )
        self.frappe = MagicMock()
        self.package = None
        self.frappe.db.exists.side_effect = self.exists
        self.frappe.db.get_value.side_effect = lambda *args: self.package["duration_minutes"]
        self.frappe.db.sql.side_effect = self.sql
        self.frappe.get_doc.side_effect = self.document
        self.frappe.throw.side_effect = self.throw
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        path = Path(__file__).parents[1] / (
            "network_billing_system/patches/migrate_daily_vouchers_to_eight_hours.py"
        )
        spec = importlib.util.spec_from_file_location("_tested_daily_migration", path)
        self.migration = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils}):
            spec.loader.exec_module(self.migration)

    @staticmethod
    def throw(message):
        raise ValueError(message)

    def exists(self, doctype, filters):
        if doctype == "Internet Package Profile":
            return self.package is not None
        self.assertEqual(filters, {
            "expiry_duration": "Daily", "package": ["is", "not set"],
            "portal_checkout": ["is", "not set"],
        })
        return self.connection.execute(
            """select name from `tabCaptive Portal Code`
               where expiry_duration='Daily' and ifnull(package, '')=''
               and ifnull(portal_checkout, '')='' limit 1"""
        ).fetchone()

    def document(self, data):
        self.package = data.copy()
        doc = MagicMock()
        doc.insert.return_value = doc
        return doc

    def sql(self, statement, parameters):
        return self.connection.execute(statement.replace("%s", "?"), parameters)

    def add_voucher(self, name, duration="Daily", package=None, checkout=None, status="Active", sent=0):
        self.connection.execute(
            "insert into `tabCaptive Portal Code` values (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, "code-" + name, duration, package, 0, checkout, status, sent),
        )

    def rows(self):
        return self.connection.execute(
            "select * from `tabCaptive Portal Code` order by name"
        ).fetchall()

    def test_maps_daily_vouchers_without_reactivating_used_codes(self):
        self.add_voucher("available")
        self.add_voucher("sold", status="Inactive", sent=1)
        before = self.rows()
        self.migration.execute()
        self.assertEqual(self.package["package_name"], "time_8hrs")
        self.assertEqual(self.package["duration_minutes"], 480)
        self.assertEqual(self.package["price"], 30)
        self.assertEqual(self.package["enabled"], 0)
        for old, new in zip(before, self.rows()):
            self.assertEqual(new, old[:3] + ("time_8hrs", 480) + old[5:])

    def test_preserves_monthly_unknown_assigned_and_allocated_vouchers(self):
        self.add_voucher("monthly", duration="Monthly")
        self.add_voucher("unknown", duration=None)
        self.add_voucher("assigned", package="custom-package")
        self.add_voucher("allocated", checkout="checkout-1")
        before = self.rows()
        self.migration.execute()
        self.assertEqual(self.rows(), before)
        self.frappe.get_doc.assert_not_called()

    def test_reuses_existing_package_without_changing_price_or_availability(self):
        self.add_voucher("daily", package="")
        self.package = {"duration_minutes": 480, "price": 45, "enabled": 1}
        self.migration.execute()
        self.assertEqual(self.package, {"duration_minutes": 480, "price": 45, "enabled": 1})
        self.frappe.get_doc.assert_not_called()
        self.assertEqual(self.rows()[0][3:5], ("time_8hrs", 480))

    def test_conflicting_package_duration_stops_migration(self):
        self.add_voucher("daily")
        self.package = {"duration_minutes": 1440}
        before = self.rows()
        with self.assertRaisesRegex(ValueError, "480 minutes"):
            self.migration.execute()
        self.assertEqual(self.rows(), before)
        self.frappe.db.sql.assert_not_called()

    def test_rerun_does_not_duplicate_package_or_reset_vouchers(self):
        self.add_voucher("daily")
        self.migration.execute()
        before = self.rows()
        self.migration.execute()
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.frappe.get_doc.call_count, 1)

    def test_patch_registered_after_model_sync(self):
        patches = (Path(__file__).parents[1] / "network_billing_system/patches.txt").read_text()
        parser = configparser.ConfigParser(allow_no_value=True)
        parser.read_string(patches)
        self.assertTrue(parser.has_section("pre_model_sync"))
        self.assertEqual(list(parser["pre_model_sync"]), [])
        self.assertEqual(
            list(parser["post_model_sync"]),
            [
                "network_billing_system.patches.assign_legacy_portal_zone",
                "network_billing_system.patches.migrate_daily_vouchers_to_eight_hours",
            ],
        )
