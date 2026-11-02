import frappe
from frappe.model.document import Document


class ZoneFinanceEntry(Document):
    def validate(self):
        if not self.is_new() or not self.flags.transfer_posting:
            frappe.throw("Finance entries are append-only and must be posted by transfer operations.")

    def on_trash(self):
        frappe.throw("Finance entries cannot be deleted. Post a reversal.")
