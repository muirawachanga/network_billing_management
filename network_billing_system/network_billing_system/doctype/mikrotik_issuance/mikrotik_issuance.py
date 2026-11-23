import frappe
from frappe.model.document import Document


class MikroTikIssuance(Document):
    def validate(self):
        if bool(self.checkout) == bool(self.complimentary_grant):
            frappe.throw("An issuance must have exactly one source.")
        if self.complimentary_grant and (self.predecessor or self.incident):
            frappe.throw("Complimentary access cannot be a paid outage replacement.")
