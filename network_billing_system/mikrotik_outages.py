"""Collect outage evidence and replace vouchers only after confirmed revocation."""

from datetime import timedelta
from hashlib import sha256

import frappe
from frappe.utils import get_datetime, now_datetime

from network_billing_system import mikrotik_access as access, routeros


ROUTER = "MikroTik Router"
ISSUANCE = "MikroTik Issuance"
INCIDENT = "MikroTik Outage Incident"
SAMPLE = "MikroTik Outage Sample"
OUTBOX = "MikroTik Notification Outbox"

BOOT_TOLERANCE = 15
CAPTURE_WINDOW = timedelta(minutes=15)
ACTIVITY_FRESHNESS = timedelta(minutes=2)
SMS_CLAIM_TIMEOUT = timedelta(minutes=10)
BATCH_SIZE = 10


def _insert(doctype, **fields):
    return frappe.get_doc(dict(doctype=doctype, **fields)).insert(ignore_permissions=True)


def _lock(doctype, name):
    return frappe.db.get_value(doctype, name, "name", for_update=True)


def _patch(doc, **fields):
    frappe.db.set_value(doc.doctype, doc.name, fields)


def _digest(*values):
    return sha256("\0".join(values).encode()).hexdigest()


def _target(policy):
    return _digest(*(str(policy[field]) for field in ("host", "port", "transport", "router_identity", "server")))


def _nonnegative_int(value):
    return type(value) is int and value >= 0


def _same_boot(reference, current_at, uptime):
    if not _nonnegative_int(reference.uptime_seconds) or not _nonnegative_int(uptime):
        return False
    elapsed = (get_datetime(current_at) - get_datetime(reference.observed_at)).total_seconds()
    return (
        elapsed >= 0
        and uptime >= reference.uptime_seconds
        and uptime > elapsed
        and abs(uptime - reference.uptime_seconds - elapsed) <= BOOT_TOLERANCE
    )


def _issued_during_boot(issuance, reference):
    # Accounts that predate the verified boot may have lost their usage counters.
    oldest_safe = get_datetime(reference.observed_at) - timedelta(
        seconds=reference.uptime_seconds - BOOT_TOLERANCE
    )
    return get_datetime(issuance.creation) >= oldest_safe


def _health(router_name):
    if not frappe.db.get_value(ROUTER, router_name, "pilot_verified", for_update=True):
        return
    previous = frappe.db.get_value(
        SAMPLE, {"router": router_name, "kind": "Health"},
        ["name", "status", "observed_at", "uptime_seconds"],
        order_by="observed_at desc, name desc", as_dict=True, for_update=True,
    )
    baseline = frappe.db.get_value(
        SAMPLE, {"router": router_name, "kind": "Health", "status": "Reachable"},
        ["name", "observed_at", "uptime_seconds", "target_fingerprint"],
        order_by="observed_at desc, name desc", as_dict=True, for_update=True,
    )
    open_name = frappe.db.get_value(
        INCIDENT, {"router": router_name, "status": "Open"}, "name", for_update=True,
    )
    fingerprint = None
    try:
        policy = access.policy_for_router(frappe.get_doc(ROUTER, router_name))
        fingerprint = _target(policy)
        with routeros.connect(policy) as client:
            uptime = client.health()
    except (routeros.RouterError, frappe.ValidationError):
        uptime = None
    observed_at = now_datetime()
    reachable = _nonnegative_int(uptime)
    sample = _insert(
        SAMPLE, router=router_name, incident=open_name, kind="Health", observed_at=observed_at,
        status="Reachable" if reachable else "Unreachable", uptime_seconds=uptime,
        target_fingerprint=fingerprint,
    )
    if not reachable:
        if not open_name and previous and previous.status == "Unreachable":
            incident = _insert(
                INCIDENT, router=router_name, status="Open", started_at=previous.observed_at,
                baseline_sample=baseline.name if baseline else None, detection_sample=previous.name,
                classification="Connectivity unconfirmed",
            )
            _patch(frappe.get_doc(SAMPLE, previous.name), incident=incident.name)
            _patch(sample, incident=incident.name)
        return

    continuity = bool(
        baseline and baseline.target_fingerprint == fingerprint and _same_boot(baseline, observed_at, uptime)
    )
    if open_name:
        _patch(
            frappe.get_doc(INCIDENT, open_name), status="Restored", restored_at=observed_at,
            restoration_sample=sample.name,
            classification=(
                "Continuous uptime" if continuity else "Accounting continuity unverified"
            ),
        )
    elif baseline and not continuity:
        incident = _insert(
            INCIDENT, router=router_name, status="Restored", started_at=baseline.observed_at,
            restored_at=observed_at, baseline_sample=baseline.name, detection_sample=sample.name,
            restoration_sample=sample.name, classification="Accounting continuity unverified",
        )
        _patch(sample, incident=incident.name)


