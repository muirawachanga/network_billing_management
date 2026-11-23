frappe.ui.form.on("MikroTik Router", {
	setup(frm) {
		frm.set_query("zone", () => ({
			query: "network_billing_system.network_billing_system.doctype.mikrotik_router.mikrotik_router.search_zones",
		}));
	},
});
