"""Desk-only complimentary issuance and its durable Notify Platform outbox."""

import re
from datetime import timedelta

import frappe
from frappe.utils import get_datetime, now_datetime

from network_billing_system import mikrotik_access as access
from network_billing_system.notify_platform_transport import NotifyClient, NotifyError, SETTINGS
from network_billing_system.portal_rules import normalize_phone


GRANT = access.GRANT
OUTBOX = "MikroTik Grant Notification"
ZONE = "Captive Portal Zone"
PACKAGE = "Internet Package Profile"
APPROVERS = ("System Manager", "Network Administrator")
MAX_MINUTES = 1440
ZONE_LIMIT = 20
RECIPIENT_LIMIT = 3
RATE_WINDOW = timedelta(days=1)
DELIVERY_WINDOW = timedelta(days=3)
BATCH_SIZE = 10


def validate_details(grant):
    try:
        grant.recipient = normalize_phone(grant.recipient)
    except ValueError:
        frappe.throw("Enter a valid Kenyan mobile number.")
    grant.reason = (grant.reason or "").strip()
    grant.source_reference = (grant.source_reference or "").strip().lower()
    if not 1 <= len(grant.reason) <= 500:
        frappe.throw("Enter a reason of up to 500 characters.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.:/-]{0,139}", grant.source_reference):
        frappe.throw("Enter a unique support or campaign reference (letters, digits, or _ . : / -).")
    if type(grant.duration_minutes) is not int or not 1 <= grant.duration_minutes <= MAX_MINUTES:
        frappe.throw(f"Enter a whole-minute duration between 1 and {MAX_MINUTES}.")


def _limits(grant, now):
    # Approval holds the zone row lock. A locking read sees the most recently committed
    # approvals even when this request had already established a database snapshot.
    recent = frappe.db.sql(
        """SELECT recipient FROM `tabMikroTik Complimentary Grant`
           WHERE zone=%s AND status='Approved' AND approved_at > %s
           LIMIT %s FOR UPDATE""",
        (grant.zone, now - RATE_WINDOW, ZONE_LIMIT),
        pluck=True,
    )
    if len(recent) >= ZONE_LIMIT or recent.count(grant.recipient) >= RECIPIENT_LIMIT:
        frappe.throw("This zone or recipient reached the 24-hour complimentary access limit.")


@frappe.whitelist(methods=["POST"])
def approve_grant(grant_name):
    frappe.only_for(APPROVERS)
    zone_name = frappe.db.get_value(GRANT, grant_name, "zone")
    if not zone_name:
        frappe.throw("Complimentary grant not found.")
    # Lock the zone before touching grant rows so concurrent approvals use one order.
    zone = frappe.get_doc(ZONE, zone_name, for_update=True)
    grant = frappe.get_doc(GRANT, grant_name, for_update=True)
    grant.check_permission("write")
    if grant.zone != zone_name:
        frappe.throw("The grant's zone changed. Reload and try again.")
    if grant.status == "Approved":
        return {"issuance": grant.issuance}
    if grant.status != "Draft":
        frappe.throw("Only a draft complimentary grant may be approved.")
    validate_details(grant)
    zone.check_permission("read")
    if not zone.enabled or zone.backend != "MikroTik" or not zone.mikrotik_router or zone.pause_mikrotik_sales:
        frappe.throw("Select an enabled MikroTik zone with new access enabled.")
    package = frappe.get_doc(PACKAGE, grant.package, for_update=True)
    package.check_permission("read")
    policy = access.package_policy(package)
    if (package.zone != grant.zone or not package.enabled or not policy
            or not 1 <= grant.duration_minutes <= package.duration_minutes):
        frappe.throw("Choose an enabled package in this zone and stay within its duration.")
    router = frappe.get_doc(access.ROUTER, zone.mikrotik_router)
    if (router.zone != grant.zone or not router.pilot_verified
            or not router.hotspot_server or not router.get_password("api_password", raise_exception=False)):
        frappe.throw("Configure and verify the MikroTik router for this zone first.")

    now = now_datetime()
    _limits(grant, now)
    grant.status, grant.router = "Approved", router.name
    issuance = access.create_grant_issuance(grant)
    frappe.db.set_value(GRANT, grant.name, {
        "status": grant.status, "approved_at": now, "issuer": frappe.session.user,
        "recipient": grant.recipient, "reason": grant.reason, "source_reference": grant.source_reference,
        "router": router.name, "issuance": issuance.name, "hotspot_server": router.hotspot_server,
        "hotspot_profile": policy["mikrotik_profile"], "hotspot_rate_limit": policy["mikrotik_rate_limit"],
    })
    return {"issuance": issuance.name}


def queue_notification(issuance):
    """Provisioning calls this in the transaction that marks the issuance Ready."""
    if frappe.db.exists(OUTBOX, {"issuance": issuance.name}):
        return
    item = frappe.get_doc({
        "doctype": OUTBOX, "issuance": issuance.name, "grant": issuance.complimentary_grant,
        "status": "Pending Quote", "next_attempt_at": now_datetime(),
    }).insert(ignore_permissions=True)
    frappe.db.set_value(GRANT, issuance.complimentary_grant, "notification", item.name)


