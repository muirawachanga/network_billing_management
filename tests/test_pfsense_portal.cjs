const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "../pfsense/portal.html"), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function portal(packages, zone = "default", renderRouterTemplate = false) {
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
  element("portalZone").value = zone;
  const renderedScript = renderRouterTemplate
    ? script.replaceAll("$PORTAL_ZONE$", zone).replaceAll("#PORTAL_ZONE#", zone)
    : script;
  vm.runInContext(renderedScript, context);
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

test("all-letter M-Pesa receipt reaches receipt recovery API", async () => {
  const page = portal(catalogue);
  await page.ready();
  page.element("lookupValue").value = " abcdefghij ";
  page.respond({ status: "NOT_FOUND" });
  await page.run("lookupExistingPayment()");
  assert.equal(page.calls.length, 2);
  assert.equal(page.calls[1].payload.lookup_value, "ABCDEFGHIJ");
  assert.equal(page.calls[1].payload.lookup_type, "mpesa_receipt");
});

test("loads server packages and prices; preserves unavailable state after payment errors", async () => {
  const page = portal(catalogue);
  assert.equal(page.element("payButton").disabled, true);
  await page.ready();
  assert.match(page.calls[0].url, /network_billing_system\.api\.captive_portal\.get_packages$/);
  assert.equal(page.calls[0].payload.zone_id, "default");
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
  assert.equal(page.element("paymentStatus").attributes["data-state"], "error");
  assert.equal(page.element("hidePaymentButton").textContent, "Close and try later");
  assert.match(html, /wait a few minutes, then select Pay again/);
  page.run('setPaymentStatus("Check your phone", "Approve the prompt.", true)');
  assert.equal(page.element("paymentStatus").attributes["data-state"], "info");
  assert.equal(page.element("hidePaymentButton").textContent, "Hide progress");
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

test("customer-facing copy does not expose platform names or voucher implementation", async () => {
  const visibleMarkup = html
    .replace(/<script>[\s\S]*?<\/script>/g, "")
    .replace(/<style>[\s\S]*?<\/style>/g, "")
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<[^>]*>/g, "");
  assert.doesNotMatch(visibleMarkup, /pfsense|frappe/i);
  const withoutComments = script.replace(/^\s*\/\/.*$/gm, "");
  const messages = withoutComments.match(/"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'/g) || [];
  for (const message of messages) {
    if (/https?:|\/api\/|auth_voucher|authVoucher/.test(message)) continue;
    assert.doesNotMatch(message, /pfsense|frappe/i);
  }
});

test("direct voucher login works after billing failure without making any API request", async () => {
  const page = portal([]);
  await page.ready();
  page.reject({ message: "Billing service unavailable" });
  page.element("voucherValue").value = "  Legacy-AbC-123  ";
  const requestsBefore = page.calls.length;
  page.element("voucherForm").listeners.submit({ preventDefault() {} });
  assert.equal(page.calls.length, requestsBefore);
  assert.equal(page.element("authVoucher").value, "Legacy-AbC-123");
  assert.equal(page.element("loginForm").submitted, true);
  assert.equal(page.element("voucherButton").disabled, true);
});

test("blank voucher stays on page and failed submission can be retried", async () => {
  const page = portal([]);
  await page.ready();
  page.element("voucherValue").value = "   ";
  page.run("connectWithVoucher()");
  assert.equal(page.element("loginForm").submitted, undefined);
  assert.match(page.element("voucherNotice").textContent, /Enter your voucher/);
  page.element("voucherValue").value = "code";
  page.element("loginForm").submit = () => { throw new Error("failed"); };
  page.run("connectWithVoucher()");
  assert.equal(page.element("voucherButton").disabled, false);
  assert.equal(page.run("isSubmittingLogin"), false);
  assert.match(page.element("voucherNotice").textContent, /Could not connect/);
});

test("recovery selection displays only the chosen field and action", async () => {
  const page = portal([]);
  await page.ready();
  page.run('selectRecoveryMethod("receipt")');
  assert.equal(page.element("voucherRecovery").hidden, true);
  assert.equal(page.element("receiptRecovery").hidden, false);
  page.run('selectRecoveryMethod("voucher")');
  assert.equal(page.element("voucherRecovery").hidden, false);
  assert.equal(page.element("receiptRecovery").hidden, true);
  assert.doesNotMatch(html, /Good Wi-Fi|Good to go|Fast activation|Easy reconnection/);
});

test("unconfigured zone cannot use default inventory but direct voucher still works", async () => {
  const page = portal(catalogue, "");
  await page.ready();
  assert.equal(page.calls.length, 0);
  assert.equal(page.element("payButton").disabled, true);
  await page.run("lookupExistingPayment()");
  assert.equal(page.calls.length, 0);
  page.element("voucherValue").value = "existing-code";
  page.run("connectWithVoucher()");
  assert.equal(page.element("loginForm").submitted, true);
  assert.equal(page.calls.length, 0);
});

test("reads the zone rendered by the router without a hardcoded JavaScript zone", async () => {
  const page = portal(catalogue, "guest_wifi");
  await page.ready();
  assert.equal(page.calls[0].payload.zone_id, "guest_wifi");
  assert.match(html, /id="portalZone" value="\$PORTAL_ZONE\$"/);
  page.element("phone").value = "0712345678";
  page.element("terms").checked = true;
  page.reject({ message: "test rejection" });
  await page.run("startPayment()");
  assert.equal(page.calls[1].payload.zone_id, "guest_wifi");
});

test("unexpanded router placeholders never reach billing APIs", async () => {
  for (const zone of ["$PORTAL_ZONE$", "#PORTAL_ZONE#", "   "]) {
    const page = portal(catalogue, zone);
    await page.ready();
    assert.equal(page.calls.length, 0);
    await page.run("lookupExistingPayment()");
    assert.equal(page.calls.length, 0);
    assert.equal(page.element("payButton").disabled, true);
  }
});

test("router-wide placeholder replacement does not reject the resolved zone", async () => {
  const page = portal(catalogue, "guest_wifi", true);
  await page.ready();
  assert.equal(page.run("zoneConfigured()"), true);
  assert.equal(page.calls.length, 1);
  assert.equal(page.calls[0].payload.zone_id, "guest_wifi");
  assert.equal(page.element("payButton").disabled, false);
});
