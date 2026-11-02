from frappe.model.document import Document

from network_billing_system.transfer_configuration import validate_fee_policy


class TransferFeePolicy(Document):
    def validate(self):
        validate_fee_policy(self)
