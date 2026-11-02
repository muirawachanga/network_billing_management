"""Transfer operations deliberately isolated from checkout and voucher allocation."""

import hmac
import json
from decimal import Decimal
from urllib.parse import urlencode

import frappe
import requests
from frappe.utils import get_url, now_datetime

from network_billing_system.portal_rules import payment_id
from network_billing_system.remittance_rules import transfer_id_from_location
from network_billing_system.transfer_rules import (
    batch_payload, callback_token, diagnostic, integer_amount, verified_results,
)
from network_billing_system.zone_finance import ensure_funds, lock_zone, money


def configuration():
    from network_billing_system import transfer_configuration
    return transfer_configuration


def authorize(role):
    frappe.only_for(("System Manager", role))


def enabled(provider=None):
    settings = configuration().get_settings()
    if not settings.enabled:
        frappe.throw("Transfers are disabled in Transfer Settings.")
    if provider and provider.environment == "live" and not settings.allow_live:
        frappe.throw("Live transfers are disabled in Transfer Settings.")
    return settings


def lock_doc(doctype, name):
    rows = frappe.db.sql(
        "select * from `tab{}` where name=%s for update".format(doctype),
        (name,), as_dict=True,
    )
    if not rows:
        frappe.throw("Unknown {}.".format(doctype))
    return frappe.get_doc(dict(rows[0], doctype=doctype))


def internal_insert(values):
    doc = frappe.get_doc(values)
    doc.flags.transfer_posting = True
    return doc.insert(ignore_permissions=True)


def post_entry(zone, environment, key, kind, reference_doctype, reference_name,
               credit=0, debit=0, reserved=0, reverses=None, note=None):
    # Callers hold the zone lock. Unique posting keys make rechecks idempotent.
    existing = frappe.db.sql(
        "select name from `tabZone Finance Entry` where source_key=%s for update", (key,)
    )
    if existing:
        return existing[0][0]
    return internal_insert({
        "doctype": "Zone Finance Entry", "zone": zone, "environment": environment,
        "source_key": key, "entry_type": kind, "credit": credit, "debit": debit,
        "reserved": reserved, "reference_doctype": reference_doctype,
        "reference_name": reference_name, "reverses": reverses, "note": note,
    }).name


def policy_for(zone, provider_name, fee_policy):
    name = frappe.db.get_value(
        "Zone Remittance Policy", {
            "zone": zone, "provider_account": provider_name, "fee_policy": fee_policy, "enabled": 1,
        }, "name"
    )
    if not name:
        frappe.throw("Configure an enabled zone remittance policy for this provider account and tariff.")
    return frappe.get_doc("Zone Remittance Policy", name)


def quote_request(doc):
    provider = configuration().get_provider(doc.provider_account)
    policy = policy_for(doc.zone, provider.name, doc.fee_policy)
    snapshot = configuration().recipient_snapshot(doc.recipient_account, doc.zone, provider.name)
    fee_policy = frappe.get_doc("Transfer Fee Policy", doc.fee_policy)
    if policy.fee_policy != fee_policy.name:
        frappe.throw("Use the fee policy assigned to this zone.")
    requested = money(doc.amount)
    if requested <= 0 or doc.fee_bearer not in ("Zone", "Recipient"):
        frappe.throw("Enter a positive amount and select the fee bearer.")
    if fee_policy.destination_type != snapshot["destination"]["type"]:
        frappe.throw("The fee policy does not match the recipient account type.")
    if fee_policy.get("settlement_method") and fee_policy.settlement_method != snapshot.get("settlement_method"):
        frappe.throw("The fee policy settlement method does not match the recipient account.")
    fee = configuration().quote_fee(fee_policy.name, requested)
    if doc.fee_bearer == "Recipient":
        # Solve gross budget = recipient amount + fee(recipient amount).
        # Bisection in whole KES is exact and avoids arbitrary rounding.
        low, high, found = 1, int(requested), None
        while low <= high:
            candidate = (low + high) // 2
            candidate_fee = configuration().quote_fee(fee_policy.name, candidate)
            total = Decimal(candidate) + candidate_fee
            if total == requested:
                found = (Decimal(candidate), candidate_fee)
                break
            if total < requested:
                low = candidate + 1
            else:
                high = candidate - 1
        if not found:
            frappe.throw("The budget cannot produce a whole-KES payout with this tariff. Choose a recipient amount instead.")
        recipient_amount, fee = found
    else:
        recipient_amount = requested
    try:
        integer_amount(recipient_amount)
    except ValueError as exc:
        frappe.throw(str(exc))
    total_debit = recipient_amount + fee
    settings = configuration().get_settings()
    for limit in (settings.max_request_amount, policy.maximum_amount):
        if limit and total_debit > money(limit):
            frappe.throw("This request exceeds the configured transfer limit.")
    snapshot.update({
        "environment": provider.environment, "source_identifier": provider.source_identifier,
        "provider_account": provider.name, "fee_policy": fee_policy.name,
        "fee_bearer": doc.fee_bearer,
        "fee_rule": {field: fee_policy.get(field) for field in (
            "destination_type", "settlement_method", "fixed_fee", "percentage",
            "minimum_fee", "effective_from", "effective_to",
        )},
    })
    doc.recipient_amount, doc.estimated_fee, doc.total_debit = recipient_amount, fee, total_debit
    doc.snapshot = json.dumps(snapshot, default=str, sort_keys=True)


