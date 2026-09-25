# Amazon Delivery Tracker

Your Amazon browser stays in a local Docker service. Planned outputs include MQTT
for Home Assistant and direct Telegram notifications: “Amazon is 3 stops away” and
“Package delivered.”

**Current milestone: persistent login, package discovery, and delivered status.**
You can open Amazon, complete login or challenges through embedded noVNC, verify your
session, and discover package tracking links across recent orders. Each shipment gets
its own stable private ID, including split shipments from one order. Orders-page labels
such as **Delivered September 12** now populate each package's status. Live stop counts,
MQTT, Telegram, and Home Assistant announcements are **not implemented yet**.
See the [acceptance record](docs/acceptance.md) for actual local and live evidence.

## Start your local panel

```sh
cp .env.example .env
make up
```

Open <http://127.0.0.1:8080>. Both the panel and noVNC bind to your computer's loopback
interface. If those ports are occupied, change `PANEL_PORT` and `NOVNC_PORT` in `.env`.

The footer shows your application version and a short commit SHA. A **dirty** suffix
means the image was built with uncommitted source changes. Hover over the SHA for its
full value. `make build` and `make up` capture this metadata; a direct unstamped Docker
build shows “commit unknown” and “source state unknown” instead of claiming to be clean.

1. Select **Open Amazon / Login**. Sign into Amazon inside the embedded browser.
2. Complete any Amazon verification yourself, then select **Verify login**.
3. Once the panel confirms authentication, restart the whole container:
   `docker compose restart tracker`.
4. Select **Verify login** again. Confirm you do not need to sign in again.
5. Select **Check packages**. The scan follows recent-order pagination with 30 seconds
   between page navigations. The panel reports partial scans and unsupported links.

Discovery currently runs on demand. It does not yet schedule automatic tracking.
Your scan reads the delivery label from each package's own card. Recognized delivered
labels show their date; other labels currently show **Delivery status not recognized**.
Old records show **Delivery status not checked** until their first status scan.
Relative labels such as “today” include the observation time so they do not silently
change meaning tomorrow. Month/day labels retain Amazon's wording without guessing a year.
Repeated scans update existing packages instead of creating duplicates. Missing links
do not cause packages to be marked delivered, cancelled, or deleted.
If a later scan cannot confirm a previously observed delivery, its saved delivered
fact remains visible with a needs-refresh label. Historical deliveries do not generate
announcements; no notification event engine is enabled yet.

The verifier requires a protected orders page with visible orders/search controls,
or both an orders-page marker and a signed-in account marker for the older layout.
An unfamiliar page produces `unknown`; it never guesses authentication from a
successful HTTP response.

Your interactive browser lease lasts 15 minutes. Reopening it renews that lease.
If verification finds a login or challenge page, **Open Amazon / Login** returns
you to that page and preserves any unfinished input.
End your interactive session before requesting a browser restart. Restarting the
container preserves the named volume, profile, and session history. Your current
authentication state becomes `unknown` until you verify it again.

## Scope and privacy

This milestone supports one account on amazon.com in US English. Your Amazon password
and verification codes go directly into Amazon's page; the service has no credential
fields. Your persistent browser profile contains authenticated session data; the
database keeps private tracking links so discovery can resume. Treat the entire
`tracker-data` volume as a credential, including the installation secret used for IDs.

The current local mode has no panel password. Keep **both** published ports on
`127.0.0.1`; do not expose them on your network or through a reverse proxy. Authenticated
same-origin noVNC proxying is a later deployment gate. Remote login would require
an SSH tunnel forwarding both local ports.

The viewer checks WebSocket Origin and Host headers and restricts framing to your
local panel. These protections block hostile websites; they do not authenticate local
programs or other containers. See the [security review](docs/security-review.md).

Your API returns page roles, sanitized error codes, and verification times. It never
returns page URLs, HTML, cookies, account names, or order contents. Debug capture is
absent at this stage. SQLite records session states, discovery summaries, and private
shipment links. Session/discovery histories are capped at 1,000 rows each. Public
shipment responses contain hashed IDs and freshness, not raw links or order numbers.

