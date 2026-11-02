frappe.ui.form.on('Transfer Request', {
	setup(frm) {
		frm.set_query('recipient_account', () => ({filters: {
			zone: frm.doc.zone, provider_account: frm.doc.provider_account, readiness: 'Verified'
		}}));
	},
	refresh(frm) {
		if (frm.doc.docstatus !== 1) return;
		if (frm.doc.status === 'Awaiting Approval' &&
			frappe.user.has_role(['System Manager', 'Transfer Approver'])) {
			frm.add_custom_button(__('Approve and Reserve Funds'), () => frappe.confirm(
				__('Reserve the displayed total from this zone after verifying recipient and fees?'),
				async () => {
					await frappe.call({method: 'network_billing_system.transfer_engine.approve_request',
						args: {request_id: frm.doc.name}, freeze: true});
					await frm.reload_doc();
				}
			));
		}
		if (frm.doc.status === 'Approved' &&
			frappe.user.has_role(['System Manager', 'Transfer Operator'])) {
			frm.add_custom_button(__('Create Transfer Batch'), async () => {
				const response = await frappe.call({
					method: 'network_billing_system.transfer_engine.create_batch',
					args: {request_ids: [frm.doc.name]}, freeze: true
				});
				frappe.set_route('Form', 'Transfer Batch', response.message.batch);
			});
		}
	}
});
