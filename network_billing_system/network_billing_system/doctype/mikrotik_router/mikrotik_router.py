import ipaddress
import re

import frappe
from frappe.model.document import Document


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def search_zones(doctype, txt, searchfield, start, page_len, filters, as_dict=False):
    if doctype != "Captive Portal Zone":
        frappe.throw(frappe._("This search is only for Captive Portal Zones."))

    # Standard Link search hides disabled zones, which must be selectable during setup.
    return frappe.get_list(
        "Captive Portal Zone",
        fields=["name", "display_name"],
        filters={"backend": "MikroTik"},
        or_filters=[["name", "like", f"%{txt}%"], ["display_name", "like", f"%{txt}%"]],
        order_by="name asc",
        limit_start=max(0, start),
        limit_page_length=max(1, min(page_len, 100)),
        as_list=not as_dict,
    )


class MikroTikRouter(Document):
    def validate(self):
        host = self.host or ""
        try:
            ipaddress.ip_address(host)
        except ValueError:
            labels = host.split(".")
            if len(host) > 253 or not all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part)
                for part in labels
            ):
                frappe.throw("Use an IP address or DNS hostname, not a URL.")
        if not 1 <= int(self.port or 0) <= 65535:
            frappe.throw("Use a valid RouterOS API port.")
        if self.transport == "Plaintext" and not self.allow_plaintext:
            frappe.throw("Plaintext RouterOS API access requires explicit opt-in.")
        if self.api_user.strip().lower() == "admin":
            frappe.throw("Use a dedicated, restricted RouterOS API account.")
        if self.automatic_compensation and not self.pilot_verified:
            frappe.throw("Verify the target hardware before enabling compensation.")
        previous = self.get_doc_before_save()
        if previous and any(
            previous.get(field) != self.get(field) for field in (
                "zone", "router_identity", "hotspot_server", "host", "port",
                "transport", "allow_plaintext", "api_user",
            )
        ):
            frappe.throw("Create a new configuration revision when changing router identity or routing.")
