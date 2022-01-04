import requests
import json
import frappe

class localsms:
    def __init__(self, local_server="http://192.168.0.60:8082", api_key="f4296868"):
        self.local_server = local_server
        self.api_key = api_key

    def sms_integration(self, data):
        status_code = 0
        headers = {"Content-type": "application/json", "Authorization": self.api_key}
        try:
            frappe.log_error("Trying to send the SMS: Status code is {0} for the number: {1} localserver: {3} api key: {4}".format(status_code, data.get("to"), self.local_server, self.api_key))
            r = requests.post(self.local_server, json=data, headers=headers)
            frappe.log_error(r)
            r.raise_for_status()
            status_code = r.status_code
            frappe.log_error("Trying to send the SMS: Status code is {0} for the number: {1}".format(status_code, data.get("to")))
            return status_code
        except requests.exceptions.RequestException:
            status_code = 500
            frappe.log_error("Error trying to send the SMS: Status code: {}".format(status_code))
            return status_code
    
    def send_sms(self, phone, msg):
        if phone and msg:
            data = {"to": phone, "message": msg}
            status_code = self.sms_integration(data)
            if status_code == 200:
                self.create_sms_log(phone, msg, sent=1)
            else:
                self.create_sms_log(phone, msg)
                
    def create_sms_log(self, phone, msg, sent=0):
        doc = frappe.get_doc({"doctype": "SMS Logs"})
        doc.captive_portal_code = msg
        doc.mobile_number = phone
        doc.sent = sent
        doc.save(ignore_permissions=True)