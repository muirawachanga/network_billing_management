# Copyright (c) 2021, stephen and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document
import frappe


class CaptivePortalCode(Document):
    pass


def get_captive_code():
    code = frappe.get_list(
        "Captive Portal Code",
        {"status": "Active", "sms_sent": 0},
        limit=1,
        order_by="creation desc",
        ignore_permissions=True,
    )
    if code:
        return code[0].name


def update_sent_code(code, sms_code):
    sms_sent = 1
    if sms_code != 200:
        sms_sent = 0
    doc = frappe.get_doc("Captive Portal Code", code)
    doc.status = "Inactive"
    doc.sms_sent = sms_sent
    doc.save(ignore_permissions=True)
