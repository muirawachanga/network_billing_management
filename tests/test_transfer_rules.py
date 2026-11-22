import copy
import unittest

from network_billing_system.transfer_rules import (
    batch_payload, callback_token, integer_amount, verified_results,
)


PROVIDER_ID = "716481bc-635c-4d35-92c9-65bb0797196a"


class TestTransferRules(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"name": "request-1", "recipient_amount": "1000",
             "destination": {"type": "mobile_wallet", "phone_number": "254712345678", "network": "Safaricom"}},
            {"name": "request-2", "recipient_amount": "500",
             "destination": {"type": "mobile_wallet", "phone_number": "254722222222", "network": "Safaricom"}},
        ]
        self.payload = {
            "data": {"id": PROVIDER_ID, "type": "send_money", "attributes": {
                "status": "Processed", "source_identifier": "4321", "currency": "KES",
                "metadata": {"transfer_batch": "batch-1"},
                "destinations": [dict(row["destination"], amount=int(row["recipient_amount"])) for row in self.rows],
                "transfer_batches": [
                    {"status": "Transferred", "amount": "500.0", "disbursements": [{
                        "status": "Transferred", "amount": "500.0", "errors": None,
                        "destination_type": "mobile_wallet", "phone_number": "254722222222",
                        "transaction_reference": "TESTREFERENCE2",
                    }]},
                    {"status": "Transferred", "amount": "1000.0", "disbursements": [{
                        "status": "Transferred", "amount": "1000.0", "errors": None,
                        "destination_type": "mobile_wallet", "phone_number": "254712345678",
                        "transaction_reference": "TESTREFERENCE1",
                    }]},
                ],
            }},
        }

    def verify(self, payload=None):
        return verified_results(payload or self.payload, "batch-1", PROVIDER_ID, self.rows, "4321")

    def test_documented_processed_response_matches_disbursements_not_row_order(self):
        result = self.verify()
        self.assertEqual([(row["request"], row["status"]) for row in result],
                         [("request-2", "Paid"), ("request-1", "Paid")])

    def test_processed_alone_does_not_confirm_a_payout(self):
        self.payload["data"]["attributes"]["transfer_batches"] = []
        self.assertEqual(self.verify(), [])

    def test_partial_batch_only_returns_existing_disbursements(self):
        self.payload["data"]["attributes"]["transfer_batches"].pop()
        result = self.verify()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["request"], "request-2")

    def test_duplicate_disbursements_or_unknown_recipient_require_review(self):
        batches = self.payload["data"]["attributes"]["transfer_batches"]
        batches.append(copy.deepcopy(batches[0]))
        with self.assertRaises(ValueError):
            self.verify()
        batches.pop()
        batches[0]["disbursements"][0]["phone_number"] = "254733333333"
        with self.assertRaises(ValueError):
            self.verify()

    def test_wrong_amount_metadata_currency_and_source_are_rejected(self):
        for field, value in (("currency", "USD"), ("source_identifier", "other"),
                             ("metadata", {"transfer_batch": "other"})):
            payload = copy.deepcopy(self.payload)
            payload["data"]["attributes"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(payload)
        self.payload["data"]["attributes"]["transfer_batches"][0]["disbursements"][0]["amount"] = "499"
        with self.assertRaises(ValueError):
            self.verify()

    def test_ambiguous_failed_request_is_not_proof_all_destinations_failed(self):
        attrs = self.payload["data"]["attributes"]
        attrs["status"] = "Failed"
        attrs["transfer_batches"] = []
        self.assertEqual(self.verify(), [])

    def test_confirmed_failure_and_unknown_status(self):
        result = self.payload["data"]["attributes"]["transfer_batches"][0]["disbursements"][0]
        result["status"] = "Failed"
        self.assertEqual(self.verify()[0]["status"], "Failed")
        result["status"] = "Something Else"
        with self.assertRaises(ValueError):
            self.verify()

    def test_integer_json_amounts_and_batch_restrictions(self):
        payload = batch_payload("batch-1", self.rows, "4321", "https://example.test")
        self.assertIsInstance(payload["destinations"][0]["amount"], int)
        for bad in ("NaN", "Infinity", "-1", "1.5", "0"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                integer_amount(bad)
        self.rows[1]["destination"] = {"type": "till", "till_number": "123456"}
        with self.assertRaises(ValueError):
            batch_payload("batch-1", self.rows, "4321", "")

    def test_duplicate_accounts_are_not_ambiguous_in_a_batch(self):
        self.rows[1]["destination"] = self.rows[0]["destination"]
        with self.assertRaises(ValueError):
            batch_payload("batch-1", self.rows, "4321", "")

    def test_paybill_and_merchant_support_and_no_blind_transfer(self):
        for destination in (
            {"type": "paybill", "paybill_number": "890087", "paybill_account_number": "test"},
            {"type": "till", "till_number": "123456"},
            {"type": "merchant_wallet", "reference": "confirmed-account-reference"},
            {"type": "merchant_bank_account", "reference": "confirmed-bank-reference"},
        ):
            row = dict(self.rows[0], destination=destination)
            payload = batch_payload("batch-1", [row], None, "")
            self.assertEqual(payload["destinations"][0]["type"], destination["type"])
            if destination["type"].startswith("merchant_"):
                self.assertEqual(set(payload["destinations"][0]), {"type", "reference", "amount"})
            with self.assertRaises(ValueError):
                batch_payload("batch-1", [], None, "")

    def test_bank_disbursement_can_match_explicit_confirmed_reference(self):
        self.rows = [{
            "name": "request-1", "recipient_amount": "1000", "payout_reference": "confirmed-bank-ref",
            "destination": {"type": "bank_account", "account_number": "123456",
                            "account_name": "Test Owner", "bank_branch_ref": "branch-1"},
        }]
        attrs = self.payload["data"]["attributes"]
        attrs["destinations"] = [dict(self.rows[0]["destination"], amount=1000)]
        attrs["transfer_batches"] = [{"disbursements": [{
            "destination_type": "bank_account", "destination_reference": "confirmed-bank-ref",
            "amount": "1000.0", "status": "Transferred", "transaction_reference": None,
        }]}]
        self.assertEqual(self.verify()[0]["status"], "Paid")
        attrs["transfer_batches"][0]["disbursements"][0]["destination_reference"] = "unknown"
        with self.assertRaises(ValueError):
            self.verify()

    def test_callback_capability_is_batch_scoped(self):
        self.assertNotEqual(callback_token("batch-1", "key"), callback_token("batch-2", "key"))
        self.assertNotEqual(callback_token("batch-1", "key"), callback_token("batch-1", "other"))

    def test_malformed_metadata_and_reused_transaction_references_are_rejected(self):
        self.payload["data"]["attributes"]["metadata"] = ["invalid"]
        with self.assertRaises(ValueError):
            self.verify()
        attrs = self.payload["data"]["attributes"]
        attrs["metadata"] = {"transfer_batch": "batch-1"}
        attrs["transfer_batches"][1]["disbursements"][0]["transaction_reference"] = "TESTREFERENCE2"
        with self.assertRaises(ValueError):
            self.verify()
