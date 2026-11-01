"""Zone balances are derived from verified checkouts, never payment prompts."""

from decimal import Decimal, InvalidOperation

import frappe


FINANCE_ROLES = ("System Manager", "Network Administrator")


def money(value):
    try:
        amount = Decimal(str(value or 0))
        valid = amount.is_finite() and amount == amount.quantize(Decimal("0.01"))
    except InvalidOperation:
        valid = False
    if not valid:
        frappe.throw("Amounts must be finite and have at most two decimal places.")
    return amount


def balances(environment="live", zone=None):
    if environment not in ("live", "sandbox"):
        frappe.throw("Select live or sandbox.")
    rows = frappe.db.sql(
        """
        select z.name as zone, z.display_name,
          coalesce(p.payments, 0) as successful_payments,
          coalesce(p.collected, 0) as collected,
          coalesce(r.reserved, 0) as reserved,
          coalesce(r.withdrawn, 0) as withdrawn
        from `tabCaptive Portal Zone` z
        left join (
          select zone, count(*) as payments, sum(amount) as collected
          from `tabPortal Checkout`
          where status='SUCCESS' and currency='KES' and provider_environment=%s
          group by zone
        ) p on p.zone=z.name
        left join (
          select zone,
            sum(case when status in ('Queued','Sending','Pending','Unknown')
                     then amount + fee_allowance else 0 end) as reserved,
            sum(case when status='Paid' then amount + fee_allowance else 0 end) as withdrawn
          from `tabZone Remittance`
          where docstatus=1 and provider_environment=%s
          group by zone
        ) r on r.zone=z.name
        where (%s is null or z.name=%s)
        order by z.name
        """,
        (environment, environment, zone, zone),
        as_dict=True,
    )
    for row in rows:
        row["currency"] = "KES"
        row["available"] = money(row["collected"]) - money(row["reserved"]) - money(row["withdrawn"])
    return rows


def lock_zone(zone):
    rows = frappe.db.sql(
        "select name from `tabCaptive Portal Zone` where name=%s for update", (zone,)
    )
    if not rows:
        frappe.throw("Unknown captive portal zone.")


def ensure_funds(zone, environment, amount):
    lock_zone(zone)
    payments = frappe.db.sql(
        """select amount from `tabPortal Checkout`
        where zone=%s and provider_environment=%s and currency='KES' and status='SUCCESS'
        for update""", (zone, environment), as_dict=True
    )
    withdrawals = frappe.db.sql(
        """select amount, fee_allowance from `tabZone Remittance`
        where zone=%s and provider_environment=%s and docstatus=1
          and status in ('Queued','Sending','Pending','Unknown','Paid')
        for update""", (zone, environment), as_dict=True
    )
    available = sum((money(row["amount"]) for row in payments), Decimal(0))
    available -= sum(
        (money(row["amount"]) + money(row["fee_allowance"]) for row in withdrawals),
        Decimal(0),
    )
    if money(amount) > available:
        frappe.throw("This zone has insufficient available funds.")
