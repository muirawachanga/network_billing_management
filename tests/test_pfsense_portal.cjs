const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "../pfsense/portal.html"), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function portal(packages) {
  class Element {
    constructor() {
      this.children = [];
      this.dataset = {};
      this.attributes = {};
      this.listeners = {};
      this.classList = { add() {}, remove() {}, toggle() {} };
      this.value = "";
      this.textContent = "";
    }
    setAttribute(key, value) { this.attributes[key] = value; }
    getAttribute(key) { return this.attributes[key]; }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(event, handler) { this.listeners[event] = handler; }
    append(...children) { this.children.push(...children); }
    replaceChildren() { this.children = []; }
    querySelectorAll() { return this.children; }
    querySelector() { return new Element(); }
    click() { if (!this.disabled) this.listeners.click?.(); }
    focus() {}
    showModal() { this.open = true; }
    submit() { this.submitted = true; }
  }
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const calls = [];
  let response = packages;
  let ok = true;
  const context = vm.createContext({
    document: {
      getElementById: element,
      createElement: () => new Element(),
      querySelectorAll: () => element("packageGrid").children,
    },
    AbortController, console,
    setTimeout: () => 1,
    clearTimeout() {},
    fetch: async (url, options) => {
      calls.push({ url, payload: JSON.parse(options.body) });
      return { ok, json: async () => ok ? ({ message: response }) : response };
    },
  });
  vm.runInContext(script, context);
  return {
    element, calls,
    run: expression => vm.runInContext(expression, context),
    respond: value => { response = value; },
    reject: value => { response = value; ok = false; },
    ready: async () => { await new Promise(resolve => setImmediate(resolve)); },
  };
}

const catalogue = [
  { package_id: "custom_8hrs", label: "8 Hours", price: 35, currency: "KES", available: true },
  { package_id: "sold_out", label: "2 Hours", price: 10, currency: "KES", available: false },
];

test("loads server packages and prices; preserves unavailable state after payment errors", async () => {
  const page = portal(catalogue);
  assert.equal(page.element("payButton").disabled, true);
  await page.ready();
  assert.match(page.calls[0].url, /network_billing_system\.api\.captive_portal\.get_packages$/);
  assert.equal(page.element("payButtonText").textContent, "Pay KSh 35 with M-Pesa");
  assert.equal(page.element("payButton").disabled, false);
  const sold = page.element("packageGrid").children[1];
  page.run("setPaymentBusy(true); setPaymentBusy(false)");
  assert.equal(sold.disabled, true);
  sold.click();
  assert.equal(page.run("selectedPackage"), "custom_8hrs");
  assert.equal(page.run("REQUEST_TIMEOUT_MS"), 40000);
});

test("empty or malformed catalogue blocks payment and permits reload", async () => {
  for (const catalogue of [[], { invalid: true }, [{ price: -1 }]]) {
    const page = portal(catalogue);
    await page.ready();
    assert.equal(page.element("payButton").disabled, true);
    await page.run("startPayment()");
    assert.equal(page.calls.length, 1);
    assert.match(page.element("payNotice").textContent, /Select an available package/);
    page.respond([{
      package_id: "new", label: "New", price: 5, currency: "KES", available: true,
    }]);
    await page.run("loadPackages()");
    assert.equal(page.element("payButton").disabled, false);
  }
});

test("ineligible access stops status polling without enabling another payment", async () => {
  const page = portal(catalogue);
  await page.ready();
  page.respond({ status: "INELIGIBLE", customer_message: "Contact support.", access_ready: false });
  await page.run(`
    activePaymentToken = "token";
    pollingStartedAt = Date.now();
    setPaymentBusy(true);
    pollPaymentStatus();
  `);
  assert.equal(page.element("paymentStatusTitle").textContent, "Payment confirmed, access unavailable");
  assert.equal(page.run("pollingTimer"), null);
  assert.equal(page.element("payButton").disabled, true);
  assert.equal(page.element("loginForm").submitted, undefined);
});

test("voucher submission preserves pfSense fields and does not load external assets", async () => {
  const page = portal(catalogue);
  await page.ready();
  page.run('submitVoucher(" voucher-from-server ")');
  assert.equal(page.element("authVoucher").value, "voucher-from-server");
  assert.equal(page.element("loginForm").submitted, true);
  assert.match(html, /action="\$PORTAL_ACTION\$"/);
  assert.match(html, /name="redirurl"[^>]*value="\$PORTAL_REDIRURL\$"/);
  assert.match(html, /name="accept" value="Login"/);
  assert.doesNotMatch(html, /<script[^>]+src=|<link[^>]+href=/i);
});

test("configuration failure shows a friendly message without an ambiguous-payment warning", async () => {
  const page = portal(catalogue);
  await page.ready();
  page.element("phone").value = "0712345678";
  page.element("terms").checked = true;
  page.reject({
    _server_messages: JSON.stringify([JSON.stringify({
      message: "Configure the Kopo Kopo environment and STK till number first.",
      title: "Message", indicator: "red", raise_exception: 1,
    })]),
    exception: "frappe.exceptions.ValidationError: internal details",
  });
  await page.run("startPayment()");
  assert.equal(page.element("paymentStatusTitle").textContent, "Payments temporarily unavailable");
  assert.match(page.element("paymentStatusCopy").textContent, /No payment was requested/);
  assert.doesNotMatch(page.element("paymentStatusCopy").textContent, /Kopo|raise_exception|prompt appeared/);
  assert.equal(page.element("payButton").disabled, false);
});

test("unknown and malformed server errors never expose internal details or claim no payment", async () => {
  const page = portal(catalogue);
  await page.ready();
  for (const payload of [
    { _server_messages: "invalid JSON", exception: "private traceback" },
    { message: "database password details" },
    { _server_messages: JSON.stringify([JSON.stringify({ message: "unexpected failure" })]) },
  ]) {
    const error = page.run(`responseError(${JSON.stringify(payload)})`);
    assert.match(error.message, /contact support/);
    assert.equal(error.paymentNotStarted, undefined);
    assert.doesNotMatch(error.message, /traceback|password|unexpected failure|No payment was requested/);
  }
});
