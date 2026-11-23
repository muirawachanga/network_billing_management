"""Verify recovery state and message handling without a Frappe site or gateway."""

import importlib.util
import sys
import types
import unittest
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests


class TestMikroTikRecovery(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 22, 8, 20)
        self.rows = {}
        self.pending = {}
        self.locked = None
        self.counters = Counter()
        self.frappe = MagicMock()
        self.frappe.conf = {"encryption_key": "test-key"}
        self.frappe.local = types.SimpleNamespace(site="test.local", request_ip="caller")
        self.frappe.cache.return_value.eval.side_effect = self.increment
        self.frappe.db.commit.side_effect = self.commit
        self.frappe.db.get_value.side_effect = self.get_value
        self.frappe.db.sql.side_effect = self.get_challenge
        self.frappe.db.set_value.side_effect = self.update_challenge
        self.frappe.get_doc.side_effect = self.get_doc
        self.frappe.DoesNotExistError = LookupError
        self.checkout = types.SimpleNamespace(
            name="checkout-1", mobile_number="0712345678", status="SUCCESS",
            zone="central", backend="MikroTik", mpesa_receipt="ZYX987WVU",
        )
        self.zones = {"central", "south"}
        config = types.SimpleNamespace(load_configuration={
            "sms_gateway_server": "https://gateway.example/send",
            "sms_api_key": "test-gateway-key",
        }.get)
        path = Path(__file__).parents[1] / "network_billing_system/mikrotik_recovery.py"
        spec = importlib.util.spec_from_file_location("_tested_mikrotik_recovery", path)
        self.api = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            "frappe": self.frappe,
            "frappe.utils": types.SimpleNamespace(now_datetime=lambda: self.now),
            "network_billing_system.utils": config,
        }):
            spec.loader.exec_module(self.api)

    def increment(self, script, number_of_keys, key, expiry):
        self.assertIn("EXPIRE", script)
        self.assertEqual(number_of_keys, 1)
        self.assertGreater(expiry, 0)
        self.counters[key] += 1
        return self.counters[key]

    def get_value(self, doctype, filters, fields, **kwargs):
        if doctype == "Captive Portal Zone":
            self.assertEqual(filters["backend"], "MikroTik")
            return filters["name"] if filters["enabled"] and filters["name"] in self.zones else None
        self.assertEqual(doctype, "Portal Checkout")
        self.assertEqual(filters["backend"], "MikroTik")
        self.assertEqual(filters["status"], "SUCCESS")
        if all(getattr(self.checkout, key) == value for key, value in filters.items()):
            return self.checkout

    def get_doc(self, doctype, name=None):
        if doctype == "Portal Checkout":
            if not self.checkout or self.checkout.name != name:
                raise LookupError
            return self.checkout

        def insert(ignore_permissions, set_name):
            self.assertTrue(ignore_permissions)
            self.pending[set_name] = dict(
                used_at=None, dispatched_at=None, grant_digest=None, grant_expires_at=None,
                name=set_name, **doctype,
            )

        return types.SimpleNamespace(insert=insert)

    def get_challenge(self, sql, params, as_dict):
        name, = params
        if "FOR UPDATE" in sql:
            self.locked = name
        if name in self.rows:
            return [types.SimpleNamespace(**self.rows[name])]
        return []

    def update_challenge(self, doctype, name, values, value=None):
        self.assertEqual(self.locked, name)
        self.assertEqual(doctype, "MikroTik Recovery Challenge")
        fields = {values: value} if isinstance(values, str) else values
        self.pending[name] = dict(self.rows[name], **fields)

    def commit(self):
        self.rows.update(self.pending)
        self.pending.clear()
        self.locked = None

    def dispatched_code(self):
        challenge_id = self.api.begin("zyx987wvu", "central")["challenge_id"]
        with patch.object(self.api, "send_message", return_value=True) as sender:
            self.api.dispatch(challenge_id)
        phone, message, _ = sender.call_args.args
        self.assertEqual(phone, "+254712345678")
        return challenge_id, message.split()[-1].rstrip(".")

    def test_begin_response_is_generic_and_queue_contains_only_challenge_id(self):
        received = []

        def enqueue(path, **kwargs):
            self.assertEqual(path, "network_billing_system.mikrotik_recovery.dispatch")
            self.assertEqual(list(kwargs), ["challenge_id"])
            received.append(kwargs["challenge_id"] in self.rows)

        self.frappe.enqueue.side_effect = enqueue
        match = self.api.begin(" zyx9 87wvu ", "central")
        unknown = self.api.begin("AAA111BBB", "central")
        wrong_zone = self.api.begin("ZYX987WVU", "south")
        self.checkout.backend = "pfSense"
        pfsense = self.api.begin("ZYX987WVU", "central")
        self.checkout.backend = "MikroTik"
        self.checkout.status = "PENDING"
        pending = self.api.begin("ZYX987WVU", "central")
        for response in (unknown, wrong_zone, pfsense, pending):
            self.assertEqual(set(response), set(match))
            self.assertEqual(response["customer_message"], match["customer_message"])
            self.assertNotIn(response["challenge_id"], self.rows)
        self.assertEqual(received, [True, False, False, False, False])
        self.assertNotIn("0712345678", repr(self.rows))
        self.assertNotIn("ZYX987WVU", repr(self.rows))

    def test_start_checkout_and_caller_limits_and_cache_fail_closed(self):
        results = []
        for caller in ("a", "b", "c", "d"):
            self.frappe.local.request_ip = caller
            results.append(self.api.begin("ZYX987WVU", "central")["challenge_id"] in self.rows)
        self.assertEqual(results, [True, True, True, False])
        for _ in range(6):
            self.api.begin("AAA111BBB", "south")
        self.assertEqual(self.frappe.enqueue.call_count, 8)
        self.frappe.cache.return_value.eval.side_effect = RuntimeError
        self.api.begin("ZYX987WVU", "central")
        self.assertEqual(self.frappe.enqueue.call_count, 8)

    def test_zone_limit_applies_across_callers(self):
        for i in range(121):
            self.frappe.local.request_ip = f"caller-{i}"
            self.api.begin("AAA111BBB", "central")
        self.assertEqual(self.frappe.enqueue.call_count, 120)
        self.api.begin("AAA111BBB", "south")
        self.assertEqual(self.frappe.enqueue.call_count, 121)

    def test_dispatch_claim_commits_before_network_and_never_retries(self):
        challenge_id = self.api.begin("ZYX987WVU", "central")["challenge_id"]

        def send(phone, message, idempotency_key):
            self.assertEqual(phone, "+254712345678")
            self.assertRegex(message, r"[0-9]{6}\.$")
            self.assertEqual(idempotency_key, "mikrotik-recovery-" + challenge_id)
            self.assertEqual(self.rows[challenge_id]["dispatched_at"], self.now)
            self.assertIsNone(self.locked)
            return False

        with patch.object(self.api, "send_message", side_effect=send) as sender:
            self.api.dispatch(challenge_id)
            self.api.dispatch(challenge_id)
            sender.assert_called_once()

    def test_dispatch_is_rejected_if_commit_fails_or_payer_changed(self):
        challenge_id = self.api.begin("ZYX987WVU", "central")["challenge_id"]
        with patch.object(self.api, "send_message") as sender:
            self.checkout.mobile_number = "+254701111111"
            self.api.dispatch(challenge_id)
            self.checkout.mobile_number = "0712345678"
            self.frappe.db.commit.side_effect = RuntimeError
            with self.assertRaises(RuntimeError):
                self.api.dispatch(challenge_id)
            sender.assert_not_called()

    def test_verification_attempts_are_committed_and_otp_is_single_use(self):
        challenge_id, code = self.dispatched_code()
        self.assertNotIn("recovery_token", self.api.verify(challenge_id, code, "south"))
        self.assertEqual(self.rows[challenge_id]["attempts"], 0)
        self.assertNotIn("recovery_token", self.api.verify(challenge_id, "invalid", "central"))
        self.assertEqual(self.rows[challenge_id]["attempts"], 1)
        grant = self.api.verify(challenge_id, code, "central")["recovery_token"]
        self.assertEqual(self.rows[challenge_id]["used_at"], self.now)
        self.assertNotIn(grant, repr(self.rows))
        self.assertNotIn("recovery_token", self.api.verify(challenge_id, code, "central"))

    def test_verify_cannot_return_grant_if_commit_fails(self):
        challenge_id, code = self.dispatched_code()
        self.frappe.db.commit.side_effect = RuntimeError
        with self.assertRaises(RuntimeError):
            self.api.verify(challenge_id, code, "central")
        self.assertIsNone(self.rows[challenge_id]["grant_digest"])
        self.assertIsNone(self.rows[challenge_id]["used_at"])

    def test_expiry_and_exhausted_attempts_are_rejected(self):
        challenge_id, code = self.dispatched_code()
        for _ in range(5):
            self.api.verify(challenge_id, "invalid", "central")
        self.assertNotIn("recovery_token", self.api.verify(challenge_id, code, "central"))
        self.assertEqual(self.rows[challenge_id]["attempts"], 5)
        second, second_code = self.dispatched_code()
        self.now += timedelta(minutes=5)
        self.assertNotIn("recovery_token", self.api.verify(second, second_code, "central"))
        self.assertEqual(self.rows[second]["attempts"], 0)

    def test_grant_requires_current_checkout_payer_zone_and_backend(self):
        challenge_id, code = self.dispatched_code()
        grant = self.api.verify(challenge_id, code, "central")["recovery_token"]
        self.assertEqual(self.api.checkout_for_grant(grant, "central"), self.checkout.name)
        self.assertIsNone(self.api.checkout_for_grant(grant, "south"))
        self.assertIsNone(self.api.checkout_for_grant(grant[:-1] + "z", "central"))
        for field, value in (("mobile_number", "+254701111111"), ("zone", "south"),
                             ("status", "PENDING"), ("backend", "pfSense")):
            original = getattr(self.checkout, field)
            setattr(self.checkout, field, value)
            self.assertIsNone(self.api.checkout_for_grant(grant, "central"))
            setattr(self.checkout, field, original)
        self.now += timedelta(minutes=10)
        self.assertIsNone(self.api.checkout_for_grant(grant, "central"))

    def test_raw_transport_rejects_redirects_and_sanitizes_exceptions(self):
        with patch.object(requests, "post") as post:
            response = post.return_value.__enter__.return_value
            response.status_code = 202
            self.assertTrue(self.api.send_message("+254712345678", "confidential", "id-1"))
            self.assertEqual(post.call_args.kwargs["headers"]["Idempotency-Key"], "id-1")
            self.assertEqual(post.call_args.kwargs["json"], {
                "to": "+254712345678", "message": "confidential",
            })
            self.assertFalse(post.call_args.kwargs["allow_redirects"])
            response.status_code = 302
            self.assertFalse(self.api.send_message("+254712345678", "confidential", "id-1"))
            post.side_effect = requests.Timeout("confidential")
            self.assertFalse(self.api.send_message("+254712345678", "confidential", "id-1"))
            self.frappe.log_error.assert_not_called()