def _remaining(issuance, account):
    # The adapter returns a sample only after verifying support and completeness.
    if not isinstance(account, dict) or type(account.get("active")) is not bool:
        return None
    used = account.get("used_seconds")
    if not _nonnegative_int(used) or not _nonnegative_int(issuance.duration_seconds):
        return None
    return max(0, issuance.duration_seconds - used)


def _sample_activity(issuance_name):
    issuance = access.lock_issuance(issuance_name)
    if not issuance or issuance.state != "Ready":
        return
    reference = frappe.db.get_value(
        SAMPLE, {"router": issuance.router, "kind": "Health"},
        ["status", "observed_at", "uptime_seconds", "target_fingerprint"],
        order_by="observed_at desc, name desc", as_dict=True,
    )
    account = None
    if reference and reference.status == "Reachable" and (
        now_datetime() - get_datetime(reference.observed_at) <= ACTIVITY_FRESHNESS
    ):
        try:
            policy = access.policy_for(issuance)
            if _target(policy) == reference.target_fingerprint:
                with routeros.connect(policy) as client:
                    if _same_boot(reference, now_datetime(), client.health()):
                        result = client.account(issuance.username, access.owner_for(issuance))
                        if _same_boot(reference, now_datetime(), client.health()):
                            account = result
        except (routeros.RouterError, frappe.ValidationError):
            pass
    remaining = _remaining(issuance, account)
    _insert(
        SAMPLE, router=issuance.router, issuance=issuance.name, kind="Activity",
        status="Unverified" if remaining is None else ("Active" if account["active"] else "Idle"),
        observed_at=now_datetime(),
        used_seconds=account["used_seconds"] if remaining is not None else None,
        target_fingerprint=reference.target_fingerprint if remaining is not None else None,
    )
    if remaining == 0:
        _patch(issuance, state="Expired")


