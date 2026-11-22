import frappe
from frappe.model.document import Document

from network_billing_system.mikrotik_grants import validate_details


class MikroTikComplimentaryGrant(Document):
    def validate(self):
        status = None if self.is_new() else frappe.db.get_value(self.doctype, self.name, "status", for_update=True)
        if status == "Approved":
            frappe.throw("An approved complimentary grant cannot be edited.")
        if self.status != "Draft" or any(self.get(field) for field in (
            "issuer", "approved_at", "issuance", "notification", "router",
            "hotspot_server", "hotspot_profile", "hotspot_rate_limit",
        )):
            frappe.throw("Use Approve Grant to issue complimentary access.")
        validate_details(self)

    def on_trash(self):
        if frappe.db.get_value(self.doctype, self.name, "status", for_update=True) == "Approved":
            frappe.throw("An approved complimentary grant cannot be deleted.")
