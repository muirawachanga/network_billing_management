import importlib
import sys
import unittest
from types import ModuleType
from unittest.mock import Mock, patch


class RouterZoneSearchTest(unittest.TestCase):
    def test_search_includes_disabled_mikrotik_zones_but_not_other_backends(self):
        frappe = ModuleType("frappe")
        frappe.whitelist = lambda: lambda method: method
        frappe.validate_and_sanitize_search_inputs = lambda method: method
        document = ModuleType("frappe.model.document")
        document.Document = object
        zones = [
            {"name": "pilot", "display_name": "Wi-Fi Pilot", "backend": "MikroTik", "enabled": 0},
            {"name": "legacy", "display_name": "Legacy Pilot", "backend": "pfSense", "enabled": 1},
        ]

        def permitted_list(doctype, *, fields, filters, or_filters, as_list, **kwargs):
            self.assertEqual(doctype, "Captive Portal Zone")
            return [
                tuple(zone[field] for field in fields) if as_list else {field: zone[field] for field in fields}
                for zone in zones
                if all(zone[key] == value for key, value in filters.items())
                and any(pattern.strip("%") in zone[field] for field, _, pattern in or_filters)
            ]

        frappe.get_list = Mock(side_effect=permitted_list)
        module_path = (
            "network_billing_system.network_billing_system.doctype.mikrotik_router.mikrotik_router"
        )
        with patch.dict(sys.modules, {
            "frappe": frappe, "frappe.model": ModuleType("frappe.model"), "frappe.model.document": document,
        }):
            search = importlib.import_module(module_path).search_zones
            args = ("Captive Portal Zone", "Pilot", "name", 0, 10, {})
            self.assertEqual(search(*args), [("pilot", "Wi-Fi Pilot")])
            self.assertEqual(search(*args, as_dict=True), [{"name": "pilot", "display_name": "Wi-Fi Pilot"}])
