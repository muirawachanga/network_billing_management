import importlib.util
import json
import sys
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch


class Doc(dict):
    def __getattr__(self, field):
        return self.get(field)

    def __setattr__(self, field, value):
        self[field] = value


def fail(message, *args):
    raise ValueError(message)


class TestTransferEngine(unittest.TestCase):
    def setUp(self):
        self.frappe = MagicMock()
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.throw.side_effect = fail
        self.frappe.session.user = "approver@example.test"
        self.frappe.conf = {"encryption_key": "test-key"}
        self.frappe.ValidationError = ValueError
        self.frappe.db.get_value.return_value = "north"
        utils = types.ModuleType("frappe.utils")
        utils.now_datetime = lambda: "2026-11-02 08:00:00"
        utils.get_url = lambda path: "https://example.test" + path
        finance = types.ModuleType("network_billing_system.zone_finance")
        finance.money = lambda value: Decimal(str(value or 0))
        finance.lock_zone = MagicMock()
        finance.ensure_funds = MagicMock()
        path = Path(__file__).parents[1] / "network_billing_system/transfer_engine.py"
        spec = importlib.util.spec_from_file_location("_transfer_engine_test", path)
        self.engine = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils,
                                     "network_billing_system.zone_finance": finance}):
            spec.loader.exec_module(self.engine)
        self.config = MagicMock()
        self.engine.configuration = lambda: self.config
        self.settings = Doc(enabled=1, allow_live=0, approval_threshold=100, max_request_amount=100000)
        self.provider = Doc(name="provider-1", environment="sandbox", source_identifier="4321")
        self.config.get_settings.return_value = self.settings
        self.config.get_provider.return_value = self.provider
        self.request = Doc(
            name="request-1", zone="north", provider_account="provider-1", owner="maker@example.test",
            docstatus=1, status="Awaiting Approval", amount=1000, fee_bearer="Zone",
            recipient_amount=1000, estimated_fee=50, total_debit=1050, recipient_account="recipient-1",
            fee_policy="fee-1", approved_by=None,
        )
        self.snapshot = {
            "destination": {"type": "mobile_wallet", "phone_number": "254712345678", "network": "Safaricom"},
            "environment": "sandbox", "source_identifier": "4321",
        }
        self.request.snapshot = json.dumps(self.snapshot)
        self.batch = Doc(name="batch-1", status="Ready", provider_account="provider-1",
                         requests=[Doc(transfer_request="request-1")], provider_id=None)
        self.engine.lock_doc = MagicMock(return_value=self.request)
        self.engine.lock_batch = MagicMock(return_value=self.batch)
        self.engine.post_entry = MagicMock()
        self.frappe.get_doc.return_value = self.request
        self.engine.internal_insert = MagicMock(return_value=Doc(name="attempt-1"))

    def test_approval_is_separate_from_preparer_and_reserves_total_under_lock(self):
        self.engine.quote_request = MagicMock()
        self.engine.policy_for = MagicMock(return_value=Doc(retained_balance=20))
        self.engine.approve_request(self.request.name)
        self.engine.lock_zone.assert_called_once_with("north")
        self.engine.ensure_funds.assert_called_once_with("north", "sandbox", Decimal(1070))
        self.assertEqual(self.engine.post_entry.call_args.kwargs["reserved"], Decimal(1050))
        self.frappe.only_for.assert_called_once_with(("System Manager", "Transfer Approver"))

    def test_self_approval_above_threshold_is_denied(self):
        self.request.owner = self.frappe.session.user
        with self.assertRaisesRegex(ValueError, "different user"):
            self.engine.approve_request(self.request.name)
        self.engine.post_entry.assert_not_called()

    def test_configuration_changes_invalidate_approval(self):
        self.engine.quote_request = lambda doc: setattr(doc, "snapshot", "{}")
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            self.engine.approve_request(self.request.name)

    def test_cancel_only_unbatched_requests_releases_reservation(self):
        self.request.status = "Approved"
        self.engine.cancel_request(self.request)
        self.assertEqual(self.engine.post_entry.call_args.kwargs["reserved"], Decimal(-1050))
        self.request.status = "Sending"
        with self.assertRaises(ValueError):
            self.engine.cancel_request(self.request)

    def test_send_persists_intent_before_post_and_will_not_retry(self):
        self.frappe.db.exists.return_value = False
        self.frappe.db.get_value.return_value = "Sending"
        self.config.recipient_snapshot.return_value = self.snapshot
        connector = self.config.provider_connector.return_value
        connector.base_url = "https://sandbox.kopokopo.com"
        connector.authentication_token = "test-token"
        response = MagicMock(status_code=201)
        response.headers = {"Location": connector.base_url + "/api/v2/send_money/716481bc-635c-4d35-92c9-65bb0797196a"}
        def post(url, **kwargs):
            self.frappe.db.commit.assert_called_once()
            self.assertIsInstance(kwargs["json"]["destinations"][0]["amount"], int)
            self.assertIn("token=", kwargs["json"]["_links"]["callback_url"])
            self.assertFalse(kwargs["allow_redirects"])
            return response
        with patch.object(self.engine.requests, "post", side_effect=post) as sending:
            self.assertEqual(self.engine.send_batch("batch-1")["status"], "Pending")
            self.frappe.db.exists.return_value = True
            with self.assertRaisesRegex(ValueError, "attempt already"):
                self.engine.send_batch("batch-1")
            sending.assert_called_once()

    def test_timeout_keeps_reservations_and_marks_review(self):
        self.frappe.db.exists.return_value = False
        self.config.recipient_snapshot.return_value = self.snapshot
        self.config.provider_connector.return_value.base_url = "https://sandbox.kopokopo.com"
        self.config.provider_connector.return_value.authentication_token = "test"
        with patch.object(self.engine.requests, "post", side_effect=self.engine.requests.Timeout):
            result = self.engine.send_batch("batch-1")
        self.assertEqual(result["status"], "Needs Review")
        self.engine.post_entry.assert_not_called()
        self.frappe.log_error.assert_called_once()

    def test_live_requires_explicit_gate(self):
        self.provider.environment = "live"
        with self.assertRaisesRegex(ValueError, "Live transfers"):
            self.engine.send_batch("batch-1")
        self.config.provider_connector.assert_not_called()

    def test_principal_settlement_keeps_fee_reservation_until_reconciled(self):
        result = {"status": "Paid", "amount": "1000", "destination_reference": "account-ref",
                  "transaction_reference": "TESTREFERENCE"}
        self.request.status = "Pending"
        self.engine.settle_request(self.request, result, self.batch)
        posting = self.engine.post_entry.call_args.kwargs
        self.assertEqual(posting["debit"], Decimal(1000))
        self.assertEqual(posting["reserved"], Decimal(-1000))
        self.request.status = "Paid"
        self.engine.post_entry.reset_mock()
        self.engine.settle_request(self.request, result, self.batch)
        self.engine.post_entry.assert_not_called()

    def test_failed_principal_release_does_not_assume_zero_provider_fees(self):
        self.request.status = "Pending"
        self.engine.settle_request(self.request, {
            "status": "Failed", "amount": "1000", "destination_reference": None, "transaction_reference": None,
        }, self.batch)
        self.assertEqual(self.engine.post_entry.call_args.kwargs["reserved"], Decimal(-1000))
        self.assertNotIn("debit", self.engine.post_entry.call_args.kwargs)

    def test_invalid_callback_capability_never_queries_provider(self):
        self.engine.reconcile_batch = MagicMock()
        portal = types.ModuleType("network_billing_system.api.captive_portal")
        portal._request_limit = MagicMock()
        with patch.dict(sys.modules, {portal.__name__: portal}), self.assertRaises(ValueError):
            self.engine.transfer_callback("batch-1", "invalid")
        self.engine.reconcile_batch.assert_not_called()

    def test_recipient_fee_bearer_solves_exact_budget(self):
        self.engine.policy_for = MagicMock(return_value=Doc(fee_policy="fee-1", maximum_amount=0))
        self.config.recipient_snapshot.return_value = dict(self.snapshot)
        fee = Doc(name="fee-1", destination_type="mobile_wallet")
        self.frappe.get_doc.return_value = fee
        self.config.quote_fee.side_effect = lambda name, value: Decimal(50)
        self.request.fee_bearer = "Recipient"
        self.engine.quote_request(self.request)
        self.assertEqual(self.request.recipient_amount, Decimal(950))
        self.assertEqual(self.request.total_debit, Decimal(1000))

    def test_ready_batch_can_be_unbatched_without_releasing_reservations(self):
        self.frappe.db.exists.return_value = False
        self.request.status = "Batched"
        self.request.batch = self.batch.name
        self.assertEqual(self.engine.unbatch_requests(self.batch.name)["status"], "Cancelled")
        self.frappe.db.set_value.assert_any_call(
            "Transfer Request", self.request.name, {"status": "Approved", "batch": None}
        )
        self.engine.post_entry.assert_not_called()
        self.frappe.db.exists.return_value = True
        with self.assertRaises(ValueError):
            self.engine.unbatch_requests(self.batch.name)

    def test_late_timeout_after_callback_never_overwrites_completed_batch(self):
        self.batch.status = "Completed"
        result = self.engine._review(self.batch, "Late timeout after confirmed settlement.")
        self.assertEqual(result["status"], "Completed")
        for call in self.frappe.db.set_value.call_args_list:
            self.assertNotEqual(call.args[0], "Transfer Request")
        self.engine.post_entry.assert_not_called()

    def test_review_uses_locked_current_status_not_an_earlier_database_snapshot(self):
        self.batch.status = "Pending"
        self.request.status = "Paid"
        self.frappe.db.get_value.return_value = "Sending"
        self.engine._review(self.batch, "One other disbursement needs review.")
        for call in self.frappe.db.set_value.call_args_list:
            self.assertNotEqual(call.args[0], "Transfer Request")

    def test_stale_pending_object_cannot_repost_an_already_settled_request(self):
        self.request.status = "Paid"
        self.engine.settle_request(Doc(name="request-1", status="Pending"), {
            "status": "Paid", "amount": "1000",
        }, self.batch)
        self.engine.internal_insert.assert_not_called()
        self.engine.post_entry.assert_not_called()

    def test_submit_requotes_and_discards_forged_accounting_state(self):
        document = types.ModuleType("frappe.model.document")
        document.Document = object
        path = Path(__file__).parents[1] / "network_billing_system/network_billing_system/doctype/transfer_request/transfer_request.py"
        spec = importlib.util.spec_from_file_location("_transfer_request_controller_test", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frappe": self.frappe, document.__name__: document,
                                     "network_billing_system.transfer_engine": self.engine}):
            spec.loader.exec_module(module)
        doc = module.TransferRequest()
        doc.status, doc.fee_reconciled, doc.actual_fee = "Paid", 1, 999
        with patch.object(module, "transfer_engine", self.engine), patch.object(self.engine, "quote_request") as quote:
            doc.before_submit()
        quote.assert_called_once_with(doc)
        self.assertEqual(doc.status, "Awaiting Approval")
        self.assertEqual(doc.fee_reconciled, 0)
        self.assertEqual(doc.actual_fee, 0)
        self.assertIsNone(doc.approved_by)

    def test_reconciliation_checks_all_outcomes_before_posting_any_settlement(self):
        self.batch.status, self.batch.provider_id = "Pending", "716481bc-635c-4d35-92c9-65bb0797196a"
        self.request.status = "Paid"
        response = MagicMock(status_code=200)
        self.engine.settle_request = MagicMock()
        self.config.provider_connector.return_value.base_url = "https://sandbox.kopokopo.com"
        self.config.provider_connector.return_value.authentication_token = "test"
        results = [{"request": self.request.name, "status": "Failed"}]
        with patch.object(self.engine.requests, "get", return_value=response), \
                patch.object(self.engine, "verified_results", return_value=results):
            self.assertEqual(self.engine.reconcile_batch(self.batch.name)["status"], "Needs Review")
        self.engine.settle_request.assert_not_called()