def _capture(candidate):
    incident_name, issuance_name = candidate
    if not _lock(INCIDENT, incident_name):
        return
    incident = frappe.get_doc(INCIDENT, incident_name, for_update=True)
    issuance = frappe.get_doc(ISSUANCE, issuance_name)
    if (not issuance.checkout or issuance.state != "Ready"
            or frappe.db.exists(SAMPLE, {"incident": incident.name, "issuance": issuance.name})):
        return
    status = "Insufficient Evidence"
    reason = "Possible reboot, changed router, or missing baseline; accounting continuity is unverified."
    account = None
    remaining = None
    activity = None
    reference = None
    if incident.classification == "Continuous uptime" and incident.baseline_sample:
        reference = frappe.get_doc(SAMPLE, incident.baseline_sample)
        activity = frappe.db.get_value(
            SAMPLE, {
                "issuance": issuance.name, "kind": "Activity", "observed_at": ["between", [
                    get_datetime(incident.started_at) - ACTIVITY_FRESHNESS, incident.started_at,
                ]],
            }, ["name", "status", "used_seconds", "target_fingerprint"],
            order_by="observed_at desc, name desc", as_dict=True,
        )
        reason = "Issuance predates the verified router boot."
        if _issued_during_boot(issuance, reference):
            reason = "Accounting is unsupported, incomplete, or no longer on the verified boot."
            try:
                policy = access.policy_for(issuance)
                if _target(policy) == reference.target_fingerprint:
                    with routeros.connect(policy) as client:
                        uptime = client.health()
                        if _same_boot(reference, now_datetime(), uptime):
                            account = client.account(issuance.username, access.owner_for(issuance))
                            if _same_boot(reference, now_datetime(), client.health()):
                                remaining = _remaining(issuance, account)
            except (routeros.RouterError, frappe.ValidationError):
                pass
            if remaining is not None:
                status = "Review" if remaining else "No Balance"
                reason = None
            if (
                remaining is not None and activity and activity.target_fingerprint == reference.target_fingerprint
                and account["used_seconds"] < activity.used_seconds
            ):
                status = "Insufficient Evidence"
                remaining = None
                reason = "The usage counter decreased after the pre-incident sample."
    _insert(
        SAMPLE, router=incident.router, incident=incident.name, issuance=issuance.name,
        evidence_key=_digest(incident.name, issuance.name), kind="Account",
        observed_at=now_datetime(), status=status, diagnostic=reason,
        used_seconds=account["used_seconds"] if remaining is not None else None,
        candidate_seconds=remaining,
        pre_incident_sample=activity.name if activity else None,
        active_at_onset=int(bool(
            reference and activity and activity.status == "Active"
            and activity.target_fingerprint == reference.target_fingerprint
        )),
    )


def _review_note(note):
    frappe.only_for("System Manager")
    if not isinstance(note, str) or not 1 <= len(note.strip()) <= 500:
        frappe.throw("A review note of 1–500 characters is required.")
    return note.strip()


@frappe.whitelist(methods=["POST"])
def confirm_service_impact(incident_name, note):
    """Corroborate service impact independently of management API probes."""
    note = _review_note(note)
    if not _lock(INCIDENT, incident_name):
        frappe.throw("Outage incident not found.")
    incident = frappe.get_doc(INCIDENT, incident_name, for_update=True)
    if incident.status != "Restored" or incident.impact_verified:
        frappe.throw("This incident cannot be confirmed.")
    _patch(
        incident, impact_verified=1, verified_by=frappe.session.user,
        verified_at=now_datetime(), impact_note=note,
    )


@frappe.whitelist(methods=["POST"])
def review_account(sample_name, decision, note, account_evidence=None):
    """Record an explicit staff decision without making external changes in the request."""
    note = _review_note(note)
    if decision not in ("approve", "decline"):
        frappe.throw("A valid review decision is required.")
    if not _lock(SAMPLE, sample_name):
        frappe.throw("Outage account evidence not found.")
    sample = frappe.get_doc(SAMPLE, sample_name, for_update=True)
    if sample.kind != "Account" or sample.status != "Review":
        frappe.throw("This evidence is not awaiting review.")
    if decision == "approve":
        if not frappe.db.get_value(INCIDENT, sample.incident, "impact_verified"):
            frappe.throw("Confirm independent service impact before approving a replacement.")
        if not sample.active_at_onset and (
            not isinstance(account_evidence, str) or not 1 <= len(account_evidence.strip()) <= 500
        ):
            frappe.throw("Provide independent evidence that this account was connected at outage onset.")
    _patch(
        sample, status="Approved" if decision == "approve" else "Declined",
        reviewed_by=frappe.session.user, reviewed_at=now_datetime(), review_note=note,
        account_evidence=account_evidence.strip() if decision == "approve" and not sample.active_at_onset else None,
    )


def _eligible(issuance, evidence, reference, client, owner):
    uptime = client.health()
    if not _same_boot(reference, now_datetime(), uptime):
        return None, None
    account = client.account(issuance.username, owner)
    remaining = _remaining(issuance, account)
    if (
        remaining is None
        or account["used_seconds"] < evidence.used_seconds
    ):
        return None, None
    return min(remaining, evidence.candidate_seconds), account["used_seconds"]


