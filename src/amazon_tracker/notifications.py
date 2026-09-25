"""Explicit output tests; automatic shipment announcements are not enabled yet."""

import asyncio
import json
import ssl
import time
from datetime import UTC, datetime
from typing import Any

import aiohttp
import aiomqtt

from amazon_tracker.notification_config import NotificationConfig, NotificationValues

TEST_MESSAGE = "Amazon Delivery Tracker: your test notification arrived. This is not a delivery."


class OutputFailure(Exception):
    """Expose a fixed error code and optional retry delay, never a transport exception."""

    def __init__(self, code: str, retry_after: int = 0) -> None:
        """Keep Telegram response descriptions and token-bearing URLs private."""
        super().__init__(code)
        self.retry_after = retry_after


async def send_mqtt_test(values: NotificationValues) -> None:
    """Publish one non-retained test on a topic separate from shipment events."""
    async with aiomqtt.Client(
        values.mqtt_host,
        port=values.mqtt_port,
        username=values.mqtt_username or None,
        password=values.mqtt_password.get_secret_value() or None,
        tls_context=ssl.create_default_context() if values.mqtt_tls else None,
        timeout=10,
    ) as client:
        await client.publish(
            f"{values.mqtt_base_topic}/test",
            json.dumps({"type": "test", "message": TEST_MESSAGE}),
            qos=1,
            retain=False,
        )


async def send_telegram_test(values: NotificationValues) -> None:
    """Send plain text to the fixed HTTPS endpoint without redirects or URL logging."""
    token = values.telegram_bot_token.get_secret_value()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as client:
        async with client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": values.telegram_chat_id, "text": TEST_MESSAGE},
            allow_redirects=False,
        ) as response:
            # Do not copy provider error descriptions or response URLs into diagnostics.
            if response.status in {401, 404}:
                raise OutputFailure("telegram_token_rejected")
            if response.status == 403:
                raise OutputFailure("telegram_chat_forbidden")
            try:
                data = await response.content.readexactly(65537)
            except asyncio.IncompleteReadError as exc:
                data = exc.partial
            if len(data) > 65536:
                raise OutputFailure("telegram_invalid_response")
            body = json.loads(data)
            if not isinstance(body, dict):
                raise OutputFailure("telegram_invalid_response")
            if response.status == 429:
                parameters = body.get("parameters")
                delay = parameters.get("retry_after", 60) if isinstance(parameters, dict) else 60
                delay = delay if type(delay) is int and delay > 0 else 60
                raise OutputFailure("telegram_rate_limited", retry_after=delay)
            if response.status == 400:
                raise OutputFailure("telegram_check_chat_id_and_start_bot")
            if response.status != 200 or body.get("ok") is not True:
                raise OutputFailure("telegram_request_failed")


class NotificationTests:
    """Serialize explicit tests and expose their results separately from live connectivity."""

    def __init__(self, config: NotificationConfig) -> None:
        """Start with untested outputs; saving configuration never sends a message."""
        self.config = config
        self.lock = asyncio.Lock()
        self.results: dict[str, dict[str, Any]] = {}
        self.retry_until: dict[str, float] = {}

    def status(self) -> dict[str, Any]:
        """Keep test evidence distinct from enabled settings and continuous connection."""
        return {
            output: self.results.get(output, {"state": "not_tested"})
            | {
                "retry_after_seconds": max(
                    0, int(self.retry_until.get(output, 0) - time.monotonic())
                )
            }
            for output in ("mqtt", "telegram")
        }

    async def run(self, output: str) -> dict[str, Any]:
        """Send only on an explicit request; never retry an ambiguous send automatically."""
        if self.lock.locked():
            raise ValueError("notification_test_running")
        if time.monotonic() < self.retry_until.get(output, 0):
            raise ValueError("notification_test_cooldown")
        values = self.config.effective
        if not values.ready(output):
            raise ValueError("output_requires_configuration")
        async with self.lock:
            self.results[output] = {"state": "running"}
            self.retry_until[output] = time.monotonic() + 10
            result: dict[str, Any] = {"state": "sent", "error": None}
            try:
                async with asyncio.timeout(15):
                    if output == "mqtt":
                        await send_mqtt_test(values)
                    else:
                        await send_telegram_test(values)
            except OutputFailure as exc:
                result.update(state="failed", error=str(exc))
                self.retry_until[output] = time.monotonic() + max(10, exc.retry_after)
            except TimeoutError:
                result.update(state="uncertain", error="send_timed_out_check_destination")
            except Exception:
                result.update(state="failed", error="connection_or_send_failed_check_settings")
            finally:
                # Cancellation is also ambiguous; do not leave a permanent running badge.
                if self.results[output]["state"] == "running":
                    self.results[output] = {"state": "uncertain", "error": "send_interrupted"}
            result["checked_at"] = datetime.now(UTC).isoformat()
            self.results[output] = result
            return result
