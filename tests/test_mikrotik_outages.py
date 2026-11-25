import importlib.util
import sys
import types
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import network_billing_system


class Doc(dict):
    def __getattr__(self, key):
        return self.get(key)


class RouterError(Exception):
    pass


class TestMikroTikOutages(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 22, 8, 25)
        self.frappe = MagicMock()
        self.frappe.ValidationError = ValueError
        self.frappe.throw.side_effect = self.throw
        self.frappe.whitelist.return_value = lambda fn: fn
        self.frappe.db.exists.return_value = None
        self.frappe.db.get_value.side_effect = self.get_value
        self.frappe.get_doc.side_effect = self.get_doc
        self.frappe.db.set_value.side_effect = self.set_value
        self.access = MagicMock()
        self.access.customer_code.return_value = "2345-6789-ABCD-EFGH-JKLM-NPQR"
        self.routeros = MagicMock(RouterError=RouterError)
        self.client = MagicMock()
        self.client.revoke_user.return_value = None
        self.client.health.return_value = 2000
        self.routeros.connect.return_value = nullcontext(self.client)
        utils = types.ModuleType("frappe.utils")
        utils.now_datetime = lambda: self.now
        utils.get_datetime = lambda value: value
        path = Path(__file__).resolve().parents[1] / "network_billing_system/mikrotik_outages.py"
        spec = importlib.util.spec_from_file_location("mikrotik_outages_tests", path)
        self.worker = importlib.util.module_from_spec(spec)
        with (
            patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils}),
            patch.object(network_billing_system, "mikrotik_access", self.access, create=True),
            patch.object(network_billing_system, "routeros", self.routeros, create=True),
        ):
            spec.loader.exec_module(self.worker)
        w = self.worker
        policy = dict(host="router.example", port=8729, router_identity="r", server="hs", transport="TLS")
        self.access.policy_for.return_value = policy
        self.reference = Doc(
            doctype=w.SAMPLE, name="baseline", uptime_seconds=1880,
            observed_at=self.now - timedelta(minutes=2), target_fingerprint=w._target(policy),
            status="Reachable",
        )
        self.incident = Doc(
            doctype=w.INCIDENT, name="incident", router="router", classification="Continuous uptime",
            status="Restored", baseline_sample="baseline", impact_verified=True,
            started_at=self.now - timedelta(minutes=1),
        )
        self.issuance = Doc(
            doctype=w.ISSUANCE, name="issuance", router="router", username="username",
            checkout="checkout", state="Ready", duration_seconds=600,
            creation=self.now - timedelta(minutes=5), get_password=lambda field: "test-password",
        )
        self.evidence = Doc(
            doctype=w.SAMPLE, kind="Account", name="sample", incident=self.incident.name,
            issuance=self.issuance.name, status="Approved", router="router", active_at_onset=True,
            used_seconds=100, candidate_seconds=500,
        )
        self.outbox = Doc(
            doctype=w.OUTBOX, name="outbox", issuance="issuance", status="Pending",
            idempotency_key="stable-key",
        )
        self.checkout = Doc(doctype="Portal Checkout", name="checkout", mobile_number="phone")
        self.router = Doc(pilot_verified=True, automatic_compensation=False)
        self.docs = {
            (doc.doctype, doc.name): doc
            for doc in (self.reference, self.incident, self.evidence, self.issuance, self.outbox, self.checkout)
        }
        self.access.lock_issuance.return_value = self.issuance
        self.access.create_issuance.return_value = Doc(name="replacement")
        self.worker._insert = MagicMock()
        self.client.account.side_effect = [
            {"used_seconds": 125, "active": True}, {"used_seconds": 150, "active": False},
        ]

    def get_doc(self, doctype, name, **kwargs):
        return self.docs[(doctype, name)]

    def get_value(self, doctype, name, fields, **kwargs):
        if fields == "name":
            return name
        if doctype == self.worker.INCIDENT:
            return self.incident.impact_verified
        if doctype == self.worker.ROUTER:
            return self.router
        if doctype == "Portal Checkout":
            return self.checkout.mobile_number
        return self.reference

    def set_value(self, doctype, name, fields, value=None):
        self.docs[(doctype, name)].update(fields if isinstance(fields, dict) else {fields: value})

    @staticmethod
    def throw(message):
        raise ValueError(message)

    def test_replacement_hides_old_code_before_revoke_and_uses_final_usage(self):
        def revoke(username, owner):
            self.assertEqual(self.issuance.state, "Replacing")
            self.frappe.db.commit.assert_called_once()
            self.access.create_issuance.assert_not_called()

        self.client.revoke_user.side_effect = revoke
        self.worker._replace("sample")
        self.access.create_issuance.assert_called_once_with(
            self.checkout, seconds=450, predecessor="issuance", incident="incident",
        )
        self.assertEqual(self.issuance.state, "Replaced")
        self.assertEqual(self.evidence.credited_seconds, 450)
        self.worker._replace("sample")
        self.client.revoke_user.assert_called_once()

    def test_retry_after_failed_revocation_never_creates_until_confirmed(self):
        self.client.revoke_user.side_effect = [RouterError(), None]
        with self.assertRaises(RouterError):
            self.worker._replace("sample")
        self.assertEqual(self.issuance.state, "Replacing")
        self.access.create_issuance.assert_not_called()
        self.worker._replace("sample")
        self.access.create_issuance.assert_called_once()

    def test_decreased_post_revoke_counter_never_credits(self):
        self.client.account.side_effect = [
            {"used_seconds": 125, "active": True}, {"used_seconds": 124, "active": False},
        ]
        self.worker._replace("sample")
        self.client.revoke_user.assert_called_once()
        self.access.create_issuance.assert_not_called()
        self.assertEqual(self.evidence.status, "Needs Review")

    def test_automation_requires_flag_activity_and_corroborated_service_impact(self):
        self.evidence.update(status="Review")
        for enabled, impact, active in [(False, True, True), (True, False, True), (True, True, False)]:
            self.router.update(automatic_compensation=enabled)
            self.incident.update(impact_verified=impact)
            self.evidence.update(active_at_onset=active)
            self.worker._replace("sample")
        self.client.revoke_user.assert_not_called()
        self.assertEqual(self.issuance.state, "Ready")
        self.evidence.update(active_at_onset=True)
        self.worker._replace("sample")
        self.assertEqual(self.issuance.state, "Replaced")

    def test_staff_approval_requires_impact_and_per_account_evidence_if_not_sampled_active(self):
        self.evidence.update(status="Review", active_at_onset=False)
        self.incident.update(impact_verified=False)
        with self.assertRaisesRegex(ValueError, "service impact"):
            self.worker.review_account("sample", "approve", "Approval")
        self.incident.update(impact_verified=True)
        with self.assertRaisesRegex(ValueError, "account was connected"):
            self.worker.review_account("sample", "approve", "Generic approval")
        self.worker.review_account("sample", "approve", "Approval", "Verified in protected RADIUS logs.")
        self.worker._replace("sample")
        self.assertEqual(self.evidence.status, "Replaced")

    def test_reboot_incident_records_uncertainty_without_credit(self):
        self.incident.update(classification="Accounting continuity unverified")
        self.worker._capture(("incident", "issuance"))
        self.client.account.assert_not_called()
        self.assertEqual(self.worker._insert.call_args.kwargs["status"], "Insufficient Evidence")

    def test_capture_rejects_usage_below_pre_incident_activity(self):
        self.frappe.db.get_value.side_effect = lambda doctype, filters, fields, **kw: (
            Doc(name="recent", status="Active", used_seconds=200,
                target_fingerprint=self.reference.target_fingerprint)
            if isinstance(filters, dict) else filters
        )
        self.worker._capture(("incident", "issuance"))
        fields = self.worker._insert.call_args.kwargs
        self.assertEqual(fields["pre_incident_sample"], "recent")
        self.assertEqual(fields["status"], "Insufficient Evidence")
        self.assertIsNone(fields["candidate_seconds"])

    def test_activity_expires_only_with_reliable_exhausted_account(self):
        self.client.account.side_effect = [RouterError(), {"used_seconds": 600, "active": False}]
        self.worker._sample_activity("issuance")
        self.assertEqual(self.issuance.state, "Ready")
        self.worker._sample_activity("issuance")
        self.assertEqual(self.issuance.state, "Expired")

    def test_notification_does_not_resend_ambiguous_delivery_or_send_stale_code(self):
        self.issuance.update(predecessor="previous")
        sender = MagicMock(return_value=False)
        recovery = types.ModuleType("network_billing_system.mikrotik_recovery")
        recovery.send_message = sender
        with patch.dict(sys.modules, {recovery.__name__: recovery}):
            self.worker._notify("outbox")
            self.assertEqual(self.outbox.status, "Needs Review")
            self.worker._notify("outbox")
            sender.assert_called_once()
            self.access.customer_code.assert_called_once_with("username", "test-password")
            self.assertIn("2345-6789-ABCD-EFGH-JKLM-NPQR", sender.call_args.args[1])
            self.outbox.update(status="Pending")
            self.issuance.update(state="Replacing")
            self.worker._notify("outbox")
            sender.assert_called_once()
            self.assertEqual(self.outbox.status, "Suppressed")

    def test_notification_retries_pre_submission_failure_with_same_code(self):
        self.issuance.update(predecessor="previous")
        sender = MagicMock(side_effect=["retry", True])
        recovery = types.ModuleType("network_billing_system.mikrotik_recovery")
        recovery.send_message = sender
        with patch.dict(sys.modules, {recovery.__name__: recovery}):
            self.worker._notify("outbox")
            self.assertEqual(self.outbox.status, "Pending")
            self.worker._notify("outbox")
            sender.assert_called_once()
            self.now += timedelta(minutes=1)
            self.worker._notify("outbox")
        self.assertEqual(self.outbox.status, "Sent")
        self.assertEqual(sender.call_args_list[0], sender.call_args_list[1])

    def test_notification_outbox_only_queues_ready_replacements_once(self):
        self.worker.queue_notification(self.issuance)
        self.issuance.update(predecessor="original", state="Pending")
        self.worker.queue_notification(self.issuance)
        self.worker._insert.assert_not_called()
        self.issuance.update(state="Ready")
        self.worker.queue_notification(self.issuance)
        self.frappe.db.exists.return_value = "outbox"
        self.worker.queue_notification(self.issuance)
        self.worker._insert.assert_called_once()
