from frappe.model.document import Document

from network_billing_system.transfer_configuration import validate_provider


class TransferProviderAccount(Document):
    def validate(self):
        validate_provider(self)
