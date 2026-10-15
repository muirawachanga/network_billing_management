import hashlib
import hmac
import re
from urllib.parse import urlencode

import frappe
import requests
from frappe.utils import flt, get_datetime, get_url, now_datetime

from network_billing_system.kopokopo_integration import KopokopoConnector
from network_billing_system.portal_rules import confirmed_payment, normalize_phone, payment_id
from network_billing_system.utils import load_configuration


def _enabled():
    if not load_configuration("enable_package_portal"):
        frappe.throw("Package captive portal is not enabled.")


def _throttle(action, identifier, limit, seconds):
    digest = hashlib.sha256(str(identifier).encode()).hexdigest()
    key = "captive_portal:{}:{}:{}".format(frappe.local.site, action, digest)
    cache = frappe.cache()
    count = cache.incr(key)
    if count == 1:
        cache.expire(key, seconds)
    if count > limit:
        frappe.throw("Too many requests. Please wait before trying again.")


def _request_limit(action, limit=60, seconds=60):
    _throttle(action, frappe.local.request_ip, limit, seconds)


def _token(checkout_name):
    key = frappe.conf.get("encryption_key")
    if not key:
        frappe.throw("The site encryption key must be configured before taking payments.")
    signature = hmac.new(
        key.encode(), ("portal-checkout:" + checkout_name).encode(), hashlib.sha256
    ).hexdigest()
    return checkout_name + "." + signature


def _checkout_for_token(token):
    name, separator, signature = str(token or "").partition(".")
    if (
        not separator
        or not re.fullmatch(r"[a-zA-Z0-9_-]{1,140}", name)
        or not re.fullmatch(r"[a-f0-9]{64}", signature)
        or not hmac.compare_digest(_token(name), name + "." + signature)
    ):
        frappe.throw("Invalid payment token.", frappe.PermissionError)
    if not frappe.db.exists("Portal Checkout", name):
        return None
    return name


def _lock_checkout(name):
    rows = frappe.db.sql(
        "select * from `tabPortal Checkout` where name=%s for update", (name,), as_dict=True
    )
    if not rows:
        frappe.throw("Unknown checkout.")
    return frappe.get_doc(dict(rows[0], doctype="Portal Checkout"))


def _lock_voucher(name):
    rows = frappe.db.sql(
        "select * from `tabCaptive Portal Code` where name=%s for update", (name,), as_dict=True
    )
    if not rows:
        frappe.throw("The allocated voucher was not found. Contact support.")
    return frappe.get_doc(dict(rows[0], doctype="Captive Portal Code"))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def get_packages():
    _enabled()
    _request_limit("packages")
    packages = frappe.get_all(
        "Internet Package Profile",
        filters={"enabled": 1},
        fields=["name", "display_name", "description", "price", "duration_minutes"],
        order_by="price asc, name asc",
    )
    return [
        {
            "package_id": package.name,
            "label": package.display_name or package.name,
            "description": package.description,
            "price": package.price,
            "currency": "KES",
            "duration_minutes": package.duration_minutes,
            "available": bool(frappe.db.exists(
                "Captive Portal Code",
                {
                    "package": package.name, "status": "Active", "sms_sent": 0,
                    "duration_minutes": package.duration_minutes,
                    "portal_checkout": ["is", "not set"],
                },
            )),
        }
        for package in packages
    ]


