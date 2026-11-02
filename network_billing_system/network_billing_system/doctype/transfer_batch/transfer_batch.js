frappe.ui.form.on('Transfer Batch', {
	refresh(frm) {
		if (frm.is_new()) return;
		const call = async (method, args = {}) => {
			const response = await frappe.call({
				method: `network_billing_system.transfer_engine.${method}`,
				args: {batch_id: frm.doc.name, ...args}, freeze: true,
				freeze_message: __('Processing transfer batch...')
			});
			await frm.reload_doc();
			frappe.msgprint({
				title: __('Transfer Batch'),
				message: frappe.utils.escape_html(response.message.message || response.message.status),
				indicator: response.message.error ? 'red' : 'blue'
			});
		};
		if (frm.doc.status === 'Ready' && frappe.user.has_role(['System Manager', 'Transfer Operator'])) {
			frm.add_custom_button(__('Unbatch Before Sending'), () => frappe.confirm(
				__('Return requests to Approved without sending money? Their reservations remain until cancelled or settled.'),
				() => call('unbatch_requests')
			));
			frm.add_custom_button(__('Send Batch'), () => frappe.confirm(
				__('This initiates the displayed payouts in the configured provider environment. Verify every recipient and amount. Continue?'),
				() => call('send_batch')
			));
		}
		if (['Sending', 'Pending', 'Needs Review'].includes(frm.doc.status) &&
			frappe.user.has_role(['System Manager', 'Transfer Reconciler'])) {
			frm.add_custom_button(__('Reconcile Provider Status'), () => {
				if (frm.doc.provider_id) return call('check_batch');
				frappe.prompt([{fieldname: 'provider_id', fieldtype: 'Data',
					label: __('Existing Send Money Request UUID'), reqd: 1}],
				values => call('check_batch', values));
			});
		}
	}
});
