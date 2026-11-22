from frappe.model.document import Document

from network_billing_system.transfer_configuration import validate_settings


class TransferSettings(Document):
    def validate(self):
        validate_settings(self)
