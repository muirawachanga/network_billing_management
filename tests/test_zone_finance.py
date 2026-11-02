import importlib.util
import json
import sqlite3
import sys
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from network_billing_system.remittance_rules import (
    transfer_id_from_location, transfer_payload, verified_transfer,
)


ROOT = Path(__file__).parents[1]
TRANSFER_ID = "a652f86f-f2aa-4d70-baa2-ccfe4b78f4fc"


def load_module(name, path, modules):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


def throw(message):
    raise ValueError(message)


class TestBalances(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            create table `tabCaptive Portal Zone` (name text, display_name text);
            create table `tabPortal Checkout` (zone text, amount real, status text, currency text, provider_environment text);
            create table `tabZone Remittance` (zone text, amount real, fee_allowance real, status text, docstatus int, provider_environment text);
            insert into `tabCaptive Portal Zone` values ('north', 'North'), ('south', 'South'), ('empty', 'Empty');
            insert into `tabPortal Checkout` values
              ('north',100,'SUCCESS','KES','live'), ('north',50,'SUCCESS','KES','live'),
              ('north',1000,'PENDING','KES','live'), ('north',1000,'FAILED','KES','live'),
              ('north',999,'SUCCESS','KES','sandbox'), ('south',20,'SUCCESS','KES','live');
            insert into `tabZone Remittance` values
              ('north',30,5,'Queued',1,'live'), ('north',20,2,'Paid',1,'live'),
              ('north',900,0,'Failed',1,'live'), ('north',900,0,'Cancelled',2,'live'),
              ('north',900,0,'Draft',0,'live');
        """)
        self.frappe = MagicMock()
        self.frappe.throw.side_effect = throw
        self.queries = []

        def sql(query, params, as_dict=False):
            self.queries.append(query)
            rows = self.db.execute(query.replace("%s", "?").replace("for update", ""), params).fetchall()
            return [dict(row) for row in rows] if as_dict else [tuple(row) for row in rows]

        self.frappe.db.sql.side_effect = sql
        self.finance = load_module(
            "_zone_finance_test", "network_billing_system/zone_finance.py",
            {"frappe": self.frappe},
        )

    def tearDown(self):
        self.db.close()

    def test_dashboard_totals_do_not_mix_zones_states_or_environments(self):
        rows = {row["zone"]: row for row in self.finance.balances()}
        north = rows["north"]
        self.assertEqual(north["successful_payments"], 2)
        self.assertEqual(north["collected"], 150)
        self.assertEqual(north["reserved"], 35)
        self.assertEqual(north["withdrawn"], 22)
        self.assertEqual(north["available"], Decimal("93"))
        self.assertEqual(rows["south"]["available"], 20)
        self.assertEqual(rows["empty"]["available"], 0)
        self.assertEqual(self.finance.balances("sandbox", "north")[0]["available"], 999)

    def test_external_completed_withdrawal_exposes_a_shortfall(self):
        self.db.execute("insert into `tabZone Remittance` values ('north',100,4,'Paid',1,'live')")
        row = self.finance.balances("live", "north")[0]
        self.assertEqual(row["withdrawn"], 126)
        self.assertEqual(row["available"], -11)

    def test_insufficient_funds_and_locked_current_reads(self):
        self.finance.ensure_funds("north", "live", 93)
        with self.assertRaisesRegex(ValueError, "insufficient"):
            self.finance.ensure_funds("north", "live", 94)
        self.assertTrue(all("for update" in query for query in self.queries))
        self.db.execute("insert into `tabZone Remittance` values ('north',90,0,'Unknown',1,'live')")
        with self.assertRaises(ValueError):
            self.finance.ensure_funds("north", "live", 4)

    def test_invalid_money_and_environment(self):
        for value in ("NaN", "Infinity", "0.001"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.finance.money(value)
        with self.assertRaises(ValueError):
            self.finance.balances("invalid")

    def test_frappe_report_summary_and_chart(self):
        report = load_module(
            "_zone_report_test",
            "network_billing_system/network_billing_system/report/zone_payment_dashboard/zone_payment_dashboard.py",
            {"frappe": self.frappe, "network_billing_system.zone_finance": self.finance},
        )
        columns, rows, _, chart, summary = report.execute()
        self.assertEqual(len(columns), 7)
        self.assertEqual(len(rows), 3)
        self.assertEqual(summary[-1]["value"], 113)
        self.assertEqual(chart["type"], "bar")
        self.frappe.only_for.assert_called_once()

    def test_native_workspace_dashboard_references_and_json_schemas(self):
        module_path = ROOT / "network_billing_system/network_billing_system"
        workspace = json.loads((module_path / "workspace/network_billing_system/network_billing_system.json").read_text())
        chart = json.loads((module_path / "dashboard_chart/zone_payment_overview/zone_payment_overview.json").read_text())
        dashboard = json.loads((module_path / "network_billing_system_dashboard/zone_finance/zone_finance.json").read_text())
        report = json.loads((module_path / "report/zone_payment_dashboard/zone_payment_dashboard.json").read_text())
        self.assertEqual(chart["report_name"], report["name"])
        self.assertEqual(dashboard["charts"][0]["chart"], chart["name"])
        blocks = json.loads(workspace["content"])
        self.assertIn(chart["name"], [block["data"].get("chart_name") for block in blocks])
        self.assertIn(report["name"], [link.get("link_to") for link in workspace["links"]])
        self.assertEqual(json.loads(chart["filters_json"])["provider_environment"], "live")
        for path in module_path.rglob("*.json"):
            json.loads(path.read_text())


class TestRemittanceRules(unittest.TestCase):
    def setUp(self):
        self.doc = types.SimpleNamespace(
            name="remittance-1", zone="north", amount=20, source_identifier="source-1",
            destination_type="mobile_wallet", recipient_name="Test Recipient",
            phone_number="254712345678", network="Safaricom",
        )
        self.payload = {
            "data": {
                "id": TRANSFER_ID,
                "attributes": {
                    "source_identifier": "source-1", "currency": "KES", "status": "Success",
                    "metadata": {"zone_remittance": self.doc.name},
                    "destinations": [dict(
                        transfer_payload(self.doc, "")["destinations"][0], status="Success"
                    )],
                },
            },
        }

    def test_provider_owned_location_only(self):
        base = "https://api.kopokopo.com"
        self.assertEqual(transfer_id_from_location(base + "/api/v2/send_money/" + TRANSFER_ID, base), TRANSFER_ID)
        for location in ("https://evil.example/api/v2/send_money/" + TRANSFER_ID,
                         base + "/api/v2/send_money/" + TRANSFER_ID + "?x=1"):
            with self.assertRaises(ValueError):
                transfer_id_from_location(location, base)

    def test_mobile_and_bank_payloads(self):
        payload = transfer_payload(self.doc, "https://test.example/callback")
        self.assertEqual(payload["metadata"]["zone_remittance"], "remittance-1")
        self.assertEqual(payload["destinations"][0]["phone_number"], "254712345678")
        self.doc.destination_type = "bank_account"
        self.doc.account_name = "Test"
        self.doc.account_number = "123456"
        self.doc.bank_branch_ref = "branch-1"
        destination = transfer_payload(self.doc, "")["destinations"][0]
        self.assertEqual(destination["bank_branch_ref"], "branch-1")
        self.assertNotIn("phone_number", destination)

    def test_confirmed_single_transfer(self):
        self.assertEqual(verified_transfer(self.payload, self.doc, TRANSFER_ID), "Paid")
        attrs = self.payload["data"]["attributes"]
        attrs["status"] = attrs["destinations"][0]["status"] = "Failed"
        self.assertEqual(verified_transfer(self.payload, self.doc, TRANSFER_ID), "Failed")

    def test_wrong_recipient_or_metadata_cannot_release_funds(self):
        attrs = self.payload["data"]["attributes"]
        attrs["destinations"][0]["phone_number"] = "254722222222"
        with self.assertRaises(ValueError):
            verified_transfer(self.payload, self.doc, TRANSFER_ID)
        attrs["destinations"][0]["phone_number"] = self.doc.phone_number
        attrs["metadata"] = {}
        with self.assertRaises(ValueError):
            verified_transfer(self.payload, self.doc, TRANSFER_ID)

    def test_partial_or_unknown_outcome_is_not_paid(self):
        attrs = self.payload["data"]["attributes"]
        attrs["destinations"][0]["status"] = "Pending"
        with self.assertRaises(ValueError):
            verified_transfer(self.payload, self.doc, TRANSFER_ID)
        attrs["status"] = "Pending"
        self.assertEqual(verified_transfer(self.payload, self.doc, TRANSFER_ID), "Pending")


class TestRemittanceAPI(unittest.TestCase):
    def setUp(self):
        self.frappe = MagicMock()
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.conf = {
            "enable_zone_remittance": True,
            "kopokopo_remittance_sandbox_client_id": "sandbox-id",
            "kopokopo_remittance_sandbox_client_secret": "sandbox-secret",
            "kopokopo_remittance_live_client_id": "live-id",
            "kopokopo_remittance_live_client_secret": "live-secret",
        }
        self.frappe.throw.side_effect = throw
        connector_module = types.ModuleType("network_billing_system.kopokopo_integration")
        connector_module.KopokopoConnector = MagicMock()
        self.connector = connector_module.KopokopoConnector.return_value
        self.connector.base_url = "https://sandbox.kopokopo.com"
        self.connector.authentication_token = "test-token"
        utils = types.ModuleType("frappe.utils")
        utils.get_url = lambda path: "https://test.example" + path
        finance = types.ModuleType("network_billing_system.zone_finance")
        finance.FINANCE_ROLES = ("System Manager", "Network Administrator")
        finance.lock_zone = MagicMock()
        finance.ensure_funds = MagicMock()
        captive = types.ModuleType("network_billing_system.api.captive_portal")
        captive._request_limit = MagicMock()
        self.api = load_module(
            "_zone_remittance_api_test", "network_billing_system/api/zone_remittance.py",
            {"frappe": self.frappe, "frappe.utils": utils,
             "network_billing_system.zone_finance": finance,
             "network_billing_system.kopokopo_integration": connector_module,
             "network_billing_system.api.captive_portal": captive},
        )
        self.doc = types.SimpleNamespace(
            name="remittance-1", zone="north", status="Queued", provider_transfer_id=None,
            withdrawal_method="Kopo Kopo API",
            provider_environment="sandbox", docstatus=1, amount=20,
            source_identifier="source-1", destination_type="mobile_wallet",
            recipient_name="Test", phone_number="254712345678", network="Safaricom",
        )
        self.api._lock_remittance = MagicMock(return_value=self.doc)

    def test_remittance_credentials_are_environment_specific(self):
        for environment in ("sandbox", "live"):
            self.api._connector(environment)
            self.api.KopokopoConnector.assert_called_with(
                env=environment, app_key=environment + "-id",
                app_secret=environment + "-secret",
            )

    def test_missing_sandbox_credentials_never_fall_back_to_live_checkout_keys(self):
        self.frappe.conf = {
            "kopokopo_client_id": "checkout-live-id",
            "kopokopo_client_secret": "checkout-live-secret",
        }
        with self.assertRaisesRegex(ValueError, "separate sandbox"):
            self.api._connector("sandbox")
        self.api.KopokopoConnector.assert_not_called()

    def test_disabled_and_unauthorized_requests_cannot_send(self):
        self.frappe.conf = {}
        with self.assertRaises(ValueError):
            self.api.send_remittance(self.doc.name)
        self.api._lock_remittance.assert_not_called()
        self.frappe.only_for.side_effect = PermissionError
        with self.assertRaises(PermissionError):
            self.api.send_remittance(self.doc.name)

    def test_live_sending_is_blocked_until_sandbox_contract_validation(self):
        self.doc.provider_environment = "live"
        with patch.object(self.api.requests, "post") as request, self.assertRaisesRegex(ValueError, "Live remittance"):
            self.api.send_remittance(self.doc.name)
        request.assert_not_called()

    def test_external_withdrawal_cannot_initiate_another_payment(self):
        self.doc.withdrawal_method = "External Withdrawal"
        with patch.object(self.api.requests, "post") as request, self.assertRaisesRegex(ValueError, "ledger reconciliation"):
            self.api.send_remittance(self.doc.name)
        request.assert_not_called()

    def test_intent_is_committed_before_provider_post_and_cannot_be_retried(self):
        response = MagicMock(status_code=201)
        response.headers = {"Location": self.connector.base_url + "/api/v2/send_money/" + TRANSFER_ID}

        def post(*args, **kwargs):
            self.frappe.db.commit.assert_called_once()
            self.frappe.db.set_value.assert_called_with("Zone Remittance", self.doc.name, "status", "Sending")
            self.assertFalse(kwargs["allow_redirects"])
            return response

        with patch.object(self.api.requests, "post", side_effect=post) as request:
            result = self.api.send_remittance(self.doc.name)
            self.assertEqual(result["status"], "Pending")
            self.doc.status = "Pending"
            with self.assertRaises(ValueError):
                self.api.send_remittance(self.doc.name)
            request.assert_called_once()

    def test_timeout_keeps_reservation_and_logs_error(self):
        with patch.object(self.api.requests, "post", side_effect=self.api.requests.Timeout("timeout")):
            result = self.api.send_remittance(self.doc.name)
        self.assertEqual(result["status"], "Unknown")
        self.frappe.db.set_value.assert_called_with(
            "Zone Remittance", self.doc.name, {"status": "Unknown", "last_error": "timeout"}
        )
        self.frappe.log_error.assert_called_once()

    def test_late_failure_cannot_overwrite_verified_paid_transfer(self):
        self.doc.status = "Paid"
        result = self.api._failure(self.doc, ValueError("late timeout"))
        self.assertEqual(result["status"], "Paid")
        self.frappe.db.set_value.assert_not_called()

    def test_callback_never_trusts_supplied_status(self):
        self.doc.status = "Pending"
        self.doc.provider_transfer_id = TRANSFER_ID
        response = MagicMock(status_code=200)
        response.json.return_value = {"data": {"id": TRANSFER_ID, "attributes": {"status": "Success"}}}
        with patch.object(self.api.requests, "get", return_value=response) as request:
            result = self.api.transfer_callback(self.doc.name, status="Success")
        request.assert_called_once()
        self.assertEqual(result["status"], "Unknown")
        self.frappe.log_error.assert_called_once()


class TestRemittanceDocument(unittest.TestCase):
    def setUp(self):
        self.frappe = MagicMock()
        self.frappe.throw.side_effect = throw
        model = types.ModuleType("frappe.model.document")
        model.Document = object
        self.finance = load_module(
            "_finance_for_document", "network_billing_system/zone_finance.py",
            {"frappe": self.frappe},
        )
        self.finance.ensure_funds = MagicMock()
        self.finance.lock_zone = MagicMock()
        module = load_module(
            "_remittance_document",
            "network_billing_system/network_billing_system/doctype/zone_remittance/zone_remittance.py",
            {"frappe": self.frappe, "frappe.model.document": model,
             "network_billing_system.zone_finance": self.finance},
        )
        self.doc = module.ZoneRemittance()
        self.doc.name = "remittance-1"
        self.doc.withdrawal_method = "Kopo Kopo API"
        self.doc.zone = "north"
        self.doc.provider_environment = "live"
        self.doc.amount = 20
        self.doc.fee_allowance = 2
        self.doc.destination_type = "mobile_wallet"
        self.doc.phone_number = "0712345678"
        self.doc.network = "Safaricom"
        self.doc.source_identifier = "source-1"
        self.doc.recipient_name = "Test"
        self.doc.docstatus = 0
        self.doc.is_new = lambda: True
        self.doc.db_set = MagicMock()

    def test_draft_cannot_supply_a_paid_status_or_provider_uuid(self):
        self.doc.status = "Paid"
        self.doc.provider_transfer_id = TRANSFER_ID
        self.doc.validate()
        self.assertEqual(self.doc.status, "Draft")
        self.assertIsNone(self.doc.provider_transfer_id)
        self.assertEqual(self.doc.phone_number, "254712345678")

    def test_submission_checks_amount_plus_allowance_and_only_queues(self):
        self.doc.before_submit()
        self.doc.on_submit()
        self.finance.ensure_funds.assert_called_once_with("north", "live", Decimal(22))
        self.doc.db_set.assert_called_once_with("status", "Queued")

    def test_cancellation_cannot_release_uncertain_or_paid_transfer(self):
        for status in ("Sending", "Pending", "Unknown", "Paid"):
            self.frappe.db.get_value.return_value = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.doc.before_cancel()
        self.frappe.db.get_value.return_value = "Queued"
        self.doc.before_cancel()

    def test_negative_amount_or_fee_is_rejected(self):
        for amount, fee in ((0, 0), (-1, 0), (20, -1)):
            self.doc.amount, self.doc.fee_allowance = amount, fee
            with self.subTest(amount=amount, fee=fee), self.assertRaises(ValueError):
                self.doc.validate()

    def test_external_withdrawal_requires_private_evidence_and_verification(self):
        self.doc.withdrawal_method = "External Withdrawal"
        self.doc.external_reference = "external-1"
        self.doc.withdrawn_at = "2026-11-01 13:00:00"
        self.doc.withdrawal_evidence = "/private/files/statement.pdf"
        self.doc.external_verified = 0
        with self.assertRaises(ValueError):
            self.doc.validate()
        self.doc.external_verified = 1
        self.frappe.db.get_value.return_value = None
        with self.assertRaisesRegex(ValueError, "private"):
            self.doc.validate()
        self.frappe.db.get_value.return_value = "private-file"
        self.doc.validate()

    def test_external_withdrawal_records_paid_without_sending_or_rejecting_shortfall(self):
        self.doc.withdrawal_method = "External Withdrawal"
        self.doc.external_reference = TRANSFER_ID
        self.doc.before_submit()
        self.assertEqual(self.doc.provider_transfer_id, TRANSFER_ID)
        self.doc.on_submit()
        self.doc.db_set.assert_called_once_with("status", "Paid")
        self.finance.lock_zone.assert_called_once_with("north")
        self.finance.ensure_funds.assert_not_called()

    def test_non_uuid_external_reference_is_preserved(self):
        self.doc.withdrawal_method = "External Withdrawal"
        self.doc.external_reference = "statement-transaction-1"
        self.doc.before_submit()
        self.assertIsNone(self.doc.provider_transfer_id)
        self.assertEqual(self.doc.external_reference, "statement-transaction-1")
