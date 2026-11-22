import frappe
from frappe.model.document import Document

from network_billing_system.notify_platform_transport import NotifyClient, NotifyError


class MikroTikNotifySettings(Document):
    def validate(self):
        if self.enabled:
            try:
                # Password inputs can still be plaintext here for a newly entered value.
                NotifyClient.from_settings(self)
            except NotifyError:
                frappe.throw("Configure an HTTPS Notify Platform origin, account and API credentials.")
