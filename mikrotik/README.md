# RouterOS HotSpot portal

This is a local MikroTik bundle. The router serves the pages, JavaScript, and
stylesheet; no CDN or package installation is needed. It is not a replacement
for the pfSense portal.

**First-time setup without a router certificate:** Follow steps 1–3 to get
local HotSpot sign-in working, then [restrict the plain RouterOS API](#4-restrict-the-management-api-before-enabling-it)
to the Frappe worker before enabling it. These are separate connections:

| Connection | Certificate-free router setup |
| --- | --- |
| Guest browser → router sign-in | HTTP CHAP; the router does not need a certificate |
| Guest browser → Frappe checkout | Keep the Frappe website on HTTPS with a trusted certificate |
| Frappe worker → router management API | TCP 8728 (`api`), limited to one fixed source IP; prefer carrying it over an encrypted tunnel |

The source-IP restriction is not encryption. Direct TCP 8728 exposes the API
password and issued HotSpot credentials to anyone able to observe that
traffic. Use direct plaintext only for a controlled pilot if you accept that
risk; use a tunnel or verified TLS for production.

## What Frappe configures

**Frappe provisions paid HotSpot users, not the HotSpot itself.** After
verifying payment, it creates a local RouterOS user on the configured server,
assigns the existing package profile, applies the purchased `limit-uptime`,
and verifies the result before releasing the code. It also revokes old access
when issuing an approved replacement. It does not create bridges, DHCP, NAT,
HotSpot servers or profiles, install HTML, or change management access.
Bootstrap those locally with the steps below; there is no automatic router
setup action in v1.

For exact Desk field names and example values, see the
[Frappe field reference](#frappe-field-reference).

## RouterOS setup commands

Run these **one block at a time** from a trusted management connection
(preferably a separate wired port). Use WinBox **Safe Mode** or press
`Ctrl+X` in the RouterOS terminal before changing networking or firewall
rules. Replace `billing.example.org`, `hotspot1`, and `hsprof1` with your
actual values. Do not paste `add` blocks twice or run them over an existing
configuration blindly.

| Port | Role |
| --- | --- |
| `ether1`, `ether2` | Separate WAN uplinks; neither belongs in a LAN or HotSpot bridge |
| `ether3` | HotSpot client port in `bridge-guest`; gateway `192.168.20.1/22`, portal `wifi.digissiet.com` |
| `ether4`, `ether5` | Regular LAN on a separate trusted bridge; internet access without HotSpot login |

Put the HotSpot address and DHCP server on `bridge-guest`, not on its member
port `ether3`. Use a separate, non-overlapping LAN subnet (for example,
`192.168.88.0/24` with gateway `192.168.88.1` if that subnet is free). Neither
local subnet should overlap an upstream WAN network.

### 1. Inspect and back up

```routeros
/system resource print
/system identity print
/interface bridge port print
/interface list member print
/ip address print
/ip route print where dst-address="0.0.0.0/0"
/ip dhcp-server print
/ip firewall nat print
/ip hotspot print
/ip hotspot profile print
/system backup save name=before-mikrotik-portal
```

Download the backup to secure storage; do not share it. Manage the router
from `ether4` or `ether5`. Keep those ports in the existing trusted LAN
bridge and confirm a connected device gets a LAN address and can reach
WinBox and the internet without signing in. If no LAN exists, create a
separate bridge for those two ports, assign its gateway, and run
**IP > DHCP Server > DHCP Setup** on that bridge only. Keep management
services restricted to the trusted LAN.

Check bridge membership before moving ports. On a default configuration,
`ether2` and `ether3` may still be in the LAN bridge. Remove them from it
before using them for WAN and HotSpot respectively. Keep both WAN uplinks
separate and configure each according to its ISP (DHCP, PPPoE, or static).
For the pilot, use one preferred default route and the other as backup;
verify routes and outbound NAT for **both** LAN and HotSpot on whichever WAN
is active. If using masquerade by WAN interface list, include both actual
egress interfaces (the PPPoE interface for PPPoE). WAN failover does not
automatically fail over the Frappe API's inbound endpoint.

Once `ether3` is free, create the guest bridge. Skip the commands if the
bridge and membership already exist. If using an external access point,
connect it to `ether3` in bridge/AP mode, without its own DHCP or NAT.

```routeros
/interface bridge add name=bridge-guest
/interface bridge port add bridge=bridge-guest interface=ether3
```

### 2. Create or reuse a HotSpot

On a fresh guest segment with no HotSpot, run the interactive wizard. If a
HotSpot is already configured on `bridge-guest`, reuse it rather than adding
a second server or DHCP scope:

```routeros
/ip hotspot setup
```

Select `bridge-guest`, enter `192.168.20.1/22` as the local address and
`192.168.20.10-192.168.23.254` as the client pool (adjust for any static
reservations). The subnet is `192.168.20.0/22`, not a `/24`. Choose
masquerade only if the guest subnet needs NAT and isn't already covered by
an existing rule. For the
certificate-free pilot choose certificate `none`, leave SMTP at `0.0.0.0`,
use your approved DNS resolvers, and enter `wifi.digissiet.com` for the portal
DNS name. Use a unique temporary **HotSpot** login (not a RouterOS management
account). The guest network must resolve the portal name to its local router
address and be able to resolve the billing hostname before sign-in. Keep
the pilot guest network IPv4-only: RouterOS HotSpot does not enforce access
on IPv6. Block guest forwarding to the trusted LAN and upstream private or
management networks. Do not FastTrack HotSpot traffic; FastTrack can bypass
HotSpot processing and bandwidth limits.

Find the names actually assigned by the wizard. Use a HotSpot server profile
dedicated to this guest network before changing it.

```routeros
/ip hotspot print
/ip hotspot profile print
/ip hotspot profile set [find where name="hsprof1"] dns-name=wifi.digissiet.com login-by=http-chap use-radius=no
/ip hotspot user profile add name=paid-1m-5m-v1 shared-users=1 rate-limit="1000000/5000000" session-timeout=0s
```

That user profile gives clients 1 Mbps upload and 5 Mbps download; repeat
only the last command with a new name for each distinct rate pair. Leave
login/logout scripts unset. Package duration is set in Frappe, not in this
profile. Do not modify a profile that paid orders still use. Plain HTTP PAP
is unsupported; CHAP does not encrypt the portal page itself.

### 3. Upload the portal and allow HTTPS checkout

1. In WinBox terminal, run `/ip hotspot profile print detail` and note the
   current `html-directory` for your server profile. In WinBox **Files**,
   download that folder to your computer as a backup. Make a local copy
   named `hotspot-mikrotik`.
2. From the repository's `mikrotik/` directory, copy **only these nine
   files** into `hotspot-mikrotik`, replacing matching files:
   `login.html`, `alogin.html`, `error.html`, `logout.html`, `config.js`, `hotspot.js`,
   `portal.js`, `banners.js`, and `portal.css`. Keep the router's other generated files,
   including `redirect.html`, `rlogin.html`, and `status.html`. Do not
   upload Markdown docs, the `pfsense/` folder, or Frappe code.
3. Edit the copy of `config.js` on your computer. `billingOrigin` is the
   public HTTPS origin of **Frappe**, not the router IP or an API URL.
   `zoneId` must exactly match your Frappe zone ID. Both are public; don't
   add secrets. Blank settings disable online checkout, not manual login.

```js
window.mikrotikPortalConfig = {
  billingOrigin: "https://billing.example.org",
  zoneId: "pilot-zone",
};
```

4. In WinBox **Files**, upload the prepared `hotspot-mikrotik` folder under
   `flash/` if the router shows a `flash` directory, or to the Files root
   otherwise. Confirm that `login.html` and `config.js` are directly inside
   the new folder, not inside another nested `mikrotik/` folder. Leave the
   original directory in place so you can switch back.
5. Set `html-directory` to the exact uploaded path (`hotspot-mikrotik` if
   not under `flash/`). Add the HTTPS allowlist rule for your billing
   hostname only. Replace `hsprof1`, `hotspot1`, and `billing.example.org`
   with your actual values:

```routeros
/ip hotspot profile set [find where name="hsprof1"] html-directory=flash/hotspot-mikrotik
/ip hotspot walled-garden ip add server=hotspot1 action=accept protocol=tcp dst-port=443 dst-host=billing.example.org comment="Frappe checkout HTTPS"
```

Check the real directory in **Files** before setting `html-directory`.
Allow guest DNS resolution through your intended resolver; do not open
arbitrary HTTPS or give guests management access. Configure Frappe or its
reverse proxy to allow the exact portal browser origin in CORS
(`http://wifi.digissiet.com` for the HTTP CHAP setup), including the scheme
and non-default port, on errors as well as success. The bundle uses form-encoded `POST` without
cookie authentication. Keep the API on the configured HTTPS host without
redirects. The expanded RouterOS `link-login-only` must be on the same
origin as the portal page.

### 4. Restrict the management API before enabling it

The commands below use **TCP 8728 (`api`)**, not `api-ssl`. Before enabling
it, confirm the fixed public **outbound IP of the Frappe worker**; it may
differ from the billing website's IP. Replace `192.0.2.10/32` everywhere
with that worker source IP followed by `/32`. The Frappe router configuration
goes in the opposite direction: its **API Host** is the router's reachable
address, not the worker IP.

For a direct pilot, use one reachable WAN. The example uses `ether1`;
replace it with the actual ingress if it is `ether2` or a PPPoE interface.
Verify that replies use the same WAN. If the RB951 is
behind a modem/router you administer, direct access requires forwarding only
TCP 8728 to the RB951 and keeping the source-IP restriction. If the ISP uses
CGNAT and you do not have a reachable inbound address, the allowlist alone
will not make the router reachable. Use an encrypted tunnel; substitute its
ingress interface and the worker source IP the router sees over that tunnel.
Never use the guest interface as API ingress.

```routeros
/ip service print where name~"^api"
/ip firewall filter print where chain=input
/user group add name=frappe-hotspot-api policy=read,write,api
```

In WinBox **System > Users**, click **+** on the **Users** tab. Set **Name**
to `frappe-hotspot`, **Group** to `frappe-hotspot-api`, and **Allowed
Address** to the worker's same `/32`. Set a strong unique password and
save the user in WinBox rather than pasting the password
into terminal history. Some firmware may require `sensitive` for HotSpot
password read-back; add it only if the pilot proves it necessary. Do not use
the `full` group or grant terminal access.

In the same terminal, insert both rules before the first existing **static**
input rule that could accept or drop the API connection. Replace `YOUR-INPUT-ANCHOR`
with that rule's existing unique comment, or add and order the pair in WinBox.
Reprint the rules to verify `allow` is immediately followed by `deny` and
neither is bypassed by an earlier rule.
If this router accepts IPv6 traffic, ensure the IPv4-only setup also denies
TCP 8728 and 8729 in the IPv6 input firewall before enabling the service.

```routeros
{
    :local anchor [/ip firewall filter find where chain=input and dynamic=no and comment="YOUR-INPUT-ANCHOR"]
    :if ([:len $anchor] != 1) do={ :error "Choose one existing static input rule as the anchor" }
    /ip firewall filter add chain=input action=accept in-interface=ether1 src-address=192.0.2.10/32 protocol=tcp dst-port=8728 place-before=$anchor comment="Frappe API allow"
    /ip firewall filter add chain=input action=drop protocol=tcp dst-port=8728 place-before=$anchor comment="Frappe API deny"
}
```

Only after verifying those restrictions, enable the service. Disable the
unused service if nothing else requires it.

```routeros
/ip service set api address=192.0.2.10/32 port=8728 disabled=no
```

From the Frappe worker host, test TCP reachability (replace
`router.example.org` with the router endpoint):

```sh
nc -vz -w 5 router.example.org 8728
```

The same test must fail from a guest client and a second external source.
A successful TCP test does not verify the API credentials or permissions;
the sandbox purchase in step 5 does that. Confirm you still have management
access before leaving Safe Mode. To close the pilot listener, run
`/ip service disable api` before removing any temporary firewall rules.

### 5. Map the configuration in Frappe and start the sandbox pilot

1. On the Frappe host, run `bench --site SITE migrate` and make sure the
   scheduler and workers are running. Configure and enable package checkout
   with Kopo Kopo in **sandbox** using the [field reference](#frappe-field-reference).
2. In Desk, set **Access Backend** to **MikroTik** and save the **Captive Portal
   Zone** with **Enabled** unchecked. As a System Manager, click **Create
   MikroTik Router** on the saved zone to open the router form with **Zone**
   prefilled. If you open the router form directly, its **Zone** picker also
   lists saved, disabled MikroTik zones. Save the router, reopen the zone,
   select it under **MikroTik Router Configuration**, and enable the zone.
   Leave **Pause New MikroTik Sales** unchecked for the sandbox test. Save
   and enable at least one matching **Internet Package Profile**.
3. Connect a guest device, open the captive sign-in page, and complete a
   sandbox payment. Wait for access to be ready. Then test reconnecting
   with the issued code while Frappe is unreachable. The custom form takes
   a single `username.password` code, not a separate username and password.
   For a local test with the wizard user, enter its HotSpot username and
   password in that same format.

Leave **Hardware and API Pilot Verified** and **Allow Automatic
Compensation When Accounting Is Proven** unchecked until the corresponding
[hardware checks](DEPLOYMENT.md#hardware-acceptance-gate) pass. Remove the
wizard's temporary user after testing (replace `pilot-temp`):

```routeros
/ip hotspot user remove [find where name="pilot-temp"]
```

## Frappe field reference

Start on a staging site. Create the **zone (disabled) → router → package**,
configure sandbox payments, then link the router and enable the zone for testing.
In Desk, the **Network Billing System → MikroTik** card links to **MikroTik
Router**, **MikroTik Notify Settings**, and **MikroTik Banner**. Use Desk
search for the other forms.

### Captive Portal Zone

| Desk field | What to enter |
| --- | --- |
| **Zone ID** | A unique ID, e.g. `digissiet-pilot`. Put the exact same ID in the router's `config.js` as `zoneId`. |
| **Display Name** / **Description** | Optional labels for staff, e.g. `Digissiet Wi-Fi Pilot`. |
| **Access Backend** | `MikroTik` for this zone. Leave existing pfSense zones unchanged. |
| **MikroTik Router Configuration** | Link the saved **MikroTik Router** record for this same zone. Required before enabling. |
| **Enabled** | Leave off while building the configuration. Turn on when ready for sandbox checkout. |
| **Pause New MikroTik Sales** | Leave off for checkout tests. Enable to stop new sales and complimentary approvals without revoking already issued codes. |

**Default Registered Recipient**, **Recipient Name**, **Destination Type**,
**Mobile Number**, **Bank Account Name**, **Bank Account Number**,
**Kopo Kopo Bank Branch Reference**, and **Kopo Kopo Source Identifier** are
for remittances, not HotSpot setup or collecting a payment. Leave them
unchanged unless separately configuring payouts.

### MikroTik Router

| Desk field | What to enter |
| --- | --- |
| **Configuration Revision** | A recognizable label, e.g. `Digissiet RB951 v1`. |
| **Zone** | The zone you just saved, e.g. `digissiet-pilot`. |
| **Expected RouterOS System Identity** | Exact identity from `/system identity print`; not the portal DNS name unless you explicitly named the router that way. |
| **HotSpot Server** | Exact server name from `/ip hotspot print`, e.g. `hotspot1`. This is not the HotSpot server profile (`hsprof1`) or the package's user profile. |
| **Router API Host** | Router IP or DNS name reachable **from the Frappe worker**, without a scheme, port, or path. A tunnel IP works. Do not enter the worker's egress IP here. |
| **Router API Port** / **Router API Transport** | Normally `8729` / `TLS` with a trusted router certificate, or `8728` / `Plaintext` for the certificate-free pilot. Match the actual RouterOS service. Prefer plaintext inside an encrypted tunnel. |
| **Allow Unencrypted RouterOS API** | Required for `Plaintext` even inside a tunnel. Leave off for `TLS`. Source-IP filtering does not encrypt direct traffic. |
| **Dedicated API User** / **API Password** | The restricted RouterOS API account and its password in the Desk Password field. Never use the router's `admin` account or put this password in `config.js`. |
| **Hardware and API Pilot Verified** | Leave off during setup. Required for live paid sales and complimentary grant approval; enable only after the [hardware checks](DEPLOYMENT.md#hardware-acceptance-gate). |
| **Allow Automatic Compensation When Accounting Is Proven** | Leave off until the pilot proves reliable outage accounting. It is not needed for checkout. |

The IP allowed on RouterOS is the Frappe worker's verified source address as
seen by the router, not the value of **Router API Host**. Keep the API closed
to HotSpot guests. Create a new router configuration revision if the endpoint,
transport, API user, identity, or HotSpot server changes. Keep the old revision
reachable for existing purchases. Rotate only the password in place.

### Internet Package Profile

| Desk field | What to enter |
| --- | --- |
| **Zone** | The same MikroTik zone. |
| **Package Name** | A unique internal name, e.g. `digissiet-1-hour`. |
| **Display Name** / **Description** | Optional customer-facing name, e.g. `1 hour`, and a short description. |
| **Price (KES)** | The amount charged for this package, e.g. `20`. |
| **Duration (Minutes)** | Positive whole minutes of connected access, e.g. `60` for one hour. |
| **MikroTik HotSpot User Profile** | The exact existing RouterOS **user** profile name, e.g. `paid-1m-5m-v1`. Frappe does not create it. |
| **MikroTik Upload (bits/s)** / **MikroTik Download (bits/s)** | Positive integers matching the profile's `rate-limit` in upload/download order. Example: `1000000` and `5000000` for `1000000/5000000`. |
| **Available on Captive Portal** | Enable only when the price, duration, zone, and RouterOS profile are ready. Incomplete packages are not sold. |

Set the user profile to `shared-users=1` and `session-timeout=0s`. Use a new
profile version for rate changes instead of changing the profile for paid codes.

### Kopo Kopo checkout and Frappe site

| Setting | What to enter |
| --- | --- |
| **Kopokopo Mpesa Setting → Enable Package Captive Portal** | Enable on staging for checkout. This is site-wide and also disables legacy automatic voucher SMS from payment callbacks. |
| **Env** / **STK Till Number** | Start with `sandbox` and the corresponding provider till. Switch to matching live credentials and till only after verification. |
| **First Name** / **Last Name** / **Email** | Provider subscriber defaults. Frappe requires first and last name; enter the subscriber email if required by your provider setup. |
| Site config: `kopokopo_client_id` / `kopokopo_client_secret` | Matching provider credentials on the Frappe host. Keep these private; they do not go on the router. |
| Site config: `encryption_key` | Keep and back up the site's existing key. Do not replace it on an existing site to enable MikroTik. |
| Public HTTPS and CORS | The public Frappe HTTPS site must receive the provider callback. Allow `http://wifi.digissiet.com` as the browser origin for HTTP CHAP in Frappe or its proxy. On the router, `billingOrigin` in `config.js` is the **Frappe HTTPS origin**, not the portal origin or the router IP. |

Package checkout builds its own payment callback URL and uses the selected
package's amount. **Kopo Stk Callback**, **Default Amount**, **Customer Name**,
**Notes**, and **Allow Code Resend** are not MikroTik package-checkout inputs.
**Kopo Webhook Callback**, **Webhook Till Number**, **Set Webhook**, and
**Webhook Already Set** belong to the existing webhook integration. Saving the
settings can register a webhook if requested or not already set; preserve the
appropriate provider webhook configuration rather than replacing it with the
package callback. Paid recovery uses the original checkout token, not SMS.

### Optional: Featured ads, free codes, and outage SMS

| Desk form | Fields and values |
| --- | --- |
| **MikroTik Banner** | Set **Zone** and **Title**, save the draft, and upload a public PNG, JPEG, or WebP in **Image** before enabling. **Image Alternative Text** describes the image. **Display Price** is optional text like `KSh 200`, not a checkout price. **Description** is optional. **Target URL** is an optional HTTPS details link; the whole tile is clickable only after sign-in. **Sort Order** is ascending; **Start Time** and **End Time** are optional and use the site's time zone. Check **Enabled** to publish and use the read-only **Preview** to review the saved banner. |
| **MikroTik Notify Settings** (System Manager) | For free-code SMS only: enter the **Notify Platform HTTPS Origin** (no `/api` path), **Dedicated Notify Business Account** (the exact funded account ID), **Notify API Key**, and **Notify API Secret**. Then check **Enable Complimentary Access SMS**. Configure Advanta and the approved sender in Notify Platform, not here. |
| **MikroTik Complimentary Grant** | For each handout, enter a Kenyan **Recipient Mobile Number**, **MikroTik Zone**, enabled **Package Policy** from that zone, **Connected Time (Minutes)** (1–1440, no more than the package duration), **Reason**, and **Unique Source Reference** (e.g. `campaign:launch-001`). Approve it in Desk after the hardware pilot. Approval and provisioning fields are filled by the app; follow **SMS Notification Status** for dispatch and delivery. |
| **Kopokopo Mpesa Setting → Sms Gateway Server / Sms Api Key** | For outage replacement notifications, configure the existing authorized HTTPS SMS gateway. This is separate from Notify Platform's complimentary SMS and is not needed for paid checkout. |

**Portal Checkout**, **MikroTik Issuance**, **MikroTik Grant Notification**,
and the outage evidence and notification records are operational records,
not initial setup forms.

## Hardware pilot results

### What to share after the hardware test

The commands below do not print user passwords. Redact real management
addresses, device identity, and any sensitive identifiers before sharing:

```routeros
/system resource print
/ip hotspot print
/ip hotspot profile print
/ip hotspot user profile print
/ip service print where name~"^api"
/ip firewall filter print stats where comment~"^Frappe API"
```

Include whether the worker could connect and other sources were denied;
whether login, rate limits, and cumulative time worked; and the same test
user's **uptime and active-session presence** before disconnecting, after
reconnecting, and before/after reboot and power loss. Replace `TEST-USERNAME`
locally with the test user's RouterOS login ID (the part of the code before
the dot). These commands return only uptime and the session count:

```routeros
:put [/ip hotspot user get [find where name="TEST-USERNAME"] uptime]
/ip hotspot active print count-only where user="TEST-USERNAME"
```

Share the values, not the command with the login ID filled in. Do not send
passwords, access codes, checkout tokens, receipts, payer numbers, router
exports or backups.

## Billing contract

The portal sends form-encoded `POST` requests to
`/api/method/network_billing_system.api.captive_portal.<method>` on the
configured origin. Requests include `zone_id` and expect Frappe's `message`
response envelope.

| Method | Additional POST fields | Result used by the portal |
| --- | --- | --- |
| `get_packages` | — | Server package list with IDs, prices, and availability |
| `start_payment` | `phone`, `package_id` | `payment_token` |
| `check_payment_status` | `payment_token` | Payment `status`, MikroTik `access_state`, `access_ready`, and `voucher` when ready |
| `recover_mikrotik_payment` | `receipt`, original `payment_token` | Status of the matching verified purchase, and `voucher` only when ready |
| `get_mikrotik_banners` | — | Published tiles for this zone |

The router login is a local POST. Only `READY` with boolean `access_ready`
and a usable `username.password` voucher can unlock automatic sign-in. The
first dot is the separator; the password can contain dots. Manual login uses
that same format and does not contact Frappe. The portal never substitutes a
raw password for a missing or malformed CHAP challenge on HTTP. A bad router
destination leaves the user on the connected page with a local status link.

The same tab retains only the payment token (or an uncertain-payment marker)
in session storage, scoped to the billing origin and zone on that HotSpot
origin. Vouchers, phone numbers and receipts are not stored or placed in
URLs. Reloading resumes checking but does not
automatically repeat router login. If a prompt was sent but no token was
returned, the portal blocks a duplicate purchase in that tab; contact
support before starting a new purchase.

Select **Already paid?** on the Wi-Fi sign-in page to enter an existing
access code or recover a payment. Code entry works without Frappe and this
tab opens by default when online checkout is not configured.
Paste the M-Pesa confirmation message or enter its receipt in the same
browser tab where you started checkout. Only the extracted receipt and the
checkout token go to Frappe, not the SMS body. Frappe matches the verified
payment and reconnects the customer when access is ready. No SMS
verification is sent; without the original tab, contact staff.

Available Wi-Fi packages appear as selectable tiles with prices; the first
available package is selected by default.

Featured tiles appear at the top of sign-in, recovery, post-login, sign-out,
and error views when ads are published for the zone. Multiple tiles slide
in from left to right. Guests can hold a tile to pause, swipe to browse, or
tap a dot to jump to a tile. Reduced motion disables automatic rotation.
Configure the image, display price (e.g. `KSh 200`), and HTTPS destination in
Desk under **MikroTik Banner**. The whole tile opens the destination on the
post-login page only. Keep the walled garden limited to the Frappe checkout and its
public banner images; don't add the advertisers' websites to it.

Run the local tests from the checkout root:

```sh
node --test tests/test_mikrotik_portal.cjs
```
