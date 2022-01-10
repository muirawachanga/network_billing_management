# Copyright (c) 2021, stephen and contributors
# For license information, please see license.txt

# import frappe
import frappe
from frappe.model.document import Document
from network_billing_system.sms_integration import localsms
from network_billing_system.network_billing_system.doctype.captive_portal_code.captive_portal_code import update_sent_code, get_captive_code

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