def _reserve(evidence):
    issuance = access.lock_issuance(evidence.issuance)
    if not issuance or not issuance.checkout or not _lock(SAMPLE, evidence.name):
        return False
    evidence = frappe.get_doc(SAMPLE, evidence.name, for_update=True)
    if evidence.status == "Replacing":
        return issuance.state == "Replacing"
    automatic = evidence.status == "Review"
    if not automatic and evidence.status != "Approved":
        return False
    router = frappe.db.get_value(
        ROUTER, issuance.router, ["pilot_verified", "automatic_compensation"], as_dict=True, for_update=True,
    )
    if not router or not router.pilot_verified or (automatic and not router.automatic_compensation):
        return False
    if issuance.state != "Ready" or frappe.db.exists(ISSUANCE, {"predecessor": issuance.name}):
        _patch(evidence, status="Needs Review", diagnostic="The predecessor is no longer eligible.")
        return False
    incident = frappe.get_doc(INCIDENT, evidence.incident)
    if not incident.impact_verified:
        return False
    if not evidence.active_at_onset and (
        automatic or not evidence.reviewed_by or not (evidence.account_evidence or "").strip()
    ):
        return False
    if (
        incident.classification != "Continuous uptime"
        or incident.router != issuance.router
        or evidence.router != issuance.router
        or not _nonnegative_int(evidence.used_seconds)
        or not _nonnegative_int(evidence.candidate_seconds)
        or not evidence.candidate_seconds
    ):
        _patch(evidence, status="Needs Review", diagnostic="The outage evidence is not sufficient.")
        return False
    reference = frappe.get_doc(SAMPLE, incident.baseline_sample)
    policy = access.policy_for(issuance)
    remaining = pre_revoke_used = None
    if _target(policy) == reference.target_fingerprint:
        with routeros.connect(policy) as client:
            remaining, pre_revoke_used = _eligible(issuance, evidence, reference, client, access.owner_for(issuance))
    if not remaining:
        _patch(
            evidence, status="Needs Review" if remaining is None else "No Balance",
            diagnostic="Pre-revocation accounting could not be verified." if remaining is None else None,
        )
        return False

    _patch(issuance, state="Replacing")
    _patch(
        evidence, status="Replacing",
        pre_revoke_used_seconds=pre_revoke_used,
        reviewed_at=now_datetime() if automatic else evidence.reviewed_at,
        review_note="Automatic: continuous uptime and complete accounting." if automatic else evidence.review_note,
    )
    # Public status/recovery must stop returning the predecessor before any external write.
    frappe.db.commit()
    return True


def _replace(evidence_name):
    evidence = frappe.get_doc(SAMPLE, evidence_name)
    if not _reserve(evidence):
        return
    issuance = access.lock_issuance(evidence.issuance)
    if not issuance or not _lock(SAMPLE, evidence_name):
        return
    evidence = frappe.get_doc(SAMPLE, evidence_name, for_update=True)
    if issuance.state != "Replacing" or evidence.status != "Replacing":
        return
    incident = frappe.get_doc(INCIDENT, evidence.incident)
    reference = frappe.get_doc(SAMPLE, incident.baseline_sample)
    owner = access.owner_for(issuance)
    policy = access.policy_for(issuance)
    if _target(policy) != reference.target_fingerprint:
        _patch(evidence, status="Needs Review", diagnostic="Router configuration changed during replacement.")
        return
    with routeros.connect(policy) as client:
        uptime = client.health()
        if not _same_boot(reference, now_datetime(), uptime):
            _patch(evidence, status="Needs Review", diagnostic="Router continuity lost during replacement.")
            return
        if client.revoke_user(issuance.username, owner) is not None:
            raise routeros.RouterError("Revocation could not be confirmed.")
        final_account = client.account(issuance.username, owner)
        remaining = _remaining(issuance, final_account)
        uptime = client.health()
        if (
            remaining is None
            or final_account.get("active") is not False
            or final_account["used_seconds"] < evidence.pre_revoke_used_seconds
            or not _same_boot(reference, now_datetime(), uptime)
        ):
            _patch(evidence, status="Needs Review", diagnostic="Revoked; final accounting could not be verified.")
            return

    seconds = min(remaining, evidence.candidate_seconds)
    if not seconds:
        _patch(issuance, state="Expired")
        _patch(evidence, status="No Balance", final_used_seconds=final_account["used_seconds"], diagnostic=None)
        return
    checkout = frappe.get_doc("Portal Checkout", issuance.checkout)
    replacement = access.create_issuance(
        checkout, seconds=seconds, predecessor=issuance.name, incident=incident.name,
    )
    _patch(issuance, state="Replaced")
    _patch(
        evidence, status="Replaced", replacement=replacement.name, credited_seconds=seconds,
        final_used_seconds=final_account["used_seconds"], diagnostic=None,
    )


