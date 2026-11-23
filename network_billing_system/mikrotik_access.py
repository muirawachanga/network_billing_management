"""Durable MikroTik entitlements; RouterOS writes run after the entitlement commits."""

import hashlib
import re
import secrets
from datetime import timedelta
from functools import partial

import frappe
from frappe.utils import now_datetime


ISSUANCE = "MikroTik Issuance"
ROUTER = "MikroTik Router"
GRANT = "MikroTik Complimentary Grant"


def package_policy(package):
    profile = package.get("mikrotik_profile") or ""
    try:
        upload = int(package.get("mikrotik_upload_bps") or 0)
        download = int(package.get("mikrotik_download_bps") or 0)
    except (TypeError, ValueError):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", profile) or not (
        0 < upload <= 2_147_483_647 and 0 < download <= 2_147_483_647
    ):
        return None
    return {"mikrotik_profile": profile, "mikrotik_rate_limit": f"{upload}/{download}"}


def sales_router(zone_id, *, environment):
    if environment not in ("sandbox", "live"):
        return None
    zone = frappe.db.get_value("Captive Portal Zone", zone_id, [
        "mikrotik_router", "pause_mikrotik_sales",
    ], as_dict=True)
    if not zone or not zone.mikrotik_router or zone.pause_mikrotik_sales:
        return None
    router = frappe.get_doc(ROUTER, zone.mikrotik_router)
    if (router.zone != zone_id or (environment == "live" and not router.pilot_verified)
            or not router.get_password("api_password", raise_exception=False)):
        return None
    return router


def policy_for_router(router):
    return {
        "host": router.host, "port": router.port, "transport": router.transport,
        "allow_plaintext": bool(router.allow_plaintext), "api_user": router.api_user,
        "api_password": router.get_password("api_password"),
        "router_identity": router.router_identity, "server": router.hotspot_server,
    }


def policy_for(issuance):
    if issuance.get("complimentary_grant"):
        grant = frappe.get_doc(GRANT, issuance.complimentary_grant)
        if (issuance.checkout or issuance.predecessor or grant.status != "Approved"
                or grant.issuance != issuance.name or grant.router != issuance.router
                or grant.duration_minutes * 60 != issuance.duration_seconds):
            frappe.throw("Issuance does not match the approved complimentary grant.")
        policy = policy_for_router(frappe.get_doc(ROUTER, issuance.router))
        policy.update(server=grant.hotspot_server, profile=grant.hotspot_profile,
                      rate_limit=grant.hotspot_rate_limit)
        return policy
    checkout = frappe.get_doc("Portal Checkout", issuance.checkout)
    if checkout.backend != "MikroTik" or checkout.mikrotik_router != issuance.router:
        frappe.throw("Issuance does not match the purchased router.")
    policy = policy_for_router(frappe.get_doc(ROUTER, issuance.router))
    policy.update(server=checkout.mikrotik_server, profile=checkout.mikrotik_profile,
                  rate_limit=checkout.mikrotik_rate_limit)
    return policy


def owner_for(issuance):
    return "frappe-hotspot:" + issuance.username


def _initial_key(checkout_name):
    return "purchase:" + checkout_name


def create_issuance(checkout, seconds=None, predecessor=None, incident=None):
    if checkout.backend != "MikroTik" or checkout.status != "SUCCESS":
        frappe.throw("A verified MikroTik purchase is required.")
    total = int(checkout.duration_minutes) * 60
    seconds = total if seconds is None else seconds
    if not 0 < seconds <= total or bool(predecessor) != bool(incident):
        frappe.throw("Invalid connected-time entitlement.")
    key = "replacement:" + predecessor if predecessor else _initial_key(checkout.name)
    return _create_issuance(
        key, checkout.mikrotik_router, seconds, checkout=checkout.name,
        predecessor=predecessor, incident=incident,
    )


def create_grant_issuance(grant):
    """Called only by approval, while the grant row is locked."""
    if grant.status != "Approved" or not grant.router:
        frappe.throw("An approved complimentary grant is required.")
    return _create_issuance(
        "complimentary:" + grant.name, grant.router, grant.duration_minutes * 60,
        complimentary_grant=grant.name,
    )


