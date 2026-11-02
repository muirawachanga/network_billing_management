from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from network_billing_system.portal_rules import payment_id
from network_billing_system.recipient_rules import destination_details


def transfer_id_from_location(location, base_url):
    url, base = urlsplit(location or ""), urlsplit(base_url)
    prefix = "/api/v2/send_money/"
    if ((url.scheme, url.netloc) != (base.scheme, base.netloc)
            or not url.path.startswith(prefix) or url.query or url.fragment):
        raise ValueError("Kopo Kopo returned an unexpected transfer location.")
    return payment_id(url.path[len(prefix):])


def transfer_payload(doc, callback_url):
    destination = {
        "type": doc.destination_type,
        "nickname": doc.recipient_name,
        "amount": str(doc.amount),
        "description": "Zone remittance " + doc.name,
        "favourite": False,
    }
    destination.update(destination_details(doc))
    if doc.destination_type in ("merchant_wallet", "merchant_bank_account"):
        destination = {key: destination[key] for key in ("type", "reference", "amount")}
    return {
        "source_identifier": doc.source_identifier,
        "destinations": [destination],
        "currency": "KES",
        "metadata": {"zone_remittance": doc.name, "zone": doc.zone},
        "_links": {"callback_url": callback_url},
    }


def verified_transfer(payload, doc, expected_id):
    if not isinstance(payload, dict):
        raise ValueError("Kopo Kopo returned an invalid transfer response.")
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("Kopo Kopo returned invalid transfer data.")
    attributes = data.get("attributes") or {}
    if not isinstance(attributes, dict):
        raise ValueError("Kopo Kopo returned invalid transfer attributes.")
    if payment_id(data.get("id")) != payment_id(expected_id):
        raise ValueError("Kopo Kopo returned a different transfer.")
    if (attributes.get("metadata") or {}).get("zone_remittance") != doc.name:
        raise ValueError("The transfer does not belong to this remittance.")
    if attributes.get("currency") != "KES" or str(attributes.get("source_identifier")) != doc.source_identifier:
        raise ValueError("The transfer currency or source does not match.")
    destinations = attributes.get("destinations") or []
    if not isinstance(destinations, list) or len(destinations) != 1:
        raise ValueError("Expected exactly one transfer destination.")
    destination = destinations[0]
    if not isinstance(destination, dict):
        raise ValueError("Kopo Kopo returned an invalid transfer destination.")
    expected = transfer_payload(doc, "")["destinations"][0]
    for field in ("type", "phone_number", "network", "account_name", "account_number", "bank_branch_ref",
                  "till_number", "paybill_number", "paybill_account_number", "reference"):
        if field in expected and str(destination.get(field)) != str(expected[field]):
            raise ValueError("The transfer recipient does not match.")
    try:
        amount = Decimal(str(destination.get("amount")))
    except InvalidOperation:
        raise ValueError("The transfer amount is invalid.")
    if not amount.is_finite() or amount != Decimal(str(doc.amount)):
        raise ValueError("The transfer amount does not match.")
    status = str(attributes.get("status") or "").upper()
    destination_status = str(destination.get("status") or "").upper()
    if status == "SUCCESS" and destination_status == "SUCCESS":
        return "Paid"
    if status == "FAILED" and destination_status == "FAILED":
        return "Failed"
    if status in ("PENDING", "PROCESSING"):
        return "Pending"
    raise ValueError("The provider transfer outcome is not confirmed; funds remain reserved.")
