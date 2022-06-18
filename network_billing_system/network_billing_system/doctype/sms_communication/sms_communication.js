// Copyright (c) 2022, stephen and contributors
// For license information, please see license.txt
$.extend(cur_frm.cscript, {
	message: function () {
		var total_characters = this.frm.doc.message.length;
		var total_msg = 1;

		if (total_characters > 160) {
			total_msg = cint(total_characters / 160);
			total_msg = (total_characters % 160 == 0 ? total_msg : total_msg + 1);
		}

		this.frm.set_value("total_characters", total_characters);
		this.frm.set_value("total_messages", this.frm.doc.message ? total_msg : 0);
	}
});

