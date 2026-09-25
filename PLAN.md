# Amazon Live Delivery Tracker - Architecture Review and Revised Plan v2

**Status:** Pre-implementation design review  
**Purpose:** Review the original `plan.md` before any build work begins.  
**Primary architectural change:** keep the standalone Docker + browser + MQTT design, but promote interactive login/control to a first-class v1 component and replace repeated live-page polling with a persistent live-tracking observer whenever Amazon's page supports it.

---

## 0. Review conclusion

The original plan is directionally strong. Its most important decisions should remain:

- keep Amazon browser/session ownership outside Home Assistant;
- use a persistent Chromium profile;
- use a human-controlled browser surface for login/MFA/challenges;
- normalize Amazon-specific data behind a stable internal model;
- publish state/events through MQTT;
- keep REST for control, diagnostics, and non-MQTT clients;
- avoid depending on a private Amazon endpoint unless observation of the real web page proves that necessary.

However, several areas should change **before implementation**:

1. The login/control panel should be part of v1, not a later convenience.
2. The browser profile needs a single explicit owner and a concurrency lock.
3. Live delivery tracking should prefer an open tracking tab with DOM/network/WebSocket observation over a 30-60 second reload loop.
4. Service health, Amazon-session health, shipment state, and tracking-visibility state must be separate domains.
5. Shipment identity must not rely primarily on an Amazon order number because one order can produce multiple shipments.
6. MQTT service availability must be separate from Amazon login/challenge status.
7. MQTT transient events should be non-retained; dashboard state can be retained or republished deliberately.
8. Home Assistant Discovery should avoid creating permanent ghost entities for every ephemeral shipment.
9. SQLite should be the default state store rather than "JSON or SQLite".
10. Multi-process container supervision, stale Chromium/VNC lock cleanup, and security around noVNC/profile storage must be designed before coding.
11. `amazon-orders-ha-sidecar` and Feldorn's Free Games Claimer should be treated primarily as architecture references, not copied wholesale.
12. We should add explicit go/no-go technical gates before investing in MQTT/HA polish.

The revised architecture is:

```text
                         Amazon web application
                                  |
                                  v
                 +----------------------------------+
                 | Chromium persistent profile      |
                 | one profile, one browser owner   |
                 +----------------+-----------------+
                                  |
                    BrowserSessionManager / mutex
                                  |
             +--------------------+--------------------+
             |                                         |
             v                                         v
  Interactive login/control                   Automation workers
  - embedded noVNC                            - order/ship discovery
  - Open Amazon                               - live tracking observer
  - Verify login                              - watchdog/recovery
  - challenge handoff                         - parser adapters
             |                                         |
             +--------------------+--------------------+
                                  |
                                  v
                        Normalization + diff
                                  |
                                  v
                              SQLite
                             /      \
                            v        v
                          REST      MQTT
                                     |
                          +----------+----------+
                          |                     |
                    Home Assistant          Node-RED/etc.
```

The project should still be a standalone service. Home Assistant remains only a consumer.

---

## 1. What to borrow from Feldorn's Free Games Claimer

### 1.1 Use the **login/control-plane pattern**, not the application itself

Feldorn's project has a useful operational pattern for browser-backed automation:

- an always-on local control panel;
- a persistent browser profile;
- an embedded noVNC browser view;
- an explicit Login/Open Browser action;
- a separate session-health/verification action;
- human handoff for MFA/CAPTCHA/challenges;
- persistent browser state across container restarts;
- a browser-busy concept that prevents automated work from colliding with an interactive login session;
- optional same-origin noVNC proxying behind panel authentication.

That pattern is a better fit than exposing a naked `:6080` noVNC port as the primary UX.

### 1.2 Proposed Amazon login UX

The v1 panel should show:

```text
Amazon Session
  Status: Authenticated / Login required / Challenge / Unknown
  Last verified: 12:34:56

  [Open Amazon / Login]
  [Verify Login]
  [Show Browser]
  [Close Interactive Browser]
```

Workflow:

```text
1. User clicks Open Amazon / Login.
2. BrowserSessionManager acquires interactive ownership.
3. Chromium opens Amazon in the persistent profile.
4. noVNC view becomes visible in the panel.
5. User performs normal Amazon login, MFA, CAPTCHA, etc.
6. User clicks Verify Login.
7. The application performs a real authenticated-page check.
8. If authenticated, the interactive session is released.
9. Automation resumes using the same persistent profile.
```

The application does **not** ask for or store an Amazon password or MFA secret.

### 1.3 Browser-busy mutex is mandatory

This is a major missing piece in the original plan.

A persistent Chromium `user_data_dir` cannot safely be owned by competing browser instances. Manual login, order discovery, tracking observation, debug capture, and browser recovery must therefore coordinate through one owner.

Introduce:

```text
BrowserSessionManager
  - current_owner
  - mode: idle | automation | interactive | debug | restarting
  - acquire(owner, timeout)
  - release(owner)
  - browser/context/page registry
  - stale-owner watchdog
  - restart()
```

Rules:

- only one Chromium process owns the profile;
- interactive login has priority over scheduled scraping;
- scheduled refreshes are coalesced while interactive mode is active;
- browser restart waits until the lock is safely released or forcibly recovers a stale owner;
- debugging cannot spawn a second browser against the same profile;
- API endpoints return `409 Conflict` or queue work when the browser is intentionally busy.

