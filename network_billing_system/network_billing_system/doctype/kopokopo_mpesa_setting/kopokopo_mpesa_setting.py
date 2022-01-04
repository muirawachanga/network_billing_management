# Copyright (c) 2022, stephen and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document
import frappe
from network_billing_system.kopokopo_integration import KopokopoConnector
from frappe.utils import get_request_site_address

class KopokopoMpesaSetting(Document):

	def validate(self):
		self.set_webook_()

	def set_webook_(self):
		if  self.set_webhook or not self.webhook_already_set:
			connector = KopokopoConnector(env=self.env)
			connector.authenticate()
			webhook_callback = self.kopo_webhook_callback
			connector.create_webhook(webhook_callback)
			self.webhook_already_set = 1
