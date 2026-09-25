# Development

This guide describes the source and its verification workflow. Start with
[README.md](README.md) for running the tracker. [PLAN.md](PLAN.md) is the original
reviewed architecture; [docs/acceptance.md](docs/acceptance.md) records evidence and
unfinished gates. [docs/notifications.md](docs/notifications.md) records the later
MQTT/Telegram and UI/environment decisions.

## Local setup

Use Python 3.12 or newer, `uv`, and one application process.

```sh
uv sync --locked
uv run playwright install chromium --only-shell
make check
```

Plain `make` prints help. `make check` runs Ruff formatting checks, Ruff lint, strict
mypy, and pytest. Run `uv run pytest -m 'not browser'` for only the non-browser checks.
Real-browser tests use isolated temporary profiles and intercepted synthetic pages.
They must never reuse the live Docker browser profile or sign into your real account.

On Linux, install the browser's system dependencies with
`uv run playwright install --with-deps chromium --only-shell`. The supplied Docker
seccomp profile permits unprivileged Chromium sandboxing. Headless Shell is suitable
for macOS automation; do not launch the user's full Chrome application for tests.

For a local headless development server:

```sh
BROWSER_HEADLESS=true uv run uvicorn amazon_tracker.app:create_app --factory --host 127.0.0.1
```

The noVNC viewer requires Docker's virtual desktop stack. API liveness is separate
from browser readiness, Amazon session health, and future notification connectivity.

## Current architecture

```text
FastAPI + small static panel
  -> asynchronous Runtime operations
    -> BrowserManager: one context, profile flock, page registry, ownership
      -> Amazon session verifier / rendered tracking-link discovery
    -> SQLite: session history, shipment candidates, discovery summaries
```

| File | Responsibility |
| --- | --- |
| `config.py` | Validated settings and fixed US orders URL |
| `browser.py` | Persistent context, interactive leases, paced discovery, ownership |
| `session.py` | Login/challenge/protected-orders classification |
| `discovery.py` | Visible tracking anchors, package-scoped delivery labels, pagination |
| `delivery.py` | Conservative delivered-label normalization |
| `shipments.py` | Shipment-specific source keys and installation HMAC IDs |
| `storage.py` | Schema migrations, durable identities, private links, public freshness |
| `app.py` | Operation coalescing, human takeover, sanitized REST state |
| `notification_config.py` | Private atomic UI settings, validation, environment precedence |
| `notifications.py` | Explicit MQTT/Telegram tests, bounded timeouts, sanitized results |
| `panel/` | Local login and package discovery UI |
| `docker/` | Supervised display/VNC stack and Chromium sandbox profile |

Continuous scheduling, tracking-page observers, broader delivery-state parsing, event history,
and automatic notification dispatch are still pending. Output settings and explicit
transport tests are implemented. Discovering a tracking link alone
does not prove a package's state; delivered status requires its own visible label.

## Ownership and operation rules

The process holds an OS flock for the browser profile, including during browser
restarts. It never blindly removes Chromium locks. A separate async mutex serializes
navigations. Interactive sessions expire after a bounded lease; identical API requests
coalesce, and conflicting operations receive 409.

Open Login has priority over discovery: it cancels the scan, waits for ownership to
release, then focuses the interactive page. Incomplete cancelled scans are not
persisted. A challenge during verification or discovery brings the relevant page to
the foreground; Open Login reuses it without clearing unfinished input.

Discovery follows up to `DISCOVERY_MAX_PAGES` recent-order pages, with 30-second spacing
and 12 order-page navigations per running process per hour. These counters are currently
in memory; they reset on process restart. Persistent scheduling budgets belong to the
later scheduler milestone. Discovery is currently manual, not a background poller.

## Shipment identity and storage

Order IDs are grouping keys, never the primary shipment key. Source keys are selected
in this order: `shipmentId`, `trackingId`, then the pair `orderId` + `packageIndex`.
An order-only link or ambiguous repeated query fields are rejected and counted as
unsupported. Referral parameters do not affect identity. More source shapes must be
added from observed, sanitized evidence rather than guessed URL contracts.