### 1.4 Do not copy Feldorn's browser stack blindly

Feldorn currently uses Patchright/anti-detection techniques. This Amazon tracker should start with **stock Playwright + Chromium**.

Why:

- our goal is to use a normal authenticated browser session, not to automate login challenges;
- manual login through noVNC is acceptable;
- anti-detection layers add maintenance and obscure browser behavior during reverse engineering;
- avoiding them keeps the initial experiment easier to reason about.

If normal Chromium proves unusable despite human login, that is a later design decision, not a day-one dependency.

### 1.5 Licensing boundary

Feldorn's repository is AGPL-3.0 licensed. That is fine if we intentionally choose to create an AGPL derivative, but it is not something to stumble into accidentally.

Recommended approach:

- study the documented behavior and architecture;
- reimplement the login/control-flow concepts in our own code;
- do not copy substantial source code unless we deliberately adopt compatible licensing and its obligations.

The same caution applies to any project with no clearly identified license: use it as a reference, not as a source-code donor.

---

## 2. Reassessment of `amazon-orders-ha-sidecar`

The existing sidecar remains useful as evidence that the basic operational model works:

- persistent Chromium profile;
- manual login through noVNC;
- session persistence;
- Amazon order-page parsing;
- JSON state for another consumer;
- browser ownership outside Home Assistant.

But it should **not** be assumed to be the codebase we fork.

Reasons:

1. It explicitly does not scrape tracking-detail pages, which is the core of this project.
2. The live-tracking observer will change the runtime architecture substantially.
3. The revised project needs a control panel, MQTT/event model, SQLite, and a browser lock manager.
4. A clean implementation lets us design the concurrency and state domains correctly rather than stretching an order-list scraper into a long-running observer.
5. Before copying any code, its license status must be verified explicitly.

Decision for v2 plan:

> **New repository, clean architecture.** Use `amazon-orders-ha-sidecar` as a behavioral/reference implementation for persistent Chromium + noVNC and as a source of Amazon-order-page observations, not as the assumed parent repository.

---

## 3. Major change: observe the live page instead of repeatedly refreshing it

The original plan's 30-60 second polling while `stops_remaining` is available is acceptable as a fallback, but should not be the primary live-tracking mechanism.

Amazon says Map Tracking becomes available when a package is close enough, commonly when it reaches the final ten stops. At that point Amazon's own page is designed to behave as a live page.

The better strategy is:

```text
Order poll discovers active shipment
        |
        v
Tracking page becomes map/live-capable
        |
        v
Open one persistent tracking tab
        |
        +--> read initial DOM
        +--> attach MutationObserver-equivalent observation
        +--> observe fetch/XHR responses
        +--> observe WebSocket traffic if the page creates one
        |
        v
Normalize each observed state change
        |
        v
Publish event immediately
```

### 3.1 Why this is better

- fewer full navigations and page reloads;
- lower request volume;
- lower chance of tripping site defenses;
- faster updates if the page itself receives push/poll updates more frequently than our scheduler;
- easier to distinguish "Amazon did not update" from "our scheduled poll has not run yet";
- no need to recreate an internal Amazon request if the browser already makes it correctly.

### 3.2 Live observer sources, in priority order

For each live tracking tab:

1. **DOM state**
   - read current rendered stop text;
   - observe relevant DOM changes;
   - safest and closest to what the user actually sees.

2. **Structured response observation**
   - attach Playwright `response` listeners;
   - identify route/tracking responses;
   - parse only the minimum fields needed.

3. **WebSocket observation**
   - if the page creates a WebSocket, Playwright can inspect frames;
   - this is observation of Amazon's own live page transport, not our public API design.

4. **Periodic reload watchdog**
   - only if the live page becomes stale or Amazon's page does not update itself.

5. **Direct internal endpoint**
   - last resort;
   - only after we understand the browser request contract and can justify the maintenance/risk.

### 3.3 WebSocket distinction

There are two completely different WebSocket questions:

- **Should our service expose package data to Home Assistant over a custom WebSocket?** No. MQTT is better.
- **Should our browser observer listen to a WebSocket Amazon's page already uses?** Possibly, and Playwright supports this.

That distinction should be explicit in the architecture document.

---

## 4. Revised state model

The original plan mixes service/session failure states into shipment delivery status. That makes downstream consumers ambiguous.

Split state into four independent domains.

### 4.1 Service state

```text
service.state:
  starting
  online
  degraded
  stopping
```

Examples of degradation:

- browser repeatedly crashing;
- SQLite write failures;
- scheduler unhealthy;
- parser errors above threshold.

### 4.2 Amazon session state

```text
session.state:
  unknown
  authenticated
  needs_login
  challenge
  checking
```

Optional reason fields:

```json
{
  "state": "needs_login",
  "reason": "redirected_to_signin",
  "last_verified_at": "..."
}
```

### 4.3 Shipment state

```text
shipment.status:
  unknown
  purchased
  shipped
  in_transit
  out_for_delivery
  delivered
  delayed
  cancelled
  returned
```

`needs_login`, `challenge`, and `error` do **not** belong here.

### 4.4 Tracking visibility/state

```text
tracking.visibility:
  unknown
  unavailable
  available
  map_available
  stops_available
  stale
```

This matters because a valid out-for-delivery shipment can temporarily have no stop count without being an error.

### 4.5 Freshness

Every externally visible value should carry freshness metadata:

