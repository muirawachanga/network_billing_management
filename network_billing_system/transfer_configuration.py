"""Transfer configuration and immutable, manually verified payout destinations.

These are internal server-side helpers, not whitelisted APIs. Credential results
must never be serialized into a response, snapshot, log, or transfer document.
Registration resource IDs identify registration resources, not payout references.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from types import SimpleNamespace
from urllib.parse import urlsplit

import frappe

from network_billing_system.portal_rules import normalize_phone, payment_id
from network_billing_system.recipient_rules import DESTINATION_FIELDS, RECIPIENT_FIELDS, registration_request


CENT = Decimal("0.01")
DESTINATION_TYPES = tuple(RECIPIENT_FIELDS)
SETTLEMENT_METHODS = ("RTS", "EFT")
ACCOUNT_FIELDS = (
    "recipient", "provider_account", "zone", "destination_type", "currency",
    "registration_resource_id", "payout_reference", "first_name", "last_name",
    "phone_number", "network", "account_name", "account_number", "bank_branch_ref",
    "settlement_method", "till_name", "till_number", "paybill_name",
    "paybill_number", "paybill_account_number", "verification_confirmed",
    "verification_evidence", "verification_note", "verified_by", "verified_on",
)


def _value(doc, key, default=None):
    return doc.get(key, default) if isinstance(doc, dict) else getattr(doc, key, default)


def _set(doc, key, value):
    if isinstance(doc, dict):
        doc[key] = value
    else:
        setattr(doc, key, value)


def _fail(message):
    frappe.throw(message)


def checked(value):
    if value in (None, "", 0, "0", False):
        return False
    if value in (1, "1", True):
        return True
    _fail("Check fields must be either enabled or disabled.")


def _choice(doc, field, choices):
    value = _value(doc, field)
    if value not in choices:
        _fail("Select a valid {}.".format(field.replace("_", " ")))
    return value


def money(value, label="Amount", positive=False):
    """Parse exact KES amounts; reject non-finite, negative and sub-cent values."""
    try:
        result = Decimal(str(value if value not in (None, "") else 0))
        valid = result.is_finite() and result >= 0 and result == result.quantize(CENT)
    except (InvalidOperation, ValueError, TypeError):
        valid = False
    if not valid or (positive and result <= 0):
        _fail("{} must be {}KES amount with at most two decimal places.".format(
            label, "a positive " if positive else "a non-negative "
        ))
    return result.quantize(CENT)


def _required(doc, *fields):
    for field in fields:
        value = _value(doc, field)
        if value is None or not str(value).strip():
            _fail("{} is required.".format(field.replace("_", " ").capitalize()))
        if not isinstance(value, str) or value != value.strip():
            _fail("{} must be text without surrounding whitespace.".format(field.replace("_", " ")))


def _currency(doc):
    if _value(doc, "currency") != "KES":
        _fail("Transfers support KES only.")


def _date(value, label):
    try:
        if isinstance(value, datetime):
            return value.date()
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError):
        _fail("{} must be a valid date.".format(label))


def _today():
    from frappe.utils import today
    return _date(today(), "Today")


def _previous(doc):
    getter = getattr(doc, "get_doc_before_save", None)
    return getter() if callable(getter) else None


def _immutable_value(doc, field):
    value = _value(doc, field)
    if field == "verified_on" and value:
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            _fail("The verification timestamp is invalid.")
    if field == "verification_confirmed":
        return checked(value)
    return value or ""


def validate_settings(doc):
    for field in ("enabled", "allow_live", "scheduler_enabled"):
        checked(_value(doc, field))
    money(_value(doc, "approval_threshold"), "Approval threshold")
    maximum = money(_value(doc, "max_request_amount"), "Maximum request amount")
    if checked(_value(doc, "enabled")) and maximum <= 0:
        _fail("Set a positive maximum request amount before enabling transfers.")
    if checked(_value(doc, "scheduler_enabled")) and not checked(_value(doc, "enabled")):
        _fail("Enable transfers before enabling the scheduler.")
    limit = _value(doc, "reconciliation_limit")
    try:
        number = Decimal(str(limit))
        valid = number.is_finite() and number == number.to_integral_value() and 1 <= number <= 1000
    except (InvalidOperation, ValueError, TypeError):
        valid = False
    if not valid:
        _fail("Reconciliation limit must be an integer between 1 and 1000.")


def get_settings():
    """Return the validated Single; zero maximum never means unlimited."""
    doc = frappe.get_single("Transfer Settings")
    validate_settings(doc)
    return doc


def _password(doc, field):
    value = doc.get_password(field, raise_exception=False)
    if value and (not isinstance(value, str) or value == "*****"):
        _fail("Configure a valid provider secret.")
    return value


def _credentials(doc):
    environment = _choice(doc, "environment", ("sandbox", "live"))
    use_existing = checked(_value(doc, "use_existing_live_credentials", 1))
    client_id = _value(doc, "client_id")
    secret = _password(doc, "client_secret")
    webhook_secret = _password(doc, "webhook_secret")
    if client_id or secret:
        if not client_id or not secret:
            _fail("Configure a complete provider client ID and secret pair.")
    else:
        prefix = "kopokopo_remittance_" + environment
        client_id = frappe.conf.get(prefix + "_client_id")
        secret = frappe.conf.get(prefix + "_client_secret")
        if environment == "live" and not use_existing:
            client_id = secret = None
        if environment == "live" and use_existing and not client_id and not secret:
            client_id = frappe.conf.get("kopokopo_client_id")
            secret = frappe.conf.get("kopokopo_client_secret")
        if not client_id or not secret:
            _fail("Configure a complete {} provider credential pair.".format(environment))
    if not webhook_secret:
        webhook_secret = frappe.conf.get("kopokopo_remittance_" + environment + "_webhook_secret")
        if environment == "live" and use_existing and not webhook_secret:
            webhook_secret = frappe.conf.get("kopokopo_webhook_secret")
    if not isinstance(client_id, str) or not client_id.strip() or not isinstance(secret, str) or not secret.strip():
        _fail("Configure valid provider credentials.")
    return client_id, secret, webhook_secret


def validate_provider(doc):
    _choice(doc, "environment", ("sandbox", "live"))
    checked(_value(doc, "use_existing_live_credentials", 1))
    enabled = checked(_value(doc, "enabled"))
    old = _previous(doc)
    if old and _value(old, "environment") != _value(doc, "environment"):
        _fail("A provider account environment cannot be changed; create another account.")
    if old and (_value(old, "source_identifier") or "") != (_value(doc, "source_identifier") or ""):
        submitted = frappe.db.get_value(
            "Transfer Request", {"provider_account": doc.name, "docstatus": 1}, "name"
        )
        if submitted:
            _fail("A provider funding source referenced by a submitted transfer request is immutable.")
    if enabled:
        _credentials(doc)


def get_provider(name):
    """Resolve enabled provider credentials without ever falling back to live for sandbox."""
    doc = frappe.get_doc("Transfer Provider Account", name)
    validate_provider(doc)
    if not checked(_value(doc, "enabled")):
        _fail("The transfer provider account is disabled.")
    client_id, secret, webhook_secret = _credentials(doc)
    return {
        "name": doc.name, "environment": doc.environment,
        "source_identifier": _value(doc, "source_identifier") or None,
        "client_id": client_id, "client_secret": secret, "webhook_secret": webhook_secret,
    }


def provider_connector(account):
    """Authenticate using credentials freshly resolved from the stored account."""
    from network_billing_system.kopokopo_integration import KopokopoConnector
    name = account if isinstance(account, str) else _value(account, "name")
    provider = get_provider(name)
    return KopokopoConnector(
        env=provider["environment"], app_key=provider["client_id"],
        app_secret=provider["client_secret"],
    )


def validate_recipient(doc):
    _required(doc, "recipient_name")
    checked(_value(doc, "enabled"))
    if _value(doc, "phone_number"):
        _set(doc, "phone_number", normalize_phone(_value(doc, "phone_number")))


def _account_details(doc):
    kind = _choice(doc, "destination_type", DESTINATION_TYPES)
    _currency(doc)
    _required(doc, *RECIPIENT_FIELDS[kind])
    if kind in ("mobile_wallet", "merchant_wallet"):
        _set(doc, "phone_number", normalize_phone(_value(doc, "phone_number")))
        if _value(doc, "network") != "Safaricom":
            _fail("Only the Safaricom mobile network is supported.")
    if kind in ("bank_account", "merchant_bank_account"):
        _choice(doc, "settlement_method", SETTLEMENT_METHODS)
        if kind == "bank_account" and _value(doc, "settlement_method") != "RTS":
            _fail("External bank accounts support RTS settlement only.")
    for field in ("till_number", "paybill_number"):
        if field in RECIPIENT_FIELDS[kind] and not str(_value(doc, field)).isdigit():
            _fail("Till and paybill numbers must contain digits only.")
    fields = RECIPIENT_FIELDS[kind]
    if kind == "bank_account":
        fields = fields + ("settlement_method",)
    return {field: str(_value(doc, field)) for field in fields}


def validate_recipient_account(doc):
    _required(doc, "recipient", "provider_account", "zone")
    readiness = _choice(doc, "readiness", ("Draft", "Verified", "Disabled"))
    checked(_value(doc, "verification_confirmed"))
    _choice(doc, "registration_state", ("Draft", "Registering", "Created", "Unknown"))
    _account_details(doc)
    old = _previous(doc)
    if not old:
        if _value(doc, "registration_state") != "Draft" or _value(doc, "registration_resource_id"):
            _fail("Registration audit fields are managed by the registration operation.")
    elif any(
        (_value(old, field) or "") != (_value(doc, field) or "")
        for field in ("registration_state", "registration_resource_id")
    ):
        _fail("Registration audit fields are managed by the registration operation.")
    if readiness == "Verified" and (not old or _value(old, "readiness") != "Verified"):
        if not set(frappe.get_roles()).intersection(("System Manager", "Transfer Approver")):
            _fail("Only a Transfer Approver or System Manager can verify a payout account.")
    was_verified = old and (_value(old, "readiness") == "Verified" or _value(old, "verified_on"))
    if was_verified:
        for field in ACCOUNT_FIELDS:
            if _immutable_value(old, field) != _immutable_value(doc, field):
                _fail("Verified payout details are immutable; create a new recipient account.")
        if readiness == "Draft":
            _fail("A previously verified account cannot return to Draft.")
    elif readiness == "Verified":
        if not checked(_value(doc, "verification_confirmed")):
            _fail("Explicitly confirm the payout reference and details in the provider app.")
        _required(doc, "payout_reference", "verification_evidence")
        if str(_value(doc, "payout_reference")).startswith(("http://", "https://")):
            _fail("Enter the confirmed payout reference, not a registration resource URL.")
        from frappe.utils import now_datetime
        _set(doc, "verified_by", frappe.session.user)
        _set(doc, "verified_on", now_datetime())
    elif _value(doc, "verified_by") or _value(doc, "verified_on"):
        _fail("Verification audit fields are assigned only when an account is verified.")
    if readiness == "Verified":
        _required(doc, "payout_reference", "verification_evidence")
        if not checked(_value(doc, "verification_confirmed")):
            _fail("The payout account requires explicit verification confirmation.")
        recipient = frappe.get_doc("Transfer Recipient", doc.recipient)
        provider = frappe.get_doc("Transfer Provider Account", doc.provider_account)
        if not checked(_value(recipient, "enabled")) or not checked(_value(provider, "enabled")):
            _fail("Enable the recipient identity and provider before verifying an account.")


def recipient_snapshot(name, zone, provider):
    """Return only JSON-safe payout data for the exact zone/provider boundary."""
    provider_name = provider if isinstance(provider, str) else _value(provider, "name")
    account = frappe.get_doc("Transfer Recipient Account", name)
    if account.zone != zone or account.provider_account != provider_name:
        _fail("The recipient account does not belong to this zone and provider account.")
    if account.readiness != "Verified" or not checked(_value(account, "verification_confirmed")):
        _fail("Select a verified payout account.")
    _required(account, "payout_reference", "verification_evidence", "verified_by")
    if not _value(account, "verified_on"):
        _fail("The payout account has no verification audit record.")
    identity = frappe.get_doc("Transfer Recipient", account.recipient)
    provider_doc = frappe.get_doc("Transfer Provider Account", provider_name)
    if not checked(_value(identity, "enabled")) or not checked(_value(provider_doc, "enabled")):
        _fail("The recipient identity or provider account is disabled.")
    environment = _choice(provider_doc, "environment", ("sandbox", "live"))
    details = _account_details(account)
    destination = {}
    for field in DESTINATION_FIELDS[account.destination_type]:
        value = account.payout_reference if field == "reference" else details[field]
        destination[field] = value.lstrip("+") if field == "phone_number" else value
    return {
        "recipient": account.recipient, "recipient_name": identity.recipient_name,
        "recipient_account": account.name, "provider_account": provider_name,
        "environment": environment, "zone": zone, "currency": "KES",
        "destination_type": account.destination_type, "type": account.destination_type,
        "details": details, "payout_reference": account.payout_reference,
        "destination_fields": destination,
        "destination": {"type": account.destination_type, **destination},
        "settlement_method": _value(account, "settlement_method") or None,
    }


@frappe.whitelist(methods=["POST"])
def register_account(account_name):
    """Optionally register once; registration never verifies a payout destination."""
    frappe.only_for(("System Manager", "Transfer Approver"))
    settings = get_settings()
    if not checked(settings.enabled):
        _fail("Transfers are disabled.")
    rows = frappe.db.sql(
        "select * from `tabTransfer Recipient Account` where name=%s for update",
        (account_name,), as_dict=True,
    )
    if not rows:
        _fail("Unknown recipient account.")
    account = frappe.get_doc(dict(rows[0], doctype="Transfer Recipient Account"))
    if (account.readiness != "Draft" or account.registration_state != "Draft"
            or account.registration_resource_id):
        _fail("Registration was already attempted or this account is not a draft. Do not retry.")
    provider = get_provider(account.provider_account)
    if provider["environment"] == "live" and not checked(settings.allow_live):
        _fail("Live transfers and registration are disabled.")
    identity = frappe.get_doc("Transfer Recipient", account.recipient)
    if not checked(_value(identity, "enabled")):
        _fail("The recipient identity is disabled.")
    details = _account_details(account)
    registration = SimpleNamespace(
        destination_type=account.destination_type, recipient_name=identity.recipient_name, **details
    )
    endpoint, payload = registration_request(registration)
    frappe.db.set_value("Transfer Recipient Account", account.name, "registration_state", "Registering")
    frappe.db.commit()
    try:
        import requests
        connector = provider_connector(account.provider_account)
        response = requests.post(
            connector.base_url + "/api/v2/" + endpoint, json=payload,
            headers={"Authorization": "Bearer " + connector.authentication_token},
            timeout=15, allow_redirects=False,
        )
        response.raise_for_status()
        if response.status_code != 201:
            raise ValueError("Unexpected provider registration response.")
        location = urlsplit(response.headers.get("Location") or "")
        base = urlsplit(connector.base_url)
        prefix = "/api/v2/" + endpoint + "/"
        if ((location.scheme, location.netloc) != (base.scheme, base.netloc)
                or not location.path.startswith(prefix) or location.query or location.fragment):
            raise ValueError("Unexpected registration resource location.")
        resource_id = payment_id(location.path[len(prefix):])
    except Exception:
        # A timeout, malformed response or unexpected failure cannot prove non-creation.
        frappe.db.set_value("Transfer Recipient Account", account.name, "registration_state", "Unknown")
        frappe.log_error(
            message="Provider registration outcome is unknown. Review the provider app; do not retry.",
            title="Transfer recipient registration",
        )
        return {
            "registration_state": "Unknown",
            "message": "Registration needs provider review. Do not repeat registration.",
        }
    frappe.db.set_value("Transfer Recipient Account", account.name, {
        "registration_state": "Created", "registration_resource_id": resource_id,
    })
    return {
        "registration_state": "Created",
        "message": "Registration resource created. Confirm the payout reference and details manually before verification.",
    }


def validate_fee_policy(doc):
    _currency(doc)
    _choice(doc, "destination_type", DESTINATION_TYPES)
    method = _value(doc, "settlement_method")
    if method:
        _choice(doc, "settlement_method", SETTLEMENT_METHODS)
        if _value(doc, "destination_type") not in ("bank_account", "merchant_bank_account"):
            _fail("Settlement methods apply to bank destinations only.")
        if _value(doc, "destination_type") == "bank_account" and method != "RTS":
            _fail("External bank account fee policies support RTS settlement only.")
    checked(_value(doc, "enabled"))
    _choice(doc, "fee_bearer", ("Zone", "Recipient"))
    start = _date(_value(doc, "effective_from"), "Effective from")
    end = _date(_value(doc, "effective_to"), "Effective to") if _value(doc, "effective_to") else None
    if end and end < start:
        _fail("Fee policy end date cannot precede its start date.")
    money(_value(doc, "fixed_fee"), "Fixed fee")
    money(_value(doc, "minimum_fee"), "Minimum fee")
    try:
        percent = Decimal(str(_value(doc, "percentage") or 0))
        valid = percent.is_finite() and 0 <= percent <= 100
    except (InvalidOperation, ValueError, TypeError):
        valid = False
    if not valid:
        _fail("Fee percent must be between zero and 100.")


def quote_fee(policy, amount):
    """Quote fixed + percentage fee, applying the minimum and rounding once."""
    doc = frappe.get_doc("Transfer Fee Policy", policy) if isinstance(policy, str) else policy
    validate_fee_policy(doc)
    if not checked(_value(doc, "enabled")):
        _fail("The transfer fee policy is disabled.")
    current = _today()
    if current < _date(_value(doc, "effective_from"), "Effective from") or (
        _value(doc, "effective_to") and current > _date(_value(doc, "effective_to"), "Effective to")
    ):
        _fail("The transfer fee policy is not effective today.")
    principal = money(amount, positive=True)
    fee = money(_value(doc, "fixed_fee")) + principal * Decimal(str(_value(doc, "percentage") or 0)) / 100
    return max(fee, money(_value(doc, "minimum_fee"))).quantize(CENT, rounding=ROUND_HALF_UP)


def validate_zone_policy(doc):
    _required(doc, "zone", "provider_account", "recipient_account", "fee_policy")
    _currency(doc)
    _choice(doc, "fee_bearer", ("Zone", "Recipient"))
    _choice(doc, "schedule", ("Daily", "Weekly", "Monthly"))
    enabled = checked(_value(doc, "enabled"))
    scheduled = checked(_value(doc, "schedule_enabled"))
    checked(_value(doc, "approval_needed"))
    money(_value(doc, "retained_balance"), "Retained balance")
    maximum = money(_value(doc, "maximum_amount"), "Maximum payout amount", positive=True)
    minimum = money(_value(doc, "minimum_payout"), "Minimum payout", positive=scheduled)
    if minimum > maximum:
        _fail("Minimum payout cannot exceed the maximum payout amount.")
    if scheduled and not enabled:
        _fail("Enable the zone policy before enabling its schedule.")
    existing = frappe.db.get_value(
        "Zone Remittance Policy", {
            "zone": doc.zone, "provider_account": doc.provider_account, "fee_policy": doc.fee_policy,
        }, "name"
    )
    if existing and existing != _value(doc, "name"):
        _fail("Only one remittance policy is allowed per zone, provider account and fee policy.")
    account = frappe.get_doc("Transfer Recipient Account", doc.recipient_account)
    if account.zone != doc.zone or account.provider_account != doc.provider_account:
        _fail("The recipient account must match the policy zone and provider account.")
    fee = frappe.get_doc("Transfer Fee Policy", doc.fee_policy)
    validate_fee_policy(fee)
    if fee.destination_type != account.destination_type or (
        _value(fee, "settlement_method") and fee.settlement_method != _value(account, "settlement_method")
    ):
        _fail("The fee policy does not match the recipient destination and settlement method.")
    if _value(fee, "fee_bearer") != doc.fee_bearer:
        _fail("The zone policy fee bearer must match the fee policy.")
    if enabled:
        recipient_snapshot(doc.recipient_account, doc.zone, doc.provider_account)
        quote_fee(fee, maximum)
