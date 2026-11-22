"use strict";

(() => {
  const get = id => document.getElementById(id);
  const listen = (id, event, handler) => get(id).addEventListener(event, handler);
  const submit = (id, handler) => listen(id, "submit", event => {
    event.preventDefault();
    return handler();
  });
  const message = (id, value) => { get(id).textContent = value; };
  let autoConnect = false; // Reloads resume checking, but never automatically repeat router login.
  function logIn(credentials) {
    try {
      Hotspot.submitLogin(credentials);
      message("login-message", "Signing in to Wi-Fi…");
    } catch {
      message("login-message", "The router sign-in could not start. Reopen the Wi-Fi login page and try again.");
    }
  }
  if (window.Hotspot) {
    submit("manual", () => {
      const credentials = Hotspot.splitVoucher(get("voucher").value.trim());
      if (!credentials) return message("login-message", "Enter an access code in username.password format.");
      get("voucher").value = "";
      autoConnect = false;
      logIn(credentials);
    });
    get("manual-submit").disabled = false;
  }

  function configuration(raw) {
    try {
      if (typeof raw?.billingOrigin !== "string" || typeof raw.zoneId !== "string") return null;
      const url = new URL(raw.billingOrigin);
      if (url.protocol === "https:" && (raw.billingOrigin === url.origin || raw.billingOrigin === url.origin + "/") &&
          raw.zoneId.trim()) return { origin: url.origin, zone: raw.zoneId.trim() };
    } catch { /* Billing is optional. */ }
    return null;
  }
  const config = configuration(window.mikrotikPortalConfig);
  if (config) window.HotspotBanners?.render(config, false);
  if (!config || !window.Hotspot) return;
  const storageKey = JSON.stringify(["mikrotik-checkout", config.origin, config.zone]);
  const usableToken = value => typeof value === "string" && value.trim().length > 0;
  let checkout = null;
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey));
    if (saved && (usableToken(saved.paymentToken) || saved.paymentToken === null)) {
      checkout = { paymentToken: saved.paymentToken };
    }
  } catch { /* Some captive browsers block session storage. */ }

  function saveCheckout(value) {
    checkout = value;
    try {
      if (checkout) sessionStorage.setItem(storageKey, JSON.stringify(checkout));
      else sessionStorage.removeItem(storageKey);
    } catch { /* Continue with in-memory state. */ }
  }

  async function post(method, fields = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const result = await fetch(config.origin + "/api/method/network_billing_system.api.captive_portal." + method, {
        method: "POST",
        body: new URLSearchParams({ ...fields, zone_id: config.zone }),
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer",
      });
      if (!result.ok) throw new Error("Billing request failed");
      const body = await result.json();
      if (!body || body.exc || !Object.prototype.hasOwnProperty.call(body, "message")) {
        throw new Error("Invalid billing response");
      }
      return body.message;
    } finally {
      clearTimeout(timeout);
    }
  }

  let busy = false;
  const packages = new Map();
  let selectedPackage = null;
  let recoveryReceipt = null;
  let credentials = null;
  let pollTimer;
  let pollUntil = 0;
  const billing = text => message("billing-message", text);
  const recovery = text => message("recovery-message", text);
  const statusFields = () => checkout?.paymentToken ? { payment_token: checkout.paymentToken } : null;

  function receiptFrom(input) {
    if (input.length > 2000) return null;
    const text = input.trim().toUpperCase();
    const valid = value => /^(?=.*[A-Z])[A-Z0-9]{8,20}$/.test(value);
    if (valid(text)) return text;
    const matches = Array.from(text.matchAll(/\b([A-Z0-9]{8,20})[.\s]+CONFIRMED\b/g),
      match => match[1]);
    return matches.length === 1 && valid(matches[0]) ? matches[0] : null;
  }

  function update() {
    const locked = busy || !!checkout;
    get("pay").disabled = locked || !packages.get(selectedPackage)?.available;
    for (const [id, { button, available }] of packages) {
      button.disabled = locked || !available;
      button.setAttribute("aria-pressed", String(id === selectedPackage));
    }
    get("check").hidden = !statusFields();
    get("check").disabled = busy;
    get("connect").hidden = !credentials;
    get("connect").disabled = busy;
    get("reload-packages").disabled = busy;
    get("recover-payment").disabled = busy;
    get("billing-loader").hidden = !busy && !pollTimer;
  }

  async function run(action) {
    if (busy) return;
    busy = true;
    update();
    try {
      await action();
    } finally {
      busy = false;
      update();
    }
  }

  async function loadPackages() {
    const grid = get("package-grid");
    grid.setAttribute("aria-busy", "true");
    grid.replaceChildren();
    packages.clear();
    selectedPackage = null;
    try {
      const results = await post("get_packages");
      if (!Array.isArray(results)) throw new Error("Invalid package list");
      for (const item of results) {
        if (!item || !usableToken(item.package_id) || !Number.isFinite(Number(item.price)) ||
            Number(item.price) <= 0 || packages.has(item.package_id)) continue;
        const available = item.available === true;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "package-option";
        button.disabled = true;
        const label = document.createElement("span");
        label.textContent = item.label || item.package_id;
        const price = document.createElement("span");
        price.className = "package-price";
        price.textContent = `${item.currency || "KES"} ${item.price}${available ? "" : " · Unavailable"}`;
        button.append(label, price);
        button.addEventListener("click", () => {
          if (busy || checkout || !available) return;
          selectedPackage = item.package_id;
          update();
        });
        packages.set(item.package_id, { button, available });
        grid.append(button);
        if (available && selectedPackage === null) selectedPackage = item.package_id;
      }
      get("reload-packages").hidden = selectedPackage !== null;
      message("package-message", selectedPackage !== null ? "" : "No packages are available right now.");
    } catch {
      get("reload-packages").hidden = false;
      message("package-message", "Packages could not be loaded. You can still sign in with an access code.");
    } finally {
      grid.setAttribute("aria-busy", "false");
    }
  }

  function schedulePoll() {
    pollTimer = setTimeout(() => {
      pollTimer = null;
      return busy ? schedulePoll() : run(checkStatus);
    }, 5000);
  }

  function pollAgain(text) {
    if (Date.now() < pollUntil) {
      schedulePoll();
      billing(text);
    } else {
      billing(text + " Use Check access status to try again. Do not pay again.");
    }
  }

  function stopStatus(text, allowNewPayment = false) {
    autoConnect = false;
    recoveryReceipt = null;
    if (allowNewPayment) {
      saveCheckout(null);
      get("reload-packages").hidden = selectedPackage !== null;
    }
    billing(text);
  }

  async function checkStatus() {
    clearTimeout(pollTimer);
    pollTimer = null;
    const fields = statusFields();
    if (!fields) return;
    credentials = null;
    try {
      const recovering = !!recoveryReceipt;
      const result = recovering
        ? await post("recover_mikrotik_payment", { ...fields, receipt: recoveryReceipt })
        : await post("check_payment_status", fields);
      switch (result?.access_state) {
        case "READY":
          credentials = result.access_ready === true ? Hotspot.splitVoucher(result.voucher) : null;
          if (!credentials) {
            autoConnect = false;
            billing("The service did not return a usable access code. Check access status or contact support. Do not pay again.");
            return;
          }
          billing("Wi-Fi access is ready.");
          if (recovering) recovery("Payment matched. Wi-Fi access is ready.");
          recoveryReceipt = null;
          if (autoConnect) logIn(credentials);
          autoConnect = false;
          return;
        case "PROVISIONING":
          if (recovering) recovery("Payment matched. Preparing Wi-Fi access. Do not pay again.");
          pollAgain("Payment confirmed. Wi-Fi access is being prepared.");
          return;
        case "EXPIRED":
          if (recovering) recovery("This Wi-Fi access has expired.");
          stopStatus("This Wi-Fi access has expired.", true);
          return;
        case "REPLACED":
          stopStatus("This access code was replaced. Recover your payment here or contact support.");
          return;
      }
      switch (result?.status) {
        case "FAILED":
        case "CANCELLED":
          stopStatus("This payment did not complete.", true);
          return;
        case "NOT_FOUND":
        case "INELIGIBLE":
          if (recovering) recovery("We could not match this receipt to your original payment in this tab. Check the receipt or contact support.");
          stopStatus("Access was not confirmed. Do not pay again until you have checked your payment.");
          return;
        case "PENDING":
          pollAgain("Waiting for payment confirmation. Approve the M-Pesa prompt on your phone.");
          return;
        default:
          pollAgain("We could not confirm Wi-Fi access yet.");
      }
    } catch {
      if (recoveryReceipt) recovery("The recovery service could not be reached. Check access status to retry.");
      pollAgain("The status service could not be reached.");
    }
  }

  function startPolling() {
    pollUntil = Date.now() + 5 * 60 * 1000;
    return checkStatus();
  }

  const uncertain = "We could not confirm the payment request. If you received a prompt or receipt, contact support before paying again.";
  submit("purchase", () => run(async () => {
    const packageId = selectedPackage;
    const phone = get("phone").value.trim();
    if (checkout || !phone || !packages.get(packageId)?.available) return;
    // A timeout or reload during STK initiation must not silently enable another purchase.
    saveCheckout({ paymentToken: null });
    get("phone").value = "";
    billing("Requesting payment…");
    try {
      const result = await post("start_payment", { phone, package_id: packageId });
      if (!usableToken(result?.payment_token)) throw new Error("Missing payment token");
      saveCheckout({ paymentToken: result.payment_token });
      autoConnect = true;
      await startPolling();
    } catch {
      billing(uncertain);
    }
  }));

  submit("receipt-form", () => run(async () => {
    const receipt = receiptFrom(get("receipt").value);
    if (!receipt) return recovery("Enter one receipt code or paste one M-Pesa confirmation message.");
    get("receipt").value = "";
    if (!checkout?.paymentToken) {
      return recovery("The original checkout is not available in this tab. Contact support; do not pay again.");
    }
    recoveryReceipt = receipt;
    recovery("Checking your payment…");
    autoConnect = true;
    await startPolling();
  }));

  listen("reload-packages", "click", () => run(loadPackages));
  listen("check", "click", () => run(startPolling));
  listen("connect", "click", () => { if (!busy && credentials) logIn(credentials); });
  get("billing").hidden = get("recovery").hidden = false;
  billing(checkout ? "" : "Loading Wi-Fi packages…");
  void run(async () => {
    if (checkout?.paymentToken) {
      await startPolling();
      if (!checkout) await loadPackages();
    } else if (checkout) billing(uncertain);
    else {
      await loadPackages();
      billing("");
    }
  });
})();
