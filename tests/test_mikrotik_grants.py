"""Exercise the grant and Notify state machine without a Frappe site or live SMS."""

import importlib.util
import json
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from network_billing_system import notify_platform_transport as transport, routeros
from tests.test_captive_portal_api import Document


class GrantTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 22, 9)
        self.frappe = Mock(whitelist=lambda **kw: lambda f: f)
        self.frappe.session.user = "issuer@example.test"
        self.frappe.throw.side_effect = ValueError
        self.frappe.db.get_value.return_value = None
        self.frappe.db.sql.return_value = []
        utils = types.ModuleType("frappe.utils")
        utils.now_datetime = lambda: self.now
        utils.get_datetime = lambda value: value
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils}):
            self.access = self.load("mikrotik_access")
            with patch("network_billing_system.mikrotik_access", self.access, create=True):
                self.grants = self.load("mikrotik_grants")
        self.grant = Document(
            name="gift", status="Draft", zone="zone", package="package", duration_minutes=10,
            recipient="0712 345 678", reason=" Staff goodwill ", source_reference=" CASE/1 ",
            check_permission=Mock(),
        )
        self.package = Document(zone="zone", enabled=1, duration_minutes=30, mikrotik_profile="staff",
                                mikrotik_upload_bps=300, mikrotik_download_bps=600, check_permission=Mock())
        self.zone = Document(enabled=1, backend="MikroTik", mikrotik_router="router", check_permission=Mock())
        self.router = Document(
            name="router", zone="zone", pilot_verified=1, hotspot_server="hotspot", get_password=Mock(return_value="p"),
        )
        self.settings = Document(enabled=1, base_url="https://notify.example.test/", account="wifi",
                                 get_password=Mock(side_effect=["key", "secret"] * 20))
        self.frappe.get_single.return_value = self.settings
        self.issued = Document(name="issuance", state="Ready", complimentary_grant="gift", username="wifi-user",
                               get_password=Mock(return_value="wifi-password"))
        self.item = Document(name="outbox", issuance="issuance", grant="gift", status="Pending Quote",
                             next_attempt_at=self.now)
        self.dispatch = "1" * 64
        self.created = []
        self.frappe.get_doc.side_effect = self.get_doc
        self.frappe.db.set_value.side_effect = self.set_value
        self.frappe.get_all.side_effect = lambda *args, **kwargs: (
            [self.item.name] if self.item.next_attempt_at and self.item.next_attempt_at <= self.now else []
        )

    @staticmethod
    def load(name):
        root = Path(__file__).parents[1]
        spec = importlib.util.spec_from_file_location(f"_test_grant_{name}", root / f"network_billing_system/{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def get_doc(self, doctype, name=None, **kwargs):
        if isinstance(doctype, dict):
            doc = Document(doctype)
            doc.name = "issuance" if doc.doctype == self.access.ISSUANCE else "new-outbox"
            self.created.append(doc)
            return doc
        return {
            self.grants.ZONE: self.zone, self.grants.GRANT: self.grant,
            self.grants.PACKAGE: self.package, self.access.ROUTER: self.router,
            self.grants.OUTBOX: self.item, self.access.ISSUANCE: self.issued,
        }[doctype]

    def set_value(self, doctype, name, fields, value=None):
        doc = self.get_doc(doctype, name)
        doc.update(fields if isinstance(fields, dict) else {fields: value})

    def test_approval_replay_uses_one_issuance_and_no_checkout(self):
        self.frappe.db.get_value.side_effect = lambda doctype, *args, **kwargs: (
            "zone" if doctype == self.grants.GRANT else None
        )
        for _ in range(2):
            self.assertEqual(self.grants.approve_grant("gift"), {"issuance": "issuance"})
        self.frappe.only_for.assert_called_with(self.grants.APPROVERS)
        self.grant.check_permission.assert_called_with("write")
        self.assertEqual(len(self.created), 1)
        issuance = self.created[0]
        self.assertEqual(issuance.doctype, self.access.ISSUANCE)
        self.assertEqual(issuance.complimentary_grant, "gift")
        self.assertEqual(issuance.issuance_key, "complimentary:gift")
        self.assertIsNone(issuance.checkout)
        self.assertEqual(issuance.duration_seconds, 600)
        self.assertEqual(self.grant.issuer, self.frappe.session.user)
        self.assertEqual(self.grant.source_reference, "case/1")
        self.assertEqual(self.grant.recipient, "+254712345678")
        self.assertEqual(self.access.policy_for(issuance)["rate_limit"], "300/600")
        self.frappe.db.after_commit.add.assert_called_once()
        self.frappe.enqueue.assert_not_called()

    def test_grant_approval_checks_permission_policy_and_locked_rate_limit(self):
        self.frappe.db.get_value.return_value = "zone"
        self.frappe.only_for.side_effect = PermissionError
        with self.assertRaises(PermissionError):
            self.grants.approve_grant("gift")
        self.frappe.get_doc.assert_not_called()
        self.frappe.only_for.side_effect = None
        self.package.zone = "other-zone"
        with self.assertRaises(ValueError):
            self.grants.approve_grant("gift")
        self.package.zone = "zone"
        self.frappe.db.sql.return_value = ["+254712345678"] * 3
        with self.assertRaises(ValueError):
            self.grants.approve_grant("gift")
        self.assertIn("FOR UPDATE", self.frappe.db.sql.call_args.args[0])
        self.assertEqual(self.created, [])

    def prepare_outbox(self):
        self.grant.update(status="Approved", issuance="issuance")
        self.access.lock_issuance = Mock(return_value=self.issued)

    def test_provisioning_creates_one_durable_outbox_only_after_router_confirmation(self):
        self.prepare_outbox()
        self.issued.update(state="Pending", duration_seconds=600)
        self.access.policy_for = Mock(return_value={})
        self.frappe.ValidationError = ValueError
        self.frappe.db.exists.return_value = False
        with (
            patch.dict(sys.modules, {"network_billing_system.mikrotik_grants": self.grants}),
            patch.object(routeros, "connect") as connect,
        ):
            ensure = connect.return_value.__enter__.return_value.ensure_user
            ensure.side_effect = [routeros.RouterError("unconfirmed"), None]
            self.access.provision(self.issued.name)
            self.assertEqual(self.created, [])
            self.now = self.issued.next_attempt_at
            self.access.provision(self.issued.name)
            self.access.provision(self.issued.name)
        self.assertEqual(self.issued.state, "Ready")
        self.assertEqual([doc.doctype for doc in self.created], [self.grants.OUTBOX])
        self.assertEqual(self.created[0].status, "Pending Quote")
        self.assertEqual(self.grant.notification, self.created[0].name)
        self.assertEqual(ensure.call_args_list[0], ensure.call_args_list[1])

    @staticmethod
    def response(message=None, status=200, **data):
        response = Mock(status_code=status)
        response.json.return_value = {"message": message, **data}
        return response

    @patch.object(transport.requests, "post")
    def test_transport_persists_quote_then_replays_same_confirm_then_polls_exact_delivery(self, post):
        self.prepare_outbox()
        post.side_effect = [
            self.response({"name": self.dispatch, "sufficient": True, "message": "sensitive response"}),
            requests.Timeout("sensitive timeout"),
            self.response({"name": self.dispatch, "status": "Pending"}),
            self.response({"messages": [
                {"name": "other", "dispatch_status": "Accepted", "delivery_status": "Delivered"},
                {"name": self.dispatch, "dispatch_status": "Accepted", "delivery_status": "Pending"},
            ]}),
            self.response({"messages": [
                {"name": self.dispatch, "dispatch_status": "Accepted", "delivery_status": "Delivered"},
            ]}),
        ]
        self.grants.run_notifications()
        self.assertEqual(self.item.status, "Pending Confirm")
        self.assertEqual(self.item.dispatch_name, self.dispatch)
        self.frappe.db.commit.assert_called_once()
        post.assert_called_once()
        quote = post.call_args
        self.assertTrue(quote.args[0].endswith("/notify_platform.api.v1.sms.quote_sms"))
        self.assertEqual(quote.kwargs["json"]["phone_number"], "0712 345 678")
        self.assertIn("wifi-user.wifi-password", quote.kwargs["json"]["message"])
        self.assertEqual(quote.kwargs["headers"]["Authorization"], "token key:secret")
        self.assertFalse(quote.kwargs["allow_redirects"])
        self.grants.run_notifications()
        self.assertEqual(self.item.diagnostic, "API Error")
        self.assertEqual(self.item.status, "Pending Confirm")
        self.grants.run_notifications()
        self.assertEqual(post.call_count, 2)
        self.now = self.item.next_attempt_at
        self.grants.run_notifications()
        self.assertEqual(post.call_args_list[1], post.call_args_list[2])
        confirm = post.call_args
        self.assertTrue(confirm.args[0].endswith("/notify_platform.api.v1.sms.confirm_sms"))
        self.assertEqual(confirm.kwargs["json"], {"account": "wifi", "message_name": self.dispatch})
        self.assertEqual(self.item.status, "Queued")
        for status in ("Provider Accepted", "Delivered"):
            self.now = self.item.next_attempt_at
            self.grants.run_notifications()
            self.assertEqual(self.item.status, status)
        self.assertTrue(post.call_args.args[0].endswith("/notify_platform.api.v1.dispatch.message_history"))
        self.assertEqual(self.item.provider_delivery, "Delivered")
        self.assertIsNone(self.item.next_attempt_at)

    @patch.object(transport.requests, "post")
    def test_low_balance_and_expiry_do_not_trigger_a_second_quote(self, post):
        self.prepare_outbox()
        def error(message):
            return self.response(status=417, _server_messages=json.dumps([json.dumps({"message": message})]))
        post.side_effect = [
            self.response({"name": self.dispatch, "sufficient": False}),
            error("The wallet cannot cover this change to its available or reserved balance."),
            error("This quote expired. Preview the message again."),
        ]
        self.grants.run_notifications()
        self.assertEqual(self.item.diagnostic, "Low Balance")
        self.grants.run_notifications()
        self.assertEqual(self.item.diagnostic, "Low Balance")
        self.now = self.item.next_attempt_at
        self.grants.run_notifications()
        self.assertEqual(self.item.status, "Quote Expired")
        self.assertIsNone(self.item.next_attempt_at)
        self.assertEqual(post.call_count, 3)

    @patch.object(transport.requests, "post")
    def test_never_quotes_unprovisioned_access_or_claims_unsupported_delivery(self, post):
        self.prepare_outbox()
        self.issued.state = "Pending"
        self.grants.run_notifications()
        self.assertEqual(self.item.status, "Suppressed")
        post.assert_not_called()
        self.item.update(status="Queued", poll_until=self.now, next_attempt_at=self.now, dispatch_name=self.dispatch,
                         integration_identity=transport.NotifyClient("https://notify.example.test", "wifi", "key", "secret").identity)
        post.return_value = self.response(status=404)
        self.grants.run_notifications()
        self.assertEqual(self.item.status, "Delivery Unverified")
        self.assertIsNone(self.item.next_attempt_at)


if __name__ == "__main__":
    unittest.main()