IDs are namespaced HMAC-SHA256 values truncated to 16 hex characters. The 32-byte
installation secret is stored at `/data/state/installation-secret` with mode 0600.
Do not rotate or regenerate it when shipment records exist. Schema version 2 adds
shipments and discovery history to the version 1 session database.
Schema version 3 adds delivered state/date labels, last confirmed observation, and
last status-check timestamps without changing identities or private URLs.
Migrations use an explicit SQLite transaction so a failed ALTER cannot leave the
database with only some of the new columns.

Tracking URLs remain private in SQLite. REST exposes IDs, grouping IDs, recognized
delivered status/date labels, null stops, observation time, and staleness. Loaded records start
stale until revalidated during the current process lifetime. Absent packages are
not deleted or marked delivered. Shipment retirement/retention awaits the delivery
state engine; history tables are currently capped at 1,000 rows each.

## Basic delivered status

The user's September 25 clarification permits ordinary delivered-status checks before
live stop-count validation. The original live-map gate still applies to stop parsing.
Discovery reads `.delivery-box__primary-text` only within the tracking link's nearest
`.delivery-box`. It requires one visible label and one distinct visible tracking link
in that box. It never copies a status from the containing order or a neighboring package.
Missing, conflicting, hidden, or unfamiliar labels yield unknown evidence.

Only complete delivered month/day or today/yesterday labels are recognized currently.
Calendar validation rejects impossible month/day combinations; no year or delivery
time is invented. Relative wording is displayed with its observation time. The parser
does not persist arbitrary card text, products, addresses, or tracking numbers.

An unknown observation does not erase a saved delivered fact or refresh its confirmed
timestamp. Such a saved fact is marked stale when the latest scan fails to revalidate
it. Initial historical deliveries establish dashboard state; they are not new delivery
events. Return/cancellation handling and notification transitions remain separate work.

## Adding parser behavior

1. Identify the exact visible state and source on a real page.
2. Minimize the captured evidence. Keep account names, addresses, product titles,
   coordinates, cookies, auth tokens, and raw order/tracking identifiers out of fixtures.
3. Reproduce a failure with a synthetic or sanitized fixture before changing the parser.
4. Test unknown, login, challenge, missing-value, and split-shipment cases as applicable.
5. Validate the fix on the live page. Record that evidence separately from fixture tests.

Never use screenshots or successful HTTP responses alone as proof that stop-count
extraction works. No automated CAPTCHA/MFA/access-notice completion or anti-detection
changes are part of the implementation. Keep private endpoint replay out of the
primary design. Research DOM updates and the transport used by the real tracking tab.

## Container builds and certificates

```sh
make build
docker compose up -d
docker compose logs --tail=100 tracker
```

When a corporate CA is required, build a private certificate-enabled base locally and
use `sh scripts/build-image.sh --build-arg BASE_IMAGE=your-local-base:tag`. The application
Dockerfile uses public image names. Certificates in `/usr/local/share/ca-certificates`
are imported into Chromium's container-local trust store during the build. Keep
organization-specific hosts, bootstrap files, and certificates out of public source.
Use scoped CA settings for dependency/browser downloads; do not change global TLS settings.

The container runs as UID 1000. Supervisor manages Xvfb, Fluxbox, VNC, noVNC, and the
application; Chromium belongs to the application. `init: true` reaps processes.
The health check queries `/health`, not Amazon or a notification service.

## Build identity

The footer and `GET /api/v1/status` report the installed Python package version, full
build SHA (shortened in the footer), and a nullable dirty flag. `make build`/`make up`
run `scripts/build-image.sh`, capturing Git HEAD and tracked/untracked changes at the
start of the build. Ignored local data does not mark the source dirty. Committing
later does not change an existing image's stamp; rebuild it to show the new clean SHA.

