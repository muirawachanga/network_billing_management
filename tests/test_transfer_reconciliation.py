import importlib.util
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


def fail(message):
    raise ValueError(message)


class TestTransferReconciliation(unittest.TestCase):
    def setUp(self):
        self.frappe = MagicMock()
        self.frappe.throw.side_effect = fail
        self.frappe.db.exists.return_value = False
        self.frappe.session.user = "reviewer@example.test"
        self.provider = Doc(name="provider-1", environment="live")
        self.config = MagicMock()
        self.config.get_provider.return_value = self.provider
        self.config.get_settings.return_value = Doc(approval_threshold=100)
        self.engine = types.ModuleType("network_billing_system.transfer_engine")
        self.engine.authorize = MagicMock()
        self.engine.configuration = lambda: self.config
        self.engine.lock_doc = MagicMock()
        self.engine.post_entry = MagicMock()
        self.engine.request_snapshot = lambda doc: {"payout_reference": "account-ref"}
        self.engine.settle_request = MagicMock()
        finance = types.ModuleType("network_billing_system.zone_finance")
        finance.lock_zone = MagicMock()
        finance.money = lambda value: Decimal(str(value or 0))
        path = Path(__file__).parents[1] / "network_billing_system/transfer_reconciliation.py"
        spec = importlib.util.spec_from_file_location("_transfer_reconciliation_test", path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frappe": self.frappe, self.engine.__name__: self.engine,
                                     finance.__name__: finance}):
            spec.loader.exec_module(self.module)
        self.doc = Doc(
            name="reconciliation-1", owner="maker@example.test", provider_account="provider-1",
            reconciliation_type="External Withdrawal", verified=1,
            evidence="/private/files/provider-statement.pdf", total_amount=100,
            provider_reference="TESTREF1", provider_transfer_id=None,
            note="Verified against provider statement",
            allocations=[Doc(zone="north", amount=100, transfer_request=None, finance_entry=None)],
        )

    def test_external_reconciliation_exact_allocations_and_no_second_transfer(self):
        self.module.validate_reconciliation(self.doc)
        self.module.post_reconciliation(self.doc)
        self.engine.post_entry.assert_called_once()
        self.assertEqual(self.engine.post_entry.call_args.kwargs["debit"], Decimal(100))
        self.engine.settle_request.assert_not_called()

    def test_allocations_must_equal_provider_total_and_evidence_is_private(self):
        self.doc.allocations[0].amount = 99
        with self.assertRaisesRegex(ValueError, "exactly equal"):
            self.module.validate_reconciliation(self.doc)
        self.doc.allocations[0].amount = 100
        self.frappe.db.get_value.return_value = None
        with self.assertRaisesRegex(ValueError, "private"):
            self.module.validate_reconciliation(self.doc)

    def test_self_review_above_threshold_is_prohibited(self):
        self.doc.owner = self.frappe.session.user
        with self.assertRaisesRegex(ValueError, "different reconciler"):
            self.module.post_reconciliation(self.doc)
        self.engine.post_entry.assert_not_called()

    def test_duplicate_reference_is_environment_scoped_and_case_insensitive(self):
        self.module.validate_reconciliation(self.doc)
        key = self.doc.posting_key
        self.doc.provider_reference = "testref1"
        self.module.validate_reconciliation(self.doc)
        self.assertEqual(key, self.doc.posting_key)
        self.provider.environment = "sandbox"
        self.module.validate_reconciliation(self.doc)
        self.assertNotEqual(key, self.doc.posting_key)

    def test_external_uuid_reference_also_uses_unique_provider_id_key(self):
        self.doc.provider_reference = "716481bc-635c-4d35-92c9-65bb0797196a"
        self.module.validate_reconciliation(self.doc)
        self.assertEqual(self.doc.provider_transfer_id, self.doc.provider_reference)
        key = self.doc.external_transfer_key
        self.doc.provider_reference = "ANOTHERREF"
        self.module.validate_reconciliation(self.doc)
        self.assertEqual(self.doc.external_transfer_key, key)

    def test_actual_fee_posts_actual_debit_and_releases_estimate(self):
        self.doc.reconciliation_type = "Actual Fee"
        self.doc.total_amount = self.doc.allocations[0].amount = 40
        self.doc.allocations[0].transfer_request = "request-1"
        request = Doc(name="request-1", docstatus=1, status="Paid", fee_reconciled=0, zone="north",
                      provider_account="provider-1", estimated_fee=50)
        self.engine.lock_doc.return_value = request
        self.module.post_reconciliation(self.doc)
        self.assertEqual(self.engine.post_entry.call_args.kwargs["debit"], Decimal(40))
        self.assertEqual(self.engine.post_entry.call_args.kwargs["reserved"], Decimal(-50))
        request.fee_reconciled = 1
        with self.assertRaises(ValueError):
            self.module.post_reconciliation(self.doc)

    def test_explicit_zero_fee_can_release_failed_transfer_fee_reserve(self):
        self.doc.reconciliation_type = "Actual Fee"
        self.doc.total_amount = self.doc.allocations[0].amount = 0
        self.doc.allocations[0].transfer_request = "request-1"
        self.engine.lock_doc.return_value = Doc(
            name="request-1", docstatus=1, status="Failed", fee_reconciled=0, zone="north",
            provider_account="provider-1", estimated_fee=50,
        )
        self.module.post_reconciliation(self.doc)
        self.assertEqual(self.engine.post_entry.call_args.kwargs["debit"], 0)
        self.assertEqual(self.engine.post_entry.call_args.kwargs["reserved"], -50)

    def test_reversal_is_exact_append_only_debit_not_reservation(self):
        self.doc.reconciliation_type = "Reversal"
        self.doc.allocations[0].finance_entry = "entry-1"
        self.engine.lock_doc.return_value = Doc(name="entry-1", zone="north", environment="live", debit=100, reserved=-100)
        self.module.post_reconciliation(self.doc)
        self.assertEqual(self.engine.post_entry.call_args.kwargs["debit"], -100)
        self.assertEqual(self.engine.post_entry.call_args.kwargs["reverses"], "entry-1")
        self.assertNotIn("reserved", self.engine.post_entry.call_args.kwargs)

    def test_evidenced_outcome_completes_only_fully_settled_batch(self):
        self.doc.reconciliation_type = "Transfer Outcome"
        self.doc.provider_transfer_id = "716481bc-635c-4d35-92c9-65bb0797196a"
        self.doc.allocations[0].transfer_request = "request-1"
        self.doc.allocations[0].outcome = "Paid"
        request = Doc(name="request-1", docstatus=1, status="Needs Review", zone="north",
                      provider_account="provider-1", batch="batch-1", recipient_amount=100)
        batch = Doc(name="batch-1", provider_id=None)
        self.engine.lock_doc.side_effect = lambda doctype, name: request if doctype == "Transfer Request" else batch
        self.frappe.get_all.side_effect = [["batch-1"], ["north"], ["request-1"]]
        self.engine.settle_request.side_effect = lambda doc, result, batch: setattr(doc, "status", result["status"])
        self.module.post_reconciliation(self.doc)
        self.engine.settle_request.assert_called_once()
        self.frappe.db.set_value.assert_any_call(
            "Transfer Batch", "batch-1", {"status": "Completed", "last_error": ""}
        )

    def test_external_withdrawal_cannot_duplicate_provider_transaction(self):
        self.frappe.db.exists.return_value = True
        with self.assertRaises(ValueError):
            self.module.post_reconciliation(self.doc)
        self.engine.post_entry.assert_not_called()
