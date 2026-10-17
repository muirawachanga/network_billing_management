import frappe


def execute():
    if not frappe.db.exists("Captive Portal Zone", "default"):
        frappe.get_doc({
            "doctype": "Captive Portal Zone",
            "zone_id": "default",
            "display_name": "Legacy Zone",
            "enabled": 1,
            "description": "Review legacy inventory and assign it to its actual router zone.",
        }).insert(ignore_permissions=True)
    for doctype in (
        "Internet Package Profile", "Captive Portal Code", "Portal Checkout", "Import Captive Code"
    ):
        frappe.db.sql(
            "update `tab{}` set zone=%s where ifnull(zone, '')=''".format(doctype),
            ("default",),
        )
