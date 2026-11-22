frappe.listview_settings['Portal Checkout'] = {
	onload(listview) {
		if (!frappe.user.has_role(['System Manager', 'Network Administrator'])) return;
		let running = false;
		listview.page.add_inner_button(__('Reconcile Date Range'), () => {
			if (running) return;
			frappe.prompt([
				{ fieldname: 'from_date', fieldtype: 'Date', label: __('From Date'), reqd: 1, default: frappe.datetime.get_today() },
				{ fieldname: 'to_date', fieldtype: 'Date', label: __('To Date'), reqd: 1, default: frappe.datetime.get_today() }
			], async values => {
				if (running) return;
				running = true;
				const counts = {};
				const attention = [];
				let processed = 0;
				let after;
				try {
					do {
						const response = await frappe.call({
							method: 'network_billing_system.api.captive_portal.reconcile_date_range',
							args: { ...values, after_name: after },
							timeout: 180000,
							freeze: true,
							freeze_message: __('Reconciling payments. Records checked: {0}', [processed])
						});
						const batch = response.message;
						if (!batch || !Array.isArray(batch.results)) {
							frappe.throw(__('Invalid reconciliation response. Completed batches remain saved.'));
						}
						batch.results.forEach(result => {
							counts[result.outcome] = (counts[result.outcome] || 0) + 1;
							processed++;
							if (['missing_provider_id', 'error', 'awaiting_access'].includes(result.outcome)) {
								attention.push(result.name);
							}
						});
						after = batch.next_after;
					} while (after);
					frappe.msgprint({
						title: __('Date Range Reconciliation'),
						message: [
							__('Checked: {0}', [processed]),
							__('Confirmed now: {0}; already confirmed: {1}', [counts.confirmed || 0, counts.already_confirmed || 0]),
							__('Pending: {0}; failed: {1}; cancelled: {2}', [counts.pending || 0, counts.failed || 0, counts.cancelled || 0]),
							__('Awaiting access: {0}; missing provider ID: {1}; errors: {2}', [counts.awaiting_access || 0, counts.missing_provider_id || 0, counts.error || 0]),
							attention.length ? __('Review these checkouts: {0}', [attention.map(name => frappe.utils.escape_html(name)).join(', ')]) : ''
						].filter(Boolean).join('<br>'),
						indicator: attention.length ? 'orange' : 'green'
					});
				} finally {
					running = false;
					listview.refresh();
				}
			}, __('Reconcile by Checkout Creation Date (up to 31 days)'), __('Reconcile'));
		});
	}
};
