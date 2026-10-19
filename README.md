# FamilyBox Wi-Fi Portal

A mobile-friendly captive portal for choosing Wi-Fi packages, paying with
M-Pesa, and reconnecting after payment.

## Portal experience

- **Package selection:** View available bundles and prices, with sold-out
  packages disabled.
- **M-Pesa payment:** Enter a phone number, confirm the selection, and approve
  the payment prompt on the phone.
- **Payment progress:** Follow payment confirmation and automatic submission
  of the Wi-Fi voucher for login.
- **Already paid:** Connect directly with an existing voucher code, even when
  the billing service is unavailable. Alternatively, use an M-Pesa receipt to
  recover access through the payment service. The captive portal checks voucher
  validity; receipt recovery requires the billing service to be available.
- **Clear feedback:** Payment unavailability appears with a red heading and
  border, retry guidance, and a "Close and try later" button.

The layout adapts to desktop and mobile screens and includes keyboard-friendly
tabs, labelled inputs, and status messages.

## Payment checks in Desk

On the **Portal Checkout** list, System Managers and Network Administrators can
select **Reconcile Date Range** and choose up to 31 days of checkout creation
dates, including both endpoints. Keep the page open while records are checked.
The summary identifies confirmed payments, remaining failures, missing provider
IDs, and records needing attention. Completed batches remain saved if the run is
interrupted; it is safe to run the range again. This action does not need the
scheduler and does not send new payment prompts. The individual checkout's
**Reconcile Payment** button remains available for targeted checks.

Manual reconciliation records the Kopo Kopo response or request error in
Frappe Error Log. The checkout form also displays the response payload, with
customer names, phone numbers, and email redacted. Confirmed payments must
match the checkout, amount, currency, payer, till, and configured receipt
format. If verification raises an error, the checkout retains its previous
status; the reconciliation dialog shows the corresponding Error Log ID.

## Portal file

The uploadable page is `pfsense/portal.html`. It contains its own styling and
JavaScript and is uploaded manually to pfSense, not served as a Frappe page.

## Wi-Fi locations

Create a **Captive Portal Zone** for each location or router zone, enable it,
and select it on the location's packages and voucher imports. The page reads
the current zone automatically from pfSense's `$PORTAL_ZONE$` placeholder.
The Frappe zone ID must exactly match that pfSense zone name. Package selection,
payment, and receipt recovery use that location's inventory only.
If the zone is missing or the placeholder is not replaced, billing operations
are blocked rather than falling back to `default`. Direct voucher login still
works. For local HTTP testing, replace the hidden `portalZone` input's value
with your test zone name; restore `$PORTAL_ZONE$` before uploading to pfSense.

Migration assigns existing records to the enabled `default` zone without
changing their codes or payment status. Review this inventory before enabling
additional locations. Create separate packages for different zones; packages
with imported vouchers cannot be moved between zones.

The page's zone ID identifies inventory; it is not proof of the device's
physical location. Enforcing device location requires trusted router-side
integration. Use unique pfSense zone names across routers to keep their
inventories distinct.
