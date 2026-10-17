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
- **Already paid:** Enter an M-Pesa receipt code to retrieve the allocated
  voucher and submit it again. pfSense checks its remaining validity.
- **Clear feedback:** Payment unavailability appears with a red heading and
  border, retry guidance, and a "Close and try later" button.

The layout adapts to desktop and mobile screens and includes keyboard-friendly
tabs, labelled inputs, and status messages.

## Portal file

The uploadable page is `pfsense/portal.html`. It contains its own styling and
JavaScript and is uploaded manually to pfSense, not served as a Frappe page.

## Wi-Fi locations

Create a **Captive Portal Zone** for each location or router zone, enable it,
and select it on the location's packages and voucher imports. In each uploaded
portal copy, set `ZONE_ID` to that record's exact ID. Package selection, payment,
and receipt recovery use that location's inventory only.

Migration assigns existing records to the enabled `default` zone without
changing their codes or payment status. Review this inventory before enabling
additional locations. Create separate packages for different zones; packages
with imported vouchers cannot be moved between zones.

The page's zone ID identifies inventory; it is not proof of the device's
physical location. Enforcing device location requires trusted router-side
integration. Use unique zone IDs across routers even if their local zone names
are the same.
