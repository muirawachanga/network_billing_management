"use strict";

(() => {
  const get = id => document.getElementById(id);
  const listen = (id, event, handler) => get(id).addEventListener(event, handler);
  const submit = (id, handler) => listen(id, "submit", event => {
    event.preventDefault();
    return handler();
  });
  const message = (id, value) => { get(id).textContent = value; };
  const tabs = [get("access-tab"), get("recovery-tab")];
  const panels = [get("access-panel"), get("recovery")];
  function selectTab(index) {
    tabs.forEach((tab, i) => {
      tab.setAttribute("aria-selected", String(i === index));
      tab.tabIndex = i === index ? 0 : -1;
      panels[i].hidden = i !== index;
    });
  }
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => selectTab(i));
    tab.addEventListener("keydown", event => {
      const next = { ArrowLeft: 1 - i, ArrowRight: 1 - i, Home: 0, End: 1 }[event.key];
      if (next === undefined) return;
      event.preventDefault();
      selectTab(next);
      tabs[next].focus();
    });
  });
  selectTab(0);
  get("portal-tabs").hidden = false;
  let autoConnect = false; // Reloads resume checking, but never automatically repeat router login.
  function logIn(credentials, messageId = "login-message") {
    try {
      Hotspot.submitLogin(credentials);
      message(messageId, "Signing in to Wi-Fi…");
    } catch {
      message(messageId, "The router sign-in could not start. Reopen the Wi-Fi login page and try again.");
    }
  }
  if (window.Hotspot) {
    submit("manual", () => {
      const credentials = Hotspot.splitVoucher(get("voucher").value.trim());
      if (!credentials) return message("login-message", "Check your Wi-Fi access code and try again.");
      get("voucher").value = "";
      autoConnect = false;
      logIn(credentials);
    });
    get("manual-submit").disabled = false;
  }

  function billingOrigin(value, protocol) {
    try {
      const url = new URL(value);
      if (url.protocol === protocol && (value === url.origin || value === url.origin + "/")) return url.origin;
    } catch { /* Billing is optional. */ }
    return null;
  }
  const settings = window.mikrotikPortalConfig;
  const zone = typeof settings?.zoneId === "string" ? settings.zoneId.trim() : "";
  const checkoutOrigin = billingOrigin(settings?.billingOrigin, "https:");
  const origin = checkoutOrigin || billingOrigin(settings?.billingOriginLocal, "http:");
  const preview = !checkoutOrigin;
  window.HotspotBanners?.preview();
  if (!origin || !zone || !window.Hotspot) {
    selectTab(1);
    message("recovery-message", "Online recovery is unavailable. Use an existing access code or contact support.");
    return;
  }
  const storageKey = JSON.stringify(["mikrotik-checkout", checkoutOrigin, zone]);
  const usableToken = value => typeof value === "string" && value.trim().length > 0;
  let checkout = null;
  try {
    const saved = preview ? null : JSON.parse(sessionStorage.getItem(storageKey));
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
    if (preview && method !== "get_packages") throw new Error("Checkout requires HTTPS");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const result = await fetch(origin + "/api/method/network_billing_system.api.captive_portal." + method, {
        method: "POST",
        body: new URLSearchParams({ ...(preview ? {} : fields), zone_id: zone }),
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
  let accessCode = "";
  let recoveryReady = false;
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
    get("pay").disabled = preview || locked || !packages.get(selectedPackage)?.available;
    for (const [id, { button, available }] of packages) {
      button.disabled = locked || !available;
      button.setAttribute("aria-pressed", String(id === selectedPackage));
    }
    get("check").hidden = !statusFields();
    get("check").disabled = busy;
    get("auto-connect").disabled = locked;
    get("connect").hidden = !credentials;
    get("connect").disabled = busy;
    get("reload-packages").disabled = busy;
    get("recover-payment").disabled = busy;
    get("recovery-check").hidden = !recoveryReceipt;
    get("recovery-check").disabled = busy;
    get("recovery-connect").hidden = !recoveryReady;
    get("recovery-connect").disabled = busy;
    get("access-code-panel").hidden = !accessCode;
    message("access-code", accessCode);
    get("recovery-code-panel").hidden = !recoveryReady;
    message("recovery-code", recoveryReady ? accessCode : "");
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
      message("package-message", "Packages could not be loaded. Select “Already paid?” to use an access code.");
    } finally {
      grid.setAttribute("aria-busy", "false");
    }
  }

  function schedulePoll(delay) {
    pollTimer = setTimeout(() => {
      pollTimer = null;
      return busy ? schedulePoll(delay) : run(checkStatus);
    }, delay);
  }

  function pollAgain(text, delay = 5000) {
    const polling = Date.now() < pollUntil;
    if (polling) schedulePoll(delay);
    billing(text + (polling ? "" : " Use Check access status to try again. Do not pay again."));
    if (recoveryReceipt) {
      recovery(text + (polling ? " Do not pay again." : " Use Check recovery status to try again. Do not pay again."));
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
    accessCode = "";
    recoveryReady = false;
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
            if (recovering) recovery("The service did not return a usable access code. Check recovery status or contact support.");
            return;
          }
          billing("Wi-Fi access is ready.");
          accessCode = result.access_code || result.voucher;
          recoveryReady = recovering;
          if (recovering) recovery("Payment matched. Wi-Fi access is ready.");
          recoveryReceipt = null;
          if (autoConnect) logIn(credentials, recovering ? "recovery-message" : "billing-message");
          autoConnect = false;
          return;
        case "PROVISIONING":
          pollAgain("Payment confirmed. Wi-Fi access is being prepared.", 1000);
          return;
        case "EXPIRED":
          if (recovering) recovery("This Wi-Fi access has expired.");
          stopStatus("This Wi-Fi access has expired.", true);
          return;
        case "REPLACED":
          if (recovering) recovery("This access code was replaced. Contact support if you cannot use your replacement code.");
          stopStatus("This access code was replaced. Recover your payment here or contact support.");
          return;
        case "REPLACING":
          pollAgain("Your access is being replaced.");
          return;
      }
      switch (result?.status) {
        case "FAILED":
        case "CANCELLED":
          if (recovering) recovery("This payment did not complete.");
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
      pollAgain("The status service could not be reached.");
    }
  }

  function startPolling() {
    pollUntil = Date.now() + 5 * 60 * 1000;
    return checkStatus();
  }

  listen("reload-packages", "click", () => run(loadPackages));
  get("billing").hidden = false;
  if (preview) {
    get("payment-title").textContent = "Preview Wi-Fi packages";
    get("payment-controls").hidden = true;
    billing("Local preview only. Payments are disabled.");
    recovery("Online recovery requires HTTPS. You can still use an access code.");
    submit("purchase", () => {});
    void run(loadPackages);
    return;
  }

  const uncertain = "We could not confirm the payment request. If you received a prompt or receipt, contact support before paying again.";
  submit("purchase", () => run(async () => {
    const packageId = selectedPackage;
    const phone = get("phone").value.trim();
    if (checkout || !phone || !packages.get(packageId)?.available) return;
    const connectAutomatically = get("auto-connect").checked;
    // A timeout or reload during STK initiation must not silently enable another purchase.
    saveCheckout({ paymentToken: null });
    get("phone").value = "";
    billing("Requesting payment…");
    try {
      const result = await post("start_payment", { phone, package_id: packageId });
      if (!usableToken(result?.payment_token)) throw new Error("Missing payment token");
      saveCheckout({ paymentToken: result.payment_token });
      autoConnect = connectAutomatically;
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
    autoConnect = false;
    await startPolling();
  }));

  listen("check", "click", () => run(startPolling));
  listen("recovery-check", "click", () => run(startPolling));
  listen("connect", "click", () => { if (!busy && credentials) logIn(credentials, "billing-message"); });
  listen("recovery-connect", "click", () => {
    if (!busy && recoveryReady && credentials) logIn(credentials, "recovery-message");
  });
  get("receipt-form").hidden = false;
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
