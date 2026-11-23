# MikroTik outages and replacements

- The monitor polls pilot-verified routers and records health and recent HotSpot
  activity samples. Two failed probes open an incident. Changed or unverified
  uptime is recorded as an accounting-continuity concern, not creditable
  downtime. A RouterOS API failure is not proof of customer service loss.
- In **MikroTik Outage Incident**, System Managers can corroborate customer
  impact with independent evidence, such as an upstream alarm or access
  logs. Impact confirmation is required for all replacement approvals.
  In the linked **MikroTik Outage Sample** records, managers can approve or
  decline individual `Review` records with a note. If the activity sample
  is missing, stale, or not active, approval requires a separate note citing
  independent evidence that this particular account was connected at outage
  onset. An incident-wide alarm or generic approval is not per-account
  evidence. Do not put credentials or customer PII in either note.
- **Automatic Compensation** is off by default. Even when enabled it requires
  independently confirmed service impact, a verified active session sampled
  in the two minutes before incident detection, and complete, non-decreasing
  connected-time accounting on the same router boot. Missing activity samples
  fall back to individual review if accounting is complete. Never reset or
  manually modify usage counters on managed HotSpot accounts.
- The worker commits `Replacing` first, hiding the old code. It then confirms
  revocation and re-reads usage with no remaining active sessions. It credits
  only the smaller of the resulting unused entitlement and the recorded
  candidate balance. It does not convert outage duration into credit.
  Unconfirmed router calls are retried as `Replacing`. Evidence of lost
  continuity or regressed final usage moves the sample to `Needs Review`;
  the predecessor stays hidden in either case. Do not re-enable an account
  or issue estimated credit from these records.
- A Ready replacement queues one SMS. The outbox stores a reference, not the
  code or phone number. Connection timeouts before sending retry with backoff
  and the same key. The SMS gateway's idempotency support is not guaranteed,
  so ambiguous submissions and interrupted sends go to `Needs Review` rather
  than being resent automatically. Verify delivery with the gateway; customers
  can use payment recovery to retrieve the current access code.
