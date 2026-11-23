frappe.ui.form.on("MikroTik Complimentary Grant", {
    setup(frm) {
        frm.set_query("zone", () => ({ filters: { enabled: 1, backend: "MikroTik" } }));
        frm.set_query("package", () => ({ filters: { enabled: 1, zone: frm.doc.zone || "" } }));
    },
    refresh(frm) {
        if (frm.doc.status === "Approved") {
            frm.disable_save();
            return;
        }
        if (frm.is_new() || frm.is_dirty() || frm.doc.status !== "Draft" || !frm.perm?.[0]?.write
            || !["System Manager", "Network Administrator"].some(role => frappe.user.has_role(role))) {
            return;
        }
        frm.add_custom_button(__("Approve Grant"), () => {
            if (frm.is_dirty()) {
                frappe.msgprint(__("Save the draft before approving."));
                return;
            }
            frappe.confirm(__("Issue complimentary Wi-Fi access and queue an SMS after provisioning?"), () => {
                frappe.call({
                    method: "network_billing_system.mikrotik_grants.approve_grant",
                    type: "POST",
                    args: { grant_name: frm.doc.name },
                    freeze: true,
                    callback: () => frm.reload_doc(),
                });
            });
        }).addClass("btn-primary");
    },
});