```json
{
  "observed_at": "...",
  "source_updated_at": null,
  "stale_after_seconds": 120,
  "is_stale": false
}
```

Never convert stale data to a fake current value.

---

## 5. Shipment identity: order != package != tracking route

The original plan leans too heavily on an order ID hash.

Amazon can split one order across several shipments, and multiple packages can potentially share a delivery route/vehicle. Our identity model needs to handle this.

### 5.1 Internal IDs

Prefer a stable hierarchy:

```text
order_id       internal/hmac hash of order identifier
shipment_id    stable hash of the best shipment/tracking key available
tracking_id    normalized carrier tracking identifier, if available
route_id       optional ephemeral route/map-session identifier, if discoverable
```

Primary entity for live tracking should be **shipment**, not order.

### 5.2 Privacy-preserving IDs

Instead of a short unsalted plain hash of an order/tracking number, prefer HMAC with a locally generated installation secret:

```text
shipment_public_id = HMAC-SHA256(local_secret, source_identifier)[0:16]
```

Benefits:

- stable across restarts;
- not reversible by simple dictionary guessing;
- raw tracking/order identifiers need not be exposed to MQTT/REST clients.

Store the raw identifier only if needed internally, encrypted-at-rest only if future threat model requires it. For a local-only v1, minimizing persistence is better than adding a ceremonial encryption layer with a key stored beside the database.

---

## 6. Stop-count semantics need to be conservative

### 6.1 Preserve exactly what Amazon exposes

Model:

```json
{
  "stops_remaining": 3,
  "stops_text": "3 stops away",
  "stops_observed_at": "..."
}
```

Use `null` when not visible.

### 6.2 Do not force zero

"0 stops" may not be a real Amazon state. The UI may transition from `1 stop away` to wording such as arriving next/nearby/delivered.

Therefore model an additional semantic phase if observed:

```text
arrival_phase:
  null
  stops
  arriving_next
  arriving_now
```

Do not manufacture `stops_remaining = 0` unless Amazon actually provides zero.

### 6.3 Stop count can increase

A route can change. The state engine should record upward changes as real observations instead of treating them as impossible parser failures.

Default announcement policy:

- announce meaningful decreases;
- do not speak increases by default;
- log/store the increase;
- optional user setting can announce route changes.

### 6.4 Missing stop count is not automatically a parser failure

If the page remains valid and out-for-delivery but the count disappears:

```text
tracking.visibility = map_available or available
stops_remaining = null
```

Only declare parser failure if we have evidence the expected page structure/data source broke.

---

## 7. Revised scheduler model

There are now two scheduling modes: **discovery polling** and **live observation**.

### 7.1 Discovery polling

Suggested initial defaults:

| Situation | Interval |
|---|---:|
| No active shipment | 20-30 min |
| Shipment active but not out for delivery | 10 min |
| Out for delivery, live map not available | 2-5 min |
| Session requires login | 5 min lightweight session check, no heavy tracking scrape |
| Amazon/network failure | exponential backoff with jitter |

### 7.2 Live observation

When map/stops are available:

- keep tracking page open;
- process DOM/network/WS updates as they occur;
- run a lightweight freshness watchdog every 30-60 sec;
- perform a page reload only if the observer is stale beyond a threshold, e.g. 2-5 minutes;
- periodically confirm the shipment still exists in the order list on a much slower cadence.

### 7.3 Global budgets

Add explicit budgets:

```text
MAX_TRACKING_TABS=3             # configurable
MIN_FULL_NAVIGATION_INTERVAL=30s
MAX_ORDER_POLLS_PER_HOUR=12
MAX_TRACKING_RELOADS_PER_HOUR=20 per active shipment
```

These are safe starting guards, not promises about Amazon's tolerance.

### 7.4 Coalescing

If three callers request refresh while a refresh is running:

- execute one refresh;
- mark it as satisfying all pending requests;
- never create three browser navigations.

---

## 8. Browser lifecycle and page ownership

Use a single persistent browser context with a small page registry.

```text
BrowserSessionManager
  context
  pages:
    admin/login page
    orders page
    tracking:<shipment_id>
```

### 8.1 Page policy

- one reusable orders page;
- one live tracking page per actively observed shipment, up to configurable cap;
- close delivered/stale tracking pages;
- do not continuously create/discard tabs;
- interactive user may navigate the admin/login page freely without destroying automation pages where possible.

### 8.2 Interactive takeover policy

For v1, use the safest behavior:

1. user requests interactive login;
2. suspend new automation navigations;
3. keep existing observer pages only if safe;
4. expose a dedicated interactive page in same context;
5. verify login;
6. close or park interactive page;
7. resume automation.

If Amazon challenge state affects the whole context, automation should pause entirely until resolved.

### 8.3 Restart recovery

At startup:

1. clean stale Chromium runtime lock artifacts only when no Chromium process owns the profile;
2. start X server/window manager/VNC stack;
3. launch persistent browser;
4. restore session state;
5. query SQLite for active shipments;
6. revalidate them against Amazon;
7. recreate live observers only when still eligible.

Never blindly delete profile data as crash recovery.

---

## 9. v1 control panel is now a required component

The original plan said "provide a small `/admin` page later." This should move to Phase 1.

### 9.1 Minimal panel, not a product dashboard

Required cards:

**Amazon Session**

- state;
- last verified;
- Open Amazon/Login;
- Verify Login;
- Show Browser;
- End Interactive Session.

