// Copyright (c) 2021, stephen and contributors
// For license information, please see license.txt

frappe.ui.form.on('SMS Logs', {
	refresh(frm) {
		frm.events.make_custom_button(frm);
	  },
	//   onload(frm){
	// 	if (frm.doc.__islocal != 1) {
	// 	  frappe.call({
	// 		method: 'metering.metering.doctype.disco_meter_installation.disco_meter_installation.determine_reload',
	// 		args: {
	// 		  name: frm.doc.name,
	// 		  disco_type: 'Disco Meter Installation'
	// 		},
	// 		callback(r){
	// 		  if (r.message === true){
	// 			frm.save();
	// 		  }
	// 		}
	// 	  })
	// 	}
		
	//   },
	  make_custom_button(frm) {
		frm.add_custom_button(__('Send'),
			() => frm.events.resend(frm), __('Resend SMS'));
		frm.page.set_inner_btn_group_as_primary(__('Resend SMS'));
	  },
	  resend(frm) {
		frappe.call({
		  method: 'network_billing_system.network_billing_system.doctype.sms_logs.sms_logs.send_msg',
		  args: {
			name: frm.doc.name,
			phone: frm.doc.mobile_number,
			msg_: frm.doc.captive_portal_code
		  },
		  callback(r) {
			if (!r.exc) {
			  frappe.msgprint(__('Message resent successfully'));
			  frm.reload_doc();
			}
		  },
		});
	  },
});