def _create_issuance(key, router, seconds, **source):
    existing = frappe.db.get_value(ISSUANCE, {"issuance_key": key}, "name")
    if existing:
        return frappe.get_doc(ISSUANCE, existing)
    username = "nb-" + secrets.token_hex(12)
    password = secrets.token_urlsafe(18)
    fingerprint = hashlib.sha256(f"{username}.{password}".encode()).hexdigest()
    doc = frappe.get_doc({
        "doctype": ISSUANCE, "router": router, **source,
        "state": "Pending", "duration_seconds": seconds, "username": username,
        "password": password, "fingerprint": fingerprint, "issuance_key": key,
        "next_attempt_at": now_datetime(),
    }).insert(ignore_permissions=True)
    frappe.db.after_commit.add(partial(_enqueue_provision, doc.name))
    return doc


def _enqueue_provision(name):
    try:
        frappe.enqueue(provision, issuance_name=name)
    except Exception:
        # A failed dispatch must not turn a committed payment into an HTTP error.
        frappe.logger().warning("MikroTik provisioning queue unavailable; durable retry remains pending")


def lock_issuance(name):
    rows = frappe.db.sql(
        "select * from `tabMikroTik Issuance` where name=%s for update", (name,), as_dict=True,
    )
    return frappe.get_doc(dict(rows[0], doctype=ISSUANCE)) if rows else None


def provision(issuance_name):
    from network_billing_system import routeros

    issuance = lock_issuance(issuance_name)
    now = now_datetime()
    if (not issuance or issuance.state != "Pending"
            or (issuance.next_attempt_at and issuance.next_attempt_at > now)):
        return
    try:
        with routeros.connect(policy_for(issuance)) as router:
            router.ensure_user(issuance.username, issuance.get_password("password"),
                               owner_for(issuance), issuance.duration_seconds)
    except (routeros.RouterError, frappe.ValidationError):
        # Neither device replies nor tracebacks with local variables belong in the UI.
        attempts = (issuance.attempts or 0) + 1
        frappe.db.set_value(ISSUANCE, issuance.name, {
            "attempts": attempts, "last_failure": "Router access could not be confirmed. Check API reachability and policy.",
            "next_attempt_at": now + timedelta(seconds=min(3600, 30 * 2 ** min(attempts - 1, 7))),
        })
        return
    if issuance.predecessor:
        from network_billing_system.mikrotik_outages import queue_notification
        issuance.state = "Ready"
        queue_notification(issuance)
    elif issuance.complimentary_grant:
        from network_billing_system.mikrotik_grants import queue_notification
        queue_notification(issuance)
    frappe.db.set_value(ISSUANCE, issuance.name, {
        "state": "Ready", "last_failure": None, "next_attempt_at": None,
    })


def run_provisioning():
    names = frappe.get_all(ISSUANCE, filters={
        "state": "Pending", "next_attempt_at": ["<=", now_datetime()],
    }, order_by="next_attempt_at asc, name asc", limit_page_length=40, pluck="name")
    for name in names:
        _enqueue_provision(name)


def access_response(checkout, response, reveal=False, recover_replacement=False):
    name = frappe.db.get_value(ISSUANCE, {"issuance_key": _initial_key(checkout.name)}, "name")
    issuance = lock_issuance(name) if name else None
    if issuance and issuance.state == "Replaced" and recover_replacement:
        latest = frappe.db.get_value(ISSUANCE, {"checkout": checkout.name}, "name",
                                     order_by="creation desc, name desc", for_update=True)
        if latest and latest != name:
            issuance = lock_issuance(latest)
    state = issuance.state if issuance else "Pending"
    response["access_state"] = {
        "Pending": "PROVISIONING", "Ready": "READY", "Expired": "EXPIRED",
        "Replacing": "REPLACING", "Replaced": "REPLACED",
    }[state]
    response["access_ready"] = state == "Ready"
    response["customer_message"] = {
        "Pending": "Your payment is confirmed. Your Wi-Fi access is being prepared. Do not pay again.",
        "Ready": "Payment verified. Your Wi-Fi access is ready.",
        "Expired": "This Wi-Fi access has used all its connected time.",
        "Replacing": "Your Wi-Fi access is being replaced. Do not pay again.",
        "Replaced": "This Wi-Fi code was replaced. Check your phone for the replacement notification.",
    }[state]
    if state == "Ready" and reveal:
        response.update(voucher=f"{issuance.username}.{issuance.get_password('password')}",
                        package_id=checkout.package, zone_id=checkout.zone,
                        duration_minutes=checkout.duration_minutes,
                        duration_seconds=issuance.duration_seconds, expiry_authority="mikrotik")
    return response
