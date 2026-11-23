"""Payer verification for MikroTik receipt recovery; not a guest API itself.

Recovery dispatch requires a Frappe default-queue worker and an HTTPS SMS gateway.
"""

import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

import frappe
import requests
from frappe.utils import now_datetime

from network_billing_system.portal_rules import normalize_phone
from network_billing_system.utils import load_configuration


CHALLENGE = "MikroTik Recovery Challenge"
OTP_LIFETIME = timedelta(minutes=5)
GRANT_LIFETIME = timedelta(minutes=10)
MAX_ATTEMPTS = 5
_ID = re.compile(r"[0-9a-f]{32}")
_START_MESSAGE = (
    "If this receipt is eligible, a verification code will be sent to the payer's phone."
)
_VERIFY_MESSAGE = "The verification code is invalid or expired."

# Keep the counter and its expiry atomic, including on the first request.
_INCREMENT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


def _key():
    key = frappe.conf.get("encryption_key")
    return key.encode() if isinstance(key, str) and key else None


def _digest(key, purpose, *fields):
    payload = "\0".join(("mikrotik-recovery", purpose, *fields))
    return hmac.new(key, payload.encode(), hashlib.sha256)


def _otp(key, challenge_id):
    number = int.from_bytes(_digest(key, "otp", challenge_id).digest(), "big")
    return f"{number % 1_000_000:06d}"


def _allowed(key, bucket, identifier, limit, seconds):
    site = frappe.local.site
    identity = _digest(key, "rate-limit", site, bucket, identifier).hexdigest()
    try:
        count = frappe.cache().eval(_INCREMENT, 1, "nbs:recovery:" + identity, seconds)
        return int(count) <= limit
    except Exception:
        # No SMS or verifier access if a shared limit cannot be applied.
        return False


def _zone_id(zone_id):
    return zone_id if isinstance(zone_id, str) and 0 < len(zone_id) <= 140 else ""


def _zone_enabled(zone_id):
    return bool(zone_id and frappe.db.get_value(
        "Captive Portal Zone", {"name": zone_id, "enabled": 1, "backend": "MikroTik"}, "name",
    ) == zone_id)


def _request_allowed(key, action, zone_id, caller_limit, zone_limit, seconds):
    if not key:
        return False
    caller = str(getattr(frappe.local, "request_ip", None) or "unknown")
    return (
        _allowed(key, action + ":caller", caller, caller_limit, seconds)
        and _allowed(key, action + ":zone", zone_id, zone_limit, seconds)
    )


def _checkout_filters(zone_id, **filters):
    return {
        "zone": zone_id, "backend": "MikroTik", "status": "SUCCESS", **filters,
    }


def _payer(checkout):
    try:
        phone = normalize_phone(checkout.mobile_number) if checkout else None
    except ValueError:
        return None
    return phone if phone and phone.isascii() else None


def _current_payer(key, challenge, zone_id, *, lock=False):
    if not _zone_enabled(zone_id):
        return None
    checkout = frappe.db.get_value(
        "Portal Checkout", _checkout_filters(zone_id, name=challenge.checkout),
        ["mobile_number"], as_dict=True, for_update=lock,
    )
    phone = _payer(checkout)
    if phone and hmac.compare_digest(challenge.payer_digest, _digest(key, "payer", phone).hexdigest()):
        return phone
    return None


def _challenge(challenge_id, *, lock=False):
    suffix = " FOR UPDATE" if lock else ""
    rows = frappe.db.sql(
        """SELECT name, zone, checkout, payer_digest, expires_at, attempts,
                  dispatched_at, used_at, grant_digest, grant_expires_at
             FROM `tabMikroTik Recovery Challenge` WHERE name=%s""" + suffix,
        (challenge_id,), as_dict=True,
    )
    return rows[0] if rows else None


def begin(receipt, zone_id):
    """Start recovery without confirming whether a receipt or phone exists."""
    challenge_id = secrets.token_hex(16)
    response = {"challenge_id": challenge_id, "customer_message": _START_MESSAGE}
    key = _key()
    zone_id = _zone_id(zone_id)
    if not _request_allowed(key, "start", zone_id, 5, 120, 300):
        return response

    receipt = re.sub(r"\s+", "", receipt) if isinstance(receipt, str) and len(receipt) <= 80 else ""
    receipt = receipt.upper() if receipt.isascii() else ""
    if re.fullmatch(r"(?=.*[A-Z])[A-Z0-9]{8,20}", receipt) and _zone_enabled(zone_id):
        checkout = frappe.db.get_value(
            "Portal Checkout", _checkout_filters(zone_id, mpesa_receipt=receipt),
            ["name", "mobile_number"], as_dict=True,
        )
        phone = _payer(checkout)
        if phone and _allowed(key, "start:checkout", checkout.name, 3, 900):
            frappe.get_doc({
                "doctype": CHALLENGE,
                "zone": zone_id,
                "checkout": checkout.name,
                "payer_digest": _digest(key, "payer", phone).hexdigest(),
                "expires_at": now_datetime() + OTP_LIFETIME,
                "attempts": 0,
            }).insert(ignore_permissions=True, set_name=challenge_id)
            frappe.db.commit()

    # Queue even a no-match ID to keep the synchronous path free of gateway timing.
    try:
        frappe.enqueue(
            "network_billing_system.mikrotik_recovery.dispatch", challenge_id=challenge_id,
        )
    except Exception:
        # The same public response applies when SMS cannot be dispatched.
        pass
    return response


