const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');

function form(status, provider = 'transfer-id', environment = 'sandbox') {
  let handlers, button, confirmed, request, intro;
  const frm = {
    doc: {name: 'remittance-1', zone: 'north', docstatus: 1, status, provider_transfer_id: provider, provider_environment: environment},
    add_custom_button: (label, action) => {button = action;},
    reload_doc: async () => {},
    set_value: async values => {Object.assign(frm.doc, values);},
    set_intro: message => {intro = message;}
  };
  const frappe = {
    ui: {form: {on: (name, value) => {handlers = value;}}},
    user: {has_role: () => true},
    utils: {escape_html: value => value},
    db: {get_doc: async doctype => doctype === 'Remittance Recipient' ? {
      destination_type: 'paybill', recipient_name: 'Test Paybill',
      paybill_number: '123456', paybill_account_number: 'account-1',
      provider_reference: 'provider-recipient-1'
    } : ({
      remittance_recipient_name: 'Owner', remittance_destination_type: 'mobile_wallet',
      remittance_phone_number: '0712345678', remittance_source_identifier: 'source-1'
    })},
    confirm: (message, callback) => {confirmed = callback;},
    call: async options => {request = options; return {message: {status: 'Pending'}};},
    msgprint: () => {}
  };
  vm.runInNewContext(fs.readFileSync(
    'network_billing_system/network_billing_system/doctype/zone_remittance/zone_remittance.js', 'utf8'
  ), {frappe, __: value => value});
  handlers.refresh(frm);
  return {frm, handlers, click: () => button(), confirm: () => confirmed(), request: () => request, hasButton: () => Boolean(button), intro: () => intro};
}

test('sending requires explicit confirmation and calls the separate payout endpoint', async () => {
  const page = form('Queued');
  page.click();
  assert.equal(page.request(), undefined);
  await page.confirm();
  assert.match(page.request().method, /zone_remittance.send_remittance$/);
});

test('pending transfer can only check status, not send again', async () => {
  const page = form('Pending');
  await page.click();
  assert.match(page.request().method, /zone_remittance.check_remittance$/);
});

test('zone selection copies recipient defaults to a draft', async () => {
  const page = form('Draft');
  page.frm.doc.docstatus = 0;
  await page.handlers.zone(page.frm);
  assert.equal(page.frm.doc.recipient_name, 'Owner');
  assert.equal(page.frm.doc.phone_number, '0712345678');
  assert.equal(page.frm.doc.source_identifier, 'source-1');
});

test('live remittance has no send button and explains the rollout gate', () => {
  const page = form('Queued', null, 'live');
  assert.equal(page.hasButton(), false);
  assert.match(page.intro(), /Live transfers are not enabled/);
});

test('an external paid withdrawal has no transfer action', () => {
  const page = form('Paid');
  page.frm.doc.withdrawal_method = 'External Withdrawal';
  page.handlers.refresh(page.frm);
  assert.equal(page.hasButton(), false);
});

test('selecting a registered paybill copies the paybill and account number', async () => {
  const page = form('Draft');
  page.frm.doc.docstatus = 0;
  page.frm.doc.recipient = 'recipient-1';
  await page.handlers.recipient(page.frm);
  assert.equal(page.frm.doc.destination_type, 'paybill');
  assert.equal(page.frm.doc.paybill_number, '123456');
  assert.equal(page.frm.doc.paybill_account_number, 'account-1');
  assert.equal(page.frm.doc.provider_reference, 'provider-recipient-1');
});