def _patch(item, **fields):
    frappe.db.set_value(OUTBOX, item.name, fields)


def _retry(item, now, error, *, limit=None):
    attempts = (item.attempts or 0) + 1
    review = limit and attempts >= limit
    _patch(
        item, attempts=attempts, diagnostic=error,
        status="Needs Review" if review else item.status,
        next_attempt_at=None if review else now + timedelta(minutes=min(60, 2 ** min(attempts - 1, 6))),
    )


def _result(item, now, dispatch, delivery=None):
    fields = {"provider_dispatch": dispatch, "diagnostic": None, "attempts": 0}
    if delivery:
        fields["provider_delivery"] = delivery
    if dispatch in ("Rejected", "Cancelled", "Uncertain"):
        status = "Rejected" if dispatch == "Rejected" else "Needs Review"
    elif dispatch == "Accepted" and delivery in ("Delivered", "Failed"):
        status = "Delivered" if delivery == "Delivered" else "Delivery Failed"
    else:
        deadline = get_datetime(item.poll_until) if item.poll_until else now + DELIVERY_WINDOW
        if now < deadline:
            _patch(item, **fields, status="Provider Accepted" if dispatch == "Accepted" else "Queued",
                   next_attempt_at=min(deadline, now + timedelta(minutes=5)), poll_until=deadline)
            return
        status = "Delivery Unverified"
    _patch(item, **fields, status=status, next_attempt_at=None)


def _send(item, client, now):
    issuance = access.lock_issuance(item.issuance)
    grant = frappe.get_doc(GRANT, item.grant)
    ready = (issuance and issuance.state == "Ready" and issuance.complimentary_grant == grant.name
             and grant.status == "Approved" and grant.issuance == item.issuance)
    if item.dispatch_name:
        if not ready:
            # A previous confirmation may still have succeeded. Reconcile without
            # authorizing a new send for an issuance that is no longer usable.
            dispatch, delivery = client.history(item.dispatch_name)
            if not dispatch:
                raise NotifyError("Status Unavailable")
            if dispatch == "Quoted":
                _patch(item, status="Suppressed", next_attempt_at=None)
            else:
                _result(item, now, dispatch, delivery)
            return
        # Once a quote is stored, only that dispatch may be confirmed—even after
        # a timeout or a crash between remote authorization and local commit.
        try:
            dispatch = client.confirm(item.dispatch_name)
        except NotifyError as error:
            if str(error) in ("Quote Expired", "Quote Invalid"):
                _patch(item, status="Quote Expired" if str(error) == "Quote Expired" else "Needs Review",
                       diagnostic=str(error), next_attempt_at=None)
            else:
                # Keep retrying the same name. The remote side either returns its
                # existing authorization status or eventually rejects the expired quote.
                _retry(item, now, str(error), limit=12)
            return
        _result(item, now, dispatch)
        return

    if not ready:
        _patch(item, status="Suppressed", next_attempt_at=None)
        return
    password = issuance.get_password("password", raise_exception=False)
    if not password:
        _patch(item, status="Needs Review", diagnostic="Missing RouterOS credential", next_attempt_at=None)
        return
    try:
        dispatch, sufficient = client.quote(
            grant.recipient, f"Your complimentary Wi-Fi code: {issuance.username}.{password}",
        )
    except NotifyError as error:
        _retry(item, now, str(error), limit=6)
        return
    # No confirm in this transaction. Losing a quote response may leave an
    # unused quote remotely, but cannot queue an SMS or reserve its cost.
    _patch(item, status="Pending Confirm", dispatch_name=dispatch, integration_identity=client.identity,
           next_attempt_at=now, attempts=0, diagnostic=None if sufficient else "Low Balance",
           provider_dispatch="Quoted")


def _process(name):
    item = frappe.get_doc(OUTBOX, name, for_update=True)
    now = now_datetime()
    if not item.next_attempt_at or get_datetime(item.next_attempt_at) > now:
        return
    polling = item.status in ("Queued", "Provider Accepted")
    try:
        client = NotifyClient.from_settings(frappe.get_single(SETTINGS))
        if item.dispatch_name and client.identity != item.integration_identity:
            raise NotifyError("Integration Changed")
        if polling:
            dispatch, delivery = client.history(item.dispatch_name)
            if not dispatch or dispatch == "Quoted":
                raise NotifyError("Status Unavailable")
            _result(item, now, dispatch, delivery)
        elif item.status in ("Pending Quote", "Pending Confirm"):
            _send(item, client, now)
    except NotifyError as error:
        if polling and item.poll_until and now >= get_datetime(item.poll_until):
            _patch(item, status="Delivery Unverified", diagnostic=str(error), next_attempt_at=None)
        else:
            _retry(item, now, str(error))


def run_notifications():
    """Process due rows; each step uses a separate transaction."""
    for name in frappe.get_all(
        OUTBOX, filters={"next_attempt_at": ["<=", now_datetime()]},
        pluck="name", order_by="next_attempt_at asc, name asc", limit_page_length=BATCH_SIZE,
    ):
        try:
            _process(name)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title="MikroTik grant notification failed", message="Check the staff-only outbox.")
