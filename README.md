# FamilyBox Wi-Fi Portal

A mobile-friendly captive portal for choosing Wi-Fi packages, paying with
M-Pesa, and reconnecting after payment.

## Zone payment dashboard and remittances

### Deployment and configuration

After deploying the app, run `bench --site mel.daitkenya.com migrate` and
restart the Frappe processes. Open the **Transfer Management** workspace.
Migration creates four roles: **Transfer Preparer**, **Transfer Approver**,
**Transfer Operator**, and **Transfer Reconciler**. Assign them deliberately;
Network Administrator alone does not grant payout authority. System Manager
can administer all transfer operations.

Transfers and live sending are **disabled by default** in **Transfer Settings**.
Set the self-approval threshold, per-request limit and bounded reconciliation
limit before enabling operations. Requests at or above the threshold require
a different approver; evidence-backed manual reconciliations also require a
different submitter at that threshold. Scheduled preparation has its own switch.
The existing checkout, voucher allocation and automatic portal login are unchanged.

Create these records in order:

1. **Transfer Provider Account**: environment, source till and optional site-config
   credential-key names. Multiple accounts must not be used to double-count a
   shared provider wallet. Environment is immutable; the source is immutable once
   referenced by a submitted request. Keep referenced providers enabled until
   reconciliation finishes; disabling an account also blocks its status lookups.
2. **Transfer Recipient** and **Transfer Recipient Account**: identity/contact,
   zone, provider account and destination details. Supported types are
   `mobile_wallet`, `bank_account`, `till`, `paybill`, `merchant_wallet` and
   `merchant_bank_account`. External bank accounts require RTS; merchant bank
   accounts require RTS or EFT. These are not international-transfer support.
3. Optionally select **Register with Kopo Kopo** on a draft account. This creates
   a provider contact/account, not a payment. Its Location UUID is stored only
   as the registration resource ID. Obtain and confirm the **payout reference**
   from the provider application separately; never assume it equals that UUID.
   An Approver/System Manager verifies account details using private evidence.
   Only accounts with readiness **Verified** can be used. Uncertain registration
   is not retried automatically; resolve it against provider records.
4. **Transfer Fee Policy**: destination type, optional settlement method, fixed
   charge, percentage, minimum and effective dates. There is no silent zero-fee
   fallback. The discussed KES 50/KES 500 figures are not hardcoded tariffs:
   confirm each provider charge/category and configure it explicitly.
5. **Zone Remittance Policy**: zone, provider, default account and fee policy,
   retained balance, maximum amount and optional schedule/minimum. Assign a
   separate policy for each destination tariff needed by the same zone/provider
   (for example mobile, till and bank), rather than duplicating provider accounts.

Live provider accounts use existing `kopokopo_client_id` and
`kopokopo_client_secret` unless a complete dedicated override or explicit
credential-key pair is configured. Sandbox requires separate credentials in
`sites/<site>/site_config.json`:

```json
{
  "kopokopo_remittance_sandbox_client_id": "<sandbox application client ID>",
  "kopokopo_remittance_sandbox_client_secret": "<sandbox application secret>"
}
```

Sandbox never falls back to live checkout credentials. Optionally set both
`kopokopo_remittance_live_client_id` and `kopokopo_remittance_live_client_secret`
to use a dedicated live payout application instead. A partial override is
rejected. Keep checkout keys unchanged. Use a separate sandbox site where possible.
A public HTTPS site URL and site encryption key are required for payout callbacks.

### Prepare, approve and send

Create and submit a **Transfer Request** with purpose, account, tariff and amount.
**Zone** fee bearer means amount is the recipient payout and fees are extra;
**Recipient** means amount is the gross zone budget and fees reduce the payout.
The result must be an exact positive whole-KES payout, not silently rounded.
Submission snapshots the destination and fee rule but does not reserve funds.
**Approve and Reserve Funds** rechecks the configuration and locks the zone
while reserving principal plus estimated fee, preserving the retained balance.

An Operator creates a **Transfer Batch** from one approved request or selected
approved requests in the list. A batch uses one provider account, with no duplicate
recipient identities. Multiple destinations are allowed only for homogeneous
external mobile-wallet or external-bank requests; other types use single-item
batches. Inspect the accounts and amounts, then confirm **Send Batch**.
Amounts are JSON integers. Blind send-all requests are never generated.

