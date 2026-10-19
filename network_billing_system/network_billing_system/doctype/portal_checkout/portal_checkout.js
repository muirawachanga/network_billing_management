frappe.ui.form.on('Portal Checkout', {
	refresh(frm) {
		if (frm.is_new() || !frappe.user.has_role(['System Manager', 'Network Administrator'])) {
			return;
		}
		frm.add_custom_button(__('Reconcile Payment'), () => {
			const reconcile = async (provider_payment_id) => {
				const response = await frappe.call({
					method: 'network_billing_system.api.captive_portal.reconcile_checkout',
					args: {
						checkout_id: frm.doc.name,
						provider_payment_id: provider_payment_id || undefined
					},
					freeze: true,
					freeze_message: __('Checking payment and access...')
				});
				await frm.reload_doc();
				const result = response.message;
				if (!result || !result.status) {
					frappe.throw(__('The payment check returned no status. Please contact support.'));
				}
				const diagnostic = result.provider_payload
					? `<h4>${__('Kopo Kopo response (sensitive fields redacted)')}</h4><pre>${frappe.utils.escape_html(JSON.stringify(result.provider_payload, null, 2))}</pre>`
					: `<p>${__('No provider payload was received. Check Error Log for the request result.')}</p>`;
				const error = result.error
					? `<p><strong>${__('Reconciliation error')}:</strong> ${frappe.utils.escape_html(result.error)}</p>`
					: '';
				const logReference = result.error_log
					? `<p>${__('Frappe Error Log')}: <strong>${frappe.utils.escape_html(result.error_log)}</strong></p>`
					: '';
				frappe.msgprint({
					title: __('Payment Reconciliation'),
					message: `${error}${logReference}<p>${result.access_ready
						? __('Payment confirmed and access is ready.')
						: result.status === 'SUCCESS'
							? __('Payment confirmed, but access is not ready. Check voucher inventory or contact support. Do not request another payment.')
							: __('Current payment status: {0}', [result.status])}</p>${diagnostic}`,
					indicator: result.error ? 'red' : result.access_ready ? 'green' : 'orange'
				});
			};
			if (!frm.doc.provider_payment_id && frm.doc.status !== 'SUCCESS') {
				frappe.prompt([{
					fieldname: 'provider_payment_id',
					fieldtype: 'Data',
					label: __('Kopo Kopo Payment ID'),
					description: __('Enter the incoming payment request ID from Kopo Kopo, not the M-Pesa receipt code.'),
					reqd: 1
				}], values => reconcile(values.provider_payment_id),
				__('Find Existing Payment'), __('Reconcile'));
			} else {
				return reconcile();
			}
		});
	}
});