**Browser**

- running/stopped;
- current owner/mode;
- active pages;
- restart button, guarded by confirmation.

**Tracker**

- last order discovery;
- number of active shipments;
- number of live observers;
- last successful tracking observation;
- manual refresh button.

**MQTT**

- enabled/disabled;
- connected/disconnected;
- broker host (redacted as appropriate);
- last publish/error.

**Diagnostics**

- last parser error;
- download/create sanitized debug bundle;
- log tail.

### 9.2 Embedded noVNC

Preferred deployment when panel auth is enabled:

```text
https://tracker.local/
https://tracker.local/novnc/...
```

The panel reverse-proxies both noVNC HTTP assets and its WebSocket upgrade so the same application auth/session protects the browser framebuffer.

Simpler local-only v1 deployment may still expose:

```text
127.0.0.1:6080
127.0.0.1:8080
```

through SSH tunnel, but the code should not assume raw port 6080 must remain public forever.

### 9.3 Panel authentication

If API/panel binds beyond localhost:

- require a local admin password/token;
- store a password hash, not plaintext;
- rate-limit login attempts;
- CSRF-protect mutation endpoints if cookie sessions are used;
- set secure/HTTP-only/same-site cookies behind TLS;
- never expose raw noVNC separately when same-origin proxy is in use.

For strictly localhost-only operation, auth can remain optional.

---

## 10. Revised MQTT design

The original topic tree is close, but service availability and Amazon session state must be separated.

### 10.1 Service LWT/availability

```text
amazon/tracker/availability
```

Payload only:

```text
online
offline
```

Use MQTT Last Will for `offline` and publish `online` after startup.

Do **not** publish `needs_login` or `challenge` here. Those mean the process is alive.

### 10.2 Service state

```text
amazon/tracker/state
```

```json
{
  "service_state": "online",
  "browser_state": "running",
  "session_state": "authenticated",
  "active_shipments": 2,
  "live_observers": 1,
  "updated_at": "..."
}
```

### 10.3 Session state

```text
amazon/tracker/session/state
```

```json
{
  "state": "needs_login",
  "reason": "signin_redirect",
  "last_verified_at": "..."
}
```

### 10.4 Shipment state

```text
amazon/tracker/shipments/<shipment_id>/state
```

```json
{
  "status": "out_for_delivery",
  "tracking_visibility": "stops_available",
  "stops_remaining": 3,
  "eta": "...",
  "observed_at": "...",
  "is_stale": false
}
```

State is suitable for dashboard consumers. Retain policy should be deliberate. If retained, the payload must include freshness and retired entities must be cleared.

### 10.5 Shipment events

```text
amazon/tracker/shipments/<shipment_id>/events
```

Examples:

```json
{
  "type": "stops_changed",
  "from": 4,
  "to": 3,
  "observed_at": "...",
  "event_id": "..."
}
```

Events are **not retained**.

This is what TTS/automation should normally consume because replaying old retained state after a restart must not cause "3 stops away" to be spoken again.

### 10.6 Event de-duplication

Persist generated event IDs/state transitions in SQLite so an application restart does not replay the same transition as a new event.

Example event key:

```text
shipment_id + event_type + normalized_new_value + source_observed_at
```

or a monotonic per-shipment sequence stored transactionally.

---

## 11. Home Assistant integration strategy

### 11.1 MQTT Discovery is still optional and useful

But dynamic per-shipment entities have lifecycle problems.

If every delivery creates a newly discovered sensor forever, Home Assistant can accumulate ghost entities unless the tracker explicitly removes their discovery configs.

Two reasonable modes:

#### Mode A: fixed summary device, recommended default

Expose stable entities:

```text
sensor.amazon_active_shipments
sensor.amazon_nearest_stops
sensor.amazon_next_eta
sensor.amazon_delivery_status
binary_sensor.amazon_login_required
```

Put richer shipment data in attributes or raw MQTT topics.

Pros:

- clean HA registry;
- little entity churn;
- easy automations.

#### Mode B: per-shipment entities, optional

Create temporary discovered entities per shipment.

Requirements:

- stable unique ID while shipment exists;
- on retirement, publish empty retained discovery payload to delete entity config;
- clear retained state if used;
- keep a small retirement grace period so delivered state remains visible long enough for automations/UI.

### 11.2 Discovery republish

Subscribe to Home Assistant's birth topic (`homeassistant/status`) and republish discovery/state when HA comes online, rather than assuming discovery config will always be retained forever.

### 11.3 TTS automation input

Prefer event messages or a stable event entity rather than state-change logic over retained dynamic sensors.

Example conceptual event:

```json
{
  "type": "stops_changed",
  "shipment_id": "shp_abcd",
  "to": 3
}
```

HA can announce only when `to <= 5` and the event is new.

---

## 12. SQLite should be the default persistence layer

Do not leave persistence undecided.

Use SQLite in WAL mode from v1.

Tables conceptually:

```text
settings/meta
shipments
shipment_observations
shipment_events
session_history
scheduler_state
```

### 12.1 Why SQLite now

The service has concurrent responsibilities:

- scheduler;
- browser observers;
- API reads;
- MQTT publisher;
- event de-duplication;
- restart recovery.

A JSON file quickly turns into locking/atomicity code pretending not to be a database.

### 12.2 Retention

Suggested defaults:

