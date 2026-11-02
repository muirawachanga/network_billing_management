import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestConnectorCredentials(unittest.TestCase):
    def setUp(self):
        frappe = MagicMock()
        frappe.whitelist = lambda **kwargs: lambda function: function
        frappe.conf = {"kopokopo_client_id": "existing-id", "kopokopo_client_secret": "existing-secret"}
        utils = types.ModuleType("frappe.utils")
        utils.get_datetime = MagicMock()
        utils.flt = float
        sms = types.ModuleType("network_billing_system.network_billing_system.doctype.sms_logs.sms_logs")
        sms.send_msg = MagicMock()
        sms.validate_amount = MagicMock()
        config = types.ModuleType("network_billing_system.utils")
        config.load_configuration = MagicMock(return_value=None)
        path = Path(__file__).parents[1] / "network_billing_system/kopokopo_integration.py"
        spec = importlib.util.spec_from_file_location("_credential_connector_test", path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            "frappe": frappe, "frappe.utils": utils,
            sms.__name__: sms, config.__name__: config,
        }):
            spec.loader.exec_module(self.module)

    def test_existing_checkout_credentials_are_unchanged(self):
        with patch.object(self.module.requests, "post") as post:
            post.return_value.json.return_value = {"access_token": "token"}
            self.module.KopokopoConnector(env="live")
        self.assertEqual(post.call_args.args[0], "https://api.kopokopo.com/oauth/token")
        self.assertEqual(post.call_args.kwargs["json"]["client_id"], "existing-id")
        self.assertEqual(post.call_args.kwargs["json"]["client_secret"], "existing-secret")

    def test_explicit_sandbox_credentials_are_sent_only_to_sandbox(self):
        with patch.object(self.module.requests, "post") as post:
            post.return_value.json.return_value = {"access_token": "sandbox-token"}
            self.module.KopokopoConnector(
                env="sandbox", app_key="sandbox-id", app_secret="sandbox-secret"
            )
        self.assertEqual(post.call_args.args[0], "https://sandbox.kopokopo.com/oauth/token")
        self.assertEqual(post.call_args.kwargs["json"], {
            "client_id": "sandbox-id", "client_secret": "sandbox-secret",
            "grant_type": "client_credentials",
        })
