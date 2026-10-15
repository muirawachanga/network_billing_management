## Network Billing System

A complete billing system integrated with pfsense

### Package-based pfSense portal

The package portal uses pfSense-generated vouchers. Frappe stores the package,
price in KES, voucher duration in minutes, payment, and allocation. **pfSense
alone enforces activation, remaining usage, simultaneous login rules, and
expiry.** An `Issued` voucher in Frappe does not mean that it is currently usable
in pfSense. Frappe does not generate pfSense vouchers or infer expiry from payment
time.

#### Setup and existing data

After deploying this version to the bench hosting the app:

```sh
bench --site mel.daitkenya.com migrate
bench --site mel.daitkenya.com enable-scheduler
bench restart
```

Create **Internet Package Profile** records. To match the supplied portal's
package IDs and displayed prices, use these values, or change both the records
and the portal:

| Package Name / ID | Display Name | Price (KES) | Voucher Duration (Minutes) |
| --- | --- | --- | --- |
| time_1hr | 1 Hour | 5 | 60 |
| time_2hrs | 2 Hours | 10 | 120 |
| time_6hrs | 6 Hours | 20 | 360 |
| time_8hrs | 8 Hours | 30 | 480 |

These are examples from the supplied HTML. The migration creates `time_8hrs`
at KES 30, disabled, only when unassigned legacy Daily vouchers exist and that
package does not already exist. Other packages are not automatically created.
Enable **Available on Captive Portal** for packages ready to sell. Disabled
packages cannot be purchased or listed. Prices are read from Frappe, never from
the browser. Checkout snapshots preserve the purchased price and duration if a
package is later edited.

Generate a separate voucher roll in pfSense for each duration. In **Import
Captive Code**, select the corresponding **Package**, then import that roll's
CSV using the existing pfSense CSV format (six header rows followed by one
voucher column). Imports set vouchers to `Active` and copy the selected
package's duration into each record. **A voucher is opaque: Frappe cannot
verify its encoded duration. The operator must ensure the selected package
matches the pfSense roll.** Changing a package duration after vouchers have
been linked is blocked; create a new package for a different duration.

Existing voucher names, SMS logs, and hidden legacy `Daily`/`Monthly` fields
are retained. The post-model-sync migration automatically assigns unassigned
`Daily` vouchers to `time_8hrs` and stores **480 minutes**, matching the legacy
8-hour pfSense rolls. It preserves codes, document names, statuses, sent flags,
and historical links, including for already sold vouchers; it does not
reactivate them. Vouchers already linked to a package or checkout, Monthly
vouchers, and vouchers with no legacy duration are left unchanged.

An existing `time_8hrs` package is reused without changing its price or enabled
flag. If its duration is not 480 minutes, migration stops with an explicit
error rather than mislabelling vouchers. Review the package price and enable it
when ready to sell. The patch is safe to rerun and runs automatically during
`bench --site mel.daitkenya.com migrate`. It changes Frappe metadata only, not
pfSense voucher durations or expiry.
Unassigned legacy vouchers remain available only to the old SMS flow. New
voucher records use internal IDs instead of exposing the voucher in their
document names.

Configure the existing **Kopokopo Mpesa Setting**: environment (`sandbox` or
`live`), STK till number, and subscriber first/last names. The existing site
configuration keys `kopokopo_client_id`, `kopokopo_client_secret`, and
`encryption_key` must be present. Keep these secrets on the server. The site's
public URL must resolve to `https://mel.daitkenya.com` so generated payment
callbacks reach the correct HTTPS site.

Finally enable **Enable Package Captive Portal**. This is an explicit switch:
it enables the new APIs and stops automatic voucher SMS from the legacy
payment callbacks. The old default-amount STK endpoint is rejected while the
switch is on. Manual SMS functionality is retained, but it cannot consume
package-linked voucher inventory.

#### Self-contained HTML integration

The tracked, ready-to-upload template is **`pfsense/portal.html`**. It is outside
the Frappe app's `www` and `public` directories: Frappe does not serve it.
Upload it to your pfSense captive portal configuration, preserving any
additional fields required by your working pfSense template. It includes the
API URLs below, dynamic package loading, unavailable-package handling, a
40-second request timeout, and explicit handling for ineligible access.
All CSS and JavaScript are inline. The following snippets explain the integration
for existing portal copies; they do not need to be applied again to the tracked file.

Replace the placeholder `API` object in the supplied `portal2.html` with:

```js
const API = {
  startPayment: "https://mel.daitkenya.com/api/method/network_billing_system.api.captive_portal.start_payment",
  paymentStatus: "https://mel.daitkenya.com/api/method/network_billing_system.api.captive_portal.check_payment_status",
  existingPayment: "https://mel.daitkenya.com/api/method/network_billing_system.api.captive_portal.connect_existing_customer",
  packages: "https://mel.daitkenya.com/api/method/network_billing_system.api.captive_portal.get_packages"
};
```

All endpoints accept guest **POST** requests and return Frappe's usual
`{"message": ...}` wrapper, which the supplied `postJson()` already unwraps:

| Method suffix | Request fields | Response |
| --- | --- | --- |
| `get_packages` | none | Array of `package_id`, `label`, `description`, `price`, `currency`, `duration_minutes`, `available` |
| `start_payment` | `phone`, `package_id` | `payment_token`, `stk_push_sent`, optional `customer_message` |
| `check_payment_status` | `payment_token` | `status`, `access_ready`; confirmed access also includes `voucher`, `package_id`, `duration_minutes`, `expiry_authority` |
| `connect_existing_customer` | `lookup_type: "mpesa_receipt"`, `lookup_value` | Same access response, or `NOT_FOUND` / `INELIGIBLE` |

