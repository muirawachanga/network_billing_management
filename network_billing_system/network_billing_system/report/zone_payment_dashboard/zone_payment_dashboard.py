import frappe

from network_billing_system.zone_finance import FINANCE_ROLES, balances


def execute(filters=None):
    frappe.only_for(FINANCE_ROLES)
    filters = filters or {}
    rows = balances(filters.get("provider_environment") or "live", filters.get("zone"))
    columns = [
        {"fieldname": "zone", "label": "Zone", "fieldtype": "Link", "options": "Captive Portal Zone", "width": 180},
        {"fieldname": "display_name", "label": "Location", "fieldtype": "Data", "width": 160},
        {"fieldname": "successful_payments", "label": "Successful Payments", "fieldtype": "Int", "width": 160},
    ] + [
        {"fieldname": field, "label": label + " (KES)", "fieldtype": "Currency", "options": "currency", "width": 170}
        for field, label in (
            ("collected", "Collected"), ("reserved", "Reserved"),
            ("withdrawn", "Withdrawn"), ("available", "Available"),
        )
    ]
    summary = [
        {"label": label, "value": sum(row[field] for row in rows), "datatype": "Currency", "currency": "KES"}
        for field, label in (
            ("collected", "Total Collected"), ("reserved", "Reserved"),
            ("withdrawn", "Withdrawn"), ("available", "Available to Withdraw"),
        )
    ]
    chart = {
        "data": {
            "labels": [row["zone"] for row in rows],
            "datasets": [
                {"name": label, "values": [float(row[field]) for row in rows]}
                for field, label in (("collected", "Collected"), ("available", "Available"))
            ],
        },
        "type": "bar",
    }
    return columns, rows, None, chart, summary
