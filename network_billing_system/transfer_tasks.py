"""Bounded synchronization and draft-only scheduling; never automatically send."""

import frappe
from frappe.utils import getdate, nowdate

from network_billing_system.transfer_engine import configuration, post_entry
from network_billing_system.zone_finance import balances, lock_zone, money


def synchronize_checkout_credits(limit=200):
    rows = frappe.db.sql(
        """select p.name, p.zone, p.provider_environment, p.amount
        from `tabPortal Checkout` p
        left join `tabZone Finance Entry` e on e.source_key=concat('checkout:',p.name)
        where p.status='SUCCESS' and p.currency='KES' and e.name is null
        order by p.name limit %s""", (limit,), as_dict=True,
    )
    for row in rows:
        lock_zone(row.zone)
        post_entry(row.zone, row.provider_environment, "checkout:" + row.name, "Checkout Credit",
                   "Portal Checkout", row.name, credit=money(row.amount))
        frappe.db.commit()
    # Credits are an audit mirror, NOT a second source of available funds.
    # Balance SQL reads successful checkouts directly, including old records.
    return len(rows)


def period_key(schedule, today):
    if schedule == "Daily":
        return today.isoformat()
    if schedule == "Weekly":
        year, week, _ = today.isocalendar()
        return "{}-W{:02}".format(year, week)
    if schedule == "Monthly":
        return today.strftime("%Y-%m")
    raise ValueError("Unsupported schedule.")


def prepare_scheduled_requests():
    settings = configuration().get_settings()
    if not settings.enabled or not settings.scheduler_enabled:
        return
    today = getdate(nowdate())
    names = frappe.get_all("Zone Remittance Policy", filters={"enabled": 1, "schedule_enabled": 1},
                          pluck="name", limit_page_length=100)
    for name in names:
        try:
            policy = frappe.get_doc("Zone Remittance Policy", name)
            provider = configuration().get_provider(policy.provider_account)
            if provider.environment == "live" and not settings.allow_live:
                continue
            lock_zone(policy.zone)
            key = name + ":" + period_key(policy.schedule, today)
            if frappe.db.exists("Transfer Request", {"schedule_key": key}):
                frappe.db.commit()
                continue
            # Do not accumulate multiple unapproved scheduled requests.
            if frappe.db.exists("Transfer Request", {
                "zone": policy.zone, "provider_account": provider.name,
                "docstatus": ["<", 2], "status": ["not in", ["Paid", "Failed", "Cancelled"]],
            }):
                frappe.db.commit()
                continue
            available = balances(provider.environment, policy.zone)[0]["available"] - money(policy.retained_balance)
            if policy.maximum_amount:
                available = min(available, money(policy.maximum_amount))
            if settings.max_request_amount:
                available = min(available, money(settings.max_request_amount))
            # Whole-KES budget with Recipient-borne fees uses the request
            # solver; no quote failures are hidden or replaced with zero fees.
            budget = available.to_integral_value(rounding="ROUND_FLOOR")
            if budget <= 0 or budget < money(policy.minimum_payout):
                frappe.db.commit()
                continue
            doc = frappe.get_doc({
                "doctype": "Transfer Request", "zone": policy.zone,
                "provider_account": provider.name, "recipient_account": policy.recipient_account,
                "fee_policy": policy.fee_policy, "purpose": "Scheduled owner remittance " + key,
                "amount": budget, "fee_bearer": "Recipient", "schedule_key": key,
            })
            doc.flags.transfer_schedule = True
            doc.insert(ignore_permissions=True)
            doc.submit()
            frappe.db.commit()
        except (ValueError, frappe.ValidationError):
            frappe.db.rollback()
            frappe.log_error(frappe.get_traceback(), "Scheduled remittance policy: " + name)


def reconcile_transfers():
    from network_billing_system.transfer_engine import reconcile_batch
    settings = configuration().get_settings()
    if not settings.enabled:
        return
    names = frappe.get_all(
        "Transfer Batch", filters={"status": ["in", ["Pending", "Needs Review"]], "provider_id": ["is", "set"]},
        pluck="name", order_by="modified asc",
        limit_page_length=min(int(settings.reconciliation_limit or 10), 50),
    )
    for name in names:
        try:
            reconcile_batch(name)
            frappe.db.commit()
        except (ValueError, frappe.ValidationError):
            frappe.db.rollback()
            frappe.log_error(frappe.get_traceback(), "Scheduled transfer reconciliation: " + name)


def run():
    synchronize_checkout_credits()
    reconcile_transfers()
    prepare_scheduled_requests()
