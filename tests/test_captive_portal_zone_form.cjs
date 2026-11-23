const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const script = fs.readFileSync(path.join(__dirname,
  "../network_billing_system/network_billing_system/doctype/captive_portal_zone/captive_portal_zone.js"), "utf8");

function form({ saved = true, backend = "MikroTik", canCreate = true, router } = {}) {
  let handlers, query, action, opened, intro;
  const frm = {
    doc: { name: "digissiet-pilot", backend, mikrotik_router: router },
    is_new: () => !saved,
    set_query: (_, callback) => { query = callback; },
    add_custom_button: (_, callback) => { action = callback; },
    set_intro: message => { intro = message; },
  };
  const frappe = {
    ui: { form: { on: (_, value) => { handlers = value; } } },
    model: { can_create: () => canCreate },
    new_doc: (doctype, { zone }) => { opened = { doctype, zone }; },
  };
  vm.runInNewContext(script, { frappe, __: value => value });
  handlers.setup(frm);
  handlers.refresh(frm);
  return { query, action, intro, opened: () => opened };
}

test("a saved MikroTik zone opens a router draft with its zone prefilled", () => {
  const page = form();
  page.action();
  assert.deepEqual(page.opened(), { doctype: "MikroTik Router", zone: "digissiet-pilot" });
  assert.equal(page.query().filters.zone, "digissiet-pilot");
});

test("router creation is not offered for unsaved, pfSense, or already-linked zones", () => {
  for (const options of [{ saved: false }, { backend: "pfSense" }, { router: "router-1" }]) {
    assert.equal(form(options).action, undefined);
  }
  const page = form({ canCreate: false });
  assert.equal(page.action, undefined);
  assert.match(page.intro, /System Manager/);
});
