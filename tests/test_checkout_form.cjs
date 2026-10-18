const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

function form({ authorized = true, fresh = false, provider = "provider-id" } = {}) {
  let handlers;
  let button;
  let prompt;
  let request;
  let reloaded = false;
  let message;
  const frm = {
    doc: { name: "checkout-1", provider_payment_id: provider, status: "PENDING" },
    is_new: () => fresh,
    add_custom_button: (label, action) => { button = action; },
    reload_doc: async () => { reloaded = true; },
  };
  const context = {
    __: text => text,
    frappe: {
      ui: { form: { on: (name, events) => { handlers = events; } } },
      user: { has_role: () => authorized },
      call: async args => { request = args; return { message: { status: "SUCCESS", access_ready: true } }; },
      prompt: (fields, action) => { prompt = action; },
      msgprint: value => { message = value; },
      throw: text => { throw new Error(text); },
    },
  };
  const source = fs.readFileSync(path.join(__dirname,
    "../network_billing_system/network_billing_system/doctype/portal_checkout/portal_checkout.js"), "utf8");
  vm.runInNewContext(source, context);
  handlers.refresh(frm);
  return {
    click: () => button(),
    prompted: () => prompt,
    hasButton: () => Boolean(button),
    request: () => request,
    reloaded: () => reloaded,
    message: () => message,
  };
}

test("authorized administrator can reconcile and reload without scheduler", async () => {
  const page = form();
  await page.click();
  assert.equal(page.request().method, "network_billing_system.api.captive_portal.reconcile_checkout");
  assert.equal(page.request().args.checkout_id, "checkout-1");
  assert.equal(page.request().freeze, true);
  assert.equal(page.reloaded(), true);
  assert.equal(page.message().indicator, "green");
});

test("missing provider ID prompts before making a request", async () => {
  const page = form({ provider: null });
  page.click();
  assert.equal(page.request(), undefined);
  await page.prompted()({ provider_payment_id: "existing-payment-id" });
  assert.equal(page.request().args.provider_payment_id, "existing-payment-id");
});

test("button is not added for new records or unauthorized roles", () => {
  assert.equal(form({ authorized: false }).hasButton(), false);
  assert.equal(form({ fresh: true }).hasButton(), false);
});

test("list reconciliation submits date range and follows batch cursor without scheduler", async () => {
  let settings;
  let action;
  let submit;
  let refreshed = false;
  let summary;
  const requests = [];
  const source = fs.readFileSync(path.join(__dirname,
    "../network_billing_system/network_billing_system/doctype/portal_checkout/portal_checkout_list.js"), "utf8");
  const frappe = {
    listview_settings: {},
    user: { has_role: () => true },
    datetime: { get_today: () => "2026-10-18" },
    utils: { escape_html: value => value },
    prompt: (fields, callback) => { submit = callback; },
    call: async options => {
      requests.push(options);
      return { message: {
        results: [{ name: "checkout-" + requests.length, outcome: "confirmed" }],
        next_after: requests.length === 1 ? "checkout-1" : null
      } };
    },
    msgprint: value => { summary = value; },
    throw: value => { throw new Error(value); }
  };
  vm.runInNewContext(source, { frappe, __: text => text });
  settings = frappe.listview_settings["Portal Checkout"];
  settings.onload({
    page: { add_inner_button: (label, callback) => { action = callback; } },
    refresh: () => { refreshed = true; }
  });
  action();
  await submit({ from_date: "2026-10-01", to_date: "2026-10-18" });
  assert.equal(requests.length, 2);
  assert.equal(requests[0].args.from_date, "2026-10-01");
  assert.equal(requests[1].args.after_name, "checkout-1");
  assert.equal(requests[0].method, "network_billing_system.api.captive_portal.reconcile_date_range");
  assert.equal(refreshed, true);
  assert.equal(summary.indicator, "green");
});
