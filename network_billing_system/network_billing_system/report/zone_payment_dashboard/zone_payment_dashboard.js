frappe.query_reports['Zone Payment Dashboard'] = {
	filters: [
		{fieldname: 'provider_environment', label: __('Provider Environment'), fieldtype: 'Select', options: 'live\nsandbox', default: 'live', reqd: 1},
		{fieldname: 'zone', label: __('Zone'), fieldtype: 'Link', options: 'Captive Portal Zone'}
	]
};