`portal_action` and `redirurl` are accepted for compatibility with the uploaded
HTML, but are not trusted, fetched, or used by the backend. Preserve the
pfSense `$PORTAL_ACTION$`, `$PORTAL_REDIRURL$`, `auth_voucher`, and `accept` form
fields. A confirmed checkout returns the actual voucher **code**, not its
Frappe document name. The browser submits that code directly to pfSense.
No external JavaScript, CSS, fonts, or CDN libraries are needed.
Increase the existing `REQUEST_TIMEOUT_MS` to `40000` to allow for server-side
OAuth authentication followed by the provider request. A browser timeout still
does not imply that the payment failed.

For package prices and availability to update without re-uploading HTML, add
this function inside the existing script and call `loadPackages()` at the end
instead of the final `updatePayButton()`. It replaces the static package buttons
with server-owned values and attaches their selection handlers. Also change
the package-button assignment in `setPaymentBusy()` to
`button.disabled = busy || button.dataset.available === "false";` so sold-out
packages stay disabled after a failed payment:

```js
async function loadPackages() {
  $("payButton").disabled = true;
  document.querySelectorAll(".package").forEach(button => { button.disabled = true; });
  try {
    const packages = await postJson(API.packages, {});
    if (!Array.isArray(packages)) throw new Error("Invalid package catalogue.");
    const grid = $("packageGrid");
    grid.replaceChildren();
    Object.keys(PACKAGE_PRICES).forEach(key => delete PACKAGE_PRICES[key]);
    selectedPackage = null;
    packages.forEach(pkg => {
      PACKAGE_PRICES[pkg.package_id] = pkg.price;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "package";
      button.dataset.available = String(pkg.available);
      button.disabled = !pkg.available;
      button.setAttribute("aria-pressed", "false");
      const label = document.createElement("span");
      label.className = "package-time";
      label.textContent = pkg.label;
      const price = document.createElement("span");
      price.className = "package-price";
      price.textContent = `KSh ${pkg.price}`;
      button.append(label, price);
      button.addEventListener("click", () => {
        if (paymentBusy || isSubmittingLogin) return;
        grid.querySelectorAll(".package").forEach(item => {
          item.classList.remove("selected");
          item.setAttribute("aria-pressed", "false");
        });
        selectedPackage = pkg.package_id;
        button.classList.add("selected");
        button.setAttribute("aria-pressed", "true");
        updatePayButton();
      });
      grid.append(button);
      if (!selectedPackage && pkg.available) button.click();
    });
    if (!selectedPackage) throw new Error("No packages are currently available.");
    $("payButton").disabled = false;
  } catch (error) {
    $("payButton").disabled = true;
    setNotice("payNotice", error.message || "Cannot load packages. Reload to retry.", "error");
  }
}
```

Allow the actual pfSense portal **origin** (scheme, hostname and port) in this
Frappe site's `allow_cors` configuration; do not use `*` or disable CSRF
globally. Add `mel.daitkenya.com` to pfSense's pre-authentication allowed
destinations, including working DNS and HTTPS access. The API domain is not
the portal origin unless both pages are actually served from that origin.

Receipt recovery returns the same issued voucher; it never creates a second
entitlement. The receipt acts as a bearer recovery credential, so keep it
private. Recovery is rate limited and only works for confirmed package
checkouts, not arbitrary direct-to-till payments or historical SMS purchases.
The portal's recovery copy should say that **pfSense checks remaining
validity**: a backend `SUCCESS` means payment is verified and the allocated
voucher is available to submit, not that login or unexpired access is guaranteed.
Frappe cannot bind a voucher to a physical device or enforce single-device use
without additional pfSense integration/configuration.

#### Payment reliability and operations

Checkout reserves one matching available voucher under a database row lock
before sending STK. Payment confirmation re-fetches Kopo Kopo's v2 incoming
payment resource using server credentials; callback payloads alone never
authorize access. Confirmation checks checkout metadata, provider ID, amount,
currency, payer, and till. Repeated callbacks and status checks use the same
checkout and voucher. Successful payments are linked to **Mpesa Transaction Log**.

Confirmed failed/cancelled requests release their reserved voucher. Ambiguous
network errors retain it and return a token with `stk_push_sent: false`;
customers must check the existing payment rather than pay again. The scheduler
reconciles pending requests with known provider IDs every minute, even if the
browser closes. Keep workers, scheduler, and Redis running.

If a request times out before its provider ID is received and its callback
never arrives, an operator must find that request in Kopo Kopo and run the
authenticated POST method `reconcile_checkout` with `checkout_id` and
`provider_payment_id`. It is restricted to System Manager / Network
Administrator and performs the same provider verification; it cannot mark an
unverified payment successful. Do not manually release an uncertain reservation
until the payment has been investigated. Error Log records initiation and
scheduled reconciliation failures.

Payment tokens are signed with the site's encryption key. Rotating that key
invalidates old polling tokens; receipt recovery still works for confirmed
checkouts. The backend does not track expired vouchers or promise a successful
pfSense login without router-side feedback.

#### Development checks

Pure rules and mocked API boundary tests can run without a Frappe site:

```sh
python -m unittest discover -s tests -v
node --test tests/test_pfsense_portal.cjs
```

They require `requests`, also used by the existing integration. Database locking,
DocType synchronization, cross-origin access, live/sandbox provider responses,
and actual pfSense login must additionally be exercised on a configured bench
before enabling live payments.

#### License

MIT