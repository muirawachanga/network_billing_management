import ast
import json
import unittest
from pathlib import Path


class TestTransferMetadata(unittest.TestCase):
    def test_every_transfer_doctype_including_children_has_a_frappe_controller(self):
        root = Path(__file__).parents[1] / "network_billing_system/network_billing_system/doctype"
        paths = list(root.glob("transfer_*/*.json"))
        paths += [root / "zone_finance_entry/zone_finance_entry.json",
                  root / "zone_remittance_policy/zone_remittance_policy.json"]
        self.assertEqual(len(paths), 14)
        for path in paths:
            with self.subTest(doctype=path.parent.name):
                schema = json.loads(path.read_text())
                controller = path.with_suffix(".py")
                self.assertTrue(controller.is_file(), "Frappe requires a controller even for child tables.")
                classes = [node.name for node in ast.parse(controller.read_text()).body if isinstance(node, ast.ClassDef)]
                self.assertIn(schema["name"].replace(" ", ""), classes)
                self.assertTrue((path.parent / "__init__.py").is_file())
                fields = [field["fieldname"] for field in schema["fields"]]
                self.assertEqual(len(fields), len(set(fields)))
                self.assertEqual(set(schema["field_order"]), set(fields))
                if schema.get("istable"):
                    self.assertFalse(schema["permissions"])

    def test_accounting_evidence_is_not_writable_through_standard_documents(self):
        root = Path(__file__).parents[1] / "network_billing_system/network_billing_system/doctype"
        for name in ("zone_finance_entry", "transfer_batch", "transfer_attempt", "transfer_disbursement"):
            schema = json.loads((root / name / (name + ".json")).read_text())
            for permission in schema["permissions"]:
                self.assertFalse(permission.get("create"))
                self.assertFalse(permission.get("write"))
                self.assertFalse(permission.get("delete"))
