import importlib.util
import sys
import types
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch


class Doc(dict):
    def __getattr__(self, field):
        return self.get(field)


class TestTransferTasks(unittest.TestCase):
    def setUp(self):
        self.frappe = MagicMock()
        self.frappe.ValidationError = ValueError
        self.frappe.db.exists.return_value = False
        self.utils = types.ModuleType("frappe.utils")
        self.utils.getdate = lambda value: date(2026, 11, 2)
        self.utils.nowdate = lambda: "2026-11-02"
        self.config = MagicMock()
        self.config.get_settings.return_value = Doc(
            enabled=1, scheduler_enabled=1, allow_live=0, max_request_amount=0, reconciliation_limit=10,
        )
        self.config.get_provider.return_value = Doc(name="provider-1", environment="sandbox")
        self.engine = types.ModuleType("network_billing_system.transfer_engine")
        self.engine.configuration = lambda: self.config
        self.engine.post_entry = MagicMock()
        self.finance = types.ModuleType("network_billing_system.zone_finance")
        self.finance.balances = MagicMock(return_value=[{"available": Decimal(1200)}])
        self.finance.lock_zone = MagicMock()
        self.finance.money = lambda value: Decimal(str(value or 0))
        path = Path(__file__).parents[1] / "network_billing_system/transfer_tasks.py"
        spec = importlib.util.spec_from_file_location("_transfer_tasks_test", path)
        self.tasks = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            "frappe": self.frappe, self.utils.__name__: self.utils,
            self.engine.__name__: self.engine, self.finance.__name__: self.finance,
        }):
            spec.loader.exec_module(self.tasks)
        self.policy = Doc(
            zone="north", provider_account="provider-1", recipient_account="recipient-1",
            fee_policy="fee-1", schedule="Daily", retained_balance=100, maximum_amount=1000,
            minimum_payout=100,
        )
        self.frappe.get_all.return_value = ["policy-1"]

    def test_schedule_periods_include_week_year_boundary(self):
        self.assertEqual(self.tasks.period_key("Daily", date(2026, 11, 2)), "2026-11-02")
        self.assertEqual(self.tasks.period_key("Monthly", date(2026, 11, 2)), "2026-11")
        self.assertEqual(self.tasks.period_key("Weekly", date(2021, 1, 1)), "2020-W53")
        with self.assertRaises(ValueError):
            self.tasks.period_key("Hourly", date.today())

    def test_schedule_prepares_only_no_approval_no_network_and_enforces_budget(self):
        draft = MagicMock()
        self.frappe.get_doc.side_effect = [self.policy, draft]
        self.tasks.prepare_scheduled_requests()
        values = self.frappe.get_doc.call_args.args[0]
        self.assertEqual(values["schedule_key"], "policy-1:2026-11-02")
        self.assertEqual(values["amount"], Decimal(1000))
        self.assertEqual(values["fee_bearer"], "Recipient")
        draft.submit.assert_called_once()
        self.assertTrue(draft.flags.transfer_schedule)
        self.engine.post_entry.assert_not_called()

    def test_schedule_skips_existing_period_and_live_when_disabled(self):
        self.frappe.get_doc.return_value = self.policy
        self.frappe.db.exists.return_value = True
        self.tasks.prepare_scheduled_requests()
        self.assertEqual(self.frappe.get_doc.call_count, 1)
        self.finance.balances.assert_not_called()
        self.config.get_provider.return_value = Doc(name="provider-1", environment="live")
        self.tasks.prepare_scheduled_requests()
        self.finance.balances.assert_not_called()

    def test_credit_mirror_posts_a_unique_audit_key_not_provider_payment(self):
        self.frappe.db.sql.return_value = [
            Doc(name="checkout-1", zone="north", provider_environment="live", amount=100),
        ]
        self.assertEqual(self.tasks.synchronize_checkout_credits(), 1)
        self.engine.post_entry.assert_called_once_with(
            "north", "live", "checkout:checkout-1", "Checkout Credit",
            "Portal Checkout", "checkout-1", credit=Decimal(100),
        )
        self.frappe.db.commit.assert_called_once()

    def test_disabled_scheduler_never_creates_requests(self):
        self.config.get_settings.return_value = Doc(enabled=1, scheduler_enabled=0)
        self.tasks.prepare_scheduled_requests()
        self.frappe.get_all.assert_not_called()
