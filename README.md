# FamilyBox Wi-Fi Portal

A mobile-friendly captive portal for choosing Wi-Fi packages, paying with
M-Pesa, and reconnecting after payment.

## Zone payment dashboard and remittances

After `bench --site <site> migrate`, open **Network Billing System → MPESA →
Zone Payment Dashboard**. The standard Frappe Script Report includes summary
totals, a zone comparison chart, and per-zone successful-payment counts,
collections, reserved withdrawals, withdrawn amounts, and available funds.
The **Zone Payment Overview** native Frappe Dashboard Chart is embedded at the
top of the workspace and is also available in the **Zone Finance** Dashboard.
Live and sandbox balances are separate; live is the default. All successful
historical package checkouts are included automatically. No checkout, voucher,
SMS, or portal-login behavior has been changed.

Balances assume 100% of a zone's successful KES collections belong to that
zone, with no opening balance, commission, refund, or revenue split. This is
an internal allocation ledger, **not Kopo Kopo's settled wallet balance**.
Availability is collected minus submitted pending/uncertain remittances minus
paid remittances, including each remittance's fee allowance. Failed transfers
release the reservation only after verified provider failure. Drafts do not
reserve funds. Use a conservative fee allowance agreed with the provider;
it is deducted in full, not automatically replaced with actual provider fees.

Configure the default mobile or bank recipient and Kopo Kopo source identifier
on **Captive Portal Zone**. Selecting a zone on a new **Zone Remittance**
copies those defaults; the submitted record preserves the recipient snapshot.
Submission reserves funds under a per-zone database lock but does not send
money. Only System Managers and Network Administrators can operate it.

Transfers are **disabled by default**, and live sending is blocked in this
first release pending sandbox verification of the provider status schema.
Before enabling sandbox transfers with
`enable_zone_remittance` as a boolean site setting, validate in sandbox with
your Kopo Kopo account, confirm Pay permissions, source funds, charges, bank
branch reference, and recipient. **Send Remittance** performs an explicit,
confirmed single-recipient transfer. Mobile destinations currently support
Safaricom M-Pesa; bank destinations require account name, number and provider
bank branch reference. A public HTTPS site URL is required for callbacks.
There is no automatic payout schedule or automatic retry.
Remittance credentials are separate from the existing checkout credentials.
Configure these site-specific keys in `sites/<site>/site_config.json`:

```json
{
  "kopokopo_remittance_sandbox_client_id": "<sandbox application client ID>",
  "kopokopo_remittance_sandbox_client_secret": "<sandbox application secret>",
  "kopokopo_remittance_live_client_id": "<live application client ID>",
  "kopokopo_remittance_live_client_secret": "<live application secret>"
}
```

Only configure the environment being used; missing credentials fail explicitly
and never fall back to another environment or to checkout credentials.
Keep existing `kopokopo_client_id` and `kopokopo_client_secret` unchanged.
Restart the Frappe processes after changing site configuration. Live sending
remains blocked regardless of whether live credentials are configured.
Use a separate test site for sandbox testing where possible.

Each remittance supports one mobile number or bank account, but different
remittance records can pay different recipients; zone recipient settings are
defaults, not a restriction to one global recipient. Kopo Kopo's endpoint
supports a `destinations` array for batch payouts. This first-stage ledger
intentionally sends one destination per request so a partial batch cannot
incorrectly mark an entire zone withdrawal paid. Multi-recipient batching
and per-destination settlement are not implemented.

The integration follows Kopo Kopo's official Python SDK contract:
`POST /api/v2/send_money` with `source_identifier`, `currency`, `destinations`,
`metadata`, and `_links.callback_url`. Mobile destinations use `mobile_wallet`,
`phone_number`, and `network`; bank destinations use `bank_account`,
`account_name`, `account_number`, and `bank_branch_ref`.
Status is queried with authenticated `GET /api/v2/send_money/<UUID>`;
callbacks only trigger that lookup. The verifier requires matching ID,
metadata, source, currency, single recipient, amount and terminal destination
status. A differing or incomplete provider response remains **Unknown** with
funds reserved and an Error Log; verify the account's sandbox response before
production use. A request UUID or callback alone never means paid.

If a transfer times out, do not create a replacement. Use **Check Transfer
Status**, supplying its provider UUID if the initial response was lost.
Sending, Pending, Unknown and Paid records cannot be cancelled. Queued records
may be cancelled before sending. This deployment has not performed a real
transfer; provider settlement and live/sandbox integration require operator
validation.

### Reconcile withdrawals made directly in Kopo Kopo

Create a **Zone Remittance** with **Withdrawal Method = External Withdrawal**
when the money was already withdrawn outside this app. Select its zone,
environment, source and recipient; enter the actual amount and charges
(using the fee allowance field), unique Kopo Kopo transaction reference,
withdrawal date, and private statement/receipt attachment. Confirm that you
verified completion against provider records, then submit.

This records a **Paid** ledger entry without calling the payout API or sending
money again. Duplicate external references are rejected; when the reference
is a transfer UUID it also shares the unique provider-ID constraint with API
remittances. Submitted external withdrawals cannot be cancelled or edited.
They can expose a negative available balance if the real withdrawal exceeded
the recorded zone entitlement; this intentionally reflects the financial
shortfall instead of refusing to record an already-completed payment.
Do not import an API remittance a second time as an external withdrawal:
reconcile the original record instead. An external entry is administrator
attestation with evidence, not automatic provider verification.

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

Manual reconciliation failures record the error and available Kopo Kopo
response in Frappe Error Log, with customer names, phone numbers, and email
redacted. Provider payloads are not returned to or displayed in the checkout
form, and successful checks do not create Error Logs. Confirmed payments must
match the checkout, amount, currency, payer, till, and configured receipt
format. If verification raises an error, the checkout retains its previous
status; the reconciliation dialog shows the corresponding Error Log ID.
Receipt codes may contain only letters; a digit is not required. The same
rule applies to provider verification and receipt-based reconnection.

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
