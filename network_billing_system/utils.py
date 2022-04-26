import frappe

def load_configuration(name, default=None):
    # check if the app is installed or not, otherwise an error is exempted
    # when Kopokopo doctype is not installed.
    installed_apps = frappe.get_installed_apps()
    if "network_billing_system" not in installed_apps:
        return
    val = frappe.db.get_single_value("Kopokopo Mpesa Setting", name)
    if val is None:
        val = default
    return val
