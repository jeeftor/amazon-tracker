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
- Schema 3 with package-scoped delivered labels and separate check/confirmation timestamps.
- Viewer Origin/Host validation and framing restrictions; disposable container regression.
- GitHub test/Docker workflows, Node 24 action pins, and Dependabot configuration.
- Version/SHA/dirty build metadata in the footer and status API; stamped local/CI builds.
- MQTT/Telegram UI settings, explicit environment overrides, private secrets, and setup tests.

## Gates

| Gate | Status | Required evidence |
| --- | --- | --- |
| Local control, ownership, discovery, notification tests | Passed | Ruff formatting/lint; strict mypy; synthetic cookie persistence in Chromium; failed migration rolls back all DDL; current observer checks recorded below |
| Container stack | Passed | Image built; UID 1000; profile/state 0700; sandboxed browser startup; noVNC visually connected |
| A: real Amazon login persistence | Passed | After manual login, the whole container was recreated with the same volume; the protected orders page verified authenticated without another login at 2026-09-25T20:41:31Z |
| Shipment discovery | Passed for observed link shapes | Two complete recent-order scans, including one after whole-container replacement, returned the same shipment IDs and split-order groups with no unsupported links |
| B/C: real stop source and transitions | Source observed in user's browser; Docker integration pending | September 26 response samples and screenshots show count changes, next stop, and delivered; Docker transport metadata confirms periodic XHR, but automated response-to-state transitions still need a live package |
| Basic delivered status | Passed for observed labels; deployed | User authorized ordinary delivered-status checks separately from live stop counts; authenticated three-page scan confirmed historical deliveries including September 12; sanitized fixtures cover dates, split shipments, hidden labels, and migration |
| Live stop fixtures and event engine | Parser/observer fixtures implemented; event engine pending | Sanitized observed shapes; intercepted Chromium response pipeline; no real automatic announcements |
| Notification configuration and tests | Implemented; local checks passed | Settings persistence/precedence/secret safety; simulated Telegram responses; disposable authenticated Mosquitto publish, rejected bad password, no retained test replay |
| Automatic MQTT, Telegram, Home Assistant announcements | Pending | Deterministic event engine, broker/HA restart checks, actual destination setup and delivery; stop counts still need a live source |
| Security review | Completed for current scope | Two viewer issues fixed; local disposable-container regression passed; Python dependency audit reported no known vulnerabilities; remote deployment remains blocked on authentication |
| GitHub workflows | Hosted evidence tracked in Actions | actionlint 1.7.12 passed; each pinned JavaScript action declares Node 24; native amd64/arm64 builds and smoke tests passed in [the initial run](https://github.com/jeeftor/amazon-tracker/actions/runs/36189432697); see [latest branch runs](https://github.com/jeeftor/amazon-tracker/actions?query=branch%3Afeature%2Fpersistent-browser) for Python runner fixes and current status |

Synthetic persistent-cookie tests do not establish Amazon session persistence.
User-provided real stop evidence is separate from automated Docker extraction,
new-delivery event transitions, and Home Assistant acceptance.

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

## Basic delivered-status milestone

On September 25 the user clarified that delivered-package status must not wait for a
live map. Inspection of the authenticated orders layout confirmed that a package's
own `.delivery-box` contains its `.delivery-box__primary-text` status. The new adapter
reads that narrow scope and rejects ambiguous cards. No account text or raw identifiers
were copied into fixtures.

After deploying schema 3, the scan completed at 2026-09-25T21:24:36Z. Existing package
IDs were preserved, historical delivered labels (including September 12) appeared in
REST, and the one unrecognized label remained unknown. No stop count or new-delivery
announcement was inferred. A private SQLite backup was kept before the migration.

## Decisions to carry forward

- Basic delivered facts can be read from orders now; live-map absence does not block them.
- Confirmed deliveries are final: preserve their saved evidence and never require refresh.
- US English and amazon.com only for the first adapter.
- One installation, one account, one persistent browser owner.
- Output configuration/setup tests are authorized now; keep automatic sends gated on event evidence.
- Retain shipment identity and event semantics from the reviewed plan.
- Add authenticated noVNC proxying before network exposure; local raw noVNC remains a spike.
- Keep Chromium lock recovery conservative: no blind deletion of lock artifacts.
- Do not copy source from reference applications with incompatible or unverified licenses.

## Notification setup milestone

MQTT and Telegram settings now persist independently with explicit environment
overrides and secret-presence flags. A disposable panel was exercised in Headless Shell:
saved broker fields survived reload, the password input cleared after saving, an
environment-owned TLS field stayed read-only, and Send test reported broker acceptance.
An authenticated disposable Mosquitto subscriber received the test; a new subscriber
received no retained replay, and an incorrect password was rejected. Container
readiness and viewer security checks passed. Telegram tests use simulated responses;
no real bot/chat was contacted. Automatic delivery announcements remain disabled.

## Final delivered records

On September 26 the user clarified that a confirmed delivery should not require more
checks. The regression first reproduced an old delivery being marked stale. It now
verifies that delivery status and timestamps remain unchanged across aging, database
reopening, unknown/different later labels, and absence from subsequent scans. Delivered
records have no freshness timeout; unfinished packages keep the existing refresh policy.
This uses saved evidence and synthetic tests, without another Amazon scan.

## September 26 live delivery source

The user supplied live screenshots and response samples containing numeric stops 3,
2, next-stop wording with 1, and finally `status: DELIVERED` with a leftover count of
1. The final page also showed delivered. These establish response shapes; screenshot
selection order does not prove exact event timing. No request token or raw shipment,
order, tracking, address, product, or coordinate data is included in fixtures.

Docker Chromium resource timing showed four HTTP-200 XHRs to the get-state endpoint
at approximately 30-second spacing. A temporary injected observer missed their bodies;
the initial diagnosis that the Docker page made no requests was incorrect. This is
transport evidence, not successful automated count extraction. There was one manual
refresh during research; no private API replay was performed.

The passive Playwright response listener is tested with an isolated intercepted
Chromium page. Tests cover observed counts, missing counts, next stop, delivery with
count 1, wrong shipment/method, independent packages, navigation/age freshness,
out-of-order completion timestamps, no additional requests, ignored post-delivery
updates, and database reopening. Parser tests also cover malformed envelopes and
count/callout conflicts. Automatic notifications remain disabled. Another live
package is needed to verify the installed listener through real count transitions.

The September 26 local gate passed all 74 tests, Ruff formatting/lint, and strict mypy.
This includes both delivered-final-state and passive response-observer changes.
