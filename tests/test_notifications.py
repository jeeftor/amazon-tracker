"""Check private configuration and explicit output actions without real recipients."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from amazon_tracker.app import create_app
from amazon_tracker.config import Settings
from amazon_tracker.notification_config import NotificationConfig, NotificationValues
from amazon_tracker.notifications import (
    NotificationTests,
    OutputFailure,
    send_mqtt_test,
    send_telegram_test,
)

TOKEN = "123456:" + "synthetic_token_" * 3
HEADERS = {"X-Tracker-Request": "1"}
ENDPOINT = "/api/v1/settings/notifications"


def test_settings_persist_without_reading_secrets_back(tmp_path: Path) -> None:
    """UI secrets survive restart, omitted values persist, explicit empty values clear."""
    settings = Settings(data_dir=tmp_path, _env_file=None)
    with TestClient(
        create_app(settings, start_browser=False), base_url="http://localhost"
    ) as client:
        response = client.patch(
            ENDPOINT,
            headers=HEADERS,
            json={"telegram_bot_token": TOKEN, "telegram_chat_id": "-12345"},
        )
        assert response.status_code == 200
        assert TOKEN not in response.text
        assert "telegram_bot_token" not in response.json()["values"]
        assert response.json()["secrets_configured"]["telegram_bot_token"] is True
        assert client.patch(ENDPOINT, headers=HEADERS, json={"mqtt_host": "broker"}).is_success
    private = tmp_path / "state" / "notifications.json"
    assert private.stat().st_mode & 0o777 == 0o600
    restored = create_app(settings, start_browser=False)
    with TestClient(restored, base_url="http://localhost") as client:
        assert client.get(ENDPOINT).json()["secrets_configured"]["telegram_bot_token"] is True
        assert (
            restored.state.runtime.notification_config.effective.telegram_bot_token.get_secret_value()
            == TOKEN
        )
        response = client.patch(ENDPOINT, headers=HEADERS, json={"telegram_bot_token": ""})
        assert response.json()["secrets_configured"]["telegram_bot_token"] is False


def test_environment_overrides_saved_values_and_rejects_edits(tmp_path: Path) -> None:
    """Explicit false and empty values still own fields; env secrets are not saved."""
    (tmp_path / "state").mkdir()
    config = NotificationConfig(tmp_path, Settings(_env_file=None))
    config.update({"mqtt_host": "saved", "mqtt_enabled": True, "mqtt_password": "saved-password"})
    environment = Settings(
        _env_file=None,
        data_dir=tmp_path,
        mqtt_enabled=False,
        mqtt_password="",
        telegram_bot_token=TOKEN,
    )
    app = create_app(environment, start_browser=False)
    with TestClient(app, base_url="http://localhost") as client:
        public = client.get(ENDPOINT).json()
        assert public["values"]["mqtt_enabled"] is False
        assert public["values"]["mqtt_host"] == "saved"
        assert public["secrets_configured"]["mqtt_password"] is False
        rejected = client.patch(
            ENDPOINT, headers=HEADERS, json={"mqtt_enabled": True, "mqtt_host": "changed"}
        )
        assert rejected.status_code == 422
        assert client.get(ENDPOINT).json()["values"]["mqtt_host"] == "saved"
        assert client.patch(ENDPOINT, headers=HEADERS, json={"mqtt_port": 8883}).is_success
    assert TOKEN not in (tmp_path / "state" / "notifications.json").read_text()
    restored = NotificationConfig(tmp_path, Settings(_env_file=None))
    assert restored.effective.mqtt_enabled is True
    assert restored.effective.mqtt_password.get_secret_value() == "saved-password"


def test_dotenv_is_explicit_but_defaults_are_editable(tmp_path: Path) -> None:
    """Compose-provided and dotenv settings have the same per-field precedence."""
    dotenv = tmp_path / ".env"
    dotenv.write_text("MQTT_ENABLED=false\nMQTT_PASSWORD=\n")
    settings = Settings(_env_file=dotenv)
    assert settings.model_fields_set == {"mqtt_enabled", "mqtt_password"}
    assert Settings(_env_file=None).model_fields_set == set()


def test_incomplete_environment_output_does_not_block_other_output(tmp_path: Path) -> None:
    """Each output can be set up even if the other's environment settings are incomplete."""
    (tmp_path / "state").mkdir()
    config = NotificationConfig(tmp_path, NotificationValues(telegram_enabled=True))
    config.update({"mqtt_host": "broker", "mqtt_enabled": True})
    assert config.effective.ready("mqtt") is True
    assert config.effective.ready("telegram") is False
    with pytest.raises(ValueError, match="requires_configuration"):
        config.update({"telegram_chat_id": "123"})
    config.update({"telegram_chat_id": "123", "telegram_bot_token": TOKEN})
    assert config.effective.ready("telegram") is True


@pytest.mark.parametrize(
    "patch",
    [
        {"telegram_bot_token": "private-invalid-token"},
        {"mqtt_port": "private-invalid-token"},
        {"private-invalid-token": True},
        {"mqtt_base_topic": "private-invalid-token/#"},
        {"mqtt_enabled": "private-invalid-token"},
        {"mqtt_host": "https://private-invalid-token@example.com"},
    ],
)
def test_validation_never_echoes_secret_input(tmp_path: Path, patch: dict[str, object]) -> None:
    """Pydantic's default error input reflection must not leak credentials."""
    with TestClient(
        create_app(Settings(data_dir=tmp_path, _env_file=None), start_browser=False),
        base_url="http://localhost",
    ) as client:
        response = client.patch(ENDPOINT, headers=HEADERS, json=patch)
        assert response.status_code == 422
        assert "private-invalid-token" not in response.text
        assert not (tmp_path / "state" / "notifications.json").exists()


