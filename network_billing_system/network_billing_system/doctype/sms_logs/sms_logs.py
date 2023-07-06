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
        if not self.captive_portal_code and self.mobile_number and validate_amount(self.mobile_number):
            # get a code
            code = get_captive_code()
            frappe.db.set_value(
                self.doctype, self.name, "captive_portal_code", code
            )
            send_msg(self.mobile_number, self.name, self.captive_portal_code)
            update_sent_code(code, 200)
            self.reload()


@frappe.whitelist()
def send_msg(phone, name=None, msg_=None):
    msg = get_captive_code()
    # when resending the message
    if msg_:
        msg = msg_
    sms_integration = localsms()
    sms_status_code = sms_integration.send_sms(phone, msg, name)
    # update the code
    if sms_status_code != 200:
        return
    if len(msg) <= 20:
        update_sent_code(msg, sms_status_code)


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


def validate_amount(phone, current_amount=None):
        # received_amount, expected_amount, balance_amount
        expected_amount =  int(frappe.db.get_single_value("Kopokopo Mpesa Setting", "default_amount"))
        msg = frappe.db.get_single_value("SMS Template Setting", "less_payment_template")
        amount_day = get_amount_day(phone)
        if amount_day == expected_amount and current_amount:
            return True
        elif amount_day > expected_amount and current_amount:
            # more than 30, if someone had paid more than 30 then high chance is paying 
            # for somelese or should be a reverse
            balance = amount_day - expected_amount
            if balance != 0:
                msg = msg.format(received_amount=current_amount, expected_amount=expected_amount, balance_amount=balance)
                send_msg(phone=phone, msg_=msg)
        elif amount_day < expected_amount and current_amount:
            # more than 30, if someone had paid more than 30 then high chance is paying 
            # for somelese or should be a reverse
            balance = expected_amount -amount_day
            if balance != 0:
                msg = msg.format(received_amount=current_amount,expected_amount=expected_amount,balance_amount=balance)
                send_msg(phone=phone, msg_=msg)
        return False


def get_amount_day(phone):
    # this is the amount for the day
    try:
        from frappe.utils import today, getdate
        from datetime import timedelta
        amount = 0
        tomorrow = getdate(today()) + timedelta(days=1)
        today_amount = frappe.db.sql("""
            select sum(amount_paid) as amount_paid from `tabMpesa Transaction Log` where mobile_number=%s and creation between %s and %s;        
        """, (phone, today(), tomorrow), as_dict=True,)
        if len(today_amount) > 0:
            amount = int(today_amount[0].get("amount_paid"))
            return amount
        return amount
    except:
        frappe.log_error(frappe.get_traceback(), "Error: Getting amount for the day.")