const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const read = file => fs.readFileSync(path.join(__dirname, "../mikrotik", file), "utf8");
const inline = file => read(file).match(/<script>([\s\S]*?)<\/script>/)[1];
const config = { billingOrigin: "https://billing.example.org", zoneId: "test-zone" };
const packages = [
  { package_id: "day", label: "<Day Pass>", price: 30, currency: "KES", available: true },
  { package_id: "evening", label: "Evening Pass", price: 40, currency: "KES", available: true },
  { package_id: "week", price: 100, available: false },
];
const ready = { status: "SUCCESS", access_state: "READY", access_ready: true, voucher: "user_1.Pass.2" };
const chapId = 129;
const challenge = Buffer.from("00112233445566778899aabbccddeeff", "hex");
const octal = bytes => Array.from(bytes, byte => "\\" + byte.toString(8).padStart(3, "0")).join("");
const digest = password => createHash("md5").update(Buffer.concat([
  Buffer.from([chapId]), Buffer.from(password), challenge,
])).digest("hex");

function portal({
  settings = config, storage = new Map(), router = "http://wifi.example.org", action = `${router}/login`,
  chap = true, original = "https://example.org/return?a=1&b=2", responses = {},
} = {}) {
  const elements = new Map();
  function element() {
    return {
      value: "", dataset: {}, listeners: {}, children: [],
      attributes: {},
      setAttribute(name, value) { this.attributes[name] = value; },
      addEventListener(type, handler) { this.listeners[type] = handler; },
      focus() { this.focused = true; },
      append(...children) { this.children.push(...children); },
      replaceChildren() { this.children = []; },
    };
  }
  const el = id => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const requests = [];
  const submissions = [];
  const redirects = [];
  const timers = new Map();
  let nextTimer = 0;
  el("router-login").getAttribute = name => name === "action" && action;
  el("router-login").submit = () => submissions.push({
    action,
    user: el("router-user").value,
    password: el("router-password").value,
    destination: el("router-destination").value,
  });
  el("router-destination").dataset.original = encodeURIComponent(original);
  const context = vm.createContext({
    document: { getElementById: el, createElement: element },
    location: { href: router + "/login", origin: router, replace: url => redirects.push(url) },
    sessionStorage: {
      getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: key => storage.delete(key),
    },
    URL, URLSearchParams, TextEncoder, AbortController,
    setTimeout: (callback, delay) => {
      timers.set(++nextTimer, { callback, delay });
      return nextTimer;
    },
    clearTimeout: id => timers.delete(id),
    fetch: async (url, options) => {
      const method = url.split(".").at(-1);
      requests.push({ url, method, options, fields: Object.fromEntries(options.body) });
      const reply = responses[method] ?? (method === "get_packages" ? packages : null);
      if (reply instanceof Error) throw reply;
      return { ok: true, json: async () => ({ message: reply }) };
    },
    mikrotikPortalConfig: settings,
  });
  context.window = context;
  const rendered = inline("login.html")
    .replace(/\$\(if chap-id\)([\s\S]*?)\$\(endif\)/, chap ? "$1" : "")
    .replace("$(chap-id)", octal([chapId]))
    .replace("$(chap-challenge)", typeof chap === "string" ? chap : octal(challenge));
  vm.runInContext(rendered, context);
  for (const file of ["hotspot.js", "portal.js"]) vm.runInContext(read(file), context);
  return {
    el, requests, submissions, redirects, storage, timers,
    send: (id, type = "submit", event = {}) => el(id).listeners[type]({ preventDefault() {}, ...event }),
    settled: () => new Promise(resolve => setImmediate(resolve)),
    tick: () => {
      const [id, timer] = [...timers].find(([, timer]) => timer.delay === 5000);
      timers.delete(id);
      return timer.callback();
    },
    redirect: destination => {
      el("redirect").dataset.destination = destination;
      vm.runInContext(inline("alogin.html"), context);
    },
  };
}

test("manual CHAP login works without billing configuration and uses RFC MD5 bytes", () => {
  const page = portal({ settings: null });
  assert.equal(page.el("recovery-tab").attributes["aria-selected"], "true");
  assert.equal(page.el("recovery").hidden, false);
  assert.equal(page.el("access-panel").hidden, true);
  const password = "páss." + "a".repeat(65);
  page.el("voucher").value = "Alice." + password;
  page.send("manual");
  assert.deepEqual(page.submissions, [{
    action: "http://wifi.example.org/login", user: "Alice", password: digest(password),
    destination: "https://example.org/return?a=1&b=2",
  }]);
  assert.equal(page.el("voucher").value, "");
  assert.equal(page.el("router-password").value, "");
  assert.equal(page.requests.length, 0);
  assert.equal(page.storage.size, 0);
});

