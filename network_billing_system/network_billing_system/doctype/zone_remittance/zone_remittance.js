frappe.ui.form.on('Zone Remittance', {
	setup(frm) {
		frm.set_query('recipient', () => ({
			filters: {zone: frm.doc.zone, provider_environment: frm.doc.provider_environment,
				enabled: 1, registration_status: 'Registered'}
		}));
	},
	async recipient(frm) {
		if (!frm.doc.recipient || frm.doc.docstatus !== 0) return;
		const recipient = await frappe.db.get_doc('Remittance Recipient', frm.doc.recipient);
		const values = {};
		for (const field of ['destination_type', 'recipient_name', 'phone_number', 'network',
			'account_name', 'account_number', 'bank_branch_ref', 'till_number',
			'paybill_number', 'paybill_account_number', 'provider_reference']) {
			values[field] = recipient[field] || '';
		}
		await frm.set_value(values);
	},
	async zone(frm) {
		if (!frm.doc.zone || frm.doc.docstatus !== 0) return;
		const response = await frappe.db.get_doc('Captive Portal Zone', frm.doc.zone);
		await frm.set_value({
			recipient: response.default_remittance_recipient || '',
			recipient_name: response.remittance_recipient_name || '',
			destination_type: response.remittance_destination_type || 'mobile_wallet',
			phone_number: response.remittance_phone_number || '',
			account_name: response.remittance_account_name || '',
			account_number: response.remittance_account_number || '',
			bank_branch_ref: response.remittance_bank_branch_ref || '',
			source_identifier: response.remittance_source_identifier || ''
		});
	},
	refresh(frm) {
		if (frm.doc.docstatus === 0 && frm.doc.withdrawal_method === 'External Withdrawal') {
			frm.set_intro(__('Record a completed withdrawal from Kopo Kopo with a unique reference and private evidence. Submission deducts funds without sending money and may reveal a negative zone balance.'), 'orange');
		}
		if (frm.doc.docstatus !== 1 || !frappe.user.has_role(['System Manager', 'Network Administrator'])) return;
		if (frm.doc.withdrawal_method === 'External Withdrawal') return;
		const call = async (method, args = {}) => {
			const response = await frappe.call({
				method: `network_billing_system.api.zone_remittance.${method}`,
				args: {remittance_id: frm.doc.name, ...args},
				freeze: true,
				freeze_message: __('Checking remittance...')
			});
			await frm.reload_doc();
			frappe.msgprint({
				title: __('Zone Remittance'),
				message: frappe.utils.escape_html(response.message.message || response.message.status),
				indicator: response.message.error ? 'red' : 'blue'
			});
		};
		if (frm.doc.status === 'Queued') {
			frm.set_intro(__('Use Transfer Request and Transfer Batch for new API payouts. Cancel this unsent legacy reservation first.'), 'orange');
		} else if (['Sending', 'Pending', 'Unknown'].includes(frm.doc.status)) {
			frm.add_custom_button(__('Check Transfer Status'), () => {
				if (!frm.doc.provider_transfer_id) {
					frappe.prompt([{fieldname: 'provider_transfer_id', fieldtype: 'Data', label: __('Kopo Kopo Transfer UUID'), reqd: 1}],
						values => call('check_remittance', values));
				} else {
					return call('check_remittance');
				}
			});
		}
	}
});
