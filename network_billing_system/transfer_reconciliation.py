import hashlib

import frappe

from network_billing_system.portal_rules import payment_id
from network_billing_system.transfer_engine import (
    authorize, configuration, lock_doc, post_entry, request_snapshot, settle_request,
)
from network_billing_system.zone_finance import lock_zone, money


def validate_reconciliation(doc):
    authorize("Transfer Reconciler")
    provider = configuration().get_provider(doc.provider_account)
    if not doc.verified or str(doc.verified) not in ("1", "True"):
        frappe.throw("Confirm provider verification before reconciliation.")
    if not frappe.db.get_value("File", {"file_url": doc.evidence, "is_private": 1}, "name"):
        frappe.throw("Upload private provider evidence.")
    if doc.reconciliation_type not in ("External Withdrawal", "Transfer Outcome", "Actual Fee", "Reversal"):
        frappe.throw("Select a reconciliation type.")
    if not doc.allocations or money(doc.total_amount) < 0:
        frappe.throw("Enter allocation rows and a nonnegative total.")
    if any(money(row.amount) < 0 for row in doc.allocations):
        frappe.throw("Allocation amounts cannot be negative.")
    if sum((money(row.amount) for row in doc.allocations), money(0)) != money(doc.total_amount):
        frappe.throw("Allocation amounts must exactly equal the provider total.")
    reference = str(doc.provider_reference or "").strip()
    if not reference:
        frappe.throw("Enter a provider reference.")
    doc.provider_reference = reference
    if doc.provider_transfer_id:
        doc.provider_transfer_id = payment_id(doc.provider_transfer_id)
    elif doc.reconciliation_type == "External Withdrawal":
        try:
            doc.provider_transfer_id = payment_id(reference)
        except ValueError:
            doc.provider_transfer_id = None
    doc.external_transfer_key = None
    if doc.reconciliation_type == "External Withdrawal" and doc.provider_transfer_id:
        doc.external_transfer_key = hashlib.sha256(
            (provider.environment + ":" + doc.provider_transfer_id).encode()
        ).hexdigest()
    # A provider account is not part of uniqueness: two configurations can
    # point at the same provider wallet. Environment + reference must dedupe.
    doc.posting_key = hashlib.sha256(
        (provider.environment + ":" + doc.reconciliation_type + ":" + reference.upper()).encode()
    ).hexdigest()


