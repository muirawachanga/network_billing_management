from frappe.model.document import Document

from network_billing_system.transfer_configuration import validate_recipient


class TransferRecipient(Document):
    def validate(self):
        validate_recipient(self)
