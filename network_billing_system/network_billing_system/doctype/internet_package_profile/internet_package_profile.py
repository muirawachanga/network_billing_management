# Copyright (c) 2021, stephen and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document
from network_billing_system.kopokopo_integration import KopokopoConnector
from frappe.utils import get_request_site_address

class InternetPackageProfile(Document):
    pass

    def validate(self):
        callback_url = get_request_site_address(True) + "/api/method/network_billing_system.kopokopo_integration.verify_transaction"
        # callback_url = "https://shaggy-ladybug-4.loca.lt/api/method/network_billing_system.kopokopo_integration.verify_transaction"
        connector = KopokopoConnector()
        connector.authenticate()
        print(connector.authentication_token)
        subcriber = {
            "name": "CUST-001",
            "first_name": "Stephen",
            "last_name": "Muira",
            "email": "wachangasteve@gmail.com",
            "phone_number": "0718215557",
            "note": "Daily Internet"
        }
        connector.stk_push(till_number="5890527", amount=20, callback_url=callback_url, subscriber=subcriber)