def dispatch(challenge_id):
    """Claim the SMS once before performing network I/O; no sensitive job arguments."""
    key = _key()
    if not key or not isinstance(challenge_id, str) or not _ID.fullmatch(challenge_id):
        return
    challenge = _challenge(challenge_id, lock=True)
    now = now_datetime()
    if not challenge or challenge.dispatched_at or challenge.used_at or challenge.expires_at <= now:
        return
    phone = _current_payer(key, challenge, challenge.zone, lock=True)
    if not phone:
        return
    frappe.db.set_value(CHALLENGE, challenge.name, "dispatched_at", now)
    frappe.db.commit()
    # A timeout may still have delivered the SMS, so don't retry or revoke it.
    send_message(
        phone, f"Your Wi-Fi verification code is {_otp(key, challenge_id)}.",
        "mikrotik-recovery-" + challenge_id,
    )


def verify(challenge_id, otp, zone_id):
    """Commit the failed-attempt counter or successful OTP consumption under lock."""
    invalid = {"customer_message": _VERIFY_MESSAGE}
    zone_id = _zone_id(zone_id)
    key = _key()
    if not _request_allowed(key, "verify", zone_id, 20, 240, 300):
        return invalid
    if not isinstance(challenge_id, str) or not _ID.fullmatch(challenge_id):
        return invalid
    challenge = _challenge(challenge_id, lock=True)
    now = now_datetime()
    if (
        not challenge or challenge.zone != zone_id or not challenge.dispatched_at
        or challenge.used_at or challenge.expires_at <= now
        or challenge.attempts >= MAX_ATTEMPTS
        or not _allowed(key, "verify:checkout", challenge.checkout, 15, 900)
        or not _current_payer(key, challenge, zone_id, lock=True)
    ):
        return invalid

    updates = {"attempts": challenge.attempts + 1}
    token = None
    if isinstance(otp, str) and re.fullmatch(r"[0-9]{6}", otp) and hmac.compare_digest(
        otp, _otp(key, challenge_id)
    ):
        secret = secrets.token_hex(32)
        token = challenge_id + "." + secret
        updates.update({
            "used_at": now,
            "grant_digest": _digest(key, "grant", challenge_id, secret).hexdigest(),
            "grant_expires_at": now + GRANT_LIFETIME,
        })
    frappe.db.set_value(CHALLENGE, challenge.name, updates)
    frappe.db.commit()
    return {"recovery_token": token} if token else invalid


def checkout_for_grant(recovery_token, zone_id):
    """Return the current checkout name for a valid bearer grant, or None."""
    zone_id = _zone_id(zone_id)
    key = _key()
    if not _request_allowed(key, "grant", zone_id, 60, 600, 300):
        return None
    if not isinstance(recovery_token, str) or len(recovery_token) != 97:
        return None
    challenge_id, _, secret = recovery_token.partition(".")
    if not _ID.fullmatch(challenge_id) or not re.fullmatch(r"[0-9a-f]{64}", secret):
        return None
    challenge = _challenge(challenge_id)
    if (
        not challenge or challenge.zone != zone_id or not challenge.used_at
        or not challenge.grant_digest or not challenge.grant_expires_at
        or challenge.grant_expires_at <= now_datetime()
        or not hmac.compare_digest(challenge.grant_digest, _digest(key, "grant", challenge_id, secret).hexdigest())
    ):
        return None
    return challenge.checkout if _current_payer(key, challenge, zone_id, lock=True) else None


def prune_expired():
    """Daily scheduler task; delete challenges whose OTP and grant have long expired."""
    frappe.db.delete(CHALLENGE, {"expires_at": ("<", now_datetime() - timedelta(days=1))})


def send_message(phone, message, idempotency_key):
    """Return True on acceptance, 'retry' before submission, else False.

    The idempotency header is forwarded; the gateway must honor it to deduplicate.
    """
    if not phone or not message or not idempotency_key:
        return False
    try:
        server = load_configuration("sms_gateway_server")
        api_key = load_configuration("sms_api_key")
        address = urlsplit(server or "")
        if address.scheme != "https" or not address.hostname or address.username or not api_key:
            return False
        with requests.post(
            server,
            json={"to": phone, "message": message},
            headers={"Authorization": api_key, "Idempotency-Key": idempotency_key},
            timeout=(3, 5),
            allow_redirects=False,
            stream=True,
        ) as response:
            return 200 <= response.status_code < 300
    except requests.ConnectTimeout:
        return "retry"
    except (requests.RequestException, ValueError):
        # Exceptions may embed request URLs, headers or payloads; don't log or propagate them.
        return False
