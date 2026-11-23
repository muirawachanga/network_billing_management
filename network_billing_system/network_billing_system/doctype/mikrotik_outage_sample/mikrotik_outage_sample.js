frappe.ui.form.on("MikroTik Outage Sample", {
	refresh(frm) {
		if (frm.doc.kind !== "Account" || frm.doc.status !== "Review") return;
		const review = (decision, title) => {
			const fields = [
				{ fieldname: "note", fieldtype: "Small Text", label: __("Review Note"), reqd: 1 },
			];
			if (decision === "approve" && !frm.doc.active_at_onset) {
				fields.push({
					fieldname: "account_evidence", fieldtype: "Small Text", reqd: 1,
					label: __("Independent Evidence This Account Was Connected at Outage Onset"),
				});
			}
			frappe.prompt(fields, (values) => frappe.call({
				method: "network_billing_system.mikrotik_outages.review_account",
				args: { sample_name: frm.doc.name, decision, ...values },
				callback: () => frm.reload_doc(),
			}), title);
		};
		frm.add_custom_button(__("Approve Replacement"), () => review("approve", __("Approve Replacement")));
		frm.add_custom_button(__("Decline Replacement"), () => review("decline", __("Decline Replacement")));
	},
});