def request_snapshot(doc):
    snapshot = json.loads(doc.snapshot or "{}")
    if not snapshot.get("destination") or not snapshot.get("environment"):
        frappe.throw("Missing approved destination snapshot.")
    return snapshot


@frappe.whitelist(methods=["POST"])
def approve_request(request_id):
    authorize("Transfer Approver")
    zone = frappe.db.get_value("Transfer Request", request_id, "zone")
    lock_zone(zone)
    doc = lock_doc("Transfer Request", request_id)
    if doc.docstatus != 1 or doc.status != "Awaiting Approval":
        frappe.throw("Only submitted requests awaiting approval can be approved.")
    settings = enabled(configuration().get_provider(doc.provider_account))
    if money(doc.total_debit) >= money(settings.approval_threshold) and doc.owner == frappe.session.user:
        frappe.throw("A different user must approve transfers at or above the approval threshold.")
    original = doc.snapshot
    quote_request(doc)
    if doc.snapshot != original:
        frappe.throw("Recipient or fee configuration changed. Cancel and prepare a new request.")
    snapshot = request_snapshot(doc)
    policy = policy_for(doc.zone, doc.provider_account, doc.fee_policy)
    ensure_funds(doc.zone, snapshot["environment"], money(doc.total_debit) + money(policy.retained_balance))
    post_entry(doc.zone, snapshot["environment"], "reserve:" + doc.name, "Reservation",
               "Transfer Request", doc.name, reserved=money(doc.total_debit))
    frappe.db.set_value("Transfer Request", doc.name, {
        "status": "Approved", "approved_by": frappe.session.user, "approved_at": now_datetime(),
    })
    return {"status": "Approved"}


def cancel_request(doc):
    lock_zone(doc.zone)
    current = lock_doc("Transfer Request", doc.name)
    if current.status not in ("Awaiting Approval", "Approved"):
        frappe.throw("Only unbatched, unsent requests can be cancelled.")
    if current.status == "Approved":
        snapshot = request_snapshot(current)
        post_entry(current.zone, snapshot["environment"], "cancel:" + current.name, "Release",
                   "Transfer Request", current.name, reserved=-money(current.total_debit))


def instructions(batch):
    result = []
    for item in batch.requests:
        doc = frappe.get_doc("Transfer Request", item.transfer_request)
        snap = request_snapshot(doc)
        result.append({
            "name": doc.name, "destination": snap["destination"],
            "payout_reference": snap.get("payout_reference"),
            "recipient_amount": str(doc.recipient_amount),
        })
    return result


