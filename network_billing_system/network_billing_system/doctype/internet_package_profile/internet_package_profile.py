# Copyright (c) 2021, stephen and contributors
# For license information, please see license.txt
from __future__ import unicode_literals

# import frappe
import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt

# from network_billing_system.kopokopo_integration import KopokopoConnector
# from frappe.utils import get_request_site_address


class InternetPackageProfile(Document):
    def validate(self):
        if flt(self.price) <= 0:
            frappe.throw("Package price must be greater than zero.")
        if cint(self.duration_minutes) <= 0:
            frappe.throw("Voucher duration must be greater than zero minutes.")
        previous = self.get_doc_before_save()
        if previous and previous.get("zone") != self.get("zone") and frappe.db.exists(
            "Captive Portal Code", {"package": self.name}
        ):
            frappe.throw("This package has vouchers. Create a new package for another zone.")
        if (
            previous
            and previous.duration_minutes != self.duration_minutes
            and frappe.db.exists("Captive Portal Code", {"package": self.name})
        ):
            frappe.throw(
                "This package already has pfSense vouchers. Create a new package "
                "to use a different duration."
            )
