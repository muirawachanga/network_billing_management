import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class ModelDocument(types.SimpleNamespace):
    def get_doc_before_save(self):
        return getattr(self, "_previous", None)

    def is_new(self):
        return not hasattr(self, "name")

    def get(self, key):
        return getattr(self, key, None)


class TestPackageModels(unittest.TestCase):
    def setUp(self):
        self.frappe = MagicMock()
        self.frappe.throw.side_effect = self._throw
        document_module = types.ModuleType("frappe.model.document")
        document_module.Document = ModelDocument
        utils = types.ModuleType("frappe.utils")
        utils.cint = int
        utils.flt = float
        configuration = types.ModuleType("network_billing_system.utils")
        configuration.load_configuration = lambda name: 1
        self.modules = {
            "frappe": self.frappe,
            "frappe.model": types.ModuleType("frappe.model"),
            "frappe.model.document": document_module,
            "frappe.utils": utils,
            "network_billing_system.utils": configuration,
        }
        self.root = Path(__file__).parents[1] / "network_billing_system/network_billing_system/doctype"

    @staticmethod
    def _throw(message):
        raise ValueError(message)

    def load_controller(self, name):
        path = self.root / name / (name + ".py")
        spec = importlib.util.spec_from_file_location("_tested_" + name, path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, self.modules):
            spec.loader.exec_module(module)
        return module

    def test_voucher_copies_package_duration(self):
        module = self.load_controller("captive_portal_code")
        self.frappe.db.get_value.return_value = 120
        voucher = module.CaptivePortalCode(
            code="  generated-voucher  ", package="time_2hrs", duration_minutes=None
        )
        voucher.validate()
        self.assertEqual(voucher.code, "generated-voucher")
        self.assertEqual(voucher.duration_minutes, 120)
        self.frappe.db.get_value.assert_called_once_with(
            "Internet Package Profile", "time_2hrs", "duration_minutes"
        )

    def test_new_vouchers_require_a_package_but_legacy_updates_do_not(self):
        module = self.load_controller("captive_portal_code")
        with self.assertRaisesRegex(ValueError, "Select the package"):
            module.CaptivePortalCode(code="new-code", package=None).validate()
        module.CaptivePortalCode(name="legacy-code", code="legacy-code", package=None).validate()

    def test_allocated_voucher_cannot_change_package_or_code(self):
        module = self.load_controller("captive_portal_code")
        previous = ModelDocument(
            code="sold-code", package="time_1hr", portal_checkout="checkout-1", duration_minutes=60
        )
        for field, value in (("code", "different"), ("package", "time_2hrs"), ("duration_minutes", 120)):
            voucher = module.CaptivePortalCode(
                name="voucher-1", code="sold-code", package="time_1hr",
                portal_checkout="checkout-1", duration_minutes=60, _previous=previous,
            )
            setattr(voucher, field, value)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "cannot be reassigned"):
                voucher.validate()

    def test_package_duration_cannot_change_after_voucher_import(self):
        module = self.load_controller("internet_package_profile")
        self.frappe.db.exists.return_value = True
        package = module.InternetPackageProfile(
            name="time_1hr", price=5, duration_minutes=120,
            _previous=ModelDocument(duration_minutes=60),
        )
        with self.assertRaisesRegex(ValueError, "Create a new package"):
            package.validate()
        package.duration_minutes = 60
        package.price = 10
        package.validate()

    def test_package_requires_positive_price_and_duration(self):
        module = self.load_controller("internet_package_profile")
        for price, minutes in ((0, 60), (-5, 60), (5, 0), (5, -60)):
            with self.subTest(price=price, minutes=minutes), self.assertRaises(ValueError):
                module.InternetPackageProfile(price=price, duration_minutes=minutes).validate()

    def test_legacy_sms_queries_cannot_consume_package_inventory(self):
        module = self.load_controller("captive_portal_code")
        self.frappe.get_list.return_value = [ModelDocument(code="actual-code")]
        self.assertEqual(module.get_captive_code(), "actual-code")
        args, kwargs = self.frappe.get_list.call_args
        self.assertEqual(args[1]["package"], ["is", "not set"])
        self.assertEqual(kwargs["fields"], ["code"])

    def test_sms_update_resolves_voucher_by_code_not_internal_name(self):
        module = self.load_controller("captive_portal_code")
        self.frappe.db.get_value.return_value = "internal-voucher-id"
        module.update_sent_code("actual-code", 200)
        self.frappe.get_doc.assert_called_once_with("Captive Portal Code", "internal-voucher-id")

    def test_legacy_sms_selection_is_preserved_when_package_portal_is_disabled(self):
        module = self.load_controller("captive_portal_code")
        module.load_configuration = lambda name: 0
        module.get_captive_code()
        args, kwargs = self.frappe.get_list.call_args
        self.assertNotIn("package", args[1])