@frappe.whitelist(methods=["POST"])
def create_batch(request_ids):
    authorize("Transfer Operator")
    ids = frappe.parse_json(request_ids) if isinstance(request_ids, str) else request_ids
    if (not isinstance(ids, list) or not ids or len(ids) > 50
            or any(not isinstance(name, str) or not name for name in ids)
            or len(ids) != len(set(ids))):
        frappe.throw("Choose 1–50 distinct approved transfer requests.")
    zones = sorted({frappe.db.get_value("Transfer Request", name, "zone") for name in ids})
    for zone in zones:
        lock_zone(zone)
    docs = [lock_doc("Transfer Request", name) for name in sorted(ids)]
    if any(doc.docstatus != 1 or doc.status != "Approved" or doc.batch for doc in docs):
        frappe.throw("All requests must be approved and not already batched.")
    if len({doc.provider_account for doc in docs}) != 1:
        frappe.throw("A batch must use one provider account.")
    provider = configuration().get_provider(docs[0].provider_account)
    enabled(provider)
    batch = internal_insert({
        "doctype": "Transfer Batch", "provider_account": provider.name, "status": "Ready",
        "environment": provider.environment, "source_identifier": provider.source_identifier,
        "total_debit": sum((money(doc.total_debit) for doc in docs), money(0)),
        "requests": [{
            "transfer_request": doc.name, "zone": doc.zone, "recipient_account": doc.recipient_account,
            "recipient_amount": doc.recipient_amount, "estimated_fee": doc.estimated_fee,
        } for doc in docs],
    })
    try:
        batch_payload(batch.name, instructions(batch), provider.source_identifier, "https://placeholder.invalid")
    except ValueError as exc:
        frappe.throw(str(exc))
    for doc in docs:
        frappe.db.set_value("Transfer Request", doc.name, {"status": "Batched", "batch": batch.name})
    return {"batch": batch.name}


@frappe.whitelist(methods=["POST"])
def unbatch_requests(batch_id):
    authorize("Transfer Operator")
    batch = lock_batch(batch_id)
    if batch.status != "Ready" or frappe.db.exists("Transfer Attempt", {"batch": batch.name}):
        frappe.throw("Only a ready batch with no send attempt can be unbatched.")
    for row in batch.requests:
        request = frappe.get_doc("Transfer Request", row.transfer_request)
        if request.status != "Batched" or request.batch != batch.name:
            frappe.throw("The request no longer belongs to this ready batch.")
    for row in batch.requests:
        frappe.db.set_value("Transfer Request", row.transfer_request, {"status": "Approved", "batch": None})
    frappe.db.set_value("Transfer Batch", batch.name, "status", "Cancelled")
    return {"status": "Cancelled", "message": "Requests are approved again. Their reservations remain until cancellation or settlement."}


def lock_batch(name):
    # Consistent lock order across approval, cancellation, reconciliation and
    # legacy operations: zones -> requests -> batch.
    names = frappe.get_all("Transfer Batch Item", filters={"parent": name, "parenttype": "Transfer Batch"},
                           pluck="transfer_request")
    if not names:
        frappe.throw("Unknown or empty transfer batch.")
    for zone in sorted({frappe.db.get_value("Transfer Request", item, "zone") for item in names}):
        lock_zone(zone)
    for item in sorted(names):
        lock_doc("Transfer Request", item)
    return lock_doc("Transfer Batch", name)


def _review(batch, message):
    if batch.status == "Completed":
        frappe.db.set_value("Transfer Batch", batch.name, "last_error", message)
        frappe.log_error(message, "Transfer batch: " + batch.name)
        return {"status": "Completed", "error": True, "message": "Settlement is unchanged. Check Error Log for the late error."}
    frappe.db.set_value("Transfer Batch", batch.name, {"status": "Needs Review", "last_error": message})
    for item in batch.requests:
        current = lock_doc("Transfer Request", item.transfer_request).status
        if current not in ("Paid", "Failed"):
            frappe.db.set_value("Transfer Request", item.transfer_request, "status", "Needs Review")
    frappe.log_error(message, "Transfer batch: " + batch.name)
    return {"status": "Needs Review", "error": True, "message": "Funds remain reserved. Check Error Log; do not resend."}


