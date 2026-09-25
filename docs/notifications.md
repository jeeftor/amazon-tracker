# Notification and configuration decisions

These are agreed requirements, **not implemented features**. They extend the original
[plan](../PLAN.md) following user requests on September 25, 2026.

## Outputs

Support MQTT and Telegram independently. Either may be enabled alone or both together.
Keep Amazon parsing separate from normalization, persisted event generation, and outputs.
Each output consumes the same shipment events. Future notification types should require
another small output adapter rather than changes to Amazon selectors. Do not introduce
a plugin framework or a general workflow engine for the initial two transports.

MQTT provides retained dashboard state, separate service/session health, and non-retained
shipment events. Home Assistant decides what to speak. Telegram provides direct human
notifications and can be useful without Home Assistant or an MQTT broker.

## UI and environment configuration

Both outputs have UI settings and environment variables. Precedence is:

```text
explicit environment variable > saved UI value > application default
```

The UI identifies environment-managed fields and does not pretend that editing them
changes the running value. Reject attempts to mutate managed values through the API.
Persist UI settings in private storage that survives container replacement. Broker
passwords and bot tokens are write-only in API responses; show configured/not-configured
state instead. Do not put secrets in browser storage, logs, diagnostics, or error URLs.

| Output | Planned settings |
| --- | --- |
| MQTT | Enabled, host, port, username, password, TLS, base topic |
| Telegram | Enabled, bot token, destination chat ID |

Use the original plan's `MQTT_*` names. Proposed Telegram names are `TELEGRAM_ENABLED`,
`TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID`. They are not accepted configuration yet.
The panel will show each output's status and provide an explicit test action. Sending
a test notification requires the user to request that action; ordinary saving or
opening settings must not send a message.

## Multiple packages and announcements

Keep state and event IDs distinct per shipment, including split orders. Do not infer
that two packages share a route just because their stop counts match. Messages should
identify the package with a private stable reference. Home Assistant may debounce
equivalent announcements over a short time window; any Telegram grouping policy must
be explicit and tested so separate deliveries are not silently lost.

Record real increases in stop count but suppress spoken/direct notices about increases
by default. Preserve missing counts as null, never a fabricated zero. Delivered and
arriving-next transitions should produce one new event. UI state and transient
notifications have different retention and replay behavior.
An initial scan of already-delivered packages establishes a quiet baseline. Do not
announce historical deliveries as new just because the tracker discovered them today.

## Reliability and remaining gates

The source must first prove real live tracking transitions. Then build the transactional
event engine and output delivery records before adding notification transports. A
notification outage must not interrupt Amazon observation or discard current state.
Define expiry and retry policies so reconnection does not announce old stop counts.
External delivery may be ambiguous after a timeout; do not promise exactly-once delivery
without evidence the destination supports it.

Required checks include broker restart, Home Assistant birth/restart, Telegram errors
and rate limits, explicit test actions, secret masking, environment precedence, and
duplicate/expired event handling. Telegram bot/chat setup and real message delivery
will need the user's input at that stage.
