# Amazon Delivery Tracker

Your Amazon browser stays in a local Docker service. Planned outputs include MQTT
for Home Assistant and direct Telegram notifications: “Amazon is 3 stops away” and
“Package delivered.”

**Current milestone: persistent login, delivered status, and notification setup.**
You can open Amazon, complete login or challenges through embedded noVNC, verify your
session, and discover package tracking links across recent orders. Each shipment gets
its own stable private ID, including split shipments from one order. Orders-page labels
such as **Delivered September 12** now populate each package's status. You can configure
and explicitly test MQTT and Telegram. Live stop counts and automatic delivery
announcements are **not implemented yet**.
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
Your panel's **Notifications** section configures MQTT and Telegram independently.
Save settings, then select **Send test**. Saving or opening the panel sends nothing.
You can test either destination with its enabled switch off. The switches save your
preference for the upcoming announcement engine; they do not activate automatic sends yet.

### MQTT

Enter your broker hostname/IP, port, and any username/password. Use TLS when your
broker supports it (commonly port 8883); certificates and hostnames are verified.
The default base topic is `amazon/tracker`. **Send test** publishes a JSON `type: test`
message with QoS 1, without retention, to `amazon/tracker/test`. Subscribe there using
Home Assistant's MQTT integration or your broker client before testing. This is separate
from future shipment events, so a setup test cannot appear as a real delivery.

Inside Docker, `localhost` means the tracker container. For a broker on your Docker
Desktop host, use `host.docker.internal`; for Home Assistant on another machine, use
that machine's reachable hostname/IP. A broker acknowledgement does not prove your
Home Assistant automation consumed the message. Confirm it at the destination.

### Telegram

1. Create your bot in Telegram with the official **@BotFather** and copy its token.
2. Open a chat with your new bot and select **Start** (or add it to your destination
   group/channel with permission to post).
3. Enter the token and destination numeric chat ID in the panel. Group IDs can be
   negative; public channels can use their `@username`.
4. Save, then select **Send test**. You should receive a clearly labeled test message.

To obtain a numeric chat ID, send your bot a message and inspect `message.chat.id`
using Telegram's official [getUpdates API](https://core.telegram.org/bots/api#getupdates)
from a private local client. Do not give your token to a third-party lookup site or
paste it into an issue. A bot already using a webhook cannot use getUpdates concurrently.
The tracker sends through the official [sendMessage API](https://core.telegram.org/bots/api#sendmessage).

### Environment overrides and secrets

Precedence is **explicit environment value > saved UI value > default**. Use the
commented variables in [.env.example](.env.example). Compose loads an optional `.env`
file (Compose 2.24 or newer); recreate the container after changing it. Values supplied
directly to the container work too. Environment-owned fields show their variable name
and are read-only, including explicitly empty values and `false`.

Leave a password/token blank to keep its saved value; select **Clear** to remove it.
Your API returns only configured/not-configured flags. UI values persist in
`/data/state/notifications.json`, mode 0600, inside the private volume. This is plaintext
storage protected by permissions, like your browser profile; include it in private backups.
Environment secrets are not copied into this file. No credentials are stored in browser storage.

Tests have a short cooldown and do not retry automatically. Telegram rate limits honor
the provider's retry delay. If a send times out, check the destination before trying
again: an external service may have accepted the message before the timeout.
See [notification decisions](docs/notifications.md) for the remaining event/delivery work.

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
- `GET /api/v1/settings/notifications`: effective values, managed fields, secret flags.
- `PATCH /api/v1/settings/notifications`: partial update; omit secrets to preserve them.
- `POST /api/v1/notifications/mqtt/test` or `/telegram/test`: explicit test, at most 15 seconds.

All control requests require `X-Tracker-Request: 1`; browser requests also undergo
same-origin checks. A submitted browser operation returns 202; inspect `operation` in status
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
