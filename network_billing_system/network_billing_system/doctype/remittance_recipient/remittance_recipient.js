frappe.ui.form.on('Remittance Recipient', {
	refresh(frm) {
		if (frm.is_new() || frm.doc.registration_status !== 'Draft') return;
		frm.add_custom_button(__('Register with Kopo Kopo'), () => {
			frappe.confirm(__('Register this recipient account with Kopo Kopo? This does not send money.'), async () => {
				const response = await frappe.call({
					method: 'network_billing_system.api.remittance_recipient.register_recipient',
					args: {recipient_id: frm.doc.name}, freeze: true
				});
				await frm.reload_doc();
				frappe.msgprint(frappe.utils.escape_html(response.message.message || response.message.status));
			});
		});
	}
});
