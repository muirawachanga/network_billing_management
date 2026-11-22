from frappe.model.document import Document

from network_billing_system.mikrotik_banners import validate_banner


class MikroTikBanner(Document):
    def validate(self):
        validate_banner(self)
