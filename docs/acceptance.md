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
- Bounded, paced recent-order pagination and separate identities for split shipments.
- Schema 2 with persistent HMAC identities, private links, and public freshness metadata.
- Viewer Origin/Host validation and framing restrictions; disposable container regression.
- GitHub test/Docker workflows, Node 24 action pins, and Dependabot configuration.

## Gates

| Gate | Status | Required evidence |
| --- | --- | --- |
| Local control, ownership, discovery tests | Passed | 36 tests; Ruff formatting/lint; strict mypy; synthetic cookie persistence in Chromium |
| Container stack | Passed | Image built; UID 1000; profile/state 0700; sandboxed browser startup; noVNC visually connected |
| A: real Amazon login persistence | Passed | After manual login, the whole container was recreated with the same volume; the protected orders page verified authenticated without another login at 2026-09-25T20:41:31Z |
| Shipment discovery | Passed for observed link shapes | Two complete recent-order scans, including one after whole-container replacement, returned the same shipment IDs and split-order groups with no unsupported links |
| B/C: real stop source and transitions | Not started | A real live delivery page; several observed changes without private endpoint replay |
| Parser fixtures and event engine | Gated | Sanitized real observations before committing Amazon parser behavior |
| MQTT, Telegram, Home Assistant | Gated | Proven live source, deterministic event tests, broker/HA restart checks, Telegram setup and delivery |
| Security review | Completed for current scope | Two viewer issues fixed; local disposable-container regression passed; Python dependency audit reported no known vulnerabilities; remote deployment remains blocked on authentication |
| GitHub workflows | Locally validated; hosted run pending | actionlint 1.7.12 passed; each pinned JavaScript action declares Node 24; native amd64/arm64 builds and conditional GHCR publication configured |

Synthetic persistent-cookie tests do not establish Amazon session persistence.
No claims about real stop counts, deliveries, or Home Assistant acceptance are made.

## Live observation: access notice

On September 25, 2026 the local Chromium browser successfully loaded Amazon's sign-in
page with TLS verification and Chromium sandboxing enabled. The subsequent protected
orders-page check displayed Amazon's notice about unauthorized automated access and
a Continue button. This must be handled by you in the interactive browser. It is now
classified as `challenge`, with the reason `amazon_access_notice_requires_review`.
The classifier regression was reproduced with a synthetic page before the fix.

The human handoff also remembers the verification tab: reopening login focuses
that tab without navigating away or clearing unfinished input. A regression test
first demonstrated the wrong-tab behavior, then verified the fix and recovery when
you close the challenge tab. These updates are deployed.

No anti-detection modifications or automated challenge completion were introduced.
After manual login, the current orders layout exposed visible Your Orders and Search
Orders controls without the old account marker. A synthetic regression reproduced
the false unknown state; the corrected verifier then confirmed the real protected
page after whole-container recreation. Gate A is passed. Live stop parsing and
notification outputs still require their separate acceptance gates.

## Discovery and security milestone

The first live orders scan completed three pages and recognized every visible tracking
link. It included distinct shipment IDs grouped under the same order ID. Only private
installation-specific IDs and scan counts were exposed through the API. After replacing
the whole container, saved IDs were unchanged and all records started stale pending
revalidation. Delivery state remains unknown; no stops or delivered state were inferred.
The repeat scan completed at 2026-09-25T21:03:05Z with the same IDs and all returned
records revalidated, without another login.

See [the security review](security-review.md) for reproduced viewer findings, fixes,
remaining trust boundaries, and the limits of dependency auditing. The new viewer
protections are deployed locally. [Notification decisions](notifications.md) describe
the agreed MQTT/Telegram requirements; those transports are still unimplemented.

## Decisions to carry forward

- US English and amazon.com only for the first adapter.
- One installation, one account, one persistent browser owner.
- Complete live acceptance before expanding MQTT/Home Assistant work.
- Retain shipment identity and event semantics from the reviewed plan.
- Add authenticated noVNC proxying before network exposure; local raw noVNC remains a spike.
- Keep Chromium lock recovery conservative: no blind deletion of lock artifacts.
- Do not copy source from reference applications with incompatible or unverified licenses.