def _job(method, names, error_title):
    for name in names:
        try:
            method(name)
            frappe.db.commit()
        except routeros.RouterError:
            frappe.db.rollback()
        except Exception:
            frappe.db.rollback()
            # Do not serialize credentials, responses, or request bodies into logs.
            frappe.log_error(title=error_title, message="Check the staff-only MikroTik records.")


def _attempt_replacement(name):
    try:
        _replace(name)
    except routeros.RouterError:
        frappe.db.rollback()
        evidence = frappe.get_doc(SAMPLE, name, for_update=True)
        if evidence.status == "Replacing":
            _patch(evidence, diagnostic="Awaiting confirmed revocation and complete final accounting.")
    except Exception:
        frappe.db.rollback()
        frappe.db.set_value(SAMPLE, name, "last_attempt_at", now_datetime())
        frappe.db.commit()
        raise
    frappe.db.set_value(SAMPLE, name, "last_attempt_at", now_datetime())


def run_monitoring():
    _job(
        _health, frappe.db.sql(
            """SELECT r.name FROM `tabMikroTik Router` r
               LEFT JOIN `tabMikroTik Outage Sample` s ON s.router=r.name AND s.kind='Health'
               WHERE r.pilot_verified=1
               GROUP BY r.name ORDER BY MAX(s.observed_at), r.name LIMIT %s""",
            (BATCH_SIZE,), pluck=True,
        ),
        "MikroTik health sampling failed",
    )
    _job(
        _capture,
        frappe.db.sql(
            """SELECT e.name, i.name FROM `tabMikroTik Outage Incident` e
               INNER JOIN `tabMikroTik Issuance` i ON i.router=e.router AND i.creation<=e.started_at
               LEFT JOIN `tabMikroTik Outage Sample` s ON s.issuance=i.name AND s.incident=e.name
               WHERE e.status='Restored' AND e.restored_at >= %s AND i.state='Ready'
                 AND COALESCE(i.checkout, '') <> '' AND s.name IS NULL
               ORDER BY e.restored_at, i.creation, i.name LIMIT %s""",
            (now_datetime() - CAPTURE_WINDOW, BATCH_SIZE),
        ),
        "MikroTik outage evidence capture failed",
    )
    _job(
        _sample_activity,
        frappe.db.sql(
            """SELECT i.name FROM `tabMikroTik Issuance` i
               INNER JOIN `tabMikroTik Router` r ON r.name=i.router AND r.pilot_verified=1
               LEFT JOIN `tabMikroTik Outage Sample` s ON s.issuance=i.name AND s.kind='Activity'
               WHERE i.state='Ready' GROUP BY i.name
               ORDER BY MAX(s.observed_at), i.name LIMIT %s""",
            (BATCH_SIZE,), pluck=True,
        ),
        "MikroTik usage sampling failed",
    )
    _job(
        _attempt_replacement,
        frappe.db.sql(
            """SELECT s.name FROM `tabMikroTik Outage Sample` s
               INNER JOIN `tabMikroTik Router` r ON r.name=s.router
               INNER JOIN `tabMikroTik Outage Incident` e ON e.name=s.incident
               WHERE s.kind='Account' AND (s.status='Replacing' OR (
                 r.pilot_verified=1 AND e.impact_verified=1 AND
                 (s.status='Approved' OR (s.status='Review' AND r.automatic_compensation=1
                    AND s.active_at_onset=1))))
               ORDER BY s.last_attempt_at, s.creation, s.name LIMIT %s""",
            (BATCH_SIZE,), pluck=True,
        ),
        "MikroTik outage replacement failed",
    )


