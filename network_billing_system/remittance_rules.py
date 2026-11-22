from urllib.parse import urlsplit

from network_billing_system.portal_rules import payment_id
from network_billing_system.recipient_rules import destination_details
from network_billing_system.transfer_rules import integer_amount, verified_results


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
        "amount": integer_amount(doc.amount),
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
    metadata = attributes.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("zone_remittance") != doc.name:
        raise ValueError("The transfer does not belong to this remittance.")
    expected = transfer_payload(doc, "")["destinations"][0]
    compatible = {"data": dict(data, attributes=dict(attributes, metadata={"transfer_batch": doc.name}))}
    results = verified_results(compatible, doc.name, expected_id, [{
        "name": doc.name, "recipient_amount": doc.amount, "destination": expected,
        "payout_reference": getattr(doc, "recipient_reference", None),
    }], doc.source_identifier)
    return results[0]["status"] if results else "Pending"
