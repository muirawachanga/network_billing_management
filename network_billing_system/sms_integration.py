import requests
import json
import frappe
from network_billing_system.utils import load_configuration

class localsms:
    def __init__(self, local_server=load_configuration("sms_gateway_server"), api_key=load_configuration("sms_api_key")):
        self.local_server = local_server
        self.api_key = api_key

    def sms_integration(self, data):
        status_code = 0
        headers = {"Content-type": "application/json", "Authorization": self.api_key}
        try:
            # frappe.log_error("Trying to send the SMS: Status code is {0} for the number: {1} localserver: {2} api key: {3}".format(status_code, data.get("to"), self.local_server, self.api_key))
            r = requests.post(self.local_server, json=data, headers=headers, timeout=0.9)
            print(r.raise_for_status())
            status_code = r.status_code
            return status_code
        except requests.exceptions.RequestException:
            status_code = 500
            frappe.log_error("Error trying to send the SMS: Status code: {}".format(status_code))
            return status_code
    
    def send_sms(self, phone, msg, name=None):
        if phone and msg:
            data = {"to": phone, "message": msg}
            status_code = self.sms_integration(data)
            if name:
                self.update_sms_log(name, status_code)
                return status_code
            if status_code == 200:
                self.create_sms_log(phone, msg, sent=1)
            else:
                self.create_sms_log(phone, msg)
            return status_code
    
    def create_sms_log(self, phone, msg, sent=0):
        doc = frappe.get_doc({"doctype": "SMS Logs"})
        doc.captive_portal_code = msg
        doc.mobile_number = phone
        doc.sent = sent
        doc.save(ignore_permissions=True)

    def update_sms_log(self, name, status_code):
        if status_code == 200:
            doc = frappe.get_doc("SMS Logs", name)
            doc.sent = 1
            doc.save(ignore_permissions=True)
