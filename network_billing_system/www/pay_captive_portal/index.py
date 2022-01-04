import frappe


def get_context(context):
	is_enabled = frappe.db.get_single_value("Kopokopo Mpesa Setting", "webhook_already_set")
	if is_enabled:
		return context
	else:
		frappe.local.flags.redirect_location = '/404'
		raise frappe.Redirect
    
@frappe.whitelist(allow_guest=True)
def process_payment(contact):
    import json
    from network_billing_system.kopokopo_integration import process_stk
    contact = json.loads(contact)
    # provide the number to the sdk
    code_ = process_stk(contact.get("number", None))
    return code_