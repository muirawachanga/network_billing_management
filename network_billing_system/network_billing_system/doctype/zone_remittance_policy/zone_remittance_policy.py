from frappe.model.document import Document

from network_billing_system.transfer_configuration import validate_zone_policy


class ZoneRemittancePolicy(Document):
    def validate(self):
        validate_zone_policy(self)
