# RouterOS HotSpot portal

This is a local MikroTik bundle. The router serves the pages, JavaScript, and
stylesheet; no CDN or package installation is needed. It is not a replacement
for the pfSense portal.

## What Frappe configures

**Frappe provisions paid HotSpot users, not the HotSpot itself.** After
verifying payment, it creates a local RouterOS user on the configured server,
assigns the existing package profile, applies the purchased `limit-uptime`,
and verifies the result before releasing the code. It also revokes old access
when issuing an approved replacement. It does not create bridges, DHCP, NAT,
HotSpot servers or profiles, install HTML, or change management access.
Bootstrap those locally with the steps below; there is no automatic router
setup action in v1.

## RouterOS setup commands

Run these **one block at a time** from a trusted management connection
(preferably a separate wired port). Use WinBox **Safe Mode** or press
`Ctrl+X` in the RouterOS terminal before changing networking or firewall
rules. Replace the examples with your values: `bridge-guest`, `10.55.0.0/24`,
`wifi.example.org`, `billing.example.org`, `hotspot1`, and `hsprof1`. Do not
paste `add` blocks twice or run them over an existing configuration blindly.

### 1. Inspect and back up

```routeros
/system resource print
/system identity print
/interface bridge port print
/ip address print
/ip dhcp-server print
/ip firewall nat print
/ip hotspot print
/ip hotspot profile print
/system backup save name=before-mikrotik-portal
```

Download the backup to secure storage; do not share it. Use a dedicated guest
bridge or VLAN that doesn't carry your management connection. If a dedicated
guest interface already runs HotSpot, skip the wizard and use its server and
server profile names. Do not run a second DHCP server on that interface.

For a wired bench test with a spare, unassigned port, create a guest bridge
and add that port. Replace `ether5` only after confirming it isn't a bridge
member, WAN uplink, or management path. Skip this block if the guest
interface already exists. For Wi-Fi, attach a dedicated guest SSID to that
segment without moving the management SSID.

```routeros
/interface bridge add name=bridge-guest
/interface bridge port add bridge=bridge-guest interface=ether5
```

### 2. Create or reuse a HotSpot

On a fresh guest segment with no HotSpot, run the interactive wizard:

```routeros
/ip hotspot setup
```

Select `bridge-guest`, enter `10.55.0.1/24` as the local address and
`10.55.0.10-10.55.0.254` as the client pool. Choose masquerade only if the
guest subnet needs NAT and isn't already covered by an existing rule. For the
certificate-free pilot choose certificate `none`, leave SMTP at `0.0.0.0`,
use your approved DNS resolvers, and enter `wifi.example.org` for the portal
DNS name. Use a unique temporary **HotSpot** login (not a RouterOS management
account). The guest network must resolve the portal name to its local router
address and be able to resolve the billing hostname before sign-in. Keep
the pilot guest network IPv4-only: RouterOS HotSpot does not enforce access
on IPv6. Keep guest forwarding to private and management networks blocked.

Find the names actually assigned by the wizard. Use a HotSpot server profile
dedicated to this guest network before changing it.

```routeros
/ip hotspot print
/ip hotspot profile print
/ip hotspot profile set [find where name="hsprof1"] login-by=http-chap use-radius=no
/ip hotspot user profile add name=paid-1m-5m-v1 shared-users=1 rate-limit="1000000/5000000" session-timeout=0s
```

That user profile gives clients 1 Mbps upload and 5 Mbps download; repeat
only the last command with a new name for each distinct rate pair. Leave
login/logout scripts unset. Package duration is set in Frappe, not in this
profile. Do not modify a profile that paid orders still use. Plain HTTP PAP
is unsupported; CHAP does not encrypt the portal page itself.

### 3. Upload the portal and allow HTTPS checkout

In WinBox **Files**, copy or download and re-upload the router's generated
HotSpot directory under a new name (for example `flash/hotspot-mikrotik` on
devices with `flash/`). Overlay this bundle's `.html`, `.js`, and `.css`
files. Keep the router's other files, including `redirect.html`,
`rlogin.html`, and `status.html`. Before uploading `config.js`, set
`billingOrigin` to the exact public HTTPS origin (e.g.
`https://billing.example.org`, with no path) and `zoneId` to the exact Frappe
zone ID. Both are public; don't add secrets. An invalid or blank
configuration disables billing, not manual login.

```routeros
/ip hotspot profile set [find where name="hsprof1"] html-directory=flash/hotspot-mikrotik
/ip hotspot walled-garden ip add server=hotspot1 action=accept protocol=tcp dst-port=443 dst-host=billing.example.org comment="Frappe checkout HTTPS"
```

