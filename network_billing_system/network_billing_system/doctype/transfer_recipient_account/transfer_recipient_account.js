frappe.ui.form.on("Transfer Recipient Account", {
    refresh(frm) {
        if (frm.is_new() || frm.doc.readiness !== "Draft" ||
            frm.doc.registration_state !== "Draft" || frm.doc.registration_resource_id ||
            !["System Manager", "Transfer Approver"].some(role => frappe.user_roles.includes(role))) {
            return;
        }
        frm.add_custom_button(__("Register with Kopo Kopo"), () => {
            if (frm.is_dirty()) {
                frappe.msgprint(__("Save your changes before registering."));
                return;
            }
            frappe.confirm(__("Register this account once? Registration does not verify its payout reference."), () => {
                frappe.call({
                    method: "network_billing_system.transfer_configuration.register_account",
                    args: {account_name: frm.doc.name},
                    freeze: true,
                    callback(r) {
                        if (r.message) frappe.msgprint(r.message.message);
                        frm.reload_doc();
                    }
                });
            });
        });
    }
});
