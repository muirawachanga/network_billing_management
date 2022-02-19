import frappe


def load_configuration(name, default=None):
    val = frappe.db.get_single_value("Kopokopo Mpesa Setting", name)
    if val is None:
        val = default
    return val
