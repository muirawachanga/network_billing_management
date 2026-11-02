import types
import unittest
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from network_billing_system.recipient_rules import (
    RECIPIENT_FIELDS, destination_details, registration_request,
)
from network_billing_system.remittance_rules import transfer_payload


class TestRecipients(unittest.TestCase):
    def recipient(self, kind):
        return types.SimpleNamespace(
            destination_type=kind, recipient_name="Test Owner", name="remittance-1",
            zone="north", amount=100, source_identifier="source-1",
            first_name="Test", last_name="Owner", phone_number="0712345678",
            network="Safaricom", account_name="Owner", account_number="12345",
            bank_branch_ref="branch-1", settlement_method="provider-method",
            till_name="Test Till", till_number="123456", paybill_name="Test Paybill",
            paybill_number="234567", paybill_account_number="customer-1",
            provider_reference="a652f86f-f2aa-4d70-baa2-ccfe4b78f4fc",
        )

    def test_all_six_recipient_registration_contracts(self):
        for kind in RECIPIENT_FIELDS:
            with self.subTest(kind=kind):
                endpoint, payload = registration_request(self.recipient(kind))
                if kind.startswith("merchant_"):
                    self.assertIn(endpoint, ("merchant_wallets", "merchant_bank_accounts"))
                    self.assertEqual(payload["nickname"], "Test Owner")
                else:
                    self.assertEqual(endpoint, "external_recipients")
                    self.assertEqual(payload["type"], kind)
                    self.assertIn("external_recipient", payload)
        _, payload = registration_request(self.recipient("mobile_wallet"))
        self.assertEqual(payload["external_recipient"]["phone_number"], "254712345678")

    def test_all_six_send_money_destination_contracts(self):
        for kind in RECIPIENT_FIELDS:
            with self.subTest(kind=kind):
                doc = self.recipient(kind)
                destination = transfer_payload(doc, "https://example.test")["destinations"][0]
                self.assertEqual(destination["type"], kind)
                self.assertEqual(destination["amount"], "100")
                for field, value in destination_details(doc).items():
                    self.assertEqual(destination[field], value)
                if kind.startswith("merchant_"):
                    self.assertEqual(set(destination), {"type", "amount", "reference"})

    def test_incomplete_or_unknown_recipient_is_rejected(self):
        doc = self.recipient("paybill")
        doc.paybill_account_number = ""
        with self.assertRaises(ValueError):
            registration_request(doc)
        with self.assertRaises(ValueError):
            destination_details(doc)
        doc.destination_type = "international_bank"
        with self.assertRaises(ValueError):
            registration_request(doc)


class TestRecipientRegistration(unittest.TestCase):
    def setUp(self):
        self.frappe = MagicMock()
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.conf = {"enable_zone_remittance": True}
        def throw(message):
            raise ValueError(message)
        self.frappe.throw.side_effect = throw
        payout = types.ModuleType("network_billing_system.api.zone_remittance")
        payout._connector = MagicMock()
        self.connector = payout._connector.return_value
        self.connector.base_url = "https://sandbox.kopokopo.com"
        self.connector.authentication_token = "test-token"
        path = Path(__file__).parents[1] / "network_billing_system/api/remittance_recipient.py"
        spec = importlib.util.spec_from_file_location("_recipient_registration_test", path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frappe": self.frappe, payout.__name__: payout}):
            spec.loader.exec_module(self.module)
        self.doc = TestRecipients().recipient("till")
        self.doc.provider_environment = "sandbox"
        self.doc.registration_status = "Draft"
        self.doc.enabled = 1
        self.frappe.db.sql.return_value = [{"name": "recipient-1"}]
        self.frappe.get_doc.return_value = self.doc

    def test_registration_commits_intent_and_sends_till_contract(self):
        reference = "a652f86f-f2aa-4d70-baa2-ccfe4b78f4fc"
        response = MagicMock(status_code=201)
        response.headers = {"Location": "https://sandbox.kopokopo.com/api/v2/external_recipients/" + reference}
        def post(url, **kwargs):
            self.frappe.db.commit.assert_called_once()
            self.assertTrue(url.endswith("/api/v2/external_recipients"))
            self.assertEqual(kwargs["json"]["external_recipient"]["till_number"], "123456")
            return response
        with patch.object(self.module.requests, "post", side_effect=post):
            self.assertEqual(self.module.register_recipient("recipient-1")["status"], "Registered")
        self.frappe.db.set_value.assert_called_with("Remittance Recipient", self.doc.name, {
            "provider_reference": reference, "registration_status": "Registered"
        })

    def test_timeout_is_logged_and_not_retried(self):
        with patch.object(self.module.requests, "post", side_effect=self.module.requests.Timeout):
            result = self.module.register_recipient("recipient-1")
        self.assertEqual(result["status"], "Unknown")
        self.frappe.log_error.assert_called_once()
        self.doc.registration_status = "Unknown"
        with self.assertRaises(ValueError):
            self.module.register_recipient("recipient-1")

    def test_live_registration_is_blocked(self):
        self.doc.provider_environment = "live"
        with patch.object(self.module.requests, "post") as post, self.assertRaises(ValueError):
            self.module.register_recipient("recipient-1")
        post.assert_not_called()
