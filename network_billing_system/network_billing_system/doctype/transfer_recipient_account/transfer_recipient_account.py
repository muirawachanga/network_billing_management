from frappe.model.document import Document

from network_billing_system.transfer_configuration import validate_recipient_account


class TransferRecipientAccount(Document):
    def validate(self):
        validate_recipient_account(self)
