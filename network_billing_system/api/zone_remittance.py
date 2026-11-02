import frappe
import requests

from network_billing_system.kopokopo_integration import KopokopoConnector
from network_billing_system.portal_rules import payment_id
from network_billing_system.remittance_rules import verified_transfer
from network_billing_system.zone_finance import FINANCE_ROLES, lock_zone
from network_billing_system.api.captive_portal import _request_limit


def _connector(environment):
    if environment not in ("live", "sandbox"):
        raise ValueError("Select live or sandbox for remittance.")
    prefix = "kopokopo_remittance_" + environment
    client_id = frappe.conf.get(prefix + "_client_id")
    client_secret = frappe.conf.get(prefix + "_client_secret")
    if environment == "live" and not client_id and not client_secret:
        client_id = frappe.conf.get("kopokopo_client_id")
        client_secret = frappe.conf.get("kopokopo_client_secret")
    if not client_id or not client_secret:
        raise ValueError("Configure a complete {} credential pair in site_config.json.".format(environment))
    return KopokopoConnector(env=environment, app_key=client_id, app_secret=client_secret)


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
    frappe.throw("Use Transfer Request and Transfer Batch for new API payouts. Legacy manual reconciliation remains available.")


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
        connector = _connector(doc.provider_environment)
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
