# Amazon Delivery Tracker

Your Amazon browser stays in a local Docker service. The intended output is MQTT state
and events that Home Assistant can announce: “Amazon is 3 stops away” and “Package delivered.”

**Current milestone: persistent browser and interactive login (plan phases 0–1).**
You can open Amazon, complete login or challenges through embedded noVNC, verify your
session, and restart the browser while keeping its profile. Delivery discovery,
live stop observation, MQTT, and Home Assistant integration remain gated on real Amazon
acceptance. See your original [reviewed plan](PLAN.md) and [acceptance record](docs/acceptance.md).

## Start your local panel

```sh
cp .env.example .env
docker compose up --build -d
```

Open <http://127.0.0.1:8080>. Both the panel and noVNC bind to your computer's loopback
interface. If those ports are occupied, change `PANEL_PORT` and `NOVNC_PORT` in `.env`.

1. Select **Open Amazon / Login**. Sign into Amazon inside the embedded browser.
2. Complete any Amazon verification yourself, then select **Verify login**.
3. Once the panel confirms authentication, restart the whole container:
   `docker compose restart tracker`.
4. Select **Verify login** again. Confirm you do not need to sign in again.

The verifier requires both an orders-page marker and a signed-in account marker.
An unfamiliar page produces `unknown`; it never guesses authentication from a
successful HTTP response. The selectors need validation against your account.

Your interactive browser lease lasts 15 minutes. Reopening it renews that lease.
End your interactive session before requesting a browser restart. Restarting the
container preserves the named volume, profile, and session history. Your current
authentication state becomes `unknown` until you verify it again.

## Scope and privacy

This milestone supports one account on amazon.com in US English. Your Amazon password
and verification codes go directly into Amazon's page; the service has no credential
fields. Your persistent browser profile still contains authenticated session data:
treat the `tracker-data` volume as a credential.

The current local mode has no panel password. Keep **both** published ports on
`127.0.0.1`; do not expose them on your network or through a reverse proxy. Authenticated
same-origin noVNC proxying is a later deployment gate. Remote login would require
an SSH tunnel forwarding both local ports.

Your API returns page roles, sanitized error codes, and verification times. It never
returns page URLs, HTML, cookies, account names, or order contents. Debug capture is
absent at this stage. SQLite records only session states and fixed reason codes,
with a maximum of 1,000 history rows. Browser crash details stay out of API responses.

The service runs as UID 1000. `/data`, the profile, and state directories use mode
0700. Chromium sandboxing is enabled with Playwright's published seccomp profile,
and Compose allocates 1 GiB shared memory.
If your container platform rejects sandbox creation, the API remains available but
reports browser startup failure; diagnose that platform constraint before changing
`CHROMIUM_SANDBOX`.

## Development

Python 3.12 or newer and `uv` are required. Plain `make` prints help.

```sh
uv sync --locked
uv run playwright install chromium --only-shell
make check
```

Tests use temporary profiles and synthetic pages intercepted locally. They do not
sign into Amazon or use your Docker profile. They cover profile ownership across
processes, concurrency, interactive timeout, session classification, request
coalescing, retained cookies across browser restart, and local API protection.

For a panel-only development server with a headless browser:

```sh
BROWSER_HEADLESS=true uv run uvicorn amazon_tracker.app:create_app --factory --host 127.0.0.1
```

The embedded interactive viewer requires the Docker desktop stack. Use exactly one
application worker; the profile lock rejects another process owning the same profile.

Your Dockerfile defaults to public upstream images. For a local certificate-enabled
base, build that base privately and pass `--build-arg BASE_IMAGE=your-local-base:tag`.
Custom certificates installed in `/usr/local/share/ca-certificates` in that base
are also imported into Chromium's container-local trust store during the build.
Keep organization-specific registry names and certificate bootstrap files outside
tracked source. No global tooling or TLS configuration is required.

## Operations

- `docker compose logs --tail=100 tracker`: inspect process health.
- `docker compose stop tracker`: stop without removing the volume.
- `docker compose up -d`: resume with the same profile.
- `GET /health`: API liveness only; independent of Amazon.
- `GET /ready`: browser and database readiness; independent of login state.
- `GET /api/v1/status`: separate service, session, browser, and operation state.
- `POST /api/v1/session/open-login`, `/verify`, `/end-interactive`: asynchronous controls.
- `POST /api/v1/browser/restart`: requires `X-Confirm-Restart: yes`.

All control requests require `X-Tracker-Request: 1`; browser requests also undergo
same-origin checks. A submitted operation returns 202; inspect `operation` in status
for completion or failure. Duplicate in-flight actions coalesce; conflicting actions
return 409. These local request protections are not authentication for network exposure.

For backup, stop the container and copy the **entire** named volume using your Docker
backup tool. Keep it private, including SQLite sidecar files and browser storage.
Restore with the same UID and permissions. Verify your Amazon session after restore.

For a session reset, stop the container and move only `browser-profile` aside inside
your private volume before restarting and signing in again. Do not remove your whole
volume or database unless you intend to discard history too. The application never
deletes Chromium lock files automatically: first establish that no Chromium process
owns the profile before repairing a stale lock. A reset or lock repair is a manual
operation at this milestone.

## Integration direction

Keep the live Amazon tracking tab open and observe its updates. Publish facts to MQTT;
let your Home Assistant automation decide when to speak. Shipment events will be
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