- current/active shipments: indefinitely while active;
- delivered shipment summary: 30 days;
- observation history: 7 days;
- event history: 30 days;
- sanitized debug captures: 3 days unless pinned;
- browser profile: until manually reset.

Make these configurable.

---

## 13. REST/API revisions

Versioned API:

```text
GET  /health
GET  /ready
GET  /api/v1/status
GET  /api/v1/session
POST /api/v1/session/open-login
POST /api/v1/session/verify
POST /api/v1/session/end-interactive
GET  /api/v1/shipments
GET  /api/v1/shipments/{id}
POST /api/v1/refresh
POST /api/v1/browser/restart
POST /api/v1/debug/shipments/{id}/capture
```

### 13.1 `/health`

Liveness only:

```json
{"status":"ok"}
```

No browser navigation, MQTT call, or Amazon request.

### 13.2 `/ready`

Readiness should report components separately:

```json
{
  "ready": true,
  "database": "ok",
  "browser": "ok",
  "scheduler": "ok",
  "mqtt": "ok",
  "amazon_session": "authenticated"
}
```

Whether MQTT or Amazon session blocks readiness should be configurable. A live API can still be operational while Amazon login needs attention.

### 13.3 `POST /refresh`

Do not perform browser work inline.

Return:

```text
202 Accepted
```

with an operation ID or current coalesced refresh ID.

The scheduler/browser owner performs the work asynchronously.

### 13.4 Conflict responses

If interactive login owns the browser:

```text
409 Conflict
```

or:

```json
{
  "accepted": true,
  "queued": true,
  "reason": "interactive_browser_session"
}
```

Choose one consistent API policy.

---

## 14. Reverse-engineering workflow, revised

This is still the decisive technical experiment.

### Gate A: persistent session

Before parsing anything:

- launch the container;
- log into Amazon manually;
- restart the whole container;
- confirm the session survives;
- verify no second login is required while session is valid.

**Go/no-go:** persistent profile is reliable enough for normal use.

### Gate B: live stop source

During a real out-for-delivery package with Map Tracking:

Capture, in a redacted debug session:

1. DOM text and relevant subtree;
2. HTML/embedded JSON references around tracking state;
3. fetch/XHR URLs and safe metadata;
4. response content only for candidate tracking endpoints and only in sanitized debug output;
5. WebSocket creation + frame metadata if present;
6. DOM changes over time without reloading.

**Question to answer:** where exactly does `N stops away` originate, and how does it change while the page remains open?

### Gate C: transition observation

Keep the page open and observe several real transitions, for example:

```text
8 -> 7 -> 6 -> 5
```

or whatever values occur naturally.

Measure:

- event latency;
- whether page reload was necessary;
- whether DOM changed;
- whether a network response caused the change;
- whether a WebSocket was involved;
- whether route state vanished/reappeared.

**Go/no-go:** at least one robust source can be observed without repeatedly replaying a private endpoint.

### Gate D: parser fixtures

Only after Gate C succeeds should we commit parser behavior to fixtures/tests.

Sanitize fixtures to remove:

- names;
- delivery address;
- coordinates;
- cookies/tokens;
- raw order/tracking identifiers;
- product titles if not necessary for parser tests.

### Gate E: event engine

Feed recorded/synthetic observations through normalizer/diff logic before MQTT exists.

Verify:

- 5 -> 4 generates one event;
- duplicate 4 generates none;
- 4 -> null is not automatically parser failure;
- 4 -> 6 is recorded but not announced by default;
- 1 -> arriving_next does not fabricate zero;
- delivered closes live observer.

Only after these gates should MQTT/HA integration become a major work item.

---

## 15. Diagnostics and privacy

### 15.1 Debug levels

```text
DEBUG_CAPTURE=off       # default
DEBUG_CAPTURE=metadata  # URLs redacted, timing, response MIME/size
DEBUG_CAPTURE=targeted  # selected sanitized DOM/response snippets
```

Never have a generic "dump all network traffic" mode as the default.

### 15.2 Redaction pipeline

Before writing any debug artifact:

- strip `Cookie`;
- strip `Authorization`;
- strip known CSRF/session tokens;
- redact query parameters by default;
- redact street address and coordinates where detectable;
- hash order/tracking IDs;
- remove product titles unless explicitly requested;
- cap body sizes.

### 15.3 Debug bundle

A support bundle can contain:

```text
manifest.json
service-status.json
sanitized-log.txt
sanitized-dom-snippets/
network-metadata.jsonl
parser-version.txt
```

No browser profile, cookie database, local storage, or raw HAR by default.

---

## 16. Security review

### 16.1 Browser profile is a credential

Treat `/data/browser-profile` as equivalent to a login secret.

Requirements:

- directory mode `0700` where practical;
- mounted only into this service;
- never include it in support bundles/backups sent elsewhere;
- never expose it through the REST API;
- warn users that copying it copies authenticated session state.

### 16.2 Container user

Prefer non-root runtime.

Support `PUID`/`PGID` or a fixed unprivileged UID. Configure Chromium so we do not depend on `--no-sandbox` unless platform constraints force it.

### 16.3 Chromium shared memory

Provide adequate `/dev/shm`, e.g. Compose `shm_size`, rather than papering over crashes with unsafe flags.

### 16.4 Network exposure

Default ports:

```text
127.0.0.1:8080 -> panel/API
127.0.0.1:6080 -> raw noVNC only in simple/local mode
```

