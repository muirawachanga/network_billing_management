frappe.ui.form.on("MikroTik Outage Incident", {
	refresh(frm) {
		if (frm.doc.status !== "Restored" || frm.doc.impact_verified) return;
		frm.add_custom_button(__("Confirm Service Impact"), () => {
			frappe.prompt(
				[{ fieldname: "note", fieldtype: "Small Text", label: __("Independent evidence of customer impact"), reqd: 1 }],
				({ note }) => frappe.call({
					method: "network_billing_system.mikrotik_outages.confirm_service_impact",
					args: { incident_name: frm.doc.name, note },
					callback: () => frm.reload_doc(),
				}),
				__("Confirm Service Impact")
			);
		});
	},
});
