import importlib.util
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.test_captive_portal_api import Document


class MikroTikAccessTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 22, 8)
        self.frappe = MagicMock(ValidationError=ValueError)
        self.frappe.get_doc.side_effect = lambda doc, *args: Document(doc)
        self.frappe.db.get_value.return_value = None
        utils = types.ModuleType("frappe.utils")
        utils.now_datetime = lambda: self.now
        spec = importlib.util.spec_from_file_location(
            "_test_mikrotik_access", Path(__file__).parents[1] / "network_billing_system/mikrotik_access.py"
        )
        self.access = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils}):
            spec.loader.exec_module(self.access)
        self.checkout = Document(name="paid-order", backend="MikroTik", status="SUCCESS",
                                 duration_minutes=5, mikrotik_router="router-1")

    def test_issuance_is_durable_before_enqueue_and_reused_on_repeat(self):
        issued = self.access.create_issuance(self.checkout)
        self.assertEqual(issued.state, "Pending")
        self.assertEqual(issued.duration_seconds, 300)
        self.assertEqual(issued.issuance_key, "purchase:paid-order")
        self.assertNotIn(issued.password, issued.fingerprint)
        self.frappe.enqueue.assert_not_called()
        callback = self.frappe.db.after_commit.add.call_args.args[0]
        callback()
        self.frappe.enqueue.assert_called_once_with(self.access.provision, issuance_name=issued.name)
        self.frappe.db.get_value.return_value = issued.name
        self.frappe.get_doc.side_effect = None
        self.frappe.get_doc.return_value = issued
        self.assertIs(self.access.create_issuance(self.checkout), issued)
        self.frappe.db.after_commit.add.assert_called_once()

    def test_ambiguous_confirmation_retries_same_identity_with_backoff(self):
        from network_billing_system import routeros
        issuance = MagicMock(state="Pending", next_attempt_at=self.now, attempts=0,
                             username="nb-test", duration_seconds=300)
        issuance.get_password.return_value = "test-password"
        issuance.predecessor = None
        self.access.lock_issuance = MagicMock(return_value=issuance)
        self.access.policy_for = MagicMock(return_value={})
        client = MagicMock()
        client.ensure_user.side_effect = [routeros.RouterError("unconfirmed"), None]
        with patch.object(routeros, "connect") as connect:
            connect.return_value.__enter__.return_value = client
            self.access.provision("record")
            retry_at = self.frappe.db.set_value.call_args.args[-1]["next_attempt_at"]
            self.assertEqual(retry_at, self.now + timedelta(seconds=30))
            issuance.next_attempt_at = retry_at
            self.access.provision("record")
            client.ensure_user.assert_called_once()
            self.now = retry_at
            self.access.provision("record")
        self.assertEqual(client.ensure_user.call_args_list[0], client.ensure_user.call_args_list[1])
        self.assertEqual(self.frappe.db.set_value.call_args.args[-1]["state"], "Ready")

    def test_only_ready_credential_is_returned_and_only_with_proof(self):
        self.frappe.db.get_value.return_value = "record"
        issuance = MagicMock(state="Pending", username="nb-test")
        issuance.get_password.return_value = "test-password"
        self.access.lock_issuance = MagicMock(return_value=issuance)
        for state, expected in (("Pending", "PROVISIONING"), ("Ready", "READY"),
                                ("Expired", "EXPIRED"), ("Replaced", "REPLACED")):
            issuance.state = state
            result = self.access.access_response(self.checkout, {}, reveal=True)
            self.assertEqual(result["access_state"], expected)
            self.assertEqual("voucher" in result, state == "Ready")
        issuance.state = "Ready"
        self.assertNotIn("voucher", self.access.access_response(self.checkout, {}, reveal=False))


if __name__ == "__main__":
    unittest.main()