@frappe.whitelist(allow_guest=True, methods=["POST"])
def start_payment(phone, package_id, portal_action=None, redirurl=None):
    _enabled()
    _request_limit("start", limit=60, seconds=300)
    try:
        phone = normalize_phone(phone)
    except ValueError as error:
        frappe.throw(str(error))
    _throttle("phone", phone, limit=3, seconds=300)
    # URLs from the browser are deliberately not used for server-side requests.
    package = frappe.get_doc("Internet Package Profile", package_id)
    if not package.enabled or flt(package.price) <= 0 or package.duration_minutes <= 0:
        frappe.throw("This package is not available.")
    environment = load_configuration("env")
    till = str(load_configuration("till_number") or "").lstrip("K")
    if environment not in ("sandbox", "live") or not re.fullmatch(r"\d+", till):
        frappe.throw("Configure the Kopo Kopo environment and STK till number first.")
    if not frappe.conf.get("kopokopo_client_id") or not frappe.conf.get("kopokopo_client_secret"):
        frappe.throw("Configure Kopo Kopo API credentials before taking payments.")
    subscriber = {
        "first_name": load_configuration("first_name"),
        "last_name": load_configuration("last_name"),
        "email": load_configuration("email"),
    }
    if not subscriber["first_name"] or not subscriber["last_name"]:
        frappe.throw("Configure the Kopo Kopo subscriber first and last names.")
    checkout = frappe.get_doc({
        "doctype": "Portal Checkout",
        "package": package.name,
        "mobile_number": phone,
        "amount": package.price,
        "currency": "KES",
        "duration_minutes": package.duration_minutes,
        "provider_environment": environment,
        "till_number": till,
        "status": "PENDING",
    }).insert(ignore_permissions=True)
    token = _token(checkout.name)
    vouchers = frappe.db.sql(
        """
        select name from `tabCaptive Portal Code`
        where package=%s and duration_minutes=%s and status='Active'
          and sms_sent=0 and ifnull(portal_checkout, '')=''
        order by creation asc, name asc limit 1 for update
        """,
        (package.name, package.duration_minutes),
        as_dict=True,
    )
    if not vouchers:
        frappe.throw("This package has no available vouchers. No payment was requested.")
    try:
        connector = KopokopoConnector(env=environment)
    except (requests.RequestException, ValueError, KeyError):
        frappe.log_error(frappe.get_traceback(), "Portal payment authentication failed")
        frappe.throw("The payment service could not authenticate. No payment was requested.")
    checkout.voucher = vouchers[0].name
    frappe.db.set_value("Captive Portal Code", checkout.voucher, {
        "status": "Reserved", "portal_checkout": checkout.name,
    })
    checkout.save(ignore_permissions=True)
    # Persist the reservation before a payment can be accepted or a callback arrives.
    frappe.db.commit()

    callback = get_url(
        "/api/method/network_billing_system.api.captive_portal.payment_callback"
    ) + "?" + urlencode({"checkout_id": checkout.name})
    try:
        provider_id = connector.start_portal_payment(checkout, callback, subscriber)
    except (requests.RequestException, ValueError, KeyError):
        frappe.log_error(frappe.get_traceback(), "Portal payment initiation needs reconciliation")
        # A timeout can occur after the provider accepted the request. Never release
        # its voucher or tell the customer it is safe to send another payment.
        return {
            "payment_token": token,
            "stk_push_sent": False,
            "customer_message": (
                "The payment request could not be confirmed. Do not pay again if a "
                "prompt appeared or money was deducted. Keep checking or contact support."
            ),
        }
    current = _lock_checkout(checkout.name)
    if current.provider_payment_id and current.provider_payment_id != provider_id:
        frappe.throw("The provider returned conflicting payment identifiers.")
    frappe.db.set_value("Portal Checkout", checkout.name, "provider_payment_id", provider_id)
    return {"payment_token": token, "stk_push_sent": True}


def _refresh(checkout, candidate_id=None):
    if checkout.status != "PENDING":
        return checkout
    provider_id = checkout.provider_payment_id or candidate_id
    if not provider_id:
        return checkout
    if checkout.last_checked_at and (
        now_datetime() - get_datetime(checkout.last_checked_at)
    ).total_seconds() < 5:
        return checkout

    connector = KopokopoConnector(env=checkout.provider_environment)
    payload = connector.get_portal_payment(provider_id)
    result = confirmed_payment(payload, checkout.as_dict(), provider_id)
    checkout.provider_payment_id = provider_id
    checkout.last_checked_at = now_datetime()
    checkout.status = result["status"]
    if checkout.status == "SUCCESS":
        checkout.mpesa_receipt = result["receipt"]
        checkout.confirmed_at = now_datetime()
        voucher = _lock_voucher(checkout.voucher)
        if voucher.portal_checkout != checkout.name or voucher.status != "Reserved":
            frappe.throw("The reserved voucher is unavailable. Contact support; do not pay again.")
        frappe.db.set_value("Captive Portal Code", voucher.name, "status", "Issued")
        _record_payment(checkout, result["resource"])
    elif checkout.status in ("FAILED", "CANCELLED"):
        voucher = _lock_voucher(checkout.voucher)
        if voucher.portal_checkout != checkout.name or voucher.status != "Reserved":
            frappe.throw("The reserved voucher has changed. Contact support.")
        frappe.db.set_value("Captive Portal Code", checkout.voucher, {
            "status": "Active", "portal_checkout": None,
        })
        checkout.voucher = None
    checkout.save(ignore_permissions=True)
    return checkout


