"""Provider validation independent of Frappe and of untrusted portal inputs."""

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from uuid import UUID


def normalize_phone(value):
    digits = re.sub(r"[\s()+-]", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if re.fullmatch(r"0[17]\d{8}", digits):
        digits = "254" + digits[1:]
    elif re.fullmatch(r"[17]\d{8}", digits):
        digits = "254" + digits
    if not re.fullmatch(r"254[17]\d{8}", digits):
        raise ValueError("Enter a valid Kenyan mobile number.")
    return "+" + digits


def payment_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid Kopo Kopo payment identifier.")


def payment_id_from_location(location, base_url):
    url = urlsplit(location or "")
    base = urlsplit(base_url)
    prefix = "/api/v2/incoming_payments/"
    if (
        (url.scheme, url.netloc) != (base.scheme, base.netloc)
        or not url.path.startswith(prefix)
        or url.query
        or url.fragment
    ):
        raise ValueError("Kopo Kopo returned an unexpected payment location.")
    return payment_id(url.path[len(prefix):])


def confirmed_payment(payload, checkout, expected_payment_id):
    """Validate only a response fetched from the authenticated provider API."""
    data = payload.get("data") or {}
    attributes = data.get("attributes") or {}
    if payment_id(data.get("id")) != payment_id(expected_payment_id):
        raise ValueError("The provider returned a different payment.")
    metadata = attributes.get("metadata") or {}
    if metadata.get("portal_checkout") != checkout["name"]:
        raise ValueError("The provider payment does not belong to this checkout.")

    status = str(attributes.get("status") or "").upper()
    if status in ("PENDING", "PROCESSING"):
        return {"status": "PENDING"}
    if status in ("FAILED", "CANCELLED"):
        return {"status": status}
    if status != "SUCCESS":
        raise ValueError("Kopo Kopo returned an unrecognized payment status.")

    resource = (attributes.get("event") or {}).get("resource") or {}
    try:
        paid = Decimal(str(resource.get("amount")))
        expected = Decimal(str(checkout["amount"]))
    except InvalidOperation:
        raise ValueError("The provider returned an invalid payment amount.")
    if not paid.is_finite() or paid != expected:
        raise ValueError("The confirmed amount does not match this package.")
    if resource.get("currency") != "KES":
        raise ValueError("The confirmed currency must be KES.")
    if normalize_phone(resource.get("sender_phone_number")) != checkout["mobile_number"]:
        raise ValueError("The confirmed payer does not match this checkout.")
    if str(resource.get("till_number") or "").lstrip("K") != checkout["till_number"]:
        raise ValueError("The confirmed payment was received by a different till.")
    receipt = str(resource.get("reference") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{8,20}", receipt):
        raise ValueError("The provider returned an invalid M-Pesa receipt.")
    return {"status": "SUCCESS", "receipt": receipt, "resource": resource}
