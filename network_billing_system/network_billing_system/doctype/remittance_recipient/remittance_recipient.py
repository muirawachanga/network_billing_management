import frappe
from frappe.model.document import Document

from network_billing_system.recipient_rules import RECIPIENT_FIELDS, registration_request
from network_billing_system.zone_finance import FINANCE_ROLES


class RemittanceRecipient(Document):
    def validate(self):
        frappe.only_for(FINANCE_ROLES)
        if self.provider_environment not in ("live", "sandbox"):
            frappe.throw("Select live or sandbox.")
        try:
            registration_request(self)
        except ValueError as exc:
            frappe.throw(str(exc))
        if self.is_new():
            self.registration_status = "Draft"
            self.provider_reference = None
        else:
            previous = self.get_doc_before_save()
            if previous and previous.registration_status == "Draft":
                self.registration_status = "Draft"
                self.provider_reference = None
            if previous and previous.registration_status != "Draft":
                fields = set(sum(RECIPIENT_FIELDS.values(), ())) | {
                    "zone", "destination_type", "provider_environment",
                    "provider_reference", "registration_status", "recipient_name",
                }
                if any(self.get(field) != previous.get(field) for field in fields):
                    frappe.throw("Registered or uncertain recipient details are immutable. Create a new recipient.")
