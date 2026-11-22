# MikroTik v1 deployment

## RB951Ui-2HnD port layout

| Ports | Network | Expected client behavior |
| --- | --- | --- |
| `ether1`, `ether2` | Independent WAN uplinks (preferred and backup for the pilot) | Both can carry outbound traffic when active; the management API needs a separately verified inbound path |
| `ether3` | `bridge-guest`, `192.168.20.1/22` (`192.168.20.0/22`) | Guests get DHCP from the router and sign in at `wifi.digissiet.com` before general internet access |
| `ether4`, `ether5` | Separate trusted LAN bridge (e.g. `192.168.88.0/24` if unused) | LAN clients get DHCP and internet without HotSpot login; use these ports for local management |

Never bridge either WAN or `ether3` into the trusted LAN. Configure routing
and NAT for both local networks over the active WAN, and block guest access
to the trusted LAN and management services. Keep HotSpot traffic out of
FastTrack and keep the guest network IPv4-only.

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
4. Configure the existing Kopo Kopo checkout in sandbox first. If using outage
   replacements, point the existing SMS configuration at an HTTPS gateway
   authorized to message the recorded payer.
   Make sure SMS gateway logs do not capture request bodies. Gateway HTTP
   acceptance is not proof of delivery. The app uses a stable idempotency
   key: confirm whether your gateway honors it. A retry can resend the same
   notification, but does not mint a new code or award additional time.

## Publish featured tiles from Desk

Create a **MikroTik Banner** in Desk for the target zone. Set its title and
description, upload a public banner image to the record, add meaningful alt
text, and enter its HTTPS click-through URL. Set the display order and optional
publication dates, then enable the record. Images are public; never upload
private content. Use the Desk preview before publishing. Guest users can see
the tiles without clicking through; the configured links appear only on the
post-login page. Publish two tiles to check that they slide in left to right
and that tapping dots, holding, and swiping work on sign-in, recovery,
post-login, sign-out, and error views. No new walled-garden hosts are needed.

## Send complimentary Wi-Fi codes

In **MikroTik Notify Settings**, configure the Notify Platform HTTPS origin,
dedicated business account, API key and secret, then enable complimentary
SMS. Configure Advanta and the approved sender in Notify Platform, not on
the router. Give the service user access only to the dedicated Notify account
and ensure the account is funded. Dispatch records may contain the SMS text:
restrict access and follow your retention policy.

Create a **MikroTik Complimentary Grant** for the recipient and select a
zone, package policy and duration. Record the reason and source reference
and approve from Desk with the authorized role. Frappe provisions the code
on RouterOS before authorizing the SMS. Check **MikroTik Grant Notification**
for submission and delivery status; queued or accepted does not mean
delivered. Complimentary grants do not create payment or remittance records.

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

- Plug a test device into `ether3` and verify it gets a `192.168.20.0/22`
  lease with gateway `192.168.20.1` and reaches the portal, but cannot
  reach the trusted LAN. On `ether4` and `ether5`, verify DHCP and internet
  without a portal prompt. Test both networks on each WAN, including
  failover, and check API reachability separately on the intended WAN.
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
  a code. Use **Recover a payment** on the sign-in page to test a pasted
  M-Pesa message, including a wrong receipt and a new browser tab without
  the original checkout token.

## Operations and rollback

Watch for paid **Portal Checkout** records with **MikroTik Issuance** still
pending and growing attempts, **MikroTik Outage Incident** records requiring
evidence, and **MikroTik Notification Outbox** retries. Alert on repeated
RouterOS API failures and outbox failures. Confirm endpoint and policy, and
let the scheduler retry; don't restart checkout or change an issuance's
duration. A customer can recover paid access with the verified receipt and
the original checkout tab. If the token is gone, refer them to staff. No
SMS verification is sent. When Frappe is down, customers use their already-issued codes
locally; new payments and receipt recovery wait for Frappe to return.

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
