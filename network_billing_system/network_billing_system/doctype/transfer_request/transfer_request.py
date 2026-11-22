import frappe
from frappe.model.document import Document

from network_billing_system import transfer_engine


class TransferRequest(Document):
    def before_submit(self):
        transfer_engine.quote_request(self)
        self.status = "Awaiting Approval"
        self.approved_by = self.approved_at = self.batch = None
        self.actual_fee = 0
        self.fee_reconciled = 0

    def validate(self):
        if self.docstatus == 0:
            transfer_engine.quote_request(self)
            self.status = "Draft"
            self.approved_by = self.approved_at = self.batch = None
            self.actual_fee = 0
            self.fee_reconciled = 0
            if not self.flags.transfer_schedule:
                self.schedule_key = None

    def on_submit(self):
        self.db_set("status", "Awaiting Approval")

    def before_cancel(self):
        transfer_engine.cancel_request(self)

    def on_cancel(self):
        self.db_set("status", "Cancelled")