LAN/public exposure requires authentication + TLS/reverse proxy.

### 16.5 MQTT

- broker credentials from secrets/env, not logs;
- TLS if traffic crosses an untrusted network;
- ACL restricted to tracker topic tree;
- no public broker.

---

## 17. Container/process architecture

The "one container" decision is still reasonable, but the original plan under-specifies how multiple processes are managed.

The container will likely run:

```text
Xvfb (or equivalent virtual X server)
window manager
VNC server
websockify/noVNC
Python application
Chromium child processes
```

Use a real process supervisor or a carefully designed init/supervision layer.

Options:

- `s6-overlay`;
- `supervisord`;
- a purpose-built entrypoint with explicit child health/reaping, though this is easier to get wrong.

Do not rely on a chain of shell commands ending in several background `&` processes.

### 17.1 Supervised component dependencies

Suggested order:

```text
X server
  -> window manager
      -> VNC/noVNC
      -> application
          -> Chromium
```

The API should remain able to report browser failure even if Chromium restarts.

### 17.2 Health checks

Container health should hit the lightweight local `/health` endpoint.

Do not make Docker health depend directly on Amazon availability or MQTT broker availability, otherwise an Amazon outage can make Docker restart a perfectly healthy service forever.

---

## 18. Revised repository layout

```text
amazon-delivery-tracker/
|
+- Dockerfile
+- compose.yml
+- README.md
+- PLAN.md
+- LICENSE
+- .env.example
+- pyproject.toml
+- docker/
|  +- supervisor/
|  +- novnc/
|  +- entrypoint/
|
+- src/amazon_tracker/
|  +- main.py
|  +- config.py
|  +- models/
|  |  +- service.py
|  |  +- session.py
|  |  +- shipment.py
|  |  +- events.py
|  +- browser/
|  |  +- manager.py
|  |  +- pages.py
|  |  +- locks.py
|  |  +- recovery.py
|  +- amazon/
|  |  +- session.py
|  |  +- orders.py
|  |  +- tracking.py
|  |  +- live_observer.py
|  |  +- dom_extractors.py
|  |  +- response_extractors.py
|  |  +- websocket_extractors.py
|  |  +- selectors.py
|  +- state/
|  |  +- normalizer.py
|  |  +- diff.py
|  |  +- freshness.py
|  |  +- scheduler.py
|  +- storage/
|  |  +- db.py
|  |  +- migrations/
|  +- outputs/
|  |  +- mqtt.py
|  |  +- ha_discovery.py
|  +- api/
|  |  +- app.py
|  |  +- routes_status.py
|  |  +- routes_session.py
|  |  +- routes_shipments.py
|  |  +- routes_debug.py
|  +- panel/
|  |  +- templates/
|  |  +- static/
|  |  +- novnc_proxy.py
|  +- diagnostics/
|     +- capture.py
|     +- redact.py
|     +- bundle.py
|
+- tests/
|  +- fixtures/
|  +- unit/
|  +- integration/
|  +- browser_sim/
|
+- scripts/
   +- dev_capture_tracking.py
   +- inspect_db.py
```

The separation matters because Amazon parsing will change far more often than the state/MQTT/API layers.

---

## 19. Technology choices

### Python

Python 3.12+ remains a good fit.

### Playwright

Use official Playwright for Python and Chromium initially.

Required capabilities:

- persistent context;
- DOM observation;
- request/response listeners;
- WebSocket observation if present;
- screenshots/debug artifacts;
- controlled page lifecycle.

### FastAPI

FastAPI + Uvicorn remains appropriate for REST and the small control panel backend.

For the panel UI, avoid introducing a heavy SPA framework in v1. Server-rendered templates + small JavaScript is enough.

### MQTT

Use an asyncio-friendly client if it integrates cleanly with the selected event loop. `paho-mqtt` is fine if isolated correctly, but do not let its callback threading leak unpredictably into browser/SQLite state.

### SQLite

Use `sqlite3`/`aiosqlite` with WAL and explicit migrations.

### Models

Pydantic for API/config boundaries. Dataclasses/domain objects are also acceptable internally. Do not force every hot-path observation through expensive validation if profiling later shows it unnecessary.

---

## 20. Revised configuration

Example:

```text
TZ=America/Chicago

DATA_DIR=/data
BROWSER_DATA_DIR=/data/browser-profile
DATABASE_PATH=/data/state/tracker.sqlite3
INSTALLATION_SECRET_FILE=/data/state/installation-secret

PANEL_LISTEN=0.0.0.0
PANEL_PORT=8080
PANEL_AUTH_ENABLED=false
PANEL_PASSWORD_HASH=

NOVNC_MODE=embedded
NOVNC_RAW_LISTEN=127.0.0.1
NOVNC_RAW_PORT=6080

AMAZON_MARKETPLACE=amazon.com
AMAZON_LOCALE=en-US
AMAZON_ORDERS_URL=https://www.amazon.com/your-orders/orders?timeFilter=last30

DISCOVERY_IDLE_SECONDS=1800
DISCOVERY_ACTIVE_SECONDS=600
DISCOVERY_OFD_SECONDS=180
LIVE_STALE_SECONDS=180
LIVE_RELOAD_MIN_SECONDS=60
MAX_TRACKING_TABS=3

MQTT_ENABLED=true
MQTT_HOST=mosquitto
MQTT_PORT=1883
MQTT_USERNAME=
MQTT_PASSWORD=
MQTT_TLS=false
MQTT_BASE_TOPIC=amazon/tracker

HA_DISCOVERY_ENABLED=true
HA_DISCOVERY_MODE=summary
HA_DISCOVERY_PREFIX=homeassistant

LOG_LEVEL=INFO
DEBUG_CAPTURE=off
```