test("the Already paid tab groups access code entry with receipt recovery", () => {
  const html = read("login.html");
  assert.match(html, /id="recovery-tab"[^>]*>Already paid\?<\/button>/);
  const [purchase, recovery] = html.split('<div id="recovery"');
  assert.match(purchase, /id="purchase"/);
  assert.doesNotMatch(purchase, /id="manual"/);
  assert.match(recovery, /id="manual"[\s\S]*id="receipt-form"/);
});

test("malformed CHAP, plain HTTP PAP, and off-router form actions fail closed", () => {
  for (const options of [
    { chap: "short", router: "https://wifi.example.org" },
    { chap: false },
    { action: "https://other.example.org/login" },
  ]) {
    const page = portal({ settings: null, ...options });
    page.el("voucher").value = "Username.Secret";
    page.send("manual");
    assert.equal(page.submissions.length, 0);
    assert.match(page.el("login-message").textContent, /could not start/);
  }
  const page = portal({ settings: null, chap: false, router: "https://wifi.example.org" });
  page.el("voucher").value = "Username.Secret";
  page.send("manual");
  assert.equal(page.submissions[0].password, "Secret");
  assert.equal(page.submissions[0].action, "https://wifi.example.org/login");
});

test("checkout waits for provisioning, sends a single scoped payment and resumes without a login loop", async () => {
  const responses = {
    start_payment: { payment_token: "payment-bearer" },
    check_payment_status: { ...ready, access_state: "PROVISIONING", access_ready: false },
  };
  const page = portal({ responses });
  await page.settled();
  const [day, evening, week] = page.el("package-grid").children;
  assert.deepEqual(day.children.map(span => span.textContent), ["<Day Pass>", "KES 30"]);
  assert.equal(day.attributes["aria-pressed"], "true");
  assert.equal(week.disabled, true);
  assert.match(week.children[1].textContent, /Unavailable/);
  evening.listeners.click();
  assert.equal(day.attributes["aria-pressed"], "false");
  assert.equal(evening.attributes["aria-pressed"], "true");
  page.el("phone").value = "0700000000";
  await page.send("purchase");
  await page.send("purchase");
  assert.equal(page.submissions.length, 0);
  assert.match(page.el("billing-message").textContent, /being prepared/);
  assert.deepEqual(page.requests.map(request => [request.method, request.fields]), [
    ["get_packages", { zone_id: "test-zone" }],
    ["start_payment", { package_id: "evening", phone: "0700000000", zone_id: "test-zone" }],
    ["check_payment_status", { payment_token: "payment-bearer", zone_id: "test-zone" }],
  ]);
  responses.check_payment_status = ready;
  await page.tick();
  assert.equal(page.submissions[0].user, "user_1");
  assert.equal(page.submissions[0].password, digest("Pass.2"));
  assert.deepEqual([...page.storage.values()].map(value => JSON.parse(value)), [{ paymentToken: "payment-bearer" }]);

  const reload = portal({ responses, storage: page.storage });
  await reload.settled();
  assert.deepEqual(reload.requests.map(r => r.method), ["check_payment_status"]);
  assert.equal(reload.submissions.length, 0);
  reload.send("connect", "click");
  assert.equal(reload.submissions.length, 1);

  const otherZone = portal({ settings: { ...config, zoneId: "another-zone" }, storage: page.storage });
  await otherZone.settled();
  assert.deepEqual(otherZone.requests.map(r => r.method), ["get_packages"]);
});

test("unconfirmed checkout cannot be duplicated after reload", async () => {
  const page = portal({ responses: { start_payment: new Error("Network error") } });
  await page.settled();
  page.el("phone").value = "0700000000";
  await page.send("purchase");
  const reload = portal({ storage: page.storage });
  await reload.settled();
  assert.equal(reload.el("pay").disabled, true);
  assert.equal(reload.requests.length, 0);
  assert.match(reload.el("billing-message").textContent, /contact support/);
});

test("a resumed pending checkout can load packages once its access expires", async () => {
  const storage = new Map([[
    JSON.stringify(["mikrotik-checkout", config.billingOrigin, config.zoneId]),
    JSON.stringify({ paymentToken: "old-token" }),
  ]]);
  const responses = { check_payment_status: { status: "PENDING", access_ready: false } };
  const page = portal({ storage, responses });
  await page.settled();
  responses.check_payment_status = { access_state: "EXPIRED", access_ready: false };
  await page.tick();
  assert.equal(page.el("reload-packages").hidden, false);
  assert.equal(storage.size, 0);
  await page.send("reload-packages", "click");
  assert.equal(page.el("pay").disabled, false);
});