GitHub passes the checked-out `github.sha` and `BUILD_DIRTY=false` into test and publish
builds. The container smoke test checks that REST reports that same SHA and clean state.
The Dockerfile stores `BUILD_SHA` and `BUILD_DIRTY` as `TRACKER_BUILD_SHA` and
`TRACKER_BUILD_DIRTY`. Unstamped builds and ordinary local `uvicorn` runs show unknown
source metadata. This identifies code provenance; it is not a cryptographic attestation.

## Documentation and delivery

Notification settings are a bounded JSON PATCH separate from the browser operation
queue. Secret fields are write-only; validation failures never echo Pydantic input.
Settings write to a temporary mode-0600 file and atomically replace the saved file.
Only explicitly supplied environment fields are managed (`model_fields_set`), so
defaults do not lock the UI. Compose imports optional `.env` without inventing default
notification environment values. UI saves do not connect to either output.

Explicit tests serialize through a separate lock, reject concurrent settings writes,
and impose a 15-second outer deadline. MQTT tests use aiomqtt and a non-retained `/test`
topic. Telegram uses aiohttp against the fixed HTTPS endpoint with redirects disabled;
provider descriptions and URL-bearing exceptions are never returned or logged. A
timeout is an uncertain outcome and is not retried. Test status is process-local and
must not be presented as continuous connection health. Tests do not publish service
availability, retained state, discovery, or shipment events yet.

The notification tests cover persistence, secret omission/clearing, environment
ownership (including false/empty values), atomic rejection, cross-origin restrictions,
request size, no send on save, concurrency, timeouts, rate limits, TLS verification,
MQTT retention, and Telegram protocol errors using synthetic credentials/responses.

Keep README for users, this guide for maintainers, and AGENTS for short agent rules.
Update the acceptance record after meaningful local/live checks. Record new scope
decisions separately rather than rewriting the user's original reviewed plan.

Work on feature branches. Push verified milestones when authorized; never include
profiles, `.env`, scratch screenshots, private tracking links, or local CA artifacts.
Local checks do not establish CI, live account, MQTT, Telegram, or Home Assistant
acceptance. Check the actual GitHub run for the pushed commit before reporting CI success.

## GitHub Actions and images

`.github/workflows/ci.yml` runs on pushes, pull requests, manual dispatch, and weekly
on the default branch once merged. It checks Python 3.12 and 3.14, audits locked runtime
Python dependencies with pip-audit, and builds/smoke-tests Linux amd64 and arm64 images
on native hosted runners. Tests use disposable profiles and never receive Amazon credentials.
The Ubuntu runner installs an AppArmor exception for the exact downloaded fixture
browser path, permitting its user-namespace sandbox. This follows
[Chromium's scoped-profile guidance](https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md).
It does not change your development machine, disable the host's global policy, or
disable Chromium sandboxing. Fixture startup failures include isolated launch details;
production errors remain sanitized.
`scripts/container_smoke.py IMAGE` starts and removes its own disposable container;
it checks browser readiness, storage permissions, API Host validation, viewer Origin/Host
validation, and framing headers. It does not mount your existing volume.

Every JavaScript action is pinned to a full commit whose `action.yml` declares Node 24.
Upgrading `setup-node` does not change another action's bundled runtime; inspect the
action metadata when updating pins. Dependabot checks Actions, Docker, and uv weekly
after its configuration reaches the default branch. `actionlint` validates workflow syntax.
Keep the `uv` executable pins in the workflow and Dockerfile in sync. Older `uv`
releases can resolve a prerelease interpreter from their bundled download catalog;
verify the actual Python version reported by pytest when adding a matrix entry.

After all checks pass, pushes to `master` publish `ghcr.io/jeeftor/amazon-tracker:edge`;
`v*` tags publish the matching semantic version. Both publish a full commit SHA tag,
multi-platform images, BuildKit provenance, and an SBOM. Feature branches and pull
requests only build/test; they have no package write permission. The first publication
requires this workflow to reach master or a release tag. There is no published-image
acceptance claim until that path actually runs. Never publish a local CA-enabled base.

See [the security review](docs/security-review.md) for findings, fixes, and remaining limits.
