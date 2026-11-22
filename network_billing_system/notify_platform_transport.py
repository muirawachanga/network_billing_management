"""Server-side client for Notify Platform's authenticated direct-SMS methods."""

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from urllib.parse import urlsplit

import requests


SETTINGS = "MikroTik Notify Settings"
DISPATCH_NAME = re.compile(r"[0-9a-f]{64}")
DISPATCH_STATES = {"Quoted", "Pending", "Submitting", "Accepted", "Rejected", "Uncertain", "Cancelled"}
DELIVERY_STATES = {"Not Reported", "Pending", "Delivered", "Failed", "Not Applicable"}


class NotifyError(Exception):
    """Only fixed, non-sensitive error codes may leave this boundary."""


def notify_origin(value):
    try:
        parsed = urlsplit(value)
        if (not isinstance(value, str) or re.search(r"[\s\\?#]", value)
                or parsed.scheme != "https" or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.port == 0
                or not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname)):
            raise ValueError
        return f"https://{parsed.hostname}" + (f":{parsed.port}" if parsed.port not in (None, 443) else "")
    except (AttributeError, TypeError, ValueError):
        raise NotifyError("Configuration") from None


@dataclass(frozen=True)
class NotifyClient:
    origin: str
    account: str
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)

    @property
    def identity(self):
        # Do not authorize an old quote against a different account, site or API user.
        return sha256(json.dumps([self.origin, self.account, self.api_key]).encode()).hexdigest()

    @classmethod
    def from_settings(cls, settings):
        if not settings.enabled:
            raise NotifyError("Configuration")
        key = settings.get_password("api_key", raise_exception=False)
        secret = settings.get_password("api_secret", raise_exception=False)
        if not settings.account or not all(
            isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,140}", value)
            for value in (key, secret)
        ):
            raise NotifyError("Configuration")
        return cls(notify_origin(settings.base_url), settings.account, key, secret)

    def _call(self, method, **params):
        try:
            response = requests.post(
                f"{self.origin}/api/method/notify_platform.api.v1.{method}",
                json={"account": self.account, **params},
                headers={"Authorization": f"token {self.api_key}:{self.api_secret}", "Accept": "application/json"},
                timeout=(3, 8), allow_redirects=False,
            )
            data = response.json()
        except (requests.RequestException, ValueError):
            raise NotifyError("API Error") from None
        if response.status_code in (401, 403):
            raise NotifyError("Authorization")
        if not isinstance(data, dict) or not 200 <= response.status_code < 300 or data.get("exc_type"):
            raise NotifyError(_error_code(data, method))
        return data.get("message")

    def quote(self, phone, text):
        data = self._call("sms.quote_sms", phone_number=phone, message=text)
        if (not isinstance(data, dict) or not isinstance(data.get("name"), str)
                or not DISPATCH_NAME.fullmatch(data["name"]) or type(data.get("sufficient")) is not bool):
            raise NotifyError("API Error")
        return data["name"], data["sufficient"]

    def confirm(self, name):
        data = self._call("sms.confirm_sms", message_name=name)
        if (not isinstance(data, dict) or data.get("name") != name
                or not isinstance(data.get("status"), str)
                or data.get("status") not in DISPATCH_STATES - {"Quoted"}):
            raise NotifyError("API Error")
        return data["status"]

    def history(self, name):
        data = self._call("dispatch.message_history", source="direct", query=name)
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            raise NotifyError("API Error")
        for row in data["messages"]:
            if not isinstance(row, dict) or row.get("name") != name:
                continue
            dispatch, delivery = row.get("dispatch_status"), row.get("delivery_status")
            if not isinstance(dispatch, str) or dispatch not in DISPATCH_STATES:
                raise NotifyError("API Error")
            return dispatch, delivery if isinstance(delivery, str) and delivery in DELIVERY_STATES else None
        return None, None


def _error_code(data, method):
    if method != "sms.confirm_sms" or not isinstance(data, dict):
        return "API Error"
    try:
        for raw in json.loads(data.get("_server_messages", "[]")):
            message = json.loads(raw).get("message")
            if message == "This quote expired. Preview the message again.":
                return "Quote Expired"
            if message == "The wallet cannot cover this change to its available or reserved balance.":
                return "Low Balance"
            if message == "SMS pricing or sender settings changed. Preview the message again.":
                return "Quote Invalid"
    except (AttributeError, TypeError, ValueError):
        pass
    return "API Error"