def _record_payment(checkout, resource):
    existing = frappe.db.get_value(
        "Mpesa Transaction Log", {"transaction_code": checkout.mpesa_receipt},
        ["name", "portal_checkout"], as_dict=True,
    )
    if existing:
        if existing.portal_checkout and existing.portal_checkout != checkout.name:
            frappe.throw("This M-Pesa receipt is already linked to another checkout.")
        frappe.db.set_value("Mpesa Transaction Log", existing.name, {
            "portal_checkout": checkout.name,
            "mobile_number": checkout.mobile_number,
            "amount_paid": checkout.amount,
            "first_name": resource.get("sender_first_name"),
            "middle_name": resource.get("sender_middle_name"),
            "last_name": resource.get("sender_last_name"),
        })
        return
    frappe.get_doc({
        "doctype": "Mpesa Transaction Log",
        "portal_checkout": checkout.name,
        "transaction_code": checkout.mpesa_receipt,
        "mobile_number": checkout.mobile_number,
        "amount_paid": checkout.amount,
        "first_name": resource.get("sender_first_name"),
        "middle_name": resource.get("sender_middle_name"),
        "last_name": resource.get("sender_last_name"),
    }).insert(ignore_permissions=True)


def _access_response(checkout):
    response = {"status": checkout.status, "access_ready": False}
    if checkout.status != "SUCCESS":
        return response
    voucher = _lock_voucher(checkout.voucher)
    if voucher.portal_checkout != checkout.name or voucher.status != "Issued":
        return {
            "status": "INELIGIBLE", "access_ready": False,
            "customer_message": "This voucher is not available. Contact support.",
        }
    response.update({
        "access_ready": True,
        "voucher": voucher.code,
        "package_id": checkout.package,
        "duration_minutes": checkout.duration_minutes,
        "expiry_authority": "pfsense",
        "customer_message": (
            "Payment verified. pfSense will validate the voucher and its remaining usage."
        ),
    })
    return response


@frappe.whitelist(allow_guest=True, methods=["POST"])
def check_payment_status(payment_token):
    _enabled()
    _request_limit("status", limit=1200)
    name = _checkout_for_token(payment_token)
    if not name:
        return {"status": "NOT_FOUND", "access_ready": False}
    return _access_response(_refresh(_lock_checkout(name)))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def payment_callback(checkout_id, data=None, **kwargs):
    _enabled()
    if not frappe.db.exists("Portal Checkout", checkout_id):
        frappe.throw("Unknown checkout.")
    checkout = _lock_checkout(checkout_id)
    candidate_id = None
    if not checkout.provider_payment_id:
        # The callback is only a notification. Even this ID must be verified by
        # fetching the provider resource and matching our checkout metadata.
        data = frappe.parse_json(data) if isinstance(data, str) else data
        candidate_id = payment_id((data or {}).get("id"))
    _refresh(checkout, candidate_id)
    return {"received": True}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def connect_existing_customer(lookup_type, lookup_value):
    _enabled()
    _request_limit("receipt", limit=5, seconds=300)
    if lookup_type != "mpesa_receipt":
        frappe.throw("Only M-Pesa receipt recovery is supported.")
    receipt = re.sub(r"\s+", "", str(lookup_value or "")).upper()
    if not re.fullmatch(r"(?=.*[A-Z])(?=.*\d)[A-Z0-9]{8,20}", receipt):
        frappe.throw("Enter a valid M-Pesa receipt code.")
    name = frappe.db.get_value("Portal Checkout", {
        "mpesa_receipt": receipt, "status": "SUCCESS",
    }, "name")
    if not name:
        return {
            "status": "NOT_FOUND", "access_ready": False,
            "customer_message": (
                "No confirmed package checkout matches this receipt. If you just paid, "
                "wait for confirmation or contact support. Do not pay again."
            ),
        }
    return _access_response(frappe.get_doc("Portal Checkout", name))


@frappe.whitelist(methods=["POST"])
def reconcile_checkout(checkout_id, provider_payment_id=None):
    frappe.only_for(("System Manager", "Network Administrator"))
    _enabled()
    checkout = _lock_checkout(checkout_id)
    candidate = payment_id(provider_payment_id) if provider_payment_id else None
    if checkout.provider_payment_id and candidate and candidate != checkout.provider_payment_id:
        frappe.throw("This checkout already has a different Kopo Kopo payment ID.")
    return _access_response(_refresh(checkout, candidate))


def reconcile_pending_payments():
    if not load_configuration("enable_package_portal"):
        return
    pending = frappe.get_all(
        "Portal Checkout",
        filters={"status": "PENDING", "provider_payment_id": ["is", "set"]},
        order_by="last_checked_at asc, creation asc",
        limit_page_length=20,
        pluck="name",
    )
    for name in pending:
        try:
            _refresh(_lock_checkout(name))
            frappe.db.commit()
        except (requests.RequestException, ValueError, KeyError, frappe.ValidationError):
            frappe.db.rollback()
            frappe.log_error(frappe.get_traceback(), "Portal payment reconciliation failed")
            frappe.db.set_value("Portal Checkout", name, "last_checked_at", now_datetime())
            frappe.db.commit()