@frappe.whitelist(methods=["POST"])
def send_batch(batch_id):
    authorize("Transfer Operator")
    batch = lock_batch(batch_id)
    provider = configuration().get_provider(batch.provider_account)
    enabled(provider)
    if batch.status != "Ready" or frappe.db.exists("Transfer Attempt", {"batch": batch.name}):
        frappe.throw("A send attempt already exists or this batch is not ready. Reconcile; never resend.")
    key = frappe.conf.get("encryption_key")
    if not key:
        frappe.throw("Configure the site encryption key.")
    callback = get_url("/api/method/network_billing_system.transfer_engine.transfer_callback")
    if not callback.startswith("https://"):
        frappe.throw("Transfers require a public HTTPS callback URL.")
    callback += "?" + urlencode({"batch_id": batch.name, "token": callback_token(batch.name, key)})
    rows = instructions(batch)
    for item in batch.requests:
        request = frappe.get_doc("Transfer Request", item.transfer_request)
        snapshot = request_snapshot(request)
        fresh = configuration().recipient_snapshot(request.recipient_account, request.zone, provider.name)
        if (fresh["destination"] != snapshot["destination"]
                or (snapshot.get("source_identifier") or None) != (provider.source_identifier or None)):
            frappe.throw("Approved account details changed. Do not send this batch.")
        ensure_funds(request.zone, provider.environment, 0)
    payload = batch_payload(batch.name, rows, provider.source_identifier, callback)
    # Authenticate before recording the irreversible send intent.
    try:
        connector = configuration().provider_connector(provider)
    except (ValueError, requests.RequestException):
        frappe.log_error(frappe.get_traceback(), "Transfer authentication: " + batch.name)
        return {"error": True, "status": "Ready", "message": "Authentication failed. No transfer was initiated."}
    attempt = internal_insert({
        "doctype": "Transfer Attempt", "batch": batch.name,
        "started_at": now_datetime(), "status": "Sending",
    })
    frappe.db.set_value("Transfer Batch", batch.name, "status", "Sending")
    for item in batch.requests:
        frappe.db.set_value("Transfer Request", item.transfer_request, "status", "Sending")
    frappe.db.commit()
    try:
        response = requests.post(
            connector.base_url + "/api/v2/send_money", json=payload,
            headers={"Authorization": "Bearer " + connector.authentication_token,
                     "Accept": "application/json", "Content-Type": "application/json"},
            timeout=15, allow_redirects=False,
        )
        response.raise_for_status()
        if response.status_code != 201:
            raise ValueError("Provider did not confirm transfer creation.")
        provider_id = transfer_id_from_location(response.headers.get("Location"), connector.base_url)
    except (ValueError, requests.RequestException):
        batch = lock_batch(batch.name)
        frappe.db.set_value("Transfer Attempt", attempt.name, "status", "Needs Review")
        return _review(batch, "Transfer initiation outcome uncertain. " + frappe.get_traceback())
    batch = lock_batch(batch.name)
    if batch.provider_id and batch.provider_id != provider_id:
        return _review(batch, "Conflicting provider request UUIDs.")
    frappe.db.set_value("Transfer Attempt", attempt.name, {
        "provider_id": provider_id, "status": "Accepted", "http_status": response.status_code,
    })
    status = "Completed" if batch.status == "Completed" else "Pending"
    frappe.db.set_value("Transfer Batch", batch.name, {"provider_id": provider_id, "status": status})
    for item in batch.requests:
        if lock_doc("Transfer Request", item.transfer_request).status not in ("Paid", "Failed"):
            frappe.db.set_value("Transfer Request", item.transfer_request, "status", "Pending")
    return {"status": status}


