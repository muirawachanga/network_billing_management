const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function form(type, doc, role) {
	const handlers = {};
	const buttons = new Map();
	const calls = [];
	let confirm;
	let prompt;
	let query;
	const frappe = {
		ui: {form: {on(name, events) { handlers[name] = events; }}},
		user: {has_role: roles => roles.includes(role)},
		confirm: (message, callback) => { confirm = callback; },
		prompt: (fields, callback) => { prompt = callback; },
		call: async request => { calls.push(request); return {message: {status: 'Pending', batch: 'batch-1'}}; },
		utils: {escape_html: value => value},
		msgprint() {},
		set_route() {}
	};
	const file = type.toLowerCase().replaceAll(' ', '_');
	const source = fs.readFileSync(path.join(__dirname,
		`../network_billing_system/network_billing_system/doctype/${file}/${file}.js`), 'utf8');
	vm.runInNewContext(source, {frappe, __: value => value});
	const frm = {
		doc: {name: 'record-1', docstatus: 1, ...doc},
		is_new: () => false,
		add_custom_button: (label, callback) => buttons.set(label, callback),
		set_query: (field, callback) => { query = callback; },
		reload_doc: async () => {}
	};
	handlers[type].setup?.(frm);
	handlers[type].refresh(frm);
	return {buttons, calls, confirm: () => confirm(), prompt: values => prompt(values), query: () => query()};
}

test('only an approver can reserve funds, and confirmation precedes API call', async () => {
	const page = form('Transfer Request', {status: 'Awaiting Approval'}, 'Transfer Approver');
	page.buttons.get('Approve and Reserve Funds')();
	assert.equal(page.calls.length, 0);
	await page.confirm();
	assert.match(page.calls[0].method, /transfer_engine\.approve_request$/);
	assert.equal(form('Transfer Request', {status: 'Awaiting Approval'}, 'Transfer Preparer').buttons.size, 0);
	assert.equal(page.query().filters.readiness, 'Verified');
});

test('only ready batches expose send and unbatch, both require confirmation', async () => {
	const page = form('Transfer Batch', {status: 'Ready'}, 'Transfer Operator');
	page.buttons.get('Send Batch')();
	assert.equal(page.calls.length, 0);
	await page.confirm();
	assert.match(page.calls[0].method, /transfer_engine\.send_batch$/);
	page.buttons.get('Unbatch Before Sending')();
	await page.confirm();
	assert.match(page.calls[1].method, /transfer_engine\.unbatch_requests$/);
	assert.equal(form('Transfer Batch', {status: 'Pending'}, 'Transfer Operator').buttons.size, 0);
});

test('uncertain batches prompt for existing UUID, never retry payout', async () => {
	const page = form('Transfer Batch', {status: 'Needs Review'}, 'Transfer Reconciler');
	assert.equal(page.buttons.has('Send Batch'), false);
	page.buttons.get('Reconcile Provider Status')();
	assert.equal(page.calls.length, 0);
	await page.prompt({provider_id: 'existing-uuid'});
	assert.match(page.calls[0].method, /transfer_engine\.check_batch$/);
	assert.equal(page.calls[0].args.provider_id, 'existing-uuid');
	assert.equal(form('Transfer Batch', {status: 'Completed'}, 'Transfer Reconciler').buttons.size, 0);
});