v1 should explicitly target **amazon.com / US English**. Other marketplaces/locales become an adapter/testing project later rather than an accidental promise.

---

## 21. Revised implementation phases and gates

### Phase 0 - Architectural spike, no product code commitment

Deliverables:

- verify licenses of reference projects;
- prototype one container with Chromium + X + noVNC;
- persistent profile survives restart;
- BrowserSessionManager skeleton with exclusive ownership;
- no MQTT, no HA.

Exit gate: persistent login workflow works reliably enough to continue.

### Phase 1 - Minimal control panel + login flow

Deliverables:

- `/` panel;
- session status;
- Open Amazon/Login;
- embedded noVNC;
- Verify Login;
- browser-busy lock;
- stale interactive-session timeout;
- restart browser control;
- redacted logs.

Exit gate: a human can recover from login/MFA/challenge without shell access.

### Phase 2 - Order/shipment discovery

Deliverables:

- orders page adapter;
- shipment identity model;
- HMAC public IDs;
- SQLite storage;
- tracking links captured when available;
- no live stop parser yet.

Exit gate: active shipments are stable across repeated polls/restarts without duplicate identities.

### Phase 3 - Live tracking research spike

Deliverables:

- instrumented tracking page;
- DOM observation;
- response metadata observation;
- WebSocket observation;
- sanitized debug capture;
- written findings document showing exactly where stop count originates.

Exit gate: stop count can be reliably observed on a real live package.

**If this fails, stop here and reassess.** Do not build a polished MQTT system around an unproven data source.

### Phase 4 - LiveTrackingObserver

Deliverables:

- long-lived tracking tab;
- normalization;
- freshness watchdog;
- fallback reload;
- teardown on delivery;
- tests against captured fixtures/simulator.

Exit gate: several state transitions observed without duplicates or fabricated values.

### Phase 5 - State/event engine

Deliverables:

- service/session/shipment/tracking domains;
- diff engine;
- event de-duplication;
- SQLite history;
- retention;
- upward-count and disappearing-count semantics.

Exit gate: deterministic tests for all transitions.

### Phase 6 - MQTT

Deliverables:

- LWT online/offline;
- service/session state topics;
- shipment state;
- non-retained events;
- reconnection and queued/latest-state behavior;
- ACL example.

Exit gate: broker restart does not create duplicate stop events.

### Phase 7 - Home Assistant integration

Deliverables:

- summary Discovery mode;
- optional per-shipment mode with cleanup;
- HA birth republish handling;
- example TTS automation consuming events;
- login-required notification.

Exit gate: HA restart does not speak stale stop counts or accumulate orphan entities.

### Phase 8 - Hardening

Deliverables:

- panel auth/same-origin noVNC proxy if LAN exposure desired;
- non-root image;
- resource limits/shm sizing;
- crash recovery;
- support bundle;
- database migrations;
- graceful upgrades;
- stale lock cleanup;
- documented backup/restore/reset-session procedure.

---

## 22. Test plan additions

### 22.1 Browser ownership tests

- interactive login blocks scheduled navigation;
- refresh requests coalesce while busy;
- stale interactive session times out safely;
- Chromium crash releases/rebuilds ownership;
- second process cannot open same profile.

### 22.2 Live DOM simulator

Build a local fixture page that changes:

```text
10 stops -> 9 -> 8 -> null -> 7 -> 6 -> arriving next -> delivered
```

without navigation.

Verify observer event timing and de-duplication.

### 22.3 Network simulator

Fixture endpoint returns changing tracking JSON while DOM may lag. Verify response extractor fallback.

### 22.4 WebSocket simulator

If real Amazon tracking uses WebSocket, build a tiny local WS fixture that sends route updates. Verify Playwright listener behavior independently of Amazon.

### 22.5 Freshness tests

- observer receives no update beyond threshold;
- state becomes stale;
- watchdog reloads once;
- repeated failure backs off;
- last known stop count remains historical, not falsely current.

### 22.6 MQTT restart tests

- tracker starts before broker;
- broker starts later;
- broker restarts;
- HA birth message arrives;
- state republished;
- old transient event is not replayed;
- removed per-shipment discovery config is cleared correctly.

### 22.7 Parser fixtures

At minimum:

- `10 stops away`;
- `2 stops away`;
- singular `1 stop away`;
- stop count disappears;
- stop count increases;
- "arriving next"/equivalent if observed;
- map unavailable;
- delivered;
- challenge/login redirect;
- multiple shipments under one order.

---

## 23. Failure-mode review

