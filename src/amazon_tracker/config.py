"""Small, local-only configuration for the browser acceptance gate."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ORDERS_URL = "https://www.amazon.com/your-orders/orders?timeFilter=last30"


class Settings(BaseSettings):
    """Configure your private browser without storing Amazon credentials."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    data_dir: Path = Path("data")
    browser_headless: bool = False
    browser_executable: str | None = None
    chromium_sandbox: bool = True
    interactive_timeout_seconds: int = Field(default=900, ge=30, le=7200)
    novnc_port: int = Field(default=6080, ge=1024, le=65535)
    panel_port: int = Field(default=8080, ge=1024, le=65535)
