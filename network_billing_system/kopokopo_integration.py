import requests
import json
import frappe
from frappe.utils import get_datetime, flt
from network_billing_system.network_billing_system.doctype.sms_logs.sms_logs import (
    send_msg, validate_amount
)
from frappe import _
from network_billing_system.utils import load_configuration


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
        if self.env == "sandbox":
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
                "phone_number": self.sanitize_mobile_number(
                    subscriber.get("phone_number")
                ),
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
        return r.status_code

    def sanitize_mobile_number(self, number):
        """Add country code and strip leading zeroes from the phone number."""
        return "+254" + str(number).lstrip("0")

    def create_webhook(self, callback):
        """
        The payload to create the webhook
        """
        headers = {
            "Authorization": "Bearer {0}".format(self.authentication_token),
            "Content-Type": "application/json",
        }
        webhook_url = "{0}{1}".format(self.base_url, "/api/v1/webhook_subscriptions")
        payload = {
            "event_type": "buygoods_transaction_received",
            "url": callback,
            "scope": "till",
            "scope_reference": load_configuration("webhook_till_number") or "5890527",
        }
        r = requests.post(webhook_url, headers=headers, json=payload)


@frappe.whitelist(allow_guest=True)
def verify_transaction(**kwargs):
    """Verify the transaction result received via callback from stk."""
    transaction_response = frappe._dict(kwargs["data"])
    # print(transaction_response)
    if transaction_response.attributes["status"] == "Success":
        # Process the data...
        # integrate with erpnext workflow
        response = transaction_response.attributes["event"]
        process_callback_res(response)


@frappe.whitelist(allow_guest=True)
def process_webhook(**kwargs):
    """process the data that you receive from the webhook"""
    webhook_response = frappe._dict(kwargs["event"])
    # process the webhook alert here/ when user pays directly to mpesa
    # print("Webhook response ",webhook_response)
    process_callback_res(webhook_response)


@frappe.whitelist()
def process_stk(mobile, amount=load_configuration("default_amount"), callback_url=None, till_number="5890527"):
    if load_configuration("till_number"):
        till_number = load_configuration("till_number")
    if not callback_url:
        callback_url = load_configuration("kopo_stk_callback")
    connector = KopokopoConnector(env=load_configuration("env"))
    connector.authenticate()
    subcriber = {
        "name": load_configuration("customer_name"),
        "first_name": load_configuration("first_name"),
        "last_name": load_configuration("last_name"),
        "email": load_configuration("email"),
        "phone_number": mobile,
        "note": load_configuration("note"),
    }
    # if load_configuration("default_amount"):
    #     amount = load_configuration("default_amount")
    status_code = connector.stk_push(
        till_number=till_number,
        amount=amount,
        callback_url=callback_url,
        subscriber=subcriber,
    )
    return status_code


def process_callback_res(response):
    try:
        response = frappe._dict(response["resource"])
        # mpesa log after successful payment
        # frappe.log_error("Response Amount: {0} Middle Name: {1}  Phone Number: {2}".format(response.amount, response.sender_middle_name, response.sender_phone_number))
        create_mpesa_log(response)
        # check amount paid
        # Handle all cash related scenario here
        if flt(response.amount) >= flt(load_configuration("default_amount")):
            send_msg(response.sender_phone_number)
        else:
            value = validate_amount(response.sender_phone_number, response.amount)
            if value == True:
                send_msg(response.sender_phone_number)
    except:
        frappe.log_error(
            frappe.get_traceback(),
            "Error: Kopokopo Processing Call Back Url"
        )

def create_mpesa_log(response):
    doc = frappe.get_doc({"doctype": "Mpesa Transaction Log"})
    doc.flags.ignore_permissions = 1
    doc.mobile_number = response.sender_phone_number
    doc.transaction_code = response.reference
    doc.amount_paid = response.amount
    doc.first_name = response.sender_first_name
    doc.middle_name = response.sender_middle_name
    doc.last_name = response.sender_last_name
    doc.save()
