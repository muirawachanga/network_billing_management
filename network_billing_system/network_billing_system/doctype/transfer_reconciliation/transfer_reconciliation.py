import frappe
from frappe.model.document import Document

from network_billing_system.transfer_reconciliation import validate_reconciliation, post_reconciliation


class TransferReconciliation(Document):
    def validate(self):
        validate_reconciliation(self)

    def on_submit(self):
        post_reconciliation(self)

    def before_cancel(self):
        frappe.throw("Reconciliation cannot be cancelled. Post an evidence-backed reversal.")

    def on_trash(self):
        if self.docstatus:
            frappe.throw("Posted reconciliation cannot be deleted.")
