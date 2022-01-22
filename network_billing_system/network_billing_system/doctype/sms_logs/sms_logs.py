# Copyright (c) 2021, stephen and contributors
# For license information, please see license.txt

# import frappe
import frappe
from frappe.model.document import Document
from network_billing_system.sms_integration import localsms
from network_billing_system.network_billing_system.doctype.captive_portal_code.captive_portal_code import (
    update_sent_code,
    get_captive_code,
)


class SMSLogs(Document):
    pass


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
        "SMS Logs", {"sent": 0}, limit=1, order_by="creation desc", ignore_permissions=True
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
        send_msg(unsent_list.mobile_number, unsent_list.name, unsent_list.captive_portal_code)
