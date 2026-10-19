import copy
import unittest

from network_billing_system.portal_rules import (
    confirmed_payment,
    normalize_phone,
    payment_id_from_location,
)


PAYMENT_ID = "a652f86f-f2aa-4d70-baa2-ccfe4b78f4fc"


class TestPortalRules(unittest.TestCase):
    def setUp(self):
        self.checkout = {
            "name": "checkout-1",
            "amount": 5,
            "mobile_number": "+254712345678",
            "till_number": "123456",
        }
        self.payload = {
            "data": {
                "id": PAYMENT_ID,
                "attributes": {
                    "status": "Success",
                    "metadata": {"portal_checkout": "checkout-1"},
                    "event": {"resource": {
                        "reference": "QWE123ABC",
                        "sender_phone_number": "+254712345678",
                        "amount": "5.00",
                        "currency": "KES",
                        "till_number": "K123456",
                    }},
                },
            },
        }

    def test_supported_phone_formats(self):
        for phone in (
            "712345678", "0712345678", "254712345678", "+254712345678",
            "00254712345678", "0712 345 678", "(0712) 345-678",
        ):
            with self.subTest(phone=phone):
                self.assertEqual(normalize_phone(phone), "+254712345678")
        self.assertEqual(normalize_phone("0112345678"), "+254112345678")

    def test_invalid_phone_formats(self):
        for phone in (None, "", "254234567890", "071234567", "abc0712345678", "2540712345678"):
            with self.subTest(phone=phone), self.assertRaises(ValueError):
                normalize_phone(phone)

    def test_payment_location_is_provider_owned(self):
        base = "https://sandbox.kopokopo.com"
        self.assertEqual(
            payment_id_from_location(base + "/api/v2/incoming_payments/" + PAYMENT_ID, base),
            PAYMENT_ID,
        )
        for location in (
            "https://attacker.example/api/v2/incoming_payments/" + PAYMENT_ID,
            base + "/api/v2/incoming_payments/../../secrets",
            base + "/api/v2/incoming_payments/" + PAYMENT_ID + "?extra=1",
            base + "/api/v1/incoming_payments/" + PAYMENT_ID,
            "",
        ):
            with self.subTest(location=location), self.assertRaises(ValueError):
                payment_id_from_location(location, base)

    def test_verified_payment(self):
        result = confirmed_payment(self.payload, self.checkout, PAYMENT_ID)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["receipt"], "QWE123ABC")

    def test_verified_payment_accepts_all_letter_receipt(self):
        self.payload["data"]["attributes"]["event"]["resource"]["reference"] = "ABCDEFGHIJ"
        result = confirmed_payment(self.payload, self.checkout, PAYMENT_ID)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["receipt"], "ABCDEFGHIJ")

    def test_rejects_wrong_checkout_and_provider_id(self):
        payload = copy.deepcopy(self.payload)
        payload["data"]["attributes"]["metadata"]["portal_checkout"] = "other-checkout"
        with self.assertRaises(ValueError):
            confirmed_payment(payload, self.checkout, PAYMENT_ID)
        with self.assertRaises(ValueError):
            confirmed_payment(self.payload, self.checkout, "b652f86f-f2aa-4d70-baa2-ccfe4b78f4fc")

    def test_rejects_payment_mismatches(self):
        for field, value in (
            ("amount", "4.99"), ("amount", "10"), ("amount", "NaN"),
            ("amount", "Infinity"), ("amount", "not-money"),
            ("currency", "USD"), ("sender_phone_number", "+254722222222"),
            ("till_number", "K654321"), ("reference", ""),
        ):
            payload = copy.deepcopy(self.payload)
            payload["data"]["attributes"]["event"]["resource"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                confirmed_payment(payload, self.checkout, PAYMENT_ID)

    def test_pending_and_terminal_provider_states(self):
        for status in ("Pending", "Processing", "Failed", "Cancelled"):
            self.payload["data"]["attributes"]["status"] = status
            expected = "PENDING" if status == "Processing" else status.upper()
            self.assertEqual(
                confirmed_payment(self.payload, self.checkout, PAYMENT_ID),
                {"status": expected},
            )

    def test_unknown_status_is_not_payment_success(self):
        self.payload["data"]["attributes"]["status"] = "Unexpected"
        with self.assertRaises(ValueError):
            confirmed_payment(self.payload, self.checkout, PAYMENT_ID)
