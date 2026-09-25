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
| `discovery.py` | Visible tracking anchors and constrained pagination |
| `shipments.py` | Shipment-specific source keys and installation HMAC IDs |
| `storage.py` | Schema migrations, durable identities, private links, public freshness |
| `app.py` | Operation coalescing, human takeover, sanitized REST state |
| `panel/` | Local login and package discovery UI |
| `docker/` | Supervised display/VNC stack and Chromium sandbox profile |

Continuous scheduling, tracking-page observers, delivery-state parsing, event history,
and notification transports are still pending. Do not imply that discovering a
tracking link proves a package is active, out for delivery, or delivered.

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

Tracking URLs remain private in SQLite. REST exposes IDs, grouping IDs, unknown
delivery status, null stops, observation time, and staleness. Loaded records start
stale until revalidated during the current process lifetime. Absent packages are
not deleted or marked delivered. Shipment retirement/retention awaits the delivery
state engine; history tables are currently capped at 1,000 rows each.

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
docker compose build
docker compose up -d
docker compose logs --tail=100 tracker
```

When a corporate CA is required, build a private certificate-enabled base locally and
use `docker compose build --build-arg BASE_IMAGE=your-local-base:tag`. The application
Dockerfile uses public image names. Certificates in `/usr/local/share/ca-certificates`
are imported into Chromium's container-local trust store during the build. Keep
organization-specific hosts, bootstrap files, and certificates out of public source.
Use scoped CA settings for dependency/browser downloads; do not change global TLS settings.

The container runs as UID 1000. Supervisor manages Xvfb, Fluxbox, VNC, noVNC, and the
application; Chromium belongs to the application. `init: true` reaps processes.
The health check queries `/health`, not Amazon or a notification service.

## Documentation and delivery

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