def post_reconciliation(doc):
    authorize("Transfer Reconciler")
    provider = configuration().get_provider(doc.provider_account)
    zones = {row.zone for row in doc.allocations}
    if doc.reconciliation_type == "Transfer Outcome":
        names = [row.transfer_request for row in doc.allocations if row.transfer_request]
        batch_names = [name for name in frappe.get_all(
            "Transfer Request", filters={"name": ["in", names]}, pluck="batch",
        ) if name]
        if batch_names:
            zones.update(frappe.get_all(
                "Transfer Request", filters={"batch": ["in", batch_names]}, pluck="zone",
            ))
    for zone in sorted(zones):
        lock_zone(zone)
    settings = configuration().get_settings()
    if money(doc.total_amount) >= money(settings.approval_threshold) and doc.owner == frappe.session.user:
        frappe.throw("A different reconciler must submit this reconciliation above the approval threshold.")
    if doc.reconciliation_type == "External Withdrawal":
        if doc.provider_transfer_id and (
            frappe.db.exists("Transfer Batch", {"provider_id": doc.provider_transfer_id})
            or frappe.db.exists("Zone Remittance", {"provider_transfer_id": doc.provider_transfer_id})
        ):
            frappe.throw("This provider transfer is already tracked. Reconcile its original record.")
        if frappe.db.exists("Zone Remittance", {"external_reference": doc.provider_reference}):
            frappe.throw("This external withdrawal was already recorded in Zone Remittance.")
        if frappe.db.exists("Transfer Disbursement", {"transaction_reference": doc.provider_reference}):
            frappe.throw("This provider transaction already has a disbursement. Reconcile its original request.")
    seen, batches = set(), set()
    for index, row in enumerate(doc.allocations):
        key = "reconciliation:{}:{}".format(doc.name, index)
        if doc.reconciliation_type == "External Withdrawal":
            if row.transfer_request or row.finance_entry or money(row.amount) <= 0:
                frappe.throw("External withdrawals require positive zone allocations, not existing requests or entries.")
            post_entry(row.zone, provider.environment, key, "Withdrawal",
                       "Transfer Reconciliation", doc.name, debit=money(row.amount), note=row.note)
        elif doc.reconciliation_type in ("Transfer Outcome", "Actual Fee"):
            if not row.transfer_request or row.transfer_request in seen:
                frappe.throw("Choose distinct transfer requests for reconciliation.")
            seen.add(row.transfer_request)
            request = lock_doc("Transfer Request", row.transfer_request)
            snapshot = request_snapshot(request)
            if request.docstatus != 1 or request.zone != row.zone or request.provider_account != provider.name:
                frappe.throw("The allocation does not match the request's zone and provider account.")
            if doc.reconciliation_type == "Transfer Outcome":
                if row.outcome not in ("Paid", "Failed") or not doc.provider_transfer_id:
                    frappe.throw("Select a verified outcome and enter the Send Money UUID.")
                if not request.batch or request.status not in ("Sending", "Pending", "Needs Review"):
                    frappe.throw("Only uncertain or pending sent requests can be manually settled.")
                batch = lock_doc("Transfer Batch", request.batch)
                if batch.provider_id and batch.provider_id != doc.provider_transfer_id:
                    frappe.throw("The provider UUID does not match this batch.")
                if money(row.amount) != money(request.recipient_amount):
                    frappe.throw("The evidence amount must match the request's recipient amount.")
                frappe.db.set_value("Transfer Batch", batch.name, "provider_id", doc.provider_transfer_id)
                batches.add(batch.name)
                settle_request(request, {
                    "status": row.outcome, "amount": str(row.amount),
                    "destination_reference": snapshot.get("payout_reference"),
                    "transaction_reference": doc.provider_reference,
                }, batch)
            else:
                if request.status not in ("Paid", "Failed") or request.fee_reconciled:
                    frappe.throw("Actual fees can be posted once per terminal request, including an explicit zero.")
                post_entry(row.zone, provider.environment, key, "Fee",
                           "Transfer Reconciliation", doc.name, debit=money(row.amount),
                           reserved=-money(request.estimated_fee), note=row.note)
                frappe.db.set_value("Transfer Request", request.name, {
                    "actual_fee": row.amount, "fee_reconciled": 1,
                })
        else:
            if not row.finance_entry or row.finance_entry in seen:
                frappe.throw("Select distinct finance entries to reverse.")
            seen.add(row.finance_entry)
            entry = lock_doc("Zone Finance Entry", row.finance_entry)
            if (entry.zone != row.zone or entry.environment != provider.environment
                    or money(entry.debit) <= 0
                    or money(row.amount) != money(entry.debit)):
                frappe.throw("Reverse only an exact positive debit in the same zone and environment. Reservation releases are not reversed.")
            post_entry(row.zone, provider.environment, key, "Reversal",
                       "Transfer Reconciliation", doc.name, debit=-money(entry.debit),
                       reverses=entry.name, note=doc.note)
    for batch_name in batches:
        requests = frappe.get_all(
            "Transfer Batch Item", filters={"parent": batch_name, "parenttype": "Transfer Batch"},
            pluck="transfer_request",
        )
        if requests and all(
            lock_doc("Transfer Request", name).status in ("Paid", "Failed") for name in sorted(requests)
        ):
            frappe.db.set_value("Transfer Batch", batch_name, {"status": "Completed", "last_error": ""})
