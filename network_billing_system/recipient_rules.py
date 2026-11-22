"""Provider field mapping for Kopo Kopo Send Money account types."""

from network_billing_system.portal_rules import normalize_phone


RECIPIENT_FIELDS = {
    "mobile_wallet": ("first_name", "last_name", "phone_number", "network"),
    "bank_account": ("account_name", "account_number", "bank_branch_ref"),
    "till": ("till_name", "till_number"),
    "paybill": ("paybill_name", "paybill_number", "paybill_account_number"),
    "merchant_wallet": ("first_name", "last_name", "phone_number", "network"),
    "merchant_bank_account": ("account_name", "account_number", "bank_branch_ref", "settlement_method"),
}
DESTINATION_FIELDS = {
    "mobile_wallet": ("phone_number", "network"),
    "bank_account": ("account_name", "account_number", "bank_branch_ref"),
    "till": ("till_number",),
    "paybill": ("paybill_number", "paybill_account_number"),
    "merchant_wallet": ("reference",),
    "merchant_bank_account": ("reference",),
}


def registration_request(doc):
    kind = doc.destination_type
    if kind not in RECIPIENT_FIELDS:
        raise ValueError("Unsupported Kopo Kopo recipient type.")
    details = {"nickname": doc.recipient_name}
    for field in RECIPIENT_FIELDS[kind]:
        value = getattr(doc, field, None)
        if not value:
            raise ValueError("Recipient requires {}.".format(field.replace("_", " ")))
        details[field] = normalize_phone(value).lstrip("+") if field == "phone_number" else value
    if kind in ("merchant_wallet", "merchant_bank_account"):
        endpoint = "merchant_wallets" if kind == "merchant_wallet" else "merchant_bank_accounts"
        return endpoint, details
    return "external_recipients", {"type": kind, "external_recipient": details}


def destination_details(doc):
    kind = doc.destination_type
    if kind not in DESTINATION_FIELDS:
        raise ValueError("Unsupported Kopo Kopo destination type.")
    result = {}
    for field in DESTINATION_FIELDS[kind]:
        value = getattr(doc, "provider_reference" if field == "reference" else field, None)
        if not value:
            raise ValueError("Recipient requires {}.".format(field))
        if field == "phone_number":
            value = normalize_phone(value).lstrip("+")
        result[field] = value
    return result
