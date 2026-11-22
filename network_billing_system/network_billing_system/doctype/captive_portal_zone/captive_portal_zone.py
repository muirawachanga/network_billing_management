import frappe
from frappe.model.document import Document


class CaptivePortalZone(Document):
    def validate(self):
        if self.get("backend") == "MikroTik" and self.enabled:
            if not self.mikrotik_router or not frappe.db.exists("MikroTik Router", {
                "name": self.mikrotik_router, "zone": self.name,
            }):
                frappe.throw("Choose a MikroTik router configuration for this zone.")
