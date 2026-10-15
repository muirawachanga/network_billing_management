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
