# MikroTik v1 deployment

## Start in staging

1. Run `bench --site SITE migrate` and make sure workers and the scheduler are
   running. Back up the site database and site encryption key securely. A
   missing key prevents checkout-proof verification and secret recovery.
2. Follow the [RouterOS setup commands](README.md#routeros-setup-commands)
   to configure the HotSpot and install the portal files.
   Give the router a unique system identity. Use a local HotSpot server
   and dedicated, versioned **HotSpot user profiles**, not the HotSpot server
   profile. Set `shared-users=1`, no per-session cap (`session-timeout=0s`),
   and leave login/logout scripts unset. Use local password authentication;
   disable RADIUS, MAC-based and trial login for this server. Prefer HTTP CHAP
   on a certificate-free HotSpot. For each user profile set `rate-limit` as
   `upload-bps/download-bps` (RouterOS receives client uploads and transmits
   client downloads). For example, use `1000000/5000000` for 1 Mbps up and
   5 Mbps down. Leave burst options off. Do not edit profiles while there are
   purchases against them; create another version.
3. Create a disabled **Captive Portal Zone** and a **MikroTik Router**
   configuration for it. Record the expected system identity, server, API
   endpoint and dedicated account. Save the account password in the Password
   field; don't put it in scripts or source control. Choose the router in the
   zone, set **Access Backend** to **MikroTik**, and configure each enabled
   **Internet Package Profile** with that exact RouterOS profile name, its
   upload and download limits in bits/sec, and the purchased duration in
   minutes. Unmapped offers are not sold. Updating the zone or package does
   not retarget paid purchases. Create a new router configuration revision for
   endpoint, identity or server changes; keep the historical revision and
   profiles reachable while users still depend on them. Rotate the API
   password on the existing revision when only the secret changes.
4. Configure the existing Kopo Kopo checkout in sandbox first. Point the SMS
   configuration at an HTTPS gateway authorized to message the recorded payer.
   Make sure SMS gateway logs do not capture request bodies. Gateway HTTP
   acceptance is not proof of delivery. The app uses a stable idempotency
   key: confirm whether your gateway honors it. A retry can resend the same
   notification, but does not mint a new code or award additional time.

## Restrict the RouterOS API

Verify the installed RouterOS version on the RB951Ui-2HnD. Use a supported
firmware with modern API login and check actual command permissions on that
firmware. Use `api-ssl` with verified TLS when available, or prefer an
**encrypted network tunnel** for a certificate-free deployment (a supported
VPN on the router or on an adjacent gateway). Plain RouterOS API **inside**
that tunnel still requires selecting **Plaintext** and checking **Allow
Unencrypted RouterOS API**, because the application protocol is plaintext.
Direct plaintext is explicit opt-in: source filtering is not encryption, and
the account password and customer codes can be read by an on-path observer.
Do not open public service access as a substitute for stable routing.

Confirm inbound reachability from the intended Frappe worker; a router behind
CGNAT generally needs a tunnel to a reachable endpoint. Confirm Frappe's
fixed egress IP (or fixed tunnel address). On RouterOS, restrict the chosen
API service's allowed addresses, the dedicated user account's source
addresses, and input firewall rules to that specific source. Do not expose
the API to the HotSpot client network. Also check IPv6 and non-tunnel ingress,
disable the unused API service, and make sure a broader accept rule cannot
bypass the restriction. Create a dedicated group starting with only the
necessary API/read/write permissions. If your firmware requires password
visibility for HotSpot read-back, confirm the smallest additional permission
that enables it. RouterOS groups may not support command-level isolation;
do not grant full-admin, policy management, terminal, remote shell or other
unnecessary access. Leave the zone paused if the restrictions cannot be met.

## Hardware acceptance gate

Keep live sales off until these checks pass on the target router:

- Test local code entry, automatic login after a confirmed sandbox payment,
  a wrong-code error, and the post-login destination in actual captive
  browsers. Stop Frappe and confirm a previously provisioned code still
  works, with upload/download limits, forced logout and single-session
  enforcement. Verify the API is denied from a second source and from a
  HotSpot client.
- Disconnect and reconnect with one code; verify that consumption accumulates
  across sessions. Reboot, then simulate a power cut with an active session.
  Verify actual remaining time and whether the cumulative counter and
  pre-cut session evidence survive. If RouterOS enforcement resets on reboot,
  leave live sales off. Enable **Hardware and API Pilot Verified** only
  after the local enforcement and API checks pass. Do not enable automatic
  compensation until trustworthy outage attribution, accounting, and
  revocation have also been demonstrated.
- On a staging MariaDB/Frappe site, exercise two simultaneous callbacks and
  a customer status request for one verified payment. Expect one checkout
  issuance and one RouterOS user. Inject a worker crash/transport loss after
  adding the RouterOS user and verify that retry confirms the same user,
  without changing its counters. Check that receipt alone does not return
  a code and that an SMS challenge can be used only once.

## Operations and rollback

Watch for paid **Portal Checkout** records with **MikroTik Issuance** still
pending and growing attempts, **MikroTik Outage Incident** records requiring
evidence, and **MikroTik Notification Outbox** retries. Alert on repeated
RouterOS API failures and outbox failures. Confirm endpoint and policy, and
let the scheduler retry; don't restart checkout or change an issuance's
duration. A customer can recover paid access by verifying the recorded
payer phone. When Frappe is down, customers use their already-issued codes
locally; new payments and SMS recovery wait for Frappe to return.

An API outage alone does not prove a Wi-Fi outage or power cut. Review
reachability, uptime, session, and cumulative-usage evidence. Record
independent service-impact evidence before approving a replacement. Stale
samples and unknown accounting go to review, not a guessed award. The old
user must be disabled and its sessions ended before a new code becomes
usable. Replacement is unused Wi-Fi time, not an M-Pesa cash refund.
See the [outage review procedure](../docs/mikrotik-outages.md).

To roll back, select **Pause New MikroTik Sales** for the affected zone and
turn off automatic compensation. Keep the RouterOS profile, local credentials,
portal files, API restrictions, encryption key, and Frappe workers in place
for already-paid or pending orders. Don't delete RouterOS users, reset their
counters, or reassign historical checkout records. Restore pfSense sales
only on a correctly configured pfSense portal with its own stock; keep the
existing MikroTik sign-in path available until outstanding access is honored.
