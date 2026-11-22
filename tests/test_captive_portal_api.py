"""Focused API tests with a mocked Frappe boundary; no site or payment network needed."""

import importlib.util
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests


class Document(dict):
    def __getattr__(self, name):
        return self.get(name)

    def __setattr__(self, name, value):
        self[name] = value

    def as_dict(self):
        return dict(self)

    def insert(self, **kwargs):
        self.setdefault("name", "checkout-1")
        return self

    def save(self, **kwargs):
        return self


class TestCaptivePortalAPI(unittest.TestCase):
    def setUp(self):
        self.frappe = MagicMock()
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.PermissionError = PermissionError
        self.frappe.ValidationError = ValueError
        self.frappe.conf = {
            "encryption_key": "test-key",
            "kopokopo_client_id": "test-client",
            "kopokopo_client_secret": "test-secret",
        }
        self.frappe.throw.side_effect = lambda message, exc=ValueError: self._throw(message, exc)
        self.frappe.local.site = "test.local"
        self.frappe.local.request_ip = "127.0.0.1"
        self.frappe.cache.return_value.incr.return_value = 1
        self.config = {
            "enable_package_portal": 1, "env": "sandbox", "till_number": "123456",
            "first_name": "Test", "last_name": "Customer",
        }
        utils = types.ModuleType("frappe.utils")
        utils.flt = float
        utils.get_datetime = lambda value: value
        utils.get_url = lambda path: "https://mel.daitkenya.com" + path
        utils.now_datetime = lambda: datetime(2026, 10, 15, 12, 0, 0)
        connector_module = types.ModuleType("network_billing_system.kopokopo_integration")
        self.connector_class = MagicMock()
        connector_module.KopokopoConnector = self.connector_class
        configuration = types.ModuleType("network_billing_system.utils")
        configuration.load_configuration = lambda name: self.config.get(name)
        modules = {
            "frappe": self.frappe, "frappe.utils": utils,
            "network_billing_system.kopokopo_integration": connector_module,
            "network_billing_system.utils": configuration,
        }
        path = Path(__file__).parents[1] / "network_billing_system/api/captive_portal.py"
        spec = importlib.util.spec_from_file_location("_tested_captive_portal", path)
        self.api = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(self.api)
        self.checkout = Document(
            name="checkout-1", status="PENDING", voucher="voucher-1",
            package="time_1hr", zone="default", mobile_number="+254712345678", amount=5,
            duration_minutes=60, till_number="123456", provider_environment="sandbox",
            provider_payment_id="a652f86f-f2aa-4d70-baa2-ccfe4b78f4fc",
        )
        self.voucher = Document(
            name="voucher-1", code="opaque-pfsense-code", package="time_1hr",
            status="Reserved", zone="default", duration_minutes=60, portal_checkout="checkout-1",
        )
        self.package = Document(
            name="time_1hr", zone="default", enabled=1, price=5, duration_minutes=60,
        )
        self.frappe.get_doc.side_effect = self._get_doc
        self.api._lock_voucher = MagicMock(return_value=self.voucher)

    @staticmethod
    def _throw(message, exception):
        raise exception(message)

    def _get_doc(self, doctype, name=None):
        if isinstance(doctype, dict):
            return Document(doctype)
        return {
            "Internet Package Profile": self.package,
            "Portal Checkout": self.checkout,
            "Captive Portal Code": self.voucher,
        }[doctype]

    def test_tokens_are_bound_to_checkout_and_reject_tampering(self):
        token = self.api._token("checkout-1")
        self.assertEqual(self.api._checkout_for_token(token), "checkout-1")
        for invalid in (token + "0", token.replace("checkout-1", "checkout-2"), "invalid", "id.\u2603"):
            with self.subTest(token=invalid), self.assertRaises((PermissionError, ValueError)):
                self.api._checkout_for_token(invalid)

    def test_start_uses_server_price_and_reserves_before_stk(self):
        self.frappe.db.sql.return_value = [Document(name="voucher-1")]
        self.api._lock_checkout = MagicMock(return_value=Document(provider_payment_id=None))
        connector = self.connector_class.return_value
        connector.start_portal_payment.return_value = self.checkout.provider_payment_id

        def send(checkout, callback, subscriber):
            self.frappe.db.commit.assert_called_once()
            self.assertEqual(checkout.amount, 5)
            self.assertEqual(checkout.duration_minutes, 60)
            self.assertIn("checkout_id=checkout-1", callback)
            return self.checkout.provider_payment_id

        connector.start_portal_payment.side_effect = send
        result = self.api.start_payment(
            "0712345678", "time_1hr", portal_action="https://untrusted.example", zone_id="default"
        )
        self.assertTrue(result["stk_push_sent"])
        self.assertIn("payment_token", result)
        self.frappe.db.set_value.assert_any_call(
            "Captive Portal Code", "voucher-1",
            {"status": "Reserved", "portal_checkout": "checkout-1"},
        )

    def test_no_stock_does_not_start_payment(self):
        self.frappe.db.sql.return_value = []
        with self.assertRaisesRegex(ValueError, "no available vouchers"):
            self.api.start_payment("0712345678", "time_1hr", zone_id="default")
        self.connector_class.assert_not_called()
        self.frappe.db.commit.assert_not_called()

    def test_disabled_package_does_not_start_payment(self):
        self.package.enabled = 0
        with self.assertRaisesRegex(ValueError, "not available"):
            self.api.start_payment("0712345678", "time_1hr", zone_id="default")
        self.connector_class.assert_not_called()

    def test_ambiguous_timeout_preserves_token_and_reservation(self):
        self.frappe.db.sql.return_value = [Document(name="voucher-1")]
        self.connector_class.return_value.start_portal_payment.side_effect = requests.Timeout()
        result = self.api.start_payment("0712345678", "time_1hr", zone_id="default")
        self.assertFalse(result["stk_push_sent"])
        self.assertIn("payment_token", result)
        self.frappe.log_error.assert_called_once()
        self.assertEqual(self.frappe.db.set_value.call_count, 1)

    def test_authentication_failure_does_not_reserve_or_start_stk(self):
        self.frappe.db.sql.return_value = [Document(name="voucher-1")]
        self.connector_class.side_effect = requests.Timeout()
        with self.assertRaisesRegex(ValueError, "No payment was requested"):
            self.api.start_payment("0712345678", "time_1hr", zone_id="default")
        self.frappe.db.set_value.assert_not_called()
        self.frappe.db.commit.assert_not_called()

    def test_confirmed_payment_is_idempotent(self):
        self.api._record_payment = MagicMock()
        with patch.object(self.api, "confirmed_payment", return_value={
            "status": "SUCCESS", "receipt": "QWE123ABC", "resource": {},
        }):
            self.api._refresh(self.checkout)
            self.api._refresh(self.checkout)
        self.assertEqual(self.checkout.status, "SUCCESS")
        self.assertEqual(self.checkout.mpesa_receipt, "QWE123ABC")
        self.api._record_payment.assert_called_once()
        self.connector_class.return_value.get_portal_payment.assert_called_once()
        self.frappe.db.set_value.assert_called_once_with(
            "Captive Portal Code", "voucher-1", "status", "Issued"
        )

    def test_failed_payment_releases_only_its_reserved_voucher(self):
        with patch.object(self.api, "confirmed_payment", return_value={"status": "FAILED"}):
            self.api._refresh(self.checkout)
        self.assertIsNone(self.checkout.voucher)
        self.frappe.db.set_value.assert_called_once_with(
            "Captive Portal Code", "voucher-1",
            {"status": "Active", "portal_checkout": None},
        )

    def test_late_verified_success_recovers_failed_checkout_with_matching_stock(self):
        self.api._record_payment = MagicMock()
        with patch.object(self.api, "confirmed_payment", return_value={"status": "FAILED"}):
            self.api._refresh(self.checkout)
        self.assertIsNone(self.checkout.voucher)
        # The original code may already belong to another customer. Recovery
        # selects only available matching inventory, never the old code by ID.
        self.frappe.db.sql.return_value = [Document(name="replacement")]
        self.voucher.name = "replacement"
        self.frappe.db.set_value.reset_mock()
        with patch.object(self.api, "confirmed_payment", return_value={
            "status": "SUCCESS", "receipt": "QWE123ABC", "resource": {},
        }) as verify:
            self.api._refresh(self.checkout, force=True)
            self.api._refresh(self.checkout, force=True)
        verify.assert_called_once()
        self.assertEqual(self.checkout.status, "SUCCESS")
        self.assertEqual(self.checkout.voucher, "replacement")
        self.assertEqual(self.frappe.db.sql.call_args.args[1], ("time_1hr", 60, "default"))
        self.api._record_payment.assert_called_once()
        self.assertTrue(all(
            call.args[1] == "replacement" for call in self.frappe.db.set_value.call_args_list
        ))

    def test_late_success_without_stock_stays_paid_and_can_allocate_after_restock(self):
        self.checkout.status = "FAILED"
        self.checkout.voucher = None
        self.api._record_payment = MagicMock()
        self.frappe.db.sql.return_value = []
        with patch.object(self.api, "confirmed_payment", return_value={
            "status": "SUCCESS", "receipt": "QWE123ABC", "resource": {},
        }):
            self.api._refresh(self.checkout, force=True)
        self.assertEqual(self.checkout.status, "SUCCESS")
        result = self.api._access_response(self.checkout)
        self.assertFalse(result["access_ready"])
        self.assertNotIn("voucher", result)
        self.assertIn("do not pay again", result["customer_message"])
        self.frappe.db.sql.return_value = [Document(name="voucher-1")]
        self.api._refresh(self.checkout)
        self.assertEqual(self.checkout.voucher, "voucher-1")

    def test_force_recheck_bypasses_throttle_but_never_trusts_callback_status(self):
        self.checkout.status = "FAILED"
        self.checkout.voucher = None
        self.checkout.last_checked_at = datetime(2026, 10, 15, 12, 0, 0)
        with patch.object(self.api, "confirmed_payment", return_value={"status": "FAILED"}) as verify:
            self.api._refresh(self.checkout, force=True)
        self.connector_class.return_value.get_portal_payment.assert_called_once()
        verify.assert_called_once()
        self.assertEqual(self.checkout.status, "FAILED")
        self.frappe.db.set_value.assert_not_called()

    def test_date_range_reconciles_failed_and_pending_with_inclusive_end_date(self):
        self.frappe.get_all.return_value = [Document(name="checkout-1")]
        self.checkout.status = "FAILED"
        self.api._lock_checkout = MagicMock(return_value=self.checkout)
        self.api._refresh = MagicMock(return_value=Document(status="SUCCESS", voucher="voucher-1"))
        result = self.api.reconcile_date_range("2026-10-01", "2026-10-18")
        self.assertEqual(result["results"], [{"name": "checkout-1", "outcome": "confirmed"}])
        self.api._refresh.assert_called_once_with(self.checkout, force=True)
        query = self.frappe.get_all.call_args.kwargs
        self.assertIn(["creation", ">=", "2026-10-01"], query["filters"])
        self.assertIn(["creation", "<", "2026-10-19"], query["filters"])
        self.frappe.only_for.assert_called_once_with(("System Manager", "Network Administrator"))

    def test_date_range_batches_and_skips_missing_provider_id_without_stopping(self):
        self.frappe.get_all.return_value = [Document(name="checkout-" + str(n)) for n in range(3)]
        self.api._lock_checkout = MagicMock(return_value=Document(status="FAILED"))
        result = self.api.reconcile_date_range("2026-10-18", "2026-10-18", after_name="checkout-previous")
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(result["next_after"], "checkout-1")
        self.assertTrue(all(row["outcome"] == "missing_provider_id" for row in result["results"]))
        self.assertIn(
            ["name", ">", "checkout-previous"],
            self.frappe.get_all.call_args.kwargs["filters"],
        )
        self.connector_class.assert_not_called()

    def test_date_range_continues_after_one_provider_error(self):
        self.frappe.get_all.return_value = [Document(name="one"), Document(name="two")]
        self.api._lock_checkout = MagicMock(side_effect=[
            requests.Timeout(), Document(status="SUCCESS", voucher="voucher-1"),
        ])
        result = self.api.reconcile_date_range("2026-10-18", "2026-10-18")
        self.assertEqual([row["outcome"] for row in result["results"]], ["error", "already_confirmed"])
        self.frappe.db.rollback.assert_called_once()
        self.frappe.log_error.assert_called_once()
        self.frappe.db.commit.assert_called_once()

    def test_date_range_rejects_invalid_reversed_and_oversized_ranges(self):
        for start, end in (
            ("bad", "2026-10-18"), ("2026-10-18", "2026-10-01"),
            ("2026-01-01", "2026-12-31"),
        ):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.api.reconcile_date_range(start, end)
        self.frappe.get_all.assert_not_called()

    def test_mismatched_reservation_is_not_released(self):
        self.voucher.portal_checkout = "other-checkout"
        with patch.object(self.api, "confirmed_payment", return_value={"status": "FAILED"}):
            with self.assertRaisesRegex(ValueError, "has changed"):
                self.api._refresh(self.checkout)
        self.frappe.db.set_value.assert_not_called()

    def test_access_returns_actual_code_not_document_name(self):
        self.checkout.status = "SUCCESS"
        self.voucher.status = "Issued"
        result = self.api._access_response(self.checkout)
        self.assertTrue(result["access_ready"])
        self.assertEqual(result["voucher"], "opaque-pfsense-code")
        self.assertEqual(result["duration_minutes"], 60)
        self.assertEqual(result["expiry_authority"], "pfsense")
        self.assertNotIn("expires_at", result)

    def test_revoked_voucher_is_not_returned(self):
        self.checkout.status = "SUCCESS"
        self.voucher.status = "Revoked"
        result = self.api._access_response(self.checkout)
        self.assertFalse(result["access_ready"])
        self.assertNotIn("voucher", result)

    def test_receipt_recovery_uses_only_successful_checkout(self):
        self.frappe.db.get_value.return_value = "checkout-1"
        self.checkout.status = "SUCCESS"
        self.voucher.status = "Issued"
        result = self.api.connect_existing_customer("mpesa_receipt", " qwe 123abc ", zone_id="default")
        self.assertEqual(result["voucher"], "opaque-pfsense-code")
        self.frappe.db.get_value.assert_called_once_with(
            "Portal Checkout", {"mpesa_receipt": "QWE123ABC", "status": "SUCCESS", "zone": "default"}, "name"
        )

    def test_unmatched_receipt_does_not_allocate_or_start_payment(self):
        self.frappe.db.get_value.return_value = None
        result = self.api.connect_existing_customer("mpesa_receipt", "QWE123ABC", zone_id="default")
        self.assertEqual(result["status"], "NOT_FOUND")
        self.connector_class.assert_not_called()
        self.frappe.db.set_value.assert_not_called()

    def test_manual_reconciliation_confirms_all_letter_receipt(self):
        self.api._lock_checkout = MagicMock(return_value=self.checkout)
        self.api._record_payment = MagicMock()
        self.frappe.db.set_value.side_effect = (
            lambda doctype, name, field, value: self.voucher.update({field: value})
        )
        self.connector_class.return_value.get_portal_payment.return_value = {
            "data": {
                "id": self.checkout.provider_payment_id,
                "attributes": {
                    "status": "Success",
                    "metadata": {"portal_checkout": self.checkout.name},
                    "event": {"resource": {
                        "reference": "ABCDEFGHIJ",
                        "sender_phone_number": self.checkout.mobile_number,
                        "amount": str(self.checkout.amount),
                        "currency": "KES",
                        "till_number": "K" + self.checkout.till_number,
                    }},
                },
            },
        }
        result = self.api.reconcile_checkout(self.checkout.name)
        self.assertNotIn("error", result)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertTrue(result["access_ready"])
        self.assertEqual(self.checkout.mpesa_receipt, "ABCDEFGHIJ")
        self.api._record_payment.assert_called_once()
        self.frappe.log_error.assert_not_called()
        self.assertNotIn("provider_payload", result)

    def test_receipt_recovery_accepts_all_letter_receipt(self):
        self.frappe.db.get_value.return_value = "checkout-1"
        self.checkout.status = "SUCCESS"
        self.voucher.status = "Issued"
        result = self.api.connect_existing_customer(
            "mpesa_receipt", " abcdefghij ", zone_id="default"
        )
        self.assertTrue(result["access_ready"])
        self.frappe.db.get_value.assert_called_once_with(
            "Portal Checkout",
            {"mpesa_receipt": "ABCDEFGHIJ", "status": "SUCCESS", "zone": "default"},
            "name",
        )

    def test_manual_reconciliation_logs_payload_on_error_without_returning_it(self):
        self.api._lock_checkout = MagicMock(return_value=self.checkout)
        self.connector_class.return_value.get_portal_payment.return_value = {
            "data": {
                "id": self.checkout.provider_payment_id,
                "attributes": {
                    "status": "Success",
                    "metadata": {"portal_checkout": "checkout-1"},
                    "event": {"resource": {
                        "reference": "123",
                        "sender_phone_number": "+254712345678",
                        "sender_first_name": "Test",
                        "amount": "5",
                        "currency": "KES",
                        "till_number": "K123456",
                    }},
                },
            },
        }

        result = self.api.reconcile_checkout("checkout-1")

        self.assertEqual(result["status"], "PENDING")
        self.assertIn("invalid M-Pesa receipt", result["error"])
        self.assertNotIn("provider_payload", result)
        self.assertIn("error_log", result)
        self.frappe.log_error.assert_called_once()
        log_message = self.frappe.log_error.call_args.args[0]
        self.assertIn('"reference": "123"', log_message)
        self.assertNotIn("+254712345678", log_message)

    def test_manual_reconciliation_does_not_log_or_return_pending_provider_payload(self):
        self.api._lock_checkout = MagicMock(return_value=self.checkout)
        payload = {
            "data": {
                "id": self.checkout.provider_payment_id,
                "attributes": {
                    "status": "Pending",
                    "metadata": {"portal_checkout": "checkout-1"},
                },
            },
        }
        self.connector_class.return_value.get_portal_payment.return_value = payload

        result = self.api.reconcile_checkout("checkout-1")

        self.assertEqual(result["status"], "PENDING")
        self.assertNotIn("provider_payload", result)
        self.frappe.log_error.assert_not_called()

    def test_manual_reconciliation_requeries_successful_checkout_without_downgrading_it(self):
        self.checkout.status = "SUCCESS"
        self.checkout.voucher = None
        self.api._lock_checkout = MagicMock(return_value=self.checkout)
        payload = {
            "data": {
                "id": self.checkout.provider_payment_id,
                "attributes": {
                    "status": "Pending",
                    "metadata": {"portal_checkout": "checkout-1"},
                },
            },
        }
        self.connector_class.return_value.get_portal_payment.return_value = payload

        result = self.api.reconcile_checkout("checkout-1")

        self.assertEqual(result["status"], "SUCCESS")
        self.assertNotIn("provider_payload", result)
        self.frappe.log_error.assert_not_called()

    def test_callback_fetches_provider_instead_of_trusting_success_payload(self):
        self.api._lock_checkout = MagicMock(return_value=self.checkout)
        self.api._refresh = MagicMock()
        self.api.payment_callback("checkout-1", {"attributes": {"status": "Success"}})
        self.api._refresh.assert_called_once_with(self.checkout, None, force=True)

    def test_rate_limit_rejects_excess_requests(self):
        self.frappe.cache.return_value.incr.return_value = 4
        with self.assertRaisesRegex(ValueError, "Too many requests"):
            self.api._throttle("phone", "+254712345678", 3, 300)

    def test_wrong_zone_package_cannot_start_payment(self):
        self.package.zone = "another-zone"
        with self.assertRaisesRegex(ValueError, "not available"):
            self.api.start_payment("0712345678", "time_1hr", zone_id="default")
        self.connector_class.assert_not_called()

    def test_missing_zone_never_falls_back_to_default(self):
        for zone in (None, "", "   "):
            with self.subTest(zone=zone), self.assertRaisesRegex(ValueError, "zone must be configured"):
                self.api.start_payment("0712345678", "time_1hr", zone_id=zone)
        self.frappe.get_doc.assert_not_called()
        self.connector_class.assert_not_called()

    def test_disabled_or_unknown_zone_cannot_list_packages(self):
        self.frappe.db.exists.return_value = False
        with self.assertRaisesRegex(ValueError, "unavailable at this location"):
            self.api.get_packages(zone_id="unknown")
        self.frappe.get_all.assert_not_called()

    def test_stock_query_and_checkout_record_the_requested_zone(self):
        self.package.zone = "north"
        self.frappe.db.sql.return_value = [Document(name="voucher-1")]
        self.api._lock_checkout = MagicMock(return_value=Document(provider_payment_id=None))
        self.connector_class.return_value.start_portal_payment.return_value = self.checkout.provider_payment_id
        self.api.start_payment("0712345678", "time_1hr", zone_id="north")
        self.assertEqual(self.frappe.db.sql.call_args.args[1], ("time_1hr", 60, "north"))
        sent_checkout = self.connector_class.return_value.start_portal_payment.call_args.args[0]
        self.assertEqual(sent_checkout.zone, "north")

    def test_status_from_another_zone_does_not_return_code_or_query_provider(self):
        self.api._lock_checkout = MagicMock(return_value=self.checkout)
        result = self.api.check_payment_status(self.api._token("checkout-1"), zone_id="south")
        self.assertEqual(result["status"], "INELIGIBLE")
        self.assertNotIn("voucher", result)
        self.connector_class.assert_not_called()

    def test_wrong_zone_voucher_cannot_be_returned(self):
        self.checkout.status = "SUCCESS"
        self.voucher.status = "Issued"
        self.voucher.zone = "other"
        self.assertFalse(self.api._access_response(self.checkout)["access_ready"])

    def test_receipt_recovery_filters_requested_zone(self):
        self.frappe.db.get_value.return_value = None
        result = self.api.connect_existing_customer("mpesa_receipt", "QWE123ABC", zone_id="north")
        self.frappe.db.get_value.assert_called_once_with(
            "Portal Checkout", {"mpesa_receipt": "QWE123ABC", "status": "SUCCESS", "zone": "north"}, "name"
        )
        self.assertNotIn("voucher", result)
