"""Private notification settings with explicit environment precedence."""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

SECRET_FIELDS = {"mqtt_password", "telegram_bot_token"}
OUTPUTS = {"mqtt", "telegram"}


class NotificationValues(BaseModel):
    """Validate supported output settings without contacting either destination."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    mqtt_enabled: bool = False
    mqtt_host: str = Field(default="", max_length=253)
    mqtt_port: int = Field(default=1883, ge=1, le=65535)
    mqtt_username: str = Field(default="", max_length=256)
    mqtt_password: SecretStr = SecretStr("")
    mqtt_tls: bool = False
    mqtt_base_topic: str = Field(default="amazon/tracker", min_length=1, max_length=200)
    telegram_enabled: bool = False
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = Field(default="", max_length=100)

    @field_validator("mqtt_host")
    @classmethod
    def host_only(cls, value: str) -> str:
        """Accept a hostname or IP address, never a URL with embedded credentials."""
        if value and not re.fullmatch(r"[a-zA-Z0-9.:-]+", value):
            raise ValueError("Use a hostname or IP address")
        return value

    @field_validator("mqtt_base_topic")
    @classmethod
    def publish_topic(cls, value: str) -> str:
        """Exclude wildcard, reserved, empty, and control-character topic segments."""
        if (
            any(char in value for char in "+#")
            or any(ord(char) < 33 for char in value)
            or value.startswith("$")
            or any(not segment for segment in value.split("/"))
        ):
            raise ValueError("Use a concrete MQTT topic")
        return value

    @field_validator("mqtt_password", "telegram_bot_token")
    @classmethod
    def bounded_secret(cls, value: SecretStr) -> SecretStr:
        """Bound stored secrets without including their contents in error messages."""
        if len(value.get_secret_value()) > 1024:
            raise ValueError("Secret is too long")
        return value

    @field_validator("telegram_bot_token")
    @classmethod
    def token_format(cls, value: SecretStr) -> SecretStr:
        """Prevent a bot token from changing the fixed Telegram request path."""
        token = value.get_secret_value()
        if token and not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,150}", token):
            raise ValueError("Invalid bot token format")
        return value

    @field_validator("telegram_chat_id")
    @classmethod
    def chat_format(cls, value: str) -> str:
        """Support numeric chats and public channel usernames."""
        if value and not re.fullmatch(r"-?[0-9]+|@[A-Za-z][A-Za-z0-9_]{4,31}", value):
            raise ValueError("Use a numeric chat ID or channel username")
        return value

    def ready(self, output: str) -> bool:
        """Report required fields independently from the enabled switch."""
        if output == "mqtt":
            return bool(self.mqtt_host)
        return bool(self.telegram_bot_token.get_secret_value() and self.telegram_chat_id)


class NotificationConfig:
    """Persist UI values atomically; never persist environment secrets on their behalf."""

    def __init__(self, data_dir: Path, environment: NotificationValues) -> None:
        """Load private UI values, keeping explicit environment fields read-only."""
        self.path = data_dir / "state" / "notifications.json"
        self.managed = environment.model_fields_set & NotificationValues.model_fields.keys()
        self.environment = environment.model_dump(include=self.managed)
        self.saved = NotificationValues()
        if self.path.exists():
            self.saved = NotificationValues.model_validate_json(self.path.read_bytes())
            self.path.chmod(0o600)
        self.effective = self._effective(self.saved)

    def _effective(self, saved: NotificationValues) -> NotificationValues:
        """Apply only explicitly supplied environment fields over saved UI values."""
        return NotificationValues.model_validate(saved.model_dump() | self.environment)

    def public(self) -> dict[str, Any]:
        """Return safe fields and secret-presence flags, never masked secret values."""
        return {
            "values": self.effective.model_dump(exclude=SECRET_FIELDS),
            "managed": sorted(self.managed),
            "secrets_configured": {
                name: bool(getattr(self.effective, name).get_secret_value())
                for name in sorted(SECRET_FIELDS)
            },
            "ready": {output: self.effective.ready(output) for output in sorted(OUTPUTS)},
        }

    def update(self, patch: dict[str, Any]) -> None:
        """Validate a partial update before replacing its private file atomically."""
        if patch.keys() - NotificationValues.model_fields.keys():
            raise ValueError("unknown_settings_field")
        if patch.keys() & self.managed:
            raise ValueError("environment_managed_field")
        # Omission preserves secrets; an explicit empty string clears them.
        saved = NotificationValues.model_validate(self.saved.model_dump() | patch, strict=True)
        effective = self._effective(saved)
        for output in OUTPUTS:
            if (
                any(name.startswith(f"{output}_") for name in patch)
                and getattr(effective, f"{output}_enabled")
                and not effective.ready(output)
            ):
                raise ValueError("enabled_output_requires_configuration")
        private = saved.model_dump(mode="json")
        for name in SECRET_FIELDS:
            private[name] = getattr(saved, name).get_secret_value()
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".notifications-")
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(private, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.saved, self.effective = saved, effective
