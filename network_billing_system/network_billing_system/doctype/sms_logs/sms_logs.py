# Copyright (c) 2021, stephen and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document
from network_billing_system.sms_integration import localsms
from network_billing_system.network_billing_system.doctype.captive_portal_code.captive_portal_code import update_sent_code, get_captive_code

class SMSLogs(Document):
	pass

def send_msg(sms_object):
	code = get_captive_code()
	sms_integration = localsms(sms_object.get("local_server"), sms_object.get("api_key"))
	sms_integration.send_sms(sms_object.get("phone"), code)
	# update the code 
	update_sent_code(code)