def queue_notification(issuance):
    """Create the outbox item in the same transaction that marks a replacement Ready."""
    if issuance.state == "Ready" and issuance.predecessor and not frappe.db.exists(
        OUTBOX, {"issuance": issuance.name},
    ):
        _insert(
            OUTBOX, issuance=issuance.name, status="Pending",
            idempotency_key=_digest("mikrotik-replacement", issuance.name),
            next_attempt_at=now_datetime(),
        )


def _notify(outbox_name):
    from network_billing_system.mikrotik_recovery import send_message

    item = frappe.get_doc(OUTBOX, outbox_name)
    issuance = access.lock_issuance(item.issuance)
    if not _lock(OUTBOX, item.name):
        return
    item = frappe.get_doc(OUTBOX, outbox_name, for_update=True)
    if item.status == "Sending" and get_datetime(item.attempted_at) <= now_datetime() - SMS_CLAIM_TIMEOUT:
        _patch(item, status="Needs Review")
        return
    if item.status != "Pending":
        return
    if item.next_attempt_at and get_datetime(item.next_attempt_at) > now_datetime():
        return
    if not issuance or issuance.state != "Ready" or not issuance.predecessor:
        _patch(item, status="Suppressed")
        return
    attempts = (item.attempts or 0) + 1
    _patch(item, status="Sending", attempted_at=now_datetime(), attempts=attempts)
    # The legacy SMS gateway does not promise that it honors idempotency keys.
    # Commit the claim so a lost response or crashed worker cannot trigger a second send.
    frappe.db.commit()
    issuance = access.lock_issuance(item.issuance)
    item = frappe.get_doc(OUTBOX, outbox_name, for_update=True)
    if item.status != "Sending":
        return
    if not issuance or issuance.state != "Ready" or not issuance.predecessor:
        _patch(item, status="Suppressed")
        return
    phone = frappe.db.get_value("Portal Checkout", issuance.checkout, "mobile_number")
    password = issuance.get_password("password")
    if not phone or not password:
        _patch(item, status="Needs Review")
        return
    accepted = send_message(
        phone,
        f"Your replacement Wi-Fi access code: {issuance.username}.{password}",
        item.idempotency_key,
    )
    if accepted == "retry" and attempts < 6:
        _patch(item, status="Pending", next_attempt_at=now_datetime() + timedelta(minutes=2 ** (attempts - 1)))
    else:
        _patch(item, status="Sent" if accepted is True else "Needs Review", next_attempt_at=None)


def run_notifications():
    stale = frappe.get_all(
        OUTBOX, filters={"status": "Sending", "attempted_at": ["<=", now_datetime() - SMS_CLAIM_TIMEOUT]},
        pluck="name", order_by="attempted_at asc", limit_page_length=BATCH_SIZE,
    )
    _job(
        _notify,
        stale + frappe.get_all(
            OUTBOX, filters={"status": "Pending", "next_attempt_at": ["<=", now_datetime()]},
            pluck="name", order_by="next_attempt_at asc, creation asc", limit_page_length=BATCH_SIZE,
        ),
        "MikroTik replacement notification failed",
    )
