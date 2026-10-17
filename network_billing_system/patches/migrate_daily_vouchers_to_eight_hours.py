import frappe
from frappe.utils import cint


def execute():
    filters = {
        "expiry_duration": "Daily",
        "package": ["is", "not set"],
        "portal_checkout": ["is", "not set"],
    }
    if not frappe.db.exists("Captive Portal Code", filters):
        return

    package_name = "time_8hrs"
    if frappe.db.exists("Internet Package Profile", package_name):
        duration = frappe.db.get_value(
            "Internet Package Profile", package_name, "duration_minutes"
        )
        if cint(duration) != 480:
            frappe.throw(
                "Cannot migrate Daily vouchers: package time_8hrs must have a "
                "duration of 480 minutes. Correct that package before retrying migration."
            )
    else:
        frappe.get_doc({
            "doctype": "Internet Package Profile",
            "package_name": package_name,
            "zone": "default",
            "display_name": "8 Hours",
            "price": 30,
            "duration_minutes": 480,
            "enabled": 0,
            "description": "Legacy Daily pfSense vouchers with 8 hours of usage.",
        }).insert(ignore_permissions=True)

    # Preserve inventory state and historical SMS links; this is not reactivation.
    frappe.db.sql(
        """
        update `tabCaptive Portal Code`
        set package=%s, duration_minutes=%s
        where expiry_duration=%s
          and ifnull(package, '')=''
          and ifnull(portal_checkout, '')=''
        """,
        (package_name, 480, "Daily"),
    )