test("expired, replaced, and malformed READY responses do not submit stale access", async () => {
  for (const [response, expected] of [
    [{ ...ready, access_state: "EXPIRED" }, /expired/],
    [{ ...ready, access_state: "REPLACED" }, /replaced/],
    [{ ...ready, access_ready: false }, /usable access code/],
    [{ ...ready, voucher: "not-split" }, /usable access code/],
  ]) {
    const page = portal({ responses: {
      start_payment: { payment_token: "token" }, check_payment_status: response,
    } });
    await page.settled();
    page.el("phone").value = "0700000000";
    await page.send("purchase");
    assert.equal(page.submissions.length, 0);
    assert.equal(page.timers.size, 0);
    assert.match(page.el("billing-message").textContent, expected);
    assert.equal(page.el("pay").disabled, response.access_state !== "EXPIRED");
  }
});

test("pasted payment recovery submits only the receipt and original checkout token", async () => {
  const storage = new Map([[
    JSON.stringify(["mikrotik-checkout", config.billingOrigin, config.zoneId]),
    JSON.stringify({ paymentToken: "original-token" }),
  ]]);
  const responses = {
    recover_mikrotik_payment: { access_state: "PROVISIONING" },
    check_payment_status: ready,
  };
  const page = portal({ storage, responses });
  await page.settled();
  assert.equal(page.el("recovery").hidden, true);
  page.send("access-tab", "keydown", { key: "ArrowRight" });
  assert.equal(page.el("recovery-tab").attributes["aria-selected"], "true");
  assert.equal(page.el("recovery-tab").focused, true);
  assert.equal(page.el("recovery").hidden, false);
  assert.equal(page.el("access-panel").hidden, true);
  page.el("receipt").value = "ABC123DEF Confirmed. Ksh30.00 sent. Phone 0700000000.";
  await page.send("receipt-form");
  assert.deepEqual(page.requests.slice(1).map(request => [request.method, request.fields]), [
    ["recover_mikrotik_payment", { receipt: "ABC123DEF", payment_token: "original-token", zone_id: "test-zone" }],
  ]);
  assert.match(page.el("recovery-message").textContent, /being prepared/);
  assert.equal(page.el("recovery-check").hidden, false);
  assert.equal(page.submissions.length, 0);
  responses.recover_mikrotik_payment = ready;
  await page.send("recovery-check", "click");
  assert.equal(page.el("recovery-connect").hidden, false);
  page.send("access-tab", "click");
  assert.equal(page.el("recovery").hidden, true);
  assert.equal(page.el("access-panel").hidden, false);
  for (const { url, method, options } of page.requests) {
    assert.equal(url, `${config.billingOrigin}/api/method/network_billing_system.api.captive_portal.${method}`);
    assert.equal(options.method, "POST");
    assert.equal(options.credentials, "omit");
    assert.equal(options.redirect, "error");
  }
  assert.equal(page.el("receipt").value, "");
  assert.equal(page.storage.size, 1);
  assert.equal(page.submissions[0].password, digest("Pass.2"));
});

test("ambiguous receipts and missing checkout proof never call paid recovery", async () => {
  const page = portal();
  await page.settled();
  page.el("receipt").value = "ABC123DEF Confirmed. DEF123ABC Confirmed.";
  await page.send("receipt-form");
  assert.match(page.el("recovery-message").textContent, /one receipt/);
  page.el("receipt").value = "abc123def";
  await page.send("receipt-form");
  assert.match(page.el("recovery-message").textContent, /Contact support/);
  assert.deepEqual(page.requests.map(request => request.method), ["get_packages"]);
});

test("bundle uses local assets and success redirects only to a safe router-supplied web destination", () => {
  for (const file of ["login.html", "error.html", "alogin.html", "logout.html"]) {
    for (const [, asset] of read(file).matchAll(/(?:src|href)="([^"]+)"/g)) {
      assert.doesNotMatch(asset, /^(?:[a-z]+:|\/\/)/i);
      if (!asset.startsWith("/")) assert.ok(read(asset).length);
    }
  }
  for (const unsafe of ["javascript:alert(1)", "//example.org", "https://user:secret@example.org/", "http://wifi.example.org/login", "$(link-redirect-esc)"]) {
    const page = portal({ settings: null });
    page.redirect(encodeURIComponent(unsafe));
    assert.deepEqual(page.redirects, []);
  }
  const page = portal({ settings: null });
  page.redirect(encodeURIComponent("https://example.org/landing?foo=bar"));
  assert.deepEqual(page.redirects, ["https://example.org/landing?foo=bar"]);
  assert.match(read("alogin.html"), /href="\/status"/);
  assert.match(read("error.html"), /href="\/login"/);
});

