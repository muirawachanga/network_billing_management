# Copyright (c) 2021, stephen and contributors
# For license information, please see license.txt
from __future__ import unicode_literals

# import frappe
import frappe
from frappe.model.document import Document
from network_billing_system.sms_integration import localsms
from network_billing_system.network_billing_system.doctype.captive_portal_code.captive_portal_code import (
    update_sent_code,
    get_captive_code,
)


class SMSLogs(Document):
    def before_insert(self):
        self.mobile_number = sanitize_mobile_number(self.mobile_number)

    def after_insert(self):
        if not self.captive_portal_code and self.mobile_number:
            # get a code
            frappe.db.set_value(
                self.doctype, self.name, "captive_portal_code", get_captive_code()
            )
            send_msg(self.mobile_number, self.name, self.captive_portal_code)
            self.reload()


@frappe.whitelist()
def send_msg(phone, name=None, msg_=None):
    msg = get_captive_code()
    # when resending the message
    if msg_:
        msg = msg_
    sms_integration = localsms()
    sms_code = sms_integration.send_sms(phone, msg, name)
    # update the code
    update_sent_code(msg, sms_code)


def unsent_sms():
    sms_list = frappe.get_list(
        "SMS Logs",
        {"sent": 0},
        limit=1,
        order_by="creation desc",
        ignore_permissions=True,
    )
    if sms_list:
        return sms_list[0].name


def resend_sms(doc=None, method=None):
    if doc:
        if doc.doctype == "SMS Logs" and doc.sent == 0:
            send_msg(doc.mobile_number, doc.name, doc.captive_portal_code)
            return
        # after every 2 minutes
    unsent_list = unsent_sms()
    if unsent_list:
        unsent_list = frappe.get_doc("SMS Logs", unsent_list)
        send_msg(
            unsent_list.mobile_number, unsent_list.name, unsent_list.captive_portal_code
        )


def sanitize_mobile_number(number):
    """Add country code and strip leading zeroes from the phone number."""
    if str(number).startswith("0"):
        return "+254" + str(number).lstrip("0")
    elif str(number).startswith("254"):
        return "+254" + str(number).lstrip("254")
    else:
        return number
