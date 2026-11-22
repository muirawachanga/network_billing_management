import frappe
import requests
from urllib.parse import urlsplit

from network_billing_system.api.zone_remittance import _connector
from network_billing_system.portal_rules import payment_id
from network_billing_system.recipient_rules import registration_request
from network_billing_system.zone_finance import FINANCE_ROLES


@frappe.whitelist(methods=["POST"])
def register_recipient(recipient_id):
    frappe.only_for(FINANCE_ROLES)
    if frappe.conf.get("enable_zone_remittance") not in (True, 1):
        frappe.throw("Enable sandbox remittance testing first.")
    rows = frappe.db.sql(
        "select * from `tabRemittance Recipient` where name=%s for update",
        (recipient_id,), as_dict=True,
    )
    if not rows:
        frappe.throw("Unknown recipient.")
    doc = frappe.get_doc(dict(rows[0], doctype="Remittance Recipient"))
    if doc.provider_environment != "sandbox":
        frappe.throw("Live account registration remains blocked pending sandbox validation.")
    if doc.registration_status != "Draft" or not doc.enabled:
        frappe.throw("This recipient is disabled, registered or needs provider reconciliation. Do not register twice.")
    endpoint, payload = registration_request(doc)
    connector = _connector(doc.provider_environment)
    frappe.db.set_value("Remittance Recipient", doc.name, "registration_status", "Registering")
    frappe.db.commit()
    try:
        response = requests.post(
            connector.base_url + "/api/v2/" + endpoint,
            json=payload, headers={"Authorization": "Bearer " + connector.authentication_token},
            timeout=15, allow_redirects=False,
        )
        response.raise_for_status()
        if response.status_code != 201:
            raise ValueError("Kopo Kopo did not confirm recipient creation.")
        location = urlsplit(response.headers.get("Location") or "")
        base = urlsplit(connector.base_url)
        prefix = "/api/v2/" + endpoint + "/"
        if ((location.scheme, location.netloc) != (base.scheme, base.netloc)
                or not location.path.startswith(prefix) or location.query or location.fragment):
            raise ValueError("Kopo Kopo returned an unexpected recipient location.")
        reference = payment_id(location.path[len(prefix):])
    except (ValueError, requests.RequestException) as exc:
        frappe.db.set_value("Remittance Recipient", doc.name, "registration_status", "Unknown")
        frappe.log_error(frappe.get_traceback(), "Recipient registration: " + doc.name)
        return {"status": "Unknown", "message": "Registration needs provider review. Do not repeat registration. See Error Log."}
    frappe.db.set_value("Remittance Recipient", doc.name, {
        "provider_reference": reference, "registration_status": "Registered",
    })
    return {"status": "Registered"}
