from urllib.parse import urlencode

import frappe
import requests
from frappe.utils import get_url

from network_billing_system.kopokopo_integration import KopokopoConnector
from network_billing_system.portal_rules import payment_id
from network_billing_system.remittance_rules import (
    transfer_id_from_location, transfer_payload, verified_transfer,
)
from network_billing_system.zone_finance import FINANCE_ROLES, ensure_funds, lock_zone
from network_billing_system.api.captive_portal import _request_limit


def _lock_remittance(name):
    zone = frappe.db.get_value("Zone Remittance", name, "zone")
    lock_zone(zone)
    rows = frappe.db.sql(
        "select * from `tabZone Remittance` where name=%s for update", (name,), as_dict=True
    )
    if not rows:
        frappe.throw("Unknown remittance.")
    doc = frappe.get_doc(dict(rows[0], doctype="Zone Remittance"))
    if doc.docstatus != 1:
        frappe.throw("Submit the remittance before sending or checking it.")
    return doc


def _failure(doc, exc):
    # Never release funds after an ambiguous network response.
    current = _lock_remittance(doc.name)
    if current.status not in ("Paid", "Failed"):
        frappe.db.set_value("Zone Remittance", doc.name, {
            "status": "Unknown", "last_error": str(exc),
        })
    frappe.log_error(frappe.get_traceback(), "Zone remittance: " + doc.name)
    return {
        "status": current.status if current.status in ("Paid", "Failed") else "Unknown", "error": True,
        "message": "Transfer needs checking. Funds remain reserved; do not send another transfer.",
    }


@frappe.whitelist(methods=["POST"])
def send_remittance(remittance_id):
    frappe.only_for(FINANCE_ROLES)
    if frappe.conf.get("enable_zone_remittance") not in (True, 1):
        frappe.throw("Zone transfers are disabled. Enable only after sandbox validation and recipient review.")
    doc = _lock_remittance(remittance_id)
    if doc.withdrawal_method != "Kopo Kopo API":
        frappe.throw("External withdrawals are ledger reconciliation only. No transfer can be initiated.")
    if doc.provider_environment == "live":
        frappe.throw("Live remittance is not enabled in this release. Validate the payout response in sandbox first.")
    if doc.status != "Queued" or doc.provider_transfer_id:
        frappe.throw("This remittance was already sent or needs reconciliation. Do not resend it.")
    ensure_funds(doc.zone, doc.provider_environment, 0)
    callback = get_url("/api/method/network_billing_system.api.zone_remittance.transfer_callback?")
    callback += urlencode({"remittance_id": doc.name})
    if not callback.startswith("https://"):
        frappe.throw("A public HTTPS site URL is required for transfer callbacks.")
    payload = transfer_payload(doc, callback)
    try:
        connector = KopokopoConnector(env=doc.provider_environment)
    except (ValueError, requests.RequestException) as exc:
        frappe.db.set_value("Zone Remittance", doc.name, "last_error", str(exc))
        frappe.log_error(frappe.get_traceback(), "Zone remittance authentication: " + doc.name)
        return {"status": "Queued", "error": True, "message": "Provider authentication failed. No transfer was initiated."}
    frappe.db.set_value("Zone Remittance", doc.name, "status", "Sending")
    # Persist the intent before the external side effect. A crash or timeout
    # cannot make this document eligible for a second send.
    frappe.db.commit()
    try:
        response = requests.post(
            connector.base_url + "/api/v2/send_money",
            json=payload,
            headers={"Authorization": "Bearer " + connector.authentication_token, "Content-Type": "application/json"},
            timeout=15, allow_redirects=False,
        )
        response.raise_for_status()
        if response.status_code != 201:
            raise ValueError("Kopo Kopo did not confirm transfer creation.")
        transfer_id = transfer_id_from_location(response.headers.get("Location"), connector.base_url)
    except (ValueError, requests.RequestException) as exc:
        return _failure(doc, exc)
    current = _lock_remittance(doc.name)
    if current.provider_transfer_id and current.provider_transfer_id != transfer_id:
        frappe.throw("A different transfer is attached to this remittance. Contact support.")
    values = {"provider_transfer_id": transfer_id}
    if current.status not in ("Paid", "Failed"):
        values["status"] = "Pending"
    frappe.db.set_value("Zone Remittance", doc.name, values)
    return {"status": values.get("status", current.status)}


def _check(name, candidate=None):
    doc = _lock_remittance(name)
    if doc.withdrawal_method != "Kopo Kopo API":
        frappe.throw("External withdrawals are reconciled using verified provider evidence, not an API retry.")
    if doc.status in ("Paid", "Failed"):
        return {"status": doc.status}
    if doc.status not in ("Sending", "Pending", "Unknown"):
        frappe.throw("This remittance has not been sent.")
    transfer_id = doc.provider_transfer_id or candidate
    if not transfer_id:
        frappe.throw("Enter the Kopo Kopo transfer UUID. Never initiate a replacement for an uncertain transfer.")
    if candidate and doc.provider_transfer_id and candidate != doc.provider_transfer_id:
        frappe.throw("This remittance has a different transfer UUID.")
    try:
        connector = KopokopoConnector(env=doc.provider_environment)
        response = requests.get(
            connector.base_url + "/api/v2/send_money/" + payment_id(transfer_id),
            headers={"Authorization": "Bearer " + connector.authentication_token},
            timeout=15, allow_redirects=False,
        )
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Kopo Kopo did not return the transfer.")
        status = verified_transfer(response.json(), doc, transfer_id)
    except (ValueError, requests.RequestException) as exc:
        return _failure(doc, exc)
    frappe.db.set_value("Zone Remittance", doc.name, {
        "status": status, "provider_transfer_id": transfer_id, "last_error": "",
    })
    return {"status": status}


@frappe.whitelist(methods=["POST"])
def check_remittance(remittance_id, provider_transfer_id=None):
    frappe.only_for(FINANCE_ROLES)
    return _check(remittance_id, payment_id(provider_transfer_id) if provider_transfer_id else None)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def transfer_callback(remittance_id, **kwargs):
    # The callback is notification only. No outcome or recipient supplied by
    # the caller is trusted; confirmation comes from authenticated provider GET.
    _request_limit("zone-remittance-callback", limit=60, seconds=300)
    if not frappe.db.exists("Zone Remittance", remittance_id):
        frappe.throw("Unknown remittance.")
    return _check(remittance_id)
