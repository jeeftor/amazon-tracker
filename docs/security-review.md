# Security review — September 25, 2026

Scope: the persistent browser, local panel, shipment discovery/storage, container,
and proposed GitHub workflow. This is a code review plus targeted local tests, not
an independent penetration test or a claim that the whole stack is vulnerability-free.

## Findings and disposition

| Severity | Finding | Disposition |
| --- | --- | --- |
| High | The original noVNC WebSocket accepted an unrelated website's Origin, exposing the authenticated desktop to a possible cross-site WebSocket attack. Loopback port publication did not enforce browser origin isolation. | Fixed: exact viewer Origin allowlist, rejection of missing Origin, and Host checks before upgrade. A raw handshake originally returned 101 for `https://attacker.invalid`; the container regression now requires 403. Browser exploitability also depends on the browser's local-network restrictions; the server now enforces its own boundary. |
| Medium | Raw noVNC pages had no framing restriction, allowing a hostile site to frame the login desktop for clickjacking. | Fixed: CSP `frame-ancestors` allows only the viewer itself and the configured local panel origins. Static HTTP and WebSocket Host validation also reject DNS rebinding hosts. |
| Deployment blocker | Neither API nor desktop has application authentication. Local programs can forge Origin headers; peers on the Docker network can reach container ports. | Open: keep the supplied loopback bindings and a trusted host/Docker environment. Authenticated same-origin proxying, TLS, and session controls are required before LAN or Internet exposure. Do not publish VNC port 5900. |
| Medium | Ending an interactive lease releases automation ownership; it does not revoke an already connected viewer or disable its keyboard. | Open: viewer authorization and revocation belong to the authenticated proxy milestone. Do not treat the lease as a security session or leave untrusted people/programs access to your host. |
| Medium | The browser profile and tracking URLs are sensitive plaintext on disk. | Private directories use 0700, the installation secret uses 0600, and the supervised process uses umask 0077. Host/root/Docker access and backups remain trusted. Use protected host storage and private backups; application encryption is not implemented. |
| Low | Manual discovery budgets reset on process restart; shipment rows currently have no automatic retirement. | Documented: bounded per-scan navigation and history retention exist, but durable budgets and shipment retention await the scheduler/state engine. |

## Verified controls

- Stock Chromium sandbox enabled, non-root UID 1000, explicit seccomp profile,
  loopback published ports, VNC bound inside the container to localhost, X11 TCP disabled.
- Exclusive OS profile lock, serialized navigation, human challenge handoff, and
  no arbitrary URL API, private endpoint replay, automated challenge solving, or stealth flags.
- API Host allowlist, custom-header/same-origin mutation checks, no CORS allowance,
  no-store responses, CSP, and text-only DOM insertion for dynamic panel values.
- Parameterized SQL; tracking URLs accepted only from HTTPS Amazon links with
  shipment-specific identity; HMAC public IDs; no raw URLs/account contents in REST.
- Sanitized operation errors and disabled HTTP access logging. Ignored browser data,
  screenshots, local configuration, and CA artifacts stay out of Git and Docker context.
- CI uses commit-pinned Node 24 actions, read-only default permissions, no persisted
  checkout credentials, and no `pull_request_target` execution. Only successful trusted
  master/tag pushes can enter the job with package write permission. Public Docker builds
  use upstream base names and receive no local CA artifacts or Amazon credentials.

## Evidence and limits

The local Python gate passed 36 tests plus Ruff and strict mypy. Tests include API
cross-origin/rebinding rejection, safe errors, private permissions, profile ownership,
session persistence, shipment identity, and stale state after restart. The disposable
container check covers real viewer handshakes, framing headers, sandbox startup, and
storage permissions. It never uses your signed-in profile.

`pip-audit 2.10.1` against the locked production Python dependency export found no known
vulnerabilities on September 25, 2026. This result excludes Debian packages, Chromium,
the distribution-provided noVNC/websockify stack, and future advisories. An OS/browser
image vulnerability scan and a browser-driven end-to-end attack test have not been
completed. No claim is made about GHCR publication until the publish job runs.

## Notification setup review

The settings and explicit-test milestone adds MQTT passwords and Telegram tokens to
the existing local trust boundary. They are stored as plaintext only in private UI
settings (0600), written by atomic replacement, and omitted from API responses.
Environment secrets override UI values without being copied to disk. A disabled
output can still send an explicitly requested setup test; saving/startup never sends.

The new routes retain Host/same-origin/custom-header protections. Settings bodies are
limited to 16 KiB; unknown keys and invalid types are rejected atomically, and validation
errors never reflect input. Only configured/not-configured secret flags are returned.
Forms do not use browser storage. Local processes and Docker peers remain trusted;
these protections do not substitute for authentication before network exposure.

MQTT accepts a configured broker hostname/IP (including private networks by design),
uses certificate verification when TLS is enabled, and sends non-retained test payloads
on a separate topic. Without TLS, broker credentials traverse that network in plaintext.
Telegram uses the fixed official HTTPS endpoint, rejects malformed token paths,
disables redirects, bounds response size/time, and discards remote descriptions and
raw exceptions. The HTTP client does not log token-bearing request URLs by default.
Explicit tests serialize, honor cooldown/rate limits, and never automatically retry.
A timeout is reported as uncertain because delivery may already have occurred.

All 62 tests passed with Ruff and strict mypy. New checks cover secret round trips,
environment precedence, reflected-error prevention, cross-origin tests, bounded bodies,
concurrent sends, timeouts, synthetic Telegram errors/rate limits, and MQTT test retention.
A disposable authenticated Mosquitto broker confirmed delivery to a subscriber,
rejection of an incorrect password, and no replay to a new subscriber. The updated
locked Python dependency audit found no known vulnerabilities. Actual Telegram delivery,
the user's broker/HA configuration, automatic event dispatch/retry, and remote
administration still require separate acceptance and review.
