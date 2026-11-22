# Copyright (c) 2021, stephen and contributors
# For license information, please see license.txt

# import frappe
from __future__ import unicode_literals

from frappe.model.document import Document
import frappe
from network_billing_system.utils import load_configuration


class CaptivePortalCode(Document):
    def validate(self):
        self.code = (self.code or "").strip()
        if not self.code:
            frappe.throw("Voucher code is required.")
        if self.is_new() and not self.package:
            frappe.throw("Select the package matching this pfSense voucher.")
        previous = self.get_doc_before_save()
        if previous and previous.portal_checkout and any(
            previous.get(field) != self.get(field)
            for field in ("code", "package", "zone", "duration_minutes", "portal_checkout")
        ):
            frappe.throw("An allocated voucher cannot be reassigned or changed.")
        if self.package:
            package_zone = frappe.db.get_value("Internet Package Profile", self.package, "zone")
            if not self.zone or self.zone != package_zone:
                frappe.throw("Voucher zone must match the selected package's zone.")
            self.duration_minutes = frappe.db.get_value(
                "Internet Package Profile", self.package, "duration_minutes"
            )
            if not self.duration_minutes or self.duration_minutes <= 0:
                frappe.throw("Select a package with a valid voucher duration.")


def get_captive_code():
    filters = {"status": "Active", "sms_sent": 0}
    if load_configuration("enable_package_portal"):
        filters["package"] = ["is", "not set"]
    code = frappe.get_list(
        "Captive Portal Code",
        filters,
        fields=["code"],
        limit=1,
        order_by="creation desc",
        ignore_permissions=True,
    )
    if code:
        return code[0].code


def update_sent_code(code, sms_code):
    sms_sent = 1
    if sms_code != 200:
        sms_sent = 0
    name = frappe.db.get_value("Captive Portal Code", {"code": code}, "name")
    doc = frappe.get_doc("Captive Portal Code", name)
    doc.status = "Inactive"
    doc.sms_sent = sms_sent
    doc.save(ignore_permissions=True)