def settle_request(doc, result, batch):
    doc = lock_doc("Transfer Request", doc.name)
    snapshot = request_snapshot(doc)
    status = result["status"]
    if doc.status in ("Paid", "Failed"):
        if status != doc.status:
            frappe.throw("Provider outcome conflicts with an already settled request. Manual review required.")
        return
    if status == "Pending":
        return
    internal_insert({
        "doctype": "Transfer Disbursement", "batch": batch.name,
        "transfer_request": doc.name, "status": status, "amount": result["amount"],
        "destination_reference": result["destination_reference"],
        "transaction_reference": result["transaction_reference"],
    })
    if status == "Paid":
        # Keep the estimated fee reserved until its actual value is reconciled.
        post_entry(doc.zone, snapshot["environment"], "paid:" + doc.name, "Withdrawal",
                   "Transfer Request", doc.name, debit=money(doc.recipient_amount),
                   reserved=-money(doc.recipient_amount))
    else:
        # Failed principal is released, but fees remain uncertain until a
        # reconciler records actual fees (including an explicitly confirmed 0).
        post_entry(doc.zone, snapshot["environment"], "failed:" + doc.name, "Release",
                   "Transfer Request", doc.name, reserved=-money(doc.recipient_amount))
    frappe.db.set_value("Transfer Request", doc.name, "status", status)


def reconcile_batch(batch_id, candidate=None):
    batch = lock_batch(batch_id)
    if batch.status in ("Ready", "Cancelled"):
        frappe.throw("This batch has not been sent or was cancelled.")
    if batch.status == "Completed":
        return {"status": "Completed"}
    provider = configuration().get_provider(batch.provider_account)
    provider_id = batch.provider_id or candidate
    if candidate and batch.provider_id and candidate != batch.provider_id:
        frappe.throw("This batch already has a different provider UUID.")
    if not provider_id:
        frappe.throw("Enter the Send Money request UUID from Kopo Kopo. Do not create a replacement.")
    try:
        connector = configuration().provider_connector(provider)
        response = requests.get(
            connector.base_url + "/api/v2/send_money/" + payment_id(provider_id),
            headers={"Authorization": "Bearer " + connector.authentication_token, "Accept": "application/json"},
            timeout=15, allow_redirects=False,
        )
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Provider did not return the Send Money resource.")
        payload = response.json()
        results = verified_results(payload, batch.name, provider_id, instructions(batch), provider.source_identifier)
    except (ValueError, requests.RequestException):
        return _review(batch, "Provider status verification failed. " + frappe.get_traceback())
    # Validate the entire payload before applying any settlement.
    for result in results:
        doc = lock_doc("Transfer Request", result["request"])
        if doc.status in ("Paid", "Failed") and result["status"] != doc.status:
            return _review(batch, "Provider outcome conflicts with a settled request.")
    for result in results:
        settle_request(lock_doc("Transfer Request", result["request"]), result, batch)
    statuses = [lock_doc("Transfer Request", row.transfer_request).status for row in batch.requests]
    status = "Completed" if all(value in ("Paid", "Failed") for value in statuses) else "Pending"
    frappe.db.set_value("Transfer Batch", batch.name, {
        "provider_id": provider_id, "status": status, "last_error": "",
    })
    attempt = frappe.db.get_value("Transfer Attempt", {"batch": batch.name}, "name")
    if attempt:
        frappe.db.set_value("Transfer Attempt", attempt, {"provider_id": provider_id, "diagnostic": diagnostic(payload)})
    return {"status": status}


@frappe.whitelist(methods=["POST"])
def check_batch(batch_id, provider_id=None):
    authorize("Transfer Reconciler")
    return reconcile_batch(batch_id, payment_id(provider_id) if provider_id else None)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def transfer_callback(batch_id, token=None, data=None, **kwargs):
    from network_billing_system.api.captive_portal import _request_limit
    _request_limit("transfer-callback", limit=60, seconds=300)
    key = frappe.conf.get("encryption_key") or ""
    if not key or not hmac.compare_digest(str(token or ""), callback_token(batch_id, key)):
        frappe.throw("Invalid transfer callback token.", frappe.PermissionError)
    # Provider callback content is never settlement authority. The opaque
    # callback capability only permits an authenticated provider status query.
    candidate = None
    if data:
        notification = frappe.parse_json(data) if isinstance(data, str) else data
        if isinstance(notification, dict) and notification.get("id"):
            candidate = payment_id(notification["id"])
    return reconcile_batch(batch_id, candidate)
