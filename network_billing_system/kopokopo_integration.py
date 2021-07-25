import requests
import json
import frappe


class KopokopoConnector:
    def __init__(
        self,
        env="sandbox",
        app_key=None,
        app_secret=None,
        sandbox_url="https://sandbox.kopokopo.com",
        live_url="https://api.kopokopo.com",
    ):
        """Setup configuration for Kopokopo connector and generate new access token."""
        self.env = env
        self.app_key = app_key
        self.app_secret = app_secret
        if env == "sandbox":
            self.base_url = sandbox_url
        else:
            self.base_url = live_url
        self.authenticate()

    def authenticate(self):
        """
        This method is used to fetch the access token required by Kopokopo.

        Returns:
            access_token (str): This token is to be used with the Bearer 
            header for further API calls to Kopokopo.
        """
        authenticate_uri = "/oauth/token"
        authenticate_url = "{0}{1}".format(self.base_url, authenticate_uri)
        data = {
            "client_id": frappe.conf.get("kopokopo_client_id"),
            "client_secret": frappe.conf.get("kopokopo_client_secret"),
            "grant_type": "client_credentials",
        }
        headers = {"Content-type": "application/json"}
        r = requests.post(authenticate_url, json=data, headers=headers)
        self.authentication_token = r.json()["access_token"]
        return r.json()["access_token"]

    def stk_push(
        self, till_number=None, amount=None, callback_url=None, subscriber=None
    ):
        """
        This method uses kopokopo API to initiate online payment on behalf of a customer.

        Args:
            till_number (string): The short code of the organization.
            amount (int): The amount being transacted
            callback_url (str): A CallBack URL is a valid secure URL that is used to receive notifications from Kopopo API.
            phone_number(int): The Mobile Number to receive the STK Pin Prompt.
            subscriber (dict): Contain details required for the subscriber i.e first_name, last_name, phone_number

        Success Response:
            Location(str): This is returned in the headers.
        """
        payload = {
            "payment_channel": "M-PESA STK Push",
            "till_number": "K{}".format(till_number),
            "subscriber": {
                "first_name": subscriber.get("first_name"),
                "last_name": subscriber.get("last_name"),
                "phone_number": self.sanitize_mobile_number(subscriber.get("phone_number")),
                "email": subscriber.get("email"),
            },
            "amount": {"currency": "KES", "value": amount},
            "metadata": {
                "customer_id": subscriber.get("name"),
                "notes": subscriber.get("note"),
            },
            "_links": {"callback_url": callback_url},
        }
        headers = {
            "Authorization": "Bearer {0}".format(self.authentication_token),
            "Content-Type": "application/json",
        }

        kopokopo_url = "{0}{1}".format(self.base_url, "/api/v1/incoming_payments")
        r = requests.post(kopokopo_url, headers=headers, json=payload)
        print(r.status_code)

    def sanitize_mobile_number(self, number):
        """Add country code and strip leading zeroes from the phone number."""
        return "254" + str(number).lstrip("0")

@frappe.whitelist(allow_guest=True)
def verify_transaction(**kwargs):
    """Verify the transaction result received via callback from stk."""
    transaction_response = frappe._dict(kwargs["data"])
    print(transaction_response)
    if transaction_response.status == "Success":
        # Process the data...
        # integrate with erpnext workflow
        if transaction_response.amount > 20:
            return
        # Process the sms
        

