import frappe


def after_migrate():
    for role in ("Transfer Preparer", "Transfer Approver", "Transfer Operator", "Transfer Reconciler"):
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)