| Failure | Required behavior |
|---|---|
| Amazon login expired | session=`needs_login`; package data becomes stale, not fake |
| CAPTCHA/challenge | session=`challenge`; automation pauses; panel presents browser |
| DOM selector changes | try structured/network adapter; mark parser degraded if all fail |
| Live stop count disappears | publish null/visibility change, not parser failure by default |
| Stop count increases | record real observation; suppress TTS by default |
| Tracking tab hangs | freshness watchdog, one controlled reload, then backoff |
| Chromium crashes | browser manager restarts; session revalidated |
| Browser profile locked | do not spawn competing browser; recover only stale locks |
| User opens interactive mode | automation navigation pauses/coalesces |
| SQLite unavailable | service degraded; do not generate unpersisted duplicate events |
| MQTT unavailable | continue observation/persist; reconnect; publish current state after recovery |
| HA unavailable | no impact on tracker |
| Amazon outage | backoff; retain last known state as stale |
| Container restart | restore DB + session + shipment candidates; revalidate before observers |
| Multiple packages on same route | keep distinct shipment state; later dedupe announcements if evidence supports grouping |

---

## 24. Announcement policy belongs outside the scraper

The tracker publishes facts and events; Home Assistant/Node-RED decides what humans hear.

Recommended default event policy in HA:

```text
if stops_changed and new value <= 5 and new value < old value:
    announce

if arrival_phase == arriving_next:
    announce once

if status == delivered:
    announce once
```

Potential duplicate-package behavior:

If two shipments on the same vehicle both move from 4 to 3, HA could announce twice. Do not guess at route grouping initially. Add an optional announcement debounce/grouping layer only if live data gives us a reliable shared route/stop identifier or if simple time-window grouping proves acceptable to the user.

---

## 25. Explicit non-goals for v1, revised

- automatic Amazon password login;
- CAPTCHA bypass;
- MFA bypass;
- anti-detection browser modifications unless proven necessary;
- direct use of a private Amazon endpoint as the primary design;
- driver GPS reconstruction;
- public Internet service;
- multiple Amazon accounts;
- multiple Amazon marketplaces/locales;
- mobile app;
- Home Assistant custom component;
- custom consumer WebSocket protocol;
- a large React/Vue admin dashboard;
- arbitrary browser scripting from external API callers.

---

## 26. Security/privacy go-live checklist

Before running against the real account continuously:

- [ ] browser profile on private persistent volume;
- [ ] profile directory permissions verified;
- [ ] no Amazon credentials in env/config;
- [ ] no cookies/auth headers in logs;
- [ ] API/noVNC bound to localhost or protected by auth/TLS;
- [ ] MQTT broker authenticated and private;
- [ ] debug capture off by default;
- [ ] debug bundle redaction tested with synthetic secrets;
- [ ] raw order/tracking IDs absent from normal MQTT payloads;
- [ ] database backup policy documented;
- [ ] browser session reset procedure documented;
- [ ] container runs unprivileged where possible;
- [ ] Docker health check does not depend on Amazon being online.

---

## 27. Project go/no-go criteria

### Continue beyond the research spike only if all are true

1. Manual Amazon login through the embedded browser is reliable.
2. Login state survives a normal container restart while Amazon's session remains valid.
3. Active shipment/tracking pages can be found without fragile hardcoded navigation per order.
4. A real `N stops away` value can be extracted reliably.
5. At least several live transitions can be observed and normalized.
6. The extraction method does not require abusive request rates.
7. The data source can fail safely as stale/null instead of generating wrong counts.

### Reassess if any are true

- the stop count exists only as pixels/canvas with no stable accessible data;
- Amazon requires a fresh anti-bot challenge every tracking observation;
- the only workable method requires replaying short-lived internal credentials outside the browser at high frequency;
- the live page frequently invalidates the authenticated profile;
- changes cannot be detected reliably enough to make announcements trustworthy.

This protects us from spending a weekend building a cathedral around one CSS selector, a revered human tradition in home automation.

---

## 28. Final architecture recommendation

Build a **new standalone Docker service** with these boundaries:

```text
Core ownership
  Persistent Chromium profile
  BrowserSessionManager / exclusivity lock
  Minimal authenticated control panel
  Embedded noVNC human handoff

Amazon adapters
  Session verifier
  Order/shipment discovery
  LiveTrackingObserver
    - DOM observer
    - fetch/XHR observer
    - WebSocket observer when applicable
    - stale watchdog/reload fallback

Domain layer
  Separate service/session/shipment/tracking state
  Conservative normalization
  freshness
  diff/event engine
  SQLite persistence

Outputs
  REST for control/inspection
  MQTT for state + non-retained events
  Home Assistant as optional consumer
```

The single biggest improvement over the original plan is this:

> **When Amazon's map is live, keep the real Amazon tracking page alive and observe the updates Amazon sends to it, instead of repeatedly pretending to be the page.**

The single biggest operational improvement is:

> **Use Feldorn's interactive-login/control-plane pattern: one persistent browser owner, embedded noVNC, explicit login/verify flow, and a browser-busy mutex.**

That combination is simpler, gentler on Amazon, easier to debug, and less coupled to Home Assistant than the original polling-heavy design.

---

## 29. Reference material reviewed

- Feldorn's Free Games Claimer: https://github.com/feldorn/free-games-claimer
- Feldorn panel/auth documentation from the repository above
- `amazon-orders-ha-sidecar`: https://github.com/jdm0830/amazon-orders-ha-sidecar
- Playwright persistent context docs: https://playwright.dev/python/docs/api/class-browsertype
- Playwright network/WebSocket docs: https://playwright.dev/docs/network
- Amazon package/Map Tracking overview: https://www.aboutamazon.com/news/retail/track-amazon-package
- Home Assistant MQTT integration: https://www.home-assistant.io/integrations/mqtt
- Home Assistant MQTT sensor: https://www.home-assistant.io/integrations/sensor.mqtt

