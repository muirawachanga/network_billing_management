frappe.listview_settings['Transfer Request'] = {
	onload(listview) {
		if (!frappe.user.has_role(['System Manager', 'Transfer Operator'])) return;
		listview.page.add_action_item(__('Batch Selected Approved Requests'), async () => {
			const rows = listview.get_checked_items();
			if (!rows.length) frappe.throw(__('Select approved requests first.'));
			const response = await frappe.call({
				method: 'network_billing_system.transfer_engine.create_batch',
				args: {request_ids: rows.map(row => row.name)}, freeze: true
			});
			frappe.set_route('Form', 'Transfer Batch', response.message.batch);
		});
	}
};
