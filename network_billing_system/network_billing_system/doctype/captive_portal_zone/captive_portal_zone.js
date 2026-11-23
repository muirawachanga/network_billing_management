frappe.ui.form.on("Captive Portal Zone", {
	setup(frm) {
		frm.set_query("mikrotik_router", () => ({
			filters: { zone: frm.is_new() ? "" : frm.doc.name },
		}));
	},
	refresh(frm) {
		if (frm.is_new() || frm.doc.backend !== "MikroTik" || frm.doc.mikrotik_router) return;
		if (!frappe.model.can_create("MikroTik Router")) {
			frm.set_intro(__("Ask a System Manager to create and link a MikroTik Router before enabling this zone."), "blue");
			return;
		}
		frm.set_intro(__("Create the router for this zone, then return here to link it and enable the zone."), "blue");
		frm.add_custom_button(__("Create MikroTik Router"), () => {
			frappe.new_doc("MikroTik Router", { zone: frm.doc.name });
		});
	},
});
