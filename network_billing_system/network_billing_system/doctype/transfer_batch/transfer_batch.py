import frappe
from frappe.model.document import Document


class TransferBatch(Document):
    def validate(self):
        if not self.is_new() or not self.flags.transfer_posting:
            frappe.throw("Create batches through the transfer operation. Batch contents are immutable.")

    def on_trash(self):
        frappe.throw("Transfer batches cannot be deleted.")
