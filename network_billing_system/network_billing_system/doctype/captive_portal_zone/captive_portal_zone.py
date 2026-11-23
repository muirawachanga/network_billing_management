import frappe
from frappe.model.document import Document


class CaptivePortalZone(Document):
    def validate(self):
        if self.get("backend") == "MikroTik" and self.enabled:
            if not self.mikrotik_router:
                frappe.throw(frappe._(
                    "Save the zone with Enabled unchecked first. Then create and link its MikroTik Router before enabling it."
                ))
            if not frappe.db.exists("MikroTik Router", {
                "name": self.mikrotik_router, "zone": self.name,
            }):
                frappe.throw(frappe._("Choose a MikroTik router configuration for this zone."))
