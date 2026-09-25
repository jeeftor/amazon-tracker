# Acceptance record

The source plan is preserved verbatim in `PLAN.md`. This file records implementation
evidence separately from the plan's proposed behavior.

## Scope built

- One stock Playwright persistent Chromium context, with an operating-system profile lock.
- Interactive browser ownership, expiry, and conflict/coalescing behavior.
- Local panel, embedded noVNC in the plan's two-port local mode, and asynchronous controls.
- Conservative session verifier with separate service/browser/session states.
- SQLite WAL session history and initial schema migration.
- Non-root supervised Xvfb, window manager, VNC, noVNC, application, and Chromium.
- Loopback port bindings, private profile permissions, sanitized diagnostics.

## Gates

| Gate | Status | Required evidence |
| --- | --- | --- |
| Local control and ownership tests | Passed | 21 tests; Ruff formatting/lint; strict mypy; synthetic cookie persistence in Chromium |
| Container stack | Passed | Image built; UID 1000; profile/state 0700; sandboxed browser startup; noVNC visually connected |
| A: real Amazon login persistence | Pending human handoff | Sign-in page loads; subsequent orders verification encountered an Amazon access notice; restart acceptance not yet established |
| Shipment discovery | Not started | Distinct shipments stable across repeated polls and restarts |
| B/C: real stop source and transitions | Not started | A real live delivery page; several observed changes without private endpoint replay |
| Parser fixtures and event engine | Gated | Sanitized real observations before committing Amazon parser behavior |
| MQTT and Home Assistant | Gated | Proven live source, deterministic event tests, broker/HA restart checks |

Synthetic persistent-cookie tests do not establish Amazon session persistence.
No claims about real stop counts, deliveries, or Home Assistant acceptance are made.

## Live observation: access notice

On September 25, 2026 the local Chromium browser successfully loaded Amazon's sign-in
page with TLS verification and Chromium sandboxing enabled. The subsequent protected
orders-page check displayed Amazon's notice about unauthorized automated access and
a Continue button. This must be handled by you in the interactive browser. It is now
classified as `challenge`, with the reason `amazon_access_notice_requires_review`.
The classifier regression was reproduced with a synthetic page before the fix.

No anti-detection modifications or automated challenge completion were introduced.
The real login-persistence gate remains pending; MQTT and delivery parsing remain gated.

## Decisions to carry forward

- US English and amazon.com only for the first adapter.
- One installation, one account, one persistent browser owner.
- Complete live acceptance before expanding MQTT/Home Assistant work.
- Retain shipment identity and event semantics from the reviewed plan.
- Add authenticated noVNC proxying before network exposure; local raw noVNC remains a spike.
- Keep Chromium lock recovery conservative: no blind deletion of lock artifacts.
- Do not copy source from reference applications with incompatible or unverified licenses.
