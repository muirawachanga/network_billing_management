function banner_preview_hint(frm) {
	const wrapper = frm.fields_dict.preview.$wrapper;
	wrapper.empty();
	$("<p>").text(frm.is_new()
		? __("Save this draft before uploading a public banner image.")
		: __("Save the banner to update its preview.")).appendTo(wrapper);
}

async function refresh_banner_preview(frm) {
	if (frm.is_new() || frm.is_dirty()) {
		banner_preview_hint(frm);
		return;
	}
	const wrapper = frm.fields_dict.preview.$wrapper;
	const name = frm.doc.name;
	const modified = frm.doc.modified;
	const current = () => frm.doc.name === name && frm.doc.modified === modified && !frm.is_dirty();
	wrapper.empty();
	try {
		const banner = await frappe.xcall("network_billing_system.mikrotik_banners.preview", { name });
		if (!current()) return;
		wrapper.empty();
		$("<h4>").text(banner.title).appendTo(wrapper);
		if (banner.image) {
			$("<img>")
				.attr({ src: banner.image, alt: banner.alt })
				.css({ maxWidth: "100%", maxHeight: "240px" })
				.appendTo(wrapper);
		}
		$("<p>").text(banner.description).appendTo(wrapper);
		if (banner.url) {
			$("<p>").text(__("Target URL: {0}", [banner.url])).appendTo(wrapper);
		}
	} catch {
		if (current()) $("<p>").text(__("Preview is unavailable.")).appendTo(wrapper);
	}
}

frappe.ui.form.on("MikroTik Banner", {
	setup(frm) {
		frm.set_query("zone", () => ({ filters: { backend: "MikroTik" } }));
		frm.fields_dict.image.df.options = {
			allow_web_link: false,
			disable_file_browser: true,
			allow_toggle_private: false,
			allow_google_drive: false,
			restrictions: {
				allowed_file_types: ["image/png", "image/jpeg", "image/webp"],
				max_file_size: 2 * 1024 * 1024,
			},
		};
	},
	refresh(frm) {
		frm.toggle_display("image", !frm.is_new());
		frm.toggle_enable("enabled", !frm.is_new());
		return refresh_banner_preview(frm);
	},
	title: banner_preview_hint,
	description: banner_preview_hint,
	image: banner_preview_hint,
	alt: banner_preview_hint,
	target_url: banner_preview_hint,
});
