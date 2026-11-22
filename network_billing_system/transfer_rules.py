"""Strict, framework-independent Send Money execution and result rules."""

import hashlib
import hmac
import json
from decimal import Decimal, InvalidOperation


def amount(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Invalid transfer amount.")
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid transfer amount.")
    return result


def integer_amount(value):
    result = amount(value)
    if result <= 0 or result != result.to_integral_value():
        raise ValueError("Kopo Kopo payout amounts must be positive whole KES.")
    return int(result)


def identity(destination):
    kind = destination.get("type") or destination.get("destination_type")
    fields = {
        "mobile_wallet": ("phone_number",),
        "bank_account": ("account_number",),
        "till": ("till_number",),
        "paybill": ("paybill_number", "paybill_account_number"),
        "merchant_wallet": ("reference",),
        "merchant_bank_account": ("reference",),
    }
    if not isinstance(kind, str) or kind not in fields:
        raise ValueError("Unsupported transfer destination type.")
    values = []
    for field in fields[kind]:
        value = destination.get(field)
        if value is None and field == "reference":
            value = destination.get("destination_reference")
        if not value:
            raise ValueError("Missing destination identity.")
        values.append(str(value).lstrip("+") if field == "phone_number" else str(value))
    return kind, tuple(values)


def batch_payload(batch_id, requests, source, callback):
    if not requests:
        raise ValueError("A batch needs at least one approved request.")
    destinations, seen = [], set()
    for request in requests:
        destination = dict(request["destination"])
        key = identity(destination)
        if key in seen:
            raise ValueError("A recipient account can appear only once per batch.")
        seen.add(key)
        destination["amount"] = integer_amount(request["recipient_amount"])
        if destination["type"] not in ("merchant_wallet", "merchant_bank_account"):
            destination["description"] = "Transfer " + request["name"]
        destinations.append(destination)
    kinds = {row["type"] for row in destinations}
    if len(kinds) != 1 or (len(destinations) > 1 and kinds.pop() not in ("mobile_wallet", "bank_account")):
        raise ValueError("Only homogeneous external mobile-wallet or bank-account batches support multiple destinations.")
    return {
        "source_identifier": source or None,
        "currency": "KES",
        "destinations": destinations,
        "metadata": {"transfer_batch": batch_id},
        "_links": {"callback_url": callback},
    }


def verified_results(payload, batch_id, provider_id, requests, source):
    """Return matched disbursements, never infer settlement from Processed alone."""
    from network_billing_system.portal_rules import payment_id

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise ValueError("Invalid Send Money response.")
    data = payload["data"]
    attrs = data.get("attributes")
    if not isinstance(attrs, dict) or data.get("type") != "send_money":
        raise ValueError("Invalid Send Money resource.")
    if payment_id(data.get("id")) != payment_id(provider_id):
        raise ValueError("Provider returned a different Send Money request.")
    metadata = attrs.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("transfer_batch") != batch_id:
        raise ValueError("Provider batch metadata does not match.")
    if attrs.get("currency") != "KES" or (attrs.get("source_identifier") or None) != (source or None):
        raise ValueError("Provider currency or source does not match.")
    if attrs.get("status") not in ("Pending", "Processing", "Processed", "Failed", "Received", "Queued"):
        raise ValueError("Unknown Send Money status; manual review required.")
    expected = {identity(row["destination"]): row for row in requests}
    destinations = attrs.get("destinations")
    if not isinstance(destinations, list) or len(destinations) != len(requests):
        raise ValueError("Provider destinations do not match the batch.")
    found = set()
    for destination in destinations:
        if not isinstance(destination, dict):
            raise ValueError("Invalid provider destination.")
        key = identity(destination)
        if key not in expected or key in found:
            raise ValueError("Unexpected or duplicate provider destination.")
        found.add(key)
        request = expected[key]
        if amount(destination.get("amount")) != amount(request["recipient_amount"]):
            raise ValueError("Provider destination amount does not match.")
        for field in ("network", "bank_branch_ref", "account_name"):
            if field in request["destination"] and destination.get(field) != request["destination"][field]:
                raise ValueError("Provider destination details do not match.")
    batches = attrs.get("transfer_batches") or []
    if not isinstance(batches, list):
        raise ValueError("Invalid transfer batches.")
    results, matched, references = [], set(), set()
    for batch in batches:
        if not isinstance(batch, dict) or not isinstance(batch.get("disbursements"), list):
            raise ValueError("Invalid transfer disbursements.")
        for disbursement in batch["disbursements"]:
            if not isinstance(disbursement, dict):
                raise ValueError("Invalid disbursement.")
            # Some bank/till results expose only destination_reference. Match
            # that to the explicitly confirmed account reference, not row order.
            try:
                key = identity(disbursement)
            except ValueError:
                candidates = [
                    key for key, row in expected.items()
                    if row.get("payout_reference")
                    and row["payout_reference"] == disbursement.get("destination_reference")
                    and key[0] == disbursement.get("destination_type")
                ]
                if len(candidates) != 1:
                    raise ValueError("Disbursement cannot be uniquely matched to a recipient.")
                key = candidates[0]
            if key not in expected or key in matched:
                raise ValueError("Unexpected or duplicate disbursement; review required.")
            matched.add(key)
            row = expected[key]
            if amount(disbursement.get("amount")) != amount(row["recipient_amount"]):
                raise ValueError("Disbursement amount does not match.")
            status = disbursement.get("status")
            if status not in ("Transferred", "Failed", "Pending", "Processing", "Queued"):
                raise ValueError("Unknown disbursement status; manual review required.")
            if status == "Transferred" and disbursement.get("errors"):
                raise ValueError("Conflicting transferred outcome and provider errors.")
            reference = disbursement.get("transaction_reference")
            if reference is not None and not isinstance(reference, str):
                raise ValueError("Invalid provider transaction reference.")
            if reference:
                if reference in references:
                    raise ValueError("Duplicate provider transaction reference; review required.")
                references.add(reference)
            state = "Paid" if status == "Transferred" else ("Failed" if status == "Failed" else "Pending")
            results.append({
                "request": row["name"], "status": state,
                "transaction_reference": disbursement.get("transaction_reference"),
                "destination_reference": disbursement.get("destination_reference"),
                "amount": str(amount(disbursement["amount"])),
            })
    return results


def callback_token(batch_id, encryption_key):
    return hmac.new(
        encryption_key.encode(), ("transfer-batch:" + batch_id).encode(), hashlib.sha256
    ).hexdigest()


def diagnostic(payload):
    """An allowlisted diagnostic avoids storing names, account numbers or tokens."""
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    data = data if isinstance(data, dict) else {}
    attrs = data.get("attributes")
    attrs = attrs if isinstance(attrs, dict) else {}
    batches = attrs.get("transfer_batches")
    return json.dumps({
        "provider_id": data.get("id"), "status": attrs.get("status"),
        "batch_count": len(batches) if isinstance(batches, list) else 0,
    }, sort_keys=True)
