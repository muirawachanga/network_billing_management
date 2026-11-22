import importlib.util
import json
import sys
import types
import unittest
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).parents[1]
DOCTYPES = ROOT / "network_billing_system/network_billing_system/doctype"


class Doc(types.SimpleNamespace):
    def get_doc_before_save(self):
        return getattr(self, "_previous", None)

    def get_password(self, field, raise_exception=False):
        return getattr(self, "_passwords", {}).get(field)


class ConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.conf = {}
        self.frappe.db = MagicMock()
        self.frappe.db.get_value.return_value = None
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.only_for = MagicMock()
        self.frappe.log_error = MagicMock()
        self.frappe.get_roles = MagicMock(return_value=["System Manager"])
        self.frappe.session = types.SimpleNamespace(user="approver@example.test")
        def throw(message):
            raise ValueError(message)
        self.frappe.throw = MagicMock(side_effect=throw)
        self.frappe.get_single = MagicMock()
        self.frappe.get_doc = MagicMock(side_effect=lambda doctype, name: self.docs[(doctype, name)])
        self.utils = types.ModuleType("frappe.utils")
        self.utils.today = lambda: "2026-11-02"
        self.utils.now_datetime = lambda: datetime(2026, 11, 2, 8, 0)
        self.modules = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": self.utils})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location(
            "_transfer_configuration_test", ROOT / "network_billing_system/transfer_configuration.py"
        )
        self.config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.config)
        self.docs = {}
        self.provider = self.add("Transfer Provider Account", Doc(
            name="sandbox-account", account_name="Sandbox", environment="sandbox",
            enabled=1, use_existing_live_credentials=1, source_identifier="source-1",
            client_id="sandbox-client", _passwords={"client_secret": "sandbox-secret"},
        ))
        self.identity = self.add("Transfer Recipient", Doc(
            name="owner", recipient_name="Example Owner", enabled=1,
        ))
        self.settings = Doc(enabled=0, allow_live=0, scheduler_enabled=0,
                            approval_threshold="0", max_request_amount="0", reconciliation_limit=100)
        self.frappe.get_single.return_value = self.settings
        self.fee = self.add("Transfer Fee Policy", Doc(
            name="standard", enabled=1, currency="KES", destination_type="till",
            settlement_method="", effective_from="2026-01-01", effective_to="",
            fixed_fee="10.00", percentage="1.5", minimum_fee="", fee_bearer="Zone",
        ))
        self.account = self.make_account("till")

    def add(self, doctype, doc):
        self.docs[(doctype, doc.name)] = doc
        return doc

    def make_account(self, kind, **overrides):
        values = dict(
            name="account-1", account_label="Owner Account", recipient="owner",
            provider_account="sandbox-account", zone="north", currency="KES",
            destination_type=kind, readiness="Verified",
            first_name="Example", last_name="Owner", phone_number="+254712345678",
            network="Safaricom", account_name="Owner", account_number="001234",
            bank_branch_ref="branch-1", settlement_method="RTS" if kind == "bank_account" else "EFT", till_name="Owner Till",
            till_number="001234", paybill_name="Owner Paybill", paybill_number="004321",
            paybill_account_number="customer-1", registration_resource_id="", registration_state="Draft",
            payout_reference="payout-1", verification_confirmed=1,
            verification_evidence="Provider-app evidence ref evidence-1",
            verification_note="Checked by reviewer", verified_by="approver@example.test",
            verified_on=datetime(2026, 11, 2, 7, 0),
        )
        values.update(overrides)
        return self.add("Transfer Recipient Account", Doc(**values))

    def make_policy(self, **overrides):
        values = dict(
            name="north-policy", zone="north", provider_account="sandbox-account",
            recipient_account="account-1", fee_policy="standard", fee_bearer="Zone",
            currency="KES", enabled=0, schedule_enabled=0, approval_needed=1,
            retained_balance="100.00", maximum_amount="1000.00", minimum_payout="0",
            schedule="Daily",
        )
        values.update(overrides)
        return Doc(**values)

    def test_settings_are_disabled_and_zero_limit_is_not_unlimited(self):
        self.assertIs(self.config.get_settings(), self.settings)
        self.settings.enabled = 1
        with self.assertRaisesRegex(ValueError, "positive maximum"):
            self.config.get_settings()
        self.settings.max_request_amount = "5000.00"
        self.assertIs(self.config.get_settings(), self.settings)

    def test_settings_reject_invalid_switches_limits_and_amounts(self):
        for field, value in (
            ("enabled", "yes"), ("max_request_amount", "-1"), ("approval_threshold", "NaN"),
            ("reconciliation_limit", "1.5"), ("reconciliation_limit", 0),
            ("reconciliation_limit", 1001), ("reconciliation_limit", None),
            ("scheduler_enabled", 1),
        ):
            with self.subTest(field=field, value=value):
                settings = deepcopy(self.settings)
                setattr(settings, field, value)
                with self.assertRaises(ValueError):
                    self.config.validate_settings(settings)

    def test_exact_money_rejects_bad_values(self):
        for value in ("NaN", "Infinity", "-1", "1.001", True, {}, "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.config.money(value)
        self.assertEqual(self.config.money("1000000000.10"), Decimal("1000000000.10"))
        self.assertEqual(self.config.money(None), Decimal("0.00"))

    def test_explicit_provider_credentials_are_decrypted_not_exposed_in_snapshots(self):
        self.provider.get_password = MagicMock(side_effect=lambda field, **kw: {
            "client_secret": "secret-from-password-store", "webhook_secret": "webhook-from-password-store",
        }.get(field))
        provider = self.config.get_provider(self.provider.name)
        self.assertEqual(provider["client_secret"], "secret-from-password-store")
        self.assertEqual(provider["webhook_secret"], "webhook-from-password-store")
        self.provider.get_password.assert_any_call("client_secret", raise_exception=False)
        snapshot = self.config.recipient_snapshot(self.account.name, "north", provider)
        serialized = json.dumps(snapshot)
        for secret in ("client_secret", "webhook_secret", "secret-from-password-store", "webhook-from-password-store"):
            self.assertNotIn(secret, serialized)

    def test_sandbox_never_uses_live_credentials(self):
        self.provider.client_id = ""
        self.provider._passwords = {}
        self.frappe.conf.update(kopokopo_client_id="live-client", kopokopo_client_secret="live-secret",
                                kopokopo_remittance_live_client_id="other-live",
                                kopokopo_remittance_live_client_secret="other-secret")
        with self.assertRaisesRegex(ValueError, "sandbox"):
            self.config.get_provider(self.provider.name)
        self.frappe.conf.update(kopokopo_remittance_sandbox_client_id="sandbox-from-config",
                                kopokopo_remittance_sandbox_client_secret="sandbox-config-secret")
        self.assertEqual(self.config.get_provider(self.provider.name)["client_id"], "sandbox-from-config")

    def test_live_existing_credentials_are_explicitly_opted_in(self):
        self.provider.environment = "live"
        self.provider.client_id = ""
        self.provider._passwords = {}
        self.frappe.conf.update(kopokopo_client_id="legacy-live", kopokopo_client_secret="legacy-secret")
        self.assertEqual(self.config.get_provider(self.provider.name)["client_id"], "legacy-live")
        self.provider.use_existing_live_credentials = 0
        with self.assertRaises(ValueError):
            self.config.get_provider(self.provider.name)
        self.provider.client_id = "explicit-live"
        self.provider._passwords = {"client_secret": "explicit-secret"}
        self.assertEqual(self.config.get_provider(self.provider.name)["client_secret"], "explicit-secret")

    def test_partial_pairs_never_mix_or_fallback(self):
        self.frappe.conf.update(kopokopo_client_id="legacy", kopokopo_client_secret="legacy-secret",
                                kopokopo_remittance_sandbox_client_id="site-client",
                                kopokopo_remittance_sandbox_client_secret="site-secret")
        self.provider._passwords = {}
        with self.assertRaisesRegex(ValueError, "complete"):
            self.config.get_provider(self.provider.name)
        self.provider.client_id = ""
        self.provider.environment = "live"
        self.frappe.conf["kopokopo_remittance_live_client_id"] = "partial-live"
        with self.assertRaisesRegex(ValueError, "complete"):
            self.config.get_provider(self.provider.name)

    def test_disabled_invalid_provider_and_environment_changes_rejected(self):
        self.provider.enabled = 0
        with self.assertRaisesRegex(ValueError, "disabled"):
            self.config.get_provider(self.provider.name)
        self.provider.enabled = 1
        self.provider.environment = "production"
        with self.assertRaises(ValueError):
            self.config.validate_provider(self.provider)
        self.provider.environment = "live"
        self.provider._previous = Doc(environment="sandbox")
        with self.assertRaisesRegex(ValueError, "cannot be changed"):
            self.config.validate_provider(self.provider)

    def test_connector_always_passes_explicit_fresh_credentials(self):
        integration = types.ModuleType("network_billing_system.kopokopo_integration")
        integration.KopokopoConnector = MagicMock()
        with patch.dict(sys.modules, {integration.__name__: integration}):
            connector = self.config.provider_connector({"name": self.provider.name, "client_secret": "untrusted"})
        self.assertIs(connector, integration.KopokopoConnector.return_value)
        integration.KopokopoConnector.assert_called_once_with(
            env="sandbox", app_key="sandbox-client", app_secret="sandbox-secret"
        )

    def test_sandbox_connector_never_uses_existing_live_keys(self):
        self.provider.client_id = ""
        self.provider._passwords = {}
        self.provider.use_existing_live_credentials = 1
        self.frappe.conf.update(
            kopokopo_client_id="legacy-live", kopokopo_client_secret="legacy-secret",
            kopokopo_remittance_live_client_id="live-client",
            kopokopo_remittance_live_client_secret="live-secret",
        )
        integration = types.ModuleType("network_billing_system.kopokopo_integration")
        integration.KopokopoConnector = MagicMock()
        with patch.dict(sys.modules, {integration.__name__: integration}):
            with self.assertRaisesRegex(ValueError, "sandbox"):
                self.config.provider_connector(self.provider)
            integration.KopokopoConnector.assert_not_called()
            self.frappe.conf.update(
                kopokopo_remittance_sandbox_client_id="sandbox-site-client",
                kopokopo_remittance_sandbox_client_secret="sandbox-site-secret",
            )
            self.config.provider_connector(self.provider)
        integration.KopokopoConnector.assert_called_once_with(
            env="sandbox", app_key="sandbox-site-client", app_secret="sandbox-site-secret"
        )

    def test_submitted_requests_lock_provider_source_and_environment(self):
        self.provider._previous = deepcopy(self.provider)
        self.frappe.db.get_value.return_value = "submitted-request-1"
        self.provider.source_identifier = "another-source"
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.config.validate_provider(self.provider)
        self.frappe.db.get_value.assert_called_with(
            "Transfer Request", {"provider_account": self.provider.name, "docstatus": 1}, "name"
        )
        self.provider.source_identifier = self.provider._previous.source_identifier
        self.provider.environment = "live"
        with self.assertRaisesRegex(ValueError, "cannot be changed"):
            self.config.validate_provider(self.provider)

    def test_unreferenced_provider_source_can_be_configured(self):
        self.provider._previous = deepcopy(self.provider)
        self.provider.source_identifier = "new-source"
        self.config.validate_provider(self.provider)
        self.provider._previous = deepcopy(self.provider)
        self.frappe.db.get_value.return_value = "submitted-request-1"
        self.config.validate_provider(self.provider)

    def test_identity_phone_normalization(self):
        self.identity.phone_number = "0712 345 678"
        self.config.validate_recipient(self.identity)
        self.assertEqual(self.identity.phone_number, "+254712345678")
        self.identity.recipient_name = " "
        with self.assertRaises(ValueError):
            self.config.validate_recipient(self.identity)

    def test_all_six_snapshots_have_provider_destination_contracts(self):
        expected = {
            "mobile_wallet": {"phone_number", "network"},
            "bank_account": {"account_name", "account_number", "bank_branch_ref"},
            "till": {"till_number"},
            "paybill": {"paybill_number", "paybill_account_number"},
            "merchant_wallet": {"reference"},
            "merchant_bank_account": {"reference"},
        }
        for kind, fields in expected.items():
            with self.subTest(kind=kind):
                account = self.make_account(kind)
                snapshot = self.config.recipient_snapshot(account.name, "north", self.provider)
                self.assertEqual(set(snapshot["destination_fields"]), fields)
                self.assertEqual(
                    snapshot["destination"], {"type": kind, **snapshot["destination_fields"]}
                )
                self.assertEqual(snapshot["type"], kind)
                self.assertEqual(snapshot["environment"], "sandbox")
                self.assertEqual(snapshot["recipient_account"], account.name)
                self.assertEqual(snapshot["payout_reference"], "payout-1")
                self.assertNotIn("registration_resource_id", snapshot)
                json.dumps(snapshot)
                if kind.startswith("merchant_"):
                    self.assertEqual(snapshot["destination_fields"]["reference"], "payout-1")
                if kind == "mobile_wallet":
                    self.assertEqual(snapshot["destination_fields"]["phone_number"], "254712345678")

    def test_snapshot_rejects_wrong_zone_provider_disabled_or_unverified(self):
        for zone, provider in (("south", self.provider.name), ("north", "another-provider")):
            with self.subTest(zone=zone, provider=provider), self.assertRaises(ValueError):
                self.config.recipient_snapshot(self.account.name, zone, provider)
        for field, value in (
            ("readiness", "Draft"), ("readiness", "Disabled"),
            ("verification_confirmed", 0), ("verification_evidence", ""),
            ("verified_by", ""), ("verified_on", None), ("payout_reference", ""),
        ):
            with self.subTest(field=field, value=value):
                self.account = self.make_account("till", **{field: value})
                with self.assertRaises(ValueError):
                    self.config.recipient_snapshot(self.account.name, "north", self.provider.name)
        self.account = self.make_account("till")
        self.identity.enabled = 0
        with self.assertRaises(ValueError):
            self.config.recipient_snapshot(self.account.name, "north", self.provider.name)
        self.identity.enabled = 1
        self.provider.enabled = 0
        with self.assertRaises(ValueError):
            self.config.recipient_snapshot(self.account.name, "north", self.provider.name)

    def test_manual_verification_stamps_actual_actor_and_time(self):
        self.account.verified_by = self.account.verified_on = None
        self.config.validate_recipient_account(self.account)
        self.assertEqual(self.account.verified_by, "approver@example.test")
        self.assertEqual(self.account.verified_on, datetime(2026, 11, 2, 8, 0))
        self.assertNotEqual(self.account.registration_resource_id, self.account.payout_reference)

    def test_manual_verification_needs_confirmation_evidence_payout_and_role(self):
        for field, value in (
            ("verification_confirmed", 0), ("verification_evidence", ""),
            ("payout_reference", ""), ("payout_reference", "https://provider.test/registration/123"),
        ):
            with self.subTest(field=field):
                account = self.make_account("till", verified_by=None, verified_on=None, **{field: value})
                with self.assertRaises(ValueError):
                    self.config.validate_recipient_account(account)
        self.frappe.get_roles.return_value = ["Transfer Preparer"]
        account = self.make_account("till", verified_by=None, verified_on=None)
        with self.assertRaisesRegex(ValueError, "Approver"):
            self.config.validate_recipient_account(account)

    def test_verification_fields_and_all_destination_fields_become_immutable(self):
        for field in self.config.ACCOUNT_FIELDS:
            with self.subTest(field=field):
                account = self.make_account("till")
                account._previous = deepcopy(account)
                setattr(account, field, "changed")
                with self.assertRaises(ValueError):
                    self.config.validate_recipient_account(account)

    def test_verified_can_be_disabled_but_cannot_be_reset_or_reenabled_by_preparer(self):
        self.account._previous = deepcopy(self.account)
        self.account.readiness = "Disabled"
        self.config.validate_recipient_account(self.account)
        self.account.readiness = "Draft"
        with self.assertRaisesRegex(ValueError, "Draft"):
            self.config.validate_recipient_account(self.account)
        self.account.readiness = "Disabled"
        old = deepcopy(self.account)
        old._previous = None
        self.account._previous = old
        self.account.readiness = "Verified"
        self.frappe.get_roles.return_value = ["Transfer Preparer"]
        with self.assertRaisesRegex(ValueError, "Approver"):
            self.config.validate_recipient_account(self.account)

    def test_verified_timestamp_and_check_survive_frappe_json_round_trip(self):
        self.account._previous = deepcopy(self.account)
        self.account.verified_on = "2026-11-02 07:00:00.000000"
        self.account.verification_confirmed = "1"
        self.config.validate_recipient_account(self.account)

    def test_draft_rejects_spoofed_verification_metadata(self):
        account = self.make_account("till", readiness="Draft")
        with self.assertRaisesRegex(ValueError, "audit"):
            self.config.validate_recipient_account(account)

    def test_destination_validation_rejects_unknown_incomplete_or_invalid_bank_details(self):
        for kind, field, value in (
            ("unknown", "till_name", "Till"), ("till", "till_number", "A123"),
            ("paybill", "paybill_account_number", ""),
            ("mobile_wallet", "phone_number", "1234"), ("mobile_wallet", "network", "Other"),
            ("bank_account", "settlement_method", ""), ("bank_account", "settlement_method", "SWIFT"),
            ("bank_account", "settlement_method", "EFT"),
            ("merchant_bank_account", "bank_branch_ref", ""),
        ):
            with self.subTest(kind=kind, field=field):
                account = self.make_account(kind, **{field: value})
                with self.assertRaises(ValueError):
                    self.config.validate_recipient_account(account)

    def test_exact_fee_fixed_percent_minimum_and_round_once(self):
        self.assertEqual(self.config.quote_fee(self.fee, "1000.00"), Decimal("25.00"))
        self.fee.minimum_fee = "30.00"
        self.assertEqual(self.config.quote_fee(self.fee.name, "1000.00"), Decimal("30.00"))
        self.fee.minimum_fee = None
        self.fee.fixed_fee = "0.00"
        self.fee.percentage = "0.5"
        self.assertEqual(self.config.quote_fee(self.fee, "1.00"), Decimal("0.01"))
        self.fee.percentage = "0"
        self.assertEqual(self.config.quote_fee(self.fee, "1000.00"), Decimal("0.00"))

    def test_fee_date_range_is_inclusive_and_effective(self):
        self.fee.effective_from = self.fee.effective_to = "2026-11-02"
        self.assertEqual(self.config.quote_fee(self.fee, "1000.00"), Decimal("25.00"))
        for start, end in (("2026-11-03", ""), ("2026-01-01", "2026-11-01"),
                           ("2026-11-03", "2026-11-02"), ("bad", "")):
            self.fee.effective_from, self.fee.effective_to = start, end
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.config.quote_fee(self.fee, "1000.00")

    def test_fee_rejects_disabled_invalid_amounts_and_configuration(self):
        for field, value in (
            ("enabled", 0), ("currency", "USD"), ("percentage", "NaN"), ("percentage", "-1"),
            ("percentage", "101"), ("fixed_fee", "-1"), ("fixed_fee", "0.001"),
            ("minimum_fee", "-1"), ("fee_bearer", "Unknown"), ("settlement_method", "EFT"),
        ):
            with self.subTest(field=field, value=value):
                fee = deepcopy(self.fee)
                setattr(fee, field, value)
                with self.assertRaises(ValueError):
                    self.config.quote_fee(fee, "1000.00")
        for amount in ("0", "-1", "0.001", "NaN"):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.config.quote_fee(self.fee, amount)

    def test_zone_policy_checks_unique_tariff_matching_destination_and_schedule(self):
        policy = self.make_policy()
        self.config.validate_zone_policy(policy)
        self.frappe.db.get_value.return_value = policy.name
        self.config.validate_zone_policy(policy)
        self.frappe.db.get_value.return_value = "duplicate-policy"
        with self.assertRaisesRegex(ValueError, "Only one"):
            self.config.validate_zone_policy(policy)
        self.frappe.db.get_value.return_value = None
        for field, value in (
            ("zone", "south"), ("provider_account", "other"), ("maximum_amount", "0"),
            ("retained_balance", "-1"), ("minimum_payout", "1001"),
            ("schedule", "Hourly"), ("schedule_enabled", 1), ("fee_bearer", "Recipient"),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.config.validate_zone_policy(self.make_policy(**{field: value}))

    def test_same_zone_provider_can_have_separate_destination_tariffs(self):
        stored = [self.make_policy()]
        def find_policy(doctype, filters, field):
            self.assertEqual(doctype, "Zone Remittance Policy")
            self.assertEqual(set(filters), {"zone", "provider_account", "fee_policy"})
            return next((
                row.name for row in stored
                if all(getattr(row, key) == value for key, value in filters.items())
            ), None)
        self.frappe.db.get_value.side_effect = find_policy
        account = self.make_account("mobile_wallet", name="mobile-account")
        fee = deepcopy(self.fee)
        fee.name = "mobile-tariff"
        fee.destination_type = "mobile_wallet"
        self.add("Transfer Fee Policy", fee)
        policy = self.make_policy(
            name="north-mobile", recipient_account=account.name, fee_policy=fee.name,
        )
        self.config.validate_zone_policy(policy)
        stored.append(policy)
        self.config.validate_zone_policy(policy)
        duplicate = deepcopy(policy)
        duplicate.name = "duplicate-mobile"
        with self.assertRaisesRegex(ValueError, "Only one"):
            self.config.validate_zone_policy(duplicate)

    def test_enabling_zone_policy_requires_verified_account_and_effective_fee(self):
        policy = self.make_policy(enabled=1, schedule_enabled=1, minimum_payout="100")
        self.config.validate_zone_policy(policy)
        self.account.readiness = "Draft"
        with self.assertRaises(ValueError):
            self.config.validate_zone_policy(policy)
        self.account.readiness = "Verified"
        self.fee.enabled = 0
        with self.assertRaises(ValueError):
            self.config.validate_zone_policy(policy)
        self.fee.enabled = 1
        self.fee.effective_to = "2026-11-01"
        with self.assertRaises(ValueError):
            self.config.validate_zone_policy(policy)

    def test_fee_destination_and_bank_settlement_must_match_zone_account(self):
        policy = self.make_policy()
        self.fee.destination_type = "mobile_wallet"
        with self.assertRaises(ValueError):
            self.config.validate_zone_policy(policy)
        self.account = self.make_account("merchant_bank_account")
        self.fee.destination_type = "merchant_bank_account"
        self.fee.settlement_method = "RTS"
        with self.assertRaises(ValueError):
            self.config.validate_zone_policy(policy)
        self.fee.settlement_method = "EFT"
        self.config.validate_zone_policy(policy)

    def registration_setup(self, kind="bank_account"):
        self.settings.enabled = 1
        self.settings.max_request_amount = "5000.00"
        account = self.make_account(
            kind, readiness="Draft", payout_reference="", verification_confirmed=0,
            verified_by=None, verified_on=None,
        )
        self.frappe.db.sql.return_value = [{"name": account.name}]
        self.frappe.get_doc.side_effect = lambda *args: (
            account if isinstance(args[0], dict) else self.docs[(args[0], args[1])]
        )
        connector = types.SimpleNamespace(
            base_url="https://sandbox.kopokopo.com", authentication_token="private-token"
        )
        self.config.provider_connector = MagicMock(return_value=connector)
        requests = types.ModuleType("requests")
        requests.post = MagicMock()
        response = requests.post.return_value
        response.status_code = 201
        response.headers = {
            "Location": connector.base_url + "/api/v2/external_recipients/a652f86f-f2aa-4d70-baa2-ccfe4b78f4fc"
        }
        return account, requests

    def test_registration_commits_intent_before_post_and_never_sets_payout_reference(self):
        account, requests = self.registration_setup()
        response = requests.post.return_value
        def post(url, **kwargs):
            self.frappe.db.commit.assert_called_once()
            self.frappe.db.set_value.assert_called_with(
                "Transfer Recipient Account", account.name, "registration_state", "Registering"
            )
            self.assertEqual(kwargs["json"]["external_recipient"]["account_number"], "001234")
            self.assertEqual(account.settlement_method, "RTS")
            self.assertEqual(kwargs["timeout"], 15)
            self.assertFalse(kwargs["allow_redirects"])
            return response
        requests.post.side_effect = post
        with patch.dict(sys.modules, {"requests": requests}):
            result = self.config.register_account(account.name)
        self.assertEqual(result["registration_state"], "Created")
        self.frappe.only_for.assert_called_once_with(("System Manager", "Transfer Approver"))
        self.frappe.db.set_value.assert_called_with("Transfer Recipient Account", account.name, {
            "registration_state": "Created",
            "registration_resource_id": "a652f86f-f2aa-4d70-baa2-ccfe4b78f4fc",
        })
        self.assertEqual(account.payout_reference, "")
        self.assertEqual(account.readiness, "Draft")
        self.assertNotIn("private-token", json.dumps(result))

    def test_registration_ambiguous_failures_are_unknown_and_sanitized(self):
        for failure in ("timeout", "wrong-host", "invalid-id", "unexpected-status"):
            with self.subTest(failure=failure):
                account, requests = self.registration_setup()
                response = requests.post.return_value
                if failure == "timeout":
                    requests.post.side_effect = RuntimeError("private-token secret payload")
                elif failure == "wrong-host":
                    response.headers["Location"] = "https://evil.test/api/v2/external_recipients/id"
                elif failure == "invalid-id":
                    response.headers["Location"] = "https://sandbox.kopokopo.com/api/v2/external_recipients/not-an-id"
                else:
                    response.status_code = 200
                with patch.dict(sys.modules, {"requests": requests}):
                    result = self.config.register_account(account.name)
                self.assertEqual(result["registration_state"], "Unknown")
                self.frappe.db.set_value.assert_called_with(
                    "Transfer Recipient Account", account.name, "registration_state", "Unknown"
                )
                self.assertNotIn("private-token", str(self.frappe.log_error.call_args))
                self.assertNotIn("payload", str(self.frappe.log_error.call_args))

    def test_registration_guards_block_duplicates_disabled_and_live(self):
        for field, value in (
            ("readiness", "Verified"), ("registration_state", "Registering"),
            ("registration_state", "Created"), ("registration_state", "Unknown"),
            ("registration_resource_id", "existing-resource"),
        ):
            with self.subTest(field=field, value=value):
                account, requests = self.registration_setup()
                setattr(account, field, value)
                with patch.dict(sys.modules, {"requests": requests}), self.assertRaises(ValueError):
                    self.config.register_account(account.name)
                requests.post.assert_not_called()
        account, requests = self.registration_setup()
        self.settings.enabled = 0
        with self.assertRaises(ValueError):
            self.config.register_account(account.name)
        self.settings.enabled = 1
        self.provider.environment = "live"
        with self.assertRaisesRegex(ValueError, "Live"):
            self.config.register_account(account.name)
        self.config.provider_connector.assert_not_called()

    def test_registration_permission_denial_has_no_intent_and_auth_failure_is_sanitized(self):
        account, requests = self.registration_setup()
        self.frappe.only_for.side_effect = ValueError("Not permitted")
        with self.assertRaises(ValueError):
            self.config.register_account(account.name)
        self.frappe.db.commit.assert_not_called()
        self.frappe.only_for.side_effect = None
        self.config.provider_connector.side_effect = ValueError("secret-authentication-details")
        with patch.dict(sys.modules, {"requests": requests}):
            result = self.config.register_account(account.name)
        self.assertEqual(result["registration_state"], "Unknown")
        self.frappe.db.commit.assert_called_once()
        self.assertNotIn("secret-authentication-details", str(result))
        self.assertNotIn("secret-authentication-details", str(self.frappe.log_error.call_args))
        requests.post.assert_not_called()

    def test_registration_audit_fields_cannot_be_set_through_document_save(self):
        account = self.make_account("till", registration_resource_id="manual-id")
        with self.assertRaisesRegex(ValueError, "audit"):
            self.config.validate_recipient_account(account)
        account = self.make_account("till")
        account._previous = deepcopy(account)
        account.registration_state = "Created"
        with self.assertRaisesRegex(ValueError, "audit"):
            self.config.validate_recipient_account(account)


class SchemaTest(unittest.TestCase):
    def schemas(self):
        for slug in (
            "transfer_settings", "transfer_provider_account", "transfer_recipient",
            "transfer_recipient_account", "transfer_fee_policy", "zone_remittance_policy",
        ):
            path = DOCTYPES / slug
            yield slug, json.loads((path / (slug + ".json")).read_text())

    def test_frappe_schema_order_fields_roles_and_disabled_defaults(self):
        expected_roles = {
            "System Manager", "Transfer Preparer", "Transfer Approver",
            "Transfer Operator", "Transfer Reconciler",
        }
        for slug, schema in self.schemas():
            with self.subTest(doctype=slug):
                self.assertEqual(schema["doctype"], "DocType")
                self.assertEqual(schema["module"], "Network Billing System")
                self.assertEqual(schema["field_order"], [field["fieldname"] for field in schema["fields"]])
                self.assertEqual({row["role"] for row in schema["permissions"]}, expected_roles)
                self.assertFalse(any(row.get("delete") or row.get("export") for row in schema["permissions"]))
                fields = {field["fieldname"]: field for field in schema["fields"]}
                for switch in ("enabled", "allow_live", "scheduler_enabled", "schedule_enabled"):
                    if switch in fields:
                        self.assertEqual(fields[switch]["default"], "0")
                self.assertTrue((DOCTYPES / slug / "__init__.py").exists())
                self.assertTrue((DOCTYPES / slug / (slug + ".py")).exists())

    def test_passwords_restricted_to_system_manager_and_account_drafts(self):
        schemas = dict(self.schemas())
        provider = schemas["transfer_provider_account"]
        fields = {field["fieldname"]: field for field in provider["fields"]}
        for field in ("client_secret", "webhook_secret"):
            self.assertEqual(fields[field]["fieldtype"], "Password")
            self.assertEqual(fields[field]["permlevel"], 1)
        secret_permissions = [row for row in provider["permissions"] if row.get("permlevel") == 1]
        self.assertEqual([row["role"] for row in secret_permissions], ["System Manager"])
        self.assertEqual(fields["use_existing_live_credentials"]["default"], "1")
        account_fields = {field["fieldname"]: field for field in schemas["transfer_recipient_account"]["fields"]}
        self.assertEqual(account_fields["readiness"]["default"], "Draft")
        self.assertEqual(account_fields["verification_confirmed"]["default"], "0")
        self.assertEqual(schemas["transfer_settings"]["issingle"], 1)

    def test_all_controllers_delegate_validation(self):
        document_module = types.ModuleType("frappe.model.document")
        document_module.Document = type("Document", (), {})
        config_module = types.ModuleType("network_billing_system.transfer_configuration")
        validations = {
            "transfer_settings": "validate_settings",
            "transfer_provider_account": "validate_provider",
            "transfer_recipient": "validate_recipient",
            "transfer_recipient_account": "validate_recipient_account",
            "transfer_fee_policy": "validate_fee_policy",
            "zone_remittance_policy": "validate_zone_policy",
        }
        for validator in validations.values():
            setattr(config_module, validator, MagicMock())
        with patch.dict(sys.modules, {
            "frappe.model.document": document_module,
            "network_billing_system.transfer_configuration": config_module,
        }):
            for slug, validator in validations.items():
                with self.subTest(doctype=slug):
                    spec = importlib.util.spec_from_file_location(
                        "_controller_" + slug, DOCTYPES / slug / (slug + ".py")
                    )
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    controller = getattr(module, "".join(word.title() for word in slug.split("_")))()
                    controller.validate()
                    getattr(config_module, validator).assert_called_once_with(controller)


if __name__ == "__main__":
    unittest.main()