Check the real directory in **Files** before setting `html-directory`.
Allow guest DNS resolution through your intended resolver; do not open
arbitrary HTTPS or give guests management access. Configure Frappe or its
reverse proxy to allow the exact portal browser origin in CORS (e.g.
`http://wifi.example.org`), including the scheme and non-default port, on
errors as well as success. The bundle uses form-encoded `POST` without
cookie authentication. Keep the API on the configured HTTPS host without
redirects. The expanded RouterOS `link-login-only` must be on the same
origin as the portal page.

### 4. Restrict the management API before enabling it

This example uses plaintext API on **TCP 8728 inside an encrypted tunnel**.
Replace `192.0.2.10/32` with the worker's fixed source **as seen by the
router**, and `wg-management` with the actual tunnel ingress interface. The
tunnel must already exist; on a router without VPN support use an adjacent
gateway. For verified TLS, use `api-ssl`, TCP 8729, and a certificate trusted
by Frappe for the configured host. Direct plaintext is opt-in; source
filtering does not encrypt credentials. If you deliberately use the direct
path, substitute the actual WAN ingress for `wg-management` and keep the
worker's fixed egress `/32` in the firewall, service, and account. Never use
the guest interface as API ingress. See the
[deployment guide](DEPLOYMENT.md#restrict-the-routeros-api).

```routeros
/ip service print where name~"^api"
/ip firewall filter print where chain=input
/user group add name=frappe-hotspot-api policy=read,write,api
```

In WinBox **System > Users**, create a dedicated user such as
`frappe-hotspot`, group `frappe-hotspot-api`, with **Allowed Address** set to
the same `/32`. Set a strong unique password in WinBox rather than pasting it
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
    /ip firewall filter add chain=input action=accept in-interface=wg-management src-address=192.0.2.10/32 protocol=tcp dst-port=8728 place-before=$anchor comment="Frappe API allow"
    /ip firewall filter add chain=input action=drop protocol=tcp dst-port=8728 place-before=$anchor comment="Frappe API deny"
}
```

Only after verifying those restrictions, enable the service. Disable the
unused service if nothing else requires it.

```routeros
/ip service set api address=192.0.2.10/32 port=8728 disabled=no
```

Test that the Frappe worker can connect and that a guest client and a second
source cannot. Confirm you still have management access before leaving Safe
Mode to keep the changes.

### 5. Map the configuration in Frappe and start the sandbox pilot

| Frappe setting | Example or source |
| --- | --- |
| Captive Portal Zone | Backend `MikroTik`; zone ID matches `config.js`; select the router configuration |
| MikroTik Router | Zone above; API host reachable by the worker; port `8728`; `Plaintext` with explicit opt-in for the tunnel example |
| Router identity and HotSpot server | Exact names from `/system identity print` and `/ip hotspot print` |
| API user and password | Dedicated `frappe-hotspot` account; password stored in the Frappe Password field |
| Internet Package Profile | `paid-1m-5m-v1`, upload `1000000`, download `5000000`, and the purchased duration in minutes |

Follow the [staging and hardware gates](DEPLOYMENT.md#hardware-acceptance-gate).
Use the Kopo Kopo sandbox and leave **Hardware and API Pilot Verified** and
**Allow Automatic Compensation When Accounting Is Proven** unchecked.
Test from an unauthenticated guest, with billing online and then with Frappe
unreachable for the offline login check. After local login testing, remove
the wizard's temporary user using its exact name (replace `pilot-temp`):

```routeros
/ip hotspot user remove [find where name="pilot-temp"]
```

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
| `check_payment_status` | `payment_token` **or** `recovery_token` | Payment `status`, MikroTik `access_state`, `access_ready`, and `voucher` when ready |
| `begin_mikrotik_recovery` | `receipt` | `challenge_id` |
| `verify_mikrotik_recovery` | `challenge_id`, `otp` | Zone-scoped `recovery_token` |

The router login is a local POST. Only `READY` with boolean `access_ready`
and a usable `username.password` voucher can unlock automatic sign-in. The
first dot is the separator; the password can contain dots. Manual login uses
that same format and does not contact Frappe. The portal never substitutes a
raw password for a missing or malformed CHAP challenge on HTTP. A bad router
destination leaves the user on the connected page with a local status link.

The same tab retains only the payment token (or an uncertain-payment marker)
in session storage, scoped to the billing origin and zone on that HotSpot
origin. Vouchers, phones, receipts, SMS codes, challenges and recovery tokens
are not stored or placed in URLs. Reloading resumes checking but does not
automatically repeat router login. If a prompt was sent but no token was
returned, the portal blocks a duplicate purchase in that tab; use SMS
recovery or contact support before starting a new purchase.

Run the local tests from the checkout root:

```sh
node --test tests/test_mikrotik_portal.cjs
```
