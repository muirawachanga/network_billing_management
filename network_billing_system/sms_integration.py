import requests
import json
import frappe
from network_billing_system.utils import load_configuration


class localsms:
    def __init__(
        self,
        local_server=load_configuration("sms_gateway_server"),
        api_key=load_configuration("sms_api_key"),
    ):
        self.local_server = local_server
        self.api_key = api_key

    def sms_integration(self, data):
        status_code = 0
        headers = {"Content-type": "application/json", "Authorization": self.api_key}
        try:
            # frappe.log_error("Trying to send the SMS: Status code is {0} for the number: {1} localserver: {2} api key: {3}".format(status_code, data.get("to"), self.local_server, self.api_key))
            r = requests.post(
                self.local_server, json=data, headers=headers, timeout=0.98
            )
            print(r.raise_for_status())
            status_code = r.status_code
            return status_code
        except requests.exceptions.RequestException:
            status_code = 500
            frappe.log_error(
                "Error trying to send the SMS: Status code: {}".format(status_code)
            )
            return status_code

    def send_sms(self, phone, msg, name=None):
        # add a controller that prevent sendins to the same guy
        awaiting_ = awaiting_mpesa(phone)
        if awaiting_:
            # update the awaiting mpesa flag
            doc = frappe.get_doc("SMS Logs", awaiting_)
            doc.awaiting_mpesa = 0
            doc.save(ignore_permissions=True)
            return
        if phone and msg:
            data = {"to": phone, "message": msg}
            status_code = self.sms_integration(data)
            if name and status_code == 200:
                self.update_sms_log(name, status_code)
                return status_code
            if status_code == 200:
                self.create_sms_log(phone, msg, sent=1)
            else:
                self.create_sms_log(phone, msg)
            return status_code

    def send_comm_sms(self, phone_list, msg):
        data = {"to": phone_list, "message": msg}
        status_code = self.sms_integration(data)
        if status_code == 200:
            self.create_sms_comm_log(phone_list, msg, sent=1)
        else:
            self.create_sms_comm_log(phone_list, msg)


    def create_sms_comm_log(self, phone, msg, sent=0):
        doc = frappe.get_doc({"doctype": "SMS Communication Logs"})
        doc.message = msg
        doc.mobile_number = phone
        doc.sent = sent
        doc.save(ignore_permissions=True)

    def update_sms_log(self, name, status_code):
        if status_code == 200:
            doc = frappe.get_doc("SMS Logs", name)
            doc.sent = 1
            doc.save(ignore_permissions=True)
    
    def create_sms_log(self, phone, msg, sent=0):
        doc = frappe.get_doc({"doctype": "SMS Logs"})
        if sent == 1 and len(msg) < 20:
            doc.captive_portal_code = msg
        else:
            doc.custom_message = msg
        doc.mobile_number = phone
        doc.sent = sent
        doc.save(ignore_permissions=True)


def awaiting_mpesa(mobile):
    num = frappe.get_list(
        "SMS Logs",
        {"sent": 1, "awaiting_mpesa": 1, "mobile_number": mobile },
        limit=1,
        order_by="creation desc",
        ignore_permissions=True,
    )
    if num:
        return num[0].name
    return None