async function featured({ connected = false, reducedMotion = false, banners = [
  { title: "Offer", image: "/files/offer.png", url: "https://advertiser.example/", display_price: "KSh 200" },
  { title: "Invalid", image: "/files/../private.png", url: "javascript:alert(1)" },
] } = {}) {
  function element() {
    const classes = new Set();
    return {
      children: [],
      listeners: {},
      attributes: {},
      classList: { add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name) },
      append(child) { this.children.push(child); },
      addEventListener(type, listener) { this.listeners[type] = listener; },
      querySelector: () => null,
      setAttribute(name, value) { this.attributes[name] = value; },
      removeAttribute(name) { delete this.attributes[name]; },
    };
  }
  const elements = new Map();
  const el = id => {
    if (!elements.has(id)) elements.set(id, { ...element(), hidden: true });
    return elements.get(id);
  };
  let tick;
  const context = vm.createContext({
    URL, URLSearchParams, AbortController, setTimeout, clearTimeout,
    setInterval: callback => { tick = callback; },
    clearInterval: () => { tick = undefined; },
    matchMedia: () => ({ matches: reducedMotion }),
    document: {
      getElementById: el,
      createElement: tag => Object.assign(element(), {
        tag,
          querySelector(selector) {
            return this.children.flatMap(child => child.children).find(child => child.tag === selector);
          },
      }),
    },
    fetch: async () => ({ ok: true, json: async () => ({ message: banners }) }),
  });
  context.window = context;
  vm.runInContext(read("banners.js"), context);
  const shown = await context.HotspotBanners.render({ origin: config.billingOrigin, zone: config.zoneId }, connected);
  return { el, shown, get tick() { return tick; } };
}

test("one dot per featured tile tracks rotation and selects tiles; only signed-in ads link", async () => {
  for (const connected of [false, true]) {
    const page = await featured({ connected, reducedMotion: connected });
    const { el } = page;
    assert.equal(page.shown, true);
    const [first, second] = el("banner-tiles").children;
    assert.equal(first.children[0].src, config.billingOrigin + "/files/offer.png");
    assert.equal(first.tag, connected ? "a" : "article");
    assert.equal(first.href, connected ? "https://advertiser.example/" : undefined);
    assert.equal(first.children[1].children.find(child => child.className === "banner-price").textContent, "KSh 200");
    assert.equal(second.href, undefined);
    assert.deepEqual(second.children[0].children.map(child => child.tag), ["h3"]);
    const dots = el("banner-dots").children;
    assert.equal(dots.length, 2);
    assert.equal(dots[0].attributes["aria-current"], "true");
    if (connected) {
      assert.equal(page.tick, undefined); // Reduced motion still permits manual browsing.
      dots[1].listeners.click();
    } else {
      page.tick();
    }
    assert.equal(second.classList.contains("banner-active"), true);
    assert.equal(dots[1].attributes["aria-current"], "true");
    assert.equal(dots[0].attributes["aria-current"], undefined);
    dots[0].listeners.click();
    assert.equal(first.classList.contains("banner-active"), true);
  }
});

test("touch hold pauses rotation; horizontal swipes navigate without following links", async () => {
  const page = await featured({ connected: true, banners: [
    { title: "First" }, { title: "Second" }, { title: "Third" },
  ] });
  const { listeners } = page.el("banner-tiles");
  const dots = page.el("banner-dots").children;
  const active = () => dots.findIndex(dot => dot.attributes["aria-current"] === "true");
  const point = { identifier: 0, clientX: 100, clientY: 100 };
  const start = () => listeners.touchstart({ touches: [point] });
  const end = (clientX, clientY) => {
    let cancelled = false;
    listeners.touchend({
      touches: [], changedTouches: [{ ...point, clientX, clientY }],
      preventDefault: () => { cancelled = true; },
    });
    return cancelled;
  };
  start();
  assert.equal(page.tick, undefined);
  assert.equal(end(102, 100), false);
  assert.equal(active(), 0);
  assert.equal(typeof page.tick, "function");
  start();
  assert.equal(end(110, 165), false);
  assert.equal(active(), 0);
  start();
  assert.equal(end(160, 108), true);
  assert.equal(active(), 1);
  start();
  assert.equal(end(40, 110), true);
  assert.equal(active(), 0);
  start();
  listeners.touchcancel({ touches: [] });
  page.tick();
  assert.equal(active(), 1);
});

test("single featured tile has one static dot; empty featured section stays hidden", async () => {
  const single = await featured({ banners: [{ title: "Only item" }] });
  assert.equal(single.shown, true);
  assert.equal(single.el("banner-dots").children.length, 1);
  assert.equal(single.tick, undefined);
  const empty = await featured({ banners: [] });
  assert.equal(empty.shown, false);
  assert.equal(empty.el("banner-dots").hidden, true);
});