**Transfer Attempt** persists and commits the send intent before the payout POST.
Each batch has one attempt; a timeout, lost response or uncertain result must
never trigger a retry or replacement transfer. **Reconcile Provider Status**
accepts the existing Send Money UUID if the initiation response was lost.
If no UUID can be found, retain the reservation and obtain a provider trace;
a timeout alone is not evidence that the payment failed.
Use **Unbatch Before Sending** to return a ready, never-attempted batch to approved
requests; this retains their reservations. Only unbatched, unsent requests can be
cancelled to release funds. Attempted batches remain reserved until verified
settlement or evidence-backed reconciliation.

Authenticated `GET /api/v2/send_money/<UUID>` checks resource ID, metadata,
source, currency, destinations and amounts. Request status **Processed** does
not mean every recipient was paid: individual
`transfer_batches[].disbursements[]` must report **Transferred** or **Failed**.
Disbursements are matched by account identity or explicitly confirmed reference,
never array position. Partial batches settle only matched recipients.
**Transfer Disbursement** preserves terminal evidence; missing/ambiguous results
remain reserved and require review. Null EFT transaction references are supported.

Callbacks carry a site-HMAC capability token and only trigger authenticated
provider lookup. **This is not verification of `X-KopoKopo-Signature`**:
the provider's signature algorithm/encoding still needs confirmation. Callback
content, a registration UUID or a Send Money UUID alone never proves payment.

### Fees, external withdrawals and corrections

Use **Transfer Reconciliation**, with private provider evidence, an explicit
verification checkbox, reference and exact zone allocations:

- **External Withdrawal** records an already-completed withdrawal without sending
  money. Allocate the full actual debit, including confirmed charges. It may
  expose a negative balance; never import a known API payout twice.
- **Transfer Outcome** manually resolves a sent, pending/uncertain request using
  its existing Send Money UUID and evidence of Paid/Failed. It is an operator
  attestation, not automatic provider verification.
- **Actual Fee** posts the confirmed charge once per Paid/Failed request and
  releases its estimated-fee reservation. Explicit zero is supported. Principal
  settlement alone does not release the fee hold, even for failed transfers.
  Higher actual charges expose a shortfall rather than hiding it.
- **Reversal** appends an exact reversal of a positive debit, including a confirmed
  returned payment or fee correction. It does not recreate a released reservation,
  edit history, cancel provider money movement or resend money.

**Zone Finance Entry** is append-only, with unique posting keys and protected
operations. Reconciliations cannot be cancelled after posting. Duplicate
references and reversals are rejected. Live and sandbox balances stay separate.

### Dashboard, schedules and migration

The **Zone Payment Dashboard**, native **Zone Payment Overview** chart and
**Zone Finance** dashboard show collections, reservations, withdrawals and
availability. Transfer roles can read the finance report without checkout access.
Balances assume 100% of successful KES checkout collections belong to the zone,
with no opening balances, commissions, refunds or revenue splits. They are an
internal allocation ledger, **not the provider's settled wallet balance**.
Confirm actual wallet liquidity, Pay permissions and bank details before sending.

Checkout collections are read directly, including historical successes; the
bounded scheduler mirrors them into the ledger for audit without counting twice.
Every five minutes it also checks eligible batches and, when explicitly enabled,
prepares daily/weekly/monthly requests. Period keys prevent duplicate preparation;
outstanding requests block accumulating more. **Schedules never approve or send.**

Existing **Zone Remittance** records remain in balance calculations. Their API
sender is retired, including the old `enable_zone_remittance` bypass; use the
new workflow for all new payouts. Existing uncertain API records retain their
reservations and status checks now use the documented disbursement schema.
Cancel unsent legacy requests before replacing them. Do not copy sent/paid
legacy records into new requests or import their withdrawals again. Legacy
recipient records are retained; create and verify new Transfer Recipient Accounts
with confirmed references rather than treating registration IDs as payout IDs.

No live provider transfer or Frappe-site migration has been performed by this
change. Validate installation and all relevant sandbox destination, partial
settlement and fee scenarios before explicitly enabling **Allow Live**.

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