def test_save_is_quiet_and_test_requires_explicit_local_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading/saving configuration sends nothing; only a protected POST sends a test."""
    sender = AsyncMock()
    monkeypatch.setattr("amazon_tracker.notifications.send_telegram_test", sender)
    with TestClient(
        create_app(Settings(data_dir=tmp_path, _env_file=None), start_browser=False),
        base_url="http://localhost",
    ) as client:
        assert client.patch(
            ENDPOINT, headers=HEADERS, json={"telegram_bot_token": TOKEN, "telegram_chat_id": "123"}
        ).is_success
        assert client.get(ENDPOINT).is_success
        sender.assert_not_awaited()
        path = "/api/v1/notifications/telegram/test"
        assert client.post(path).status_code == 403
        assert (
            client.post(path, headers=HEADERS | {"Origin": "https://attacker.invalid"}).status_code
            == 403
        )
        sender.assert_not_awaited()
        assert client.post(path, headers=HEADERS).json()["state"] == "sent"
        sender.assert_awaited_once()
        assert client.post(path, headers=HEADERS).status_code == 409
        assert client.patch(ENDPOINT, headers=HEADERS, content=b"x" * 16385).status_code == 413


async def test_failures_and_rate_limits_remain_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transport URLs stay private and provider retry delays prevent repeated sends."""
    (tmp_path / "state").mkdir()
    config = NotificationConfig(
        tmp_path, NotificationValues(telegram_bot_token=TOKEN, telegram_chat_id="123")
    )
    tests = NotificationTests(config)
    sender = AsyncMock(side_effect=RuntimeError(f"https://api.telegram.org/bot{TOKEN}"))
    monkeypatch.setattr("amazon_tracker.notifications.send_telegram_test", sender)
    result = await tests.run("telegram")
    assert result["state"] == "failed"
    assert TOKEN not in json.dumps(tests.status())
    tests.retry_until.clear()
    sender.side_effect = OutputFailure("telegram_rate_limited", retry_after=120)
    assert (await tests.run("telegram"))["error"] == "telegram_rate_limited"
    assert tests.status()["telegram"]["retry_after_seconds"] >= 119
    with pytest.raises(ValueError, match="cooldown"):
        await tests.run("telegram")
    assert sender.await_count == 2


async def test_concurrent_tests_and_timeout_do_not_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coalescing and ambiguous outcomes must not duplicate an explicit send."""
    (tmp_path / "state").mkdir()
    config = NotificationConfig(tmp_path, NotificationValues(mqtt_host="broker"))
    tests = NotificationTests(config)
    gate = asyncio.Event()
    started = asyncio.Event()

    async def send(values: NotificationValues) -> None:
        """Pause a fake transport without contacting a broker."""
        started.set()
        await gate.wait()
        raise TimeoutError

    monkeypatch.setattr("amazon_tracker.notifications.send_mqtt_test", send)
    task = asyncio.create_task(tests.run("mqtt"))
    await started.wait()
    with pytest.raises(ValueError, match="running"):
        await tests.run("mqtt")
    gate.set()
    assert (await task)["state"] == "uncertain"


async def test_mqtt_test_is_nonretained_and_outside_event_topics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A setup test cannot masquerade as a delivered event or retained package state."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    factory = MagicMock(return_value=client)
    monkeypatch.setattr("amazon_tracker.notifications.aiomqtt.Client", factory)
    await send_mqtt_test(NotificationValues(mqtt_host="broker", mqtt_tls=True))
    assert factory.call_args.kwargs["tls_context"].check_hostname is True
    call = client.publish.call_args
    assert call.args[0] == "amazon/tracker/test"
    assert json.loads(call.args[1])["type"] == "test"
    assert call.kwargs == {"qos": 1, "retain": False}


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, {"ok": True}, None),
        (400, {"description": TOKEN}, "telegram_check_chat_id_and_start_bot"),
        (401, {}, "telegram_token_rejected"),
        (403, {}, "telegram_chat_forbidden"),
        (429, {"parameters": {"retry_after": 73}}, "telegram_rate_limited"),
        (302, {}, "telegram_request_failed"),
        (200, {"ok": False, "description": TOKEN}, "telegram_request_failed"),
    ],
)
async def test_telegram_protocol_and_safe_errors(
    monkeypatch: pytest.MonkeyPatch, status: int, body: dict[str, object], expected: str | None
) -> None:
    """Send fixed plain text without redirects and discard provider error descriptions."""
    response = MagicMock(status=status)
    response.content.readexactly = AsyncMock(
        side_effect=asyncio.IncompleteReadError(json.dumps(body).encode(), 65537)
    )
    response_context = AsyncMock()
    response_context.__aenter__.return_value = response
    client = MagicMock()
    client.post.return_value = response_context
    session_context = AsyncMock()
    session_context.__aenter__.return_value = client
    monkeypatch.setattr(
        "amazon_tracker.notifications.aiohttp.ClientSession",
        MagicMock(return_value=session_context),
    )
    values = NotificationValues(telegram_bot_token=TOKEN, telegram_chat_id="-12345")
    if expected:
        with pytest.raises(OutputFailure, match=expected) as error:
            await send_telegram_test(values)
        assert TOKEN not in str(error.value)
        if status == 429:
            assert error.value.retry_after == 73
    else:
        await send_telegram_test(values)
    assert client.post.call_args.kwargs["allow_redirects"] is False
    assert client.post.call_args.kwargs["json"]["chat_id"] == "-12345"
    assert "parse_mode" not in client.post.call_args.kwargs["json"]
