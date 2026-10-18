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