The service runs as UID 1000. `/data`, the profile, and state directories use mode
0700. Chromium sandboxing is enabled with Playwright's published seccomp profile,
and Compose allocates 1 GiB shared memory.
If your container platform rejects sandbox creation, the API remains available but
reports browser startup failure; diagnose that platform constraint before changing
`CHROMIUM_SANDBOX`.

## Configuration and notifications

Current environment settings are listed in [.env.example](.env.example): panel/viewer
ports, interactive timeout, Chromium sandboxing, and `DISCOVERY_MAX_PAGES` (default 5).
Your panel currently controls login and manual discovery; notification settings have
not been added yet.

The agreed design supports **UI settings and environment variables** for both MQTT
and Telegram. Explicit environment values take precedence over saved UI values and
appear as managed fields. Saved secrets will never be echoed by the API or logs.
MQTT and Telegram can be enabled independently. See the
[notification design](docs/notifications.md) for planned options and delivery semantics.

For development setup, testing, architecture, API behavior, and certificate-enabled
local builds, read [DEVELOPMENT.md](DEVELOPMENT.md). [AGENTS.md](AGENTS.md) contains the
short coding-agent instructions. The original [reviewed plan](PLAN.md) is preserved;
the notification design records subsequent decisions.

GitHub Actions runs Python checks and native Docker builds for amd64 and arm64.
Actions use pinned commits with Node 24, with Dependabot updates configured.
Image publication is gated on passing checks and a push to `master` or a version tag;
feature branch pushes do not publish images. See [development](DEVELOPMENT.md#github-actions-and-images).

## Operations

- `docker compose logs --tail=100 tracker`: inspect process health.
- `docker compose stop tracker`: stop without removing the volume.
- `docker compose up -d`: resume with the same profile.
- `GET /health`: API liveness only; independent of Amazon.
- `GET /ready`: browser and database readiness; independent of login state.
- `GET /api/v1/status`: separate service, session, browser, and operation state.
- `GET /api/v1/shipments`: discovered packages with private IDs and freshness.
- `POST /api/v1/refresh`: one paced orders scan; identical requests coalesce.
- `POST /api/v1/session/open-login`, `/verify`, `/end-interactive`: asynchronous controls.
- `POST /api/v1/browser/restart`: requires `X-Confirm-Restart: yes`.

All control requests require `X-Tracker-Request: 1`; browser requests also undergo
same-origin checks. A submitted operation returns 202; inspect `operation` in status
for completion or failure. Duplicate in-flight actions coalesce; conflicting actions
return 409. Open Login can interrupt an in-flight discovery scan. Discovery is capped
at 12 order-page navigations per running process per hour. These local request
protections are not authentication for network exposure.

For backup, stop the container and copy the **entire** named volume using your Docker
backup tool. Keep it private, including SQLite sidecar files and browser storage.
Restore with the same UID and permissions, including `state/installation-secret`.
Without that secret, saved shipment IDs cannot be reproduced; startup refuses to
silently regenerate them. Verify your Amazon session after restore.

For a session reset, stop the container and move only `browser-profile` aside inside
your private volume before restarting and signing in again. Do not remove your whole
volume or database unless you intend to discard history too. The application never
deletes Chromium lock files automatically: first establish that no Chromium process
owns the profile before repairing a stale lock. A reset or lock repair is a manual
operation at this milestone.

## Integration direction

Keep the live Amazon tracking tab open and observe its updates. Publish facts to MQTT
and send selected events to Telegram; let your Home Assistant automation decide when
to speak. Shipment events will be
non-retained so restarting Home Assistant does not announce an old stop count.
You do not need a custom consumer WebSocket connection for this design.

## References

This is a new implementation. No source was copied from the reference applications.
The bundled Playwright seccomp profile retains its upstream license and attribution
in [docker/NOTICE.md](docker/NOTICE.md).
[Free Games Claimer](https://github.com/feldorn/free-games-claimer) is an AGPL-3.0
architecture reference. No license was identified on the reviewed
[Amazon orders sidecar repository page](https://github.com/jdm0830/amazon-orders-ha-sidecar);
its code was not incorporated. Browser behavior uses the official
[Playwright persistent context API](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context).
