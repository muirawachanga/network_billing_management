import ipaddress
import re

import frappe
from frappe.model.document import Document


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
