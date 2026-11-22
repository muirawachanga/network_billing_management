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
  let packages = new Set();
  let challenge = null;
  let recoveryToken = null;
  let credentials = null;
  let pollTimer;
  let pollUntil = 0;
  const billing = text => message("billing-message", text);
  const recovery = text => message("recovery-message", text);
  const statusFields = () => recoveryToken ? { recovery_token: recoveryToken }
    : checkout?.paymentToken ? { payment_token: checkout.paymentToken } : null;

  function update() {
    get("pay").disabled = busy || !!checkout || !!recoveryToken || !packages.has(get("package").value);
    get("check").hidden = !statusFields();
    get("check").disabled = busy;
    get("connect").hidden = !credentials;
    get("connect").disabled = busy;
    get("reload-packages").disabled = busy;
    get("send-code").disabled = busy;
    get("otp-form").hidden = !challenge;
    get("verify-code").disabled = busy;
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
    try {
      const results = await post("get_packages");
      if (!Array.isArray(results)) throw new Error("Invalid package list");
      packages = new Set();
      const select = get("package");
      select.replaceChildren();
      for (const item of results) {
        if (!item || !usableToken(item.package_id) || !Number.isFinite(Number(item.price)) ||
            Number(item.price) <= 0) continue;
        const option = document.createElement("option");
        option.value = item.package_id;
        option.textContent = `${item.label || item.package_id} — ${item.currency || "KES"} ${item.price}` +
          (item.available === true ? "" : " (unavailable)");
        option.disabled = item.available !== true;
        if (!option.disabled) packages.add(item.package_id);
        select.append(option);
      }
      select.value = packages.values().next().value || "";
      get("reload-packages").hidden = packages.size > 0;
      message("package-message", packages.size ? "" : "No packages are available right now.");
    } catch {
      get("reload-packages").hidden = false;
      message("package-message", "Packages could not be loaded. You can still sign in with an access code.");
    }
  }

  function schedulePoll() {
    pollTimer = setTimeout(() => busy ? schedulePoll() : run(checkStatus), 5000);
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
    if (recoveryToken) recoveryToken = null;
    else if (allowNewPayment) {
      saveCheckout(null);
      get("reload-packages").hidden = packages.size > 0;
    }
    billing(text);
  }

  async function checkStatus() {
    clearTimeout(pollTimer);
    const fields = statusFields();
    if (!fields) return;
    credentials = null;
    try {
      const result = await post("check_payment_status", fields);
      switch (result?.access_state) {
        case "READY":
          credentials = result.access_ready === true ? Hotspot.splitVoucher(result.voucher) : null;
          if (!credentials) {
            autoConnect = false;
            billing("The service did not return a usable access code. Check access status or contact support. Do not pay again.");
            return;
          }
          billing("Wi-Fi access is ready.");
          if (autoConnect) logIn(credentials);
          autoConnect = false;
          return;
        case "PROVISIONING":
          pollAgain("Payment confirmed. Wi-Fi access is being prepared.");
          return;
        case "EXPIRED":
          stopStatus("This Wi-Fi access has expired.", true);
          return;
        case "REPLACED":
          stopStatus("This access code was replaced. Use SMS recovery to find current access.");
          return;
      }
      switch (result?.status) {
        case "FAILED":
        case "CANCELLED":
          stopStatus("This payment did not complete.", true);
          return;
        case "NOT_FOUND":
        case "INELIGIBLE":
          stopStatus("This payment could not be used here. Try SMS recovery or contact support before paying again.");
          return;
        case "PENDING":
          pollAgain("Waiting for payment confirmation. Approve the M-Pesa prompt on your phone.");
          return;
        default:
          pollAgain("We could not confirm Wi-Fi access yet.");
      }
    } catch {
      pollAgain("The status service could not be reached.");
    }
  }

  function startPolling() {
    pollUntil = Date.now() + 5 * 60 * 1000;
    return checkStatus();
  }

  const uncertain = "We could not confirm the payment request. If you received a prompt or receipt, use SMS recovery or contact support before paying again.";
  submit("purchase", () => run(async () => {
    const packageId = get("package").value;
    const phone = get("phone").value.trim();
    if (checkout || recoveryToken || !phone || !packages.has(packageId)) return;
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
    const receipt = get("receipt").value.replace(/\s+/g, "").toUpperCase();
    if (!receipt) return;
    get("receipt").value = get("otp").value = "";
    challenge = recoveryToken = credentials = null;
    autoConnect = false;
    try {
      const result = await post("begin_mikrotik_recovery", { receipt });
      if (!usableToken(result?.challenge_id)) throw new Error("Missing challenge");
      challenge = result.challenge_id;
      recovery("If the receipt is eligible, an SMS code will be sent to the payment phone.");
    } catch {
      recovery("Could not request a verification code. Try again later.");
    }
  }));

  submit("otp-form", () => run(async () => {
    const otp = get("otp").value.trim();
    if (!challenge || !otp) return;
    get("otp").value = "";
    try {
      const result = await post("verify_mikrotik_recovery", { challenge_id: challenge, otp });
      if (!usableToken(result?.recovery_token)) throw new Error("Missing recovery token");
      challenge = null;
      recoveryToken = result.recovery_token;
      recovery("SMS code verified.");
      autoConnect = true;
      await startPolling();
    } catch {
      recovery("The code could not be verified. Try again or request another SMS.");
    }
  }));

  listen("package", "change", update);
  listen("reload-packages", "click", () => run(loadPackages));
  listen("check", "click", () => run(startPolling));
  listen("connect", "click", () => { if (!busy && credentials) logIn(credentials); });
  get("billing").hidden = get("recovery").hidden = false;
  billing("");
  void run(async () => {
    if (checkout?.paymentToken) {
      await startPolling();
      if (!checkout) await loadPackages();
    } else if (checkout) billing(uncertain);
    else await loadPackages();
  });
})();
