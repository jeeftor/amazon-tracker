"""Verify ownership, persistence, and control-plane failure behavior."""

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from amazon_tracker.app import Runtime, create_app
from amazon_tracker.browser import BrowserBusy, BrowserManager, BrowserUnavailable
from amazon_tracker.config import Settings
from amazon_tracker.session import SessionResult
from amazon_tracker.storage import Store


def test_health_and_status_without_browser(tmp_path: Path) -> None:
    """A browser failure must leave the API alive and avoid fake authentication."""
    app = create_app(Settings(data_dir=tmp_path), start_browser=False)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").status_code == 503
        status = client.get("/api/v1/status").json()
        assert status["session"]["state"] == "unknown"
        assert status["service"]["state"] == "degraded"
        assert client.get("/").status_code == 200


def test_mutations_reject_cross_origin_and_rebinding(tmp_path: Path) -> None:
    """A remote page cannot drive your local authenticated browser."""
    app = create_app(Settings(data_dir=tmp_path), start_browser=False)
    with TestClient(app, base_url="http://localhost") as client:
        endpoint = "/api/v1/session/open-login"
        assert client.post(endpoint).status_code == 403
        assert (
            client.post(
                endpoint,
                headers={
                    "X-Tracker-Request": "1",
                    "Origin": "https://attacker.invalid",
                },
            ).status_code
            == 403
        )
        assert client.get("/", headers={"Host": "attacker.invalid"}).status_code == 400
        assert (
            client.post(
                "/api/v1/browser/restart",
                headers={
                    "X-Tracker-Request": "1",
                },
            ).status_code
            == 400
        )


async def test_interactive_blocks_automation_and_expires(tmp_path: Path) -> None:
    """A lease blocks background navigation until released or expired."""
    manager = BrowserManager(Settings(data_dir=tmp_path))
    manager.mode = "interactive"
    manager.interactive_until = time.monotonic() + 60
    with pytest.raises(BrowserBusy):
        async with manager.ownership():
            pytest.fail("Automation acquired your interactive browser")
    manager.interactive_until = time.monotonic() - 1
    async with manager.ownership():
        assert manager.mode == "automation"
    assert manager.mode == "idle"


async def test_concurrent_navigation_is_rejected(tmp_path: Path) -> None:
    """Two API operations cannot navigate the browser concurrently."""
    manager = BrowserManager(Settings(data_dir=tmp_path))
    async with manager.ownership():
        with pytest.raises(BrowserBusy):
            async with manager.ownership(interactive_allowed=True):
                pytest.fail("Concurrent ownership granted")


async def test_process_lock_survives_failed_launch(tmp_path: Path) -> None:
    """A second process cannot claim a profile until its owner shuts down."""
    manager = BrowserManager(Settings(data_dir=tmp_path))
    manager._lock_profile()
    code = (
        "from pathlib import Path; import sys; "
        "from amazon_tracker.browser import BrowserManager; "
        "from amazon_tracker.config import Settings; "
        "BrowserManager(Settings(data_dir=Path(sys.argv[1])))._lock_profile()"
    )
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", code, str(tmp_path)], capture_output=True
    )
    assert result.returncode != 0
    assert b"profile_in_use" in result.stderr
    await manager.close()
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", code, str(tmp_path)], capture_output=True
    )
    assert result.returncode == 0


async def test_profile_content_is_not_deleted_on_conflict(tmp_path: Path) -> None:
    """The lock manager never deletes session data or Chromium runtime locks."""
    owner = BrowserManager(Settings(data_dir=tmp_path))
    profile = owner._lock_profile()
    sentinel = profile / "SingletonLock"
    sentinel.write_text("existing-owner")
    other = BrowserManager(Settings(data_dir=tmp_path))
    with pytest.raises(BrowserUnavailable):
        other._lock_profile()
    assert sentinel.read_text() == "existing-owner"
    await owner.close()


async def test_identical_requests_coalesce_and_conflicts_reject(tmp_path: Path) -> None:
    """Double-clicking verify creates one operation and one history row."""
    runtime = Runtime(Settings(data_dir=tmp_path))
    gate = asyncio.Event()

    async def verify() -> SessionResult:
        """Hold a synthetic verification in flight."""
        await gate.wait()
        return SessionResult("authenticated", "synthetic_test")

    runtime.browser.verify = AsyncMock(side_effect=verify)
    runtime.submit("verify")
    task = runtime.task
    runtime.submit("verify")
    assert runtime.task is task
    with pytest.raises(BrowserBusy):
        runtime.submit("open-login")
    gate.set()
    assert task is not None
    await task
    assert runtime.browser.verify.await_count == 1
    assert runtime.session.state == "authenticated"
    assert (
        runtime.store.connection.execute("SELECT COUNT(*) FROM session_history").fetchone()[0] == 1
    )
    await runtime.close()
    restored = Runtime(Settings(data_dir=tmp_path))
    assert restored.last_verified_at is not None
    assert restored.session.state == "unknown"
    await restored.close()


async def test_raw_browser_errors_never_reach_status(tmp_path: Path) -> None:
    """Playwright errors can include signed URLs and must remain private."""
    runtime = Runtime(Settings(data_dir=tmp_path))
    runtime.browser.verify = AsyncMock(side_effect=RuntimeError("cookie=synthetic-secret"))
    runtime.submit("verify")
    assert runtime.task is not None
    await runtime.task
    assert "synthetic-secret" not in str(runtime.status())
    assert runtime.session.state == "unknown"
    await runtime.close()


async def test_restart_requires_interactive_release(tmp_path: Path) -> None:
    """A confirmed restart still cannot interrupt a live login lease."""
    runtime = Runtime(Settings(data_dir=tmp_path))
    runtime.browser.mode = "interactive"
    runtime.browser.interactive_until = time.monotonic() + 60
    with pytest.raises(BrowserBusy):
        runtime.submit("restart")
    await runtime.close()


def test_schema_and_private_permissions(tmp_path: Path) -> None:
    """Persist history in WAL mode and private directories."""
    store = Store(tmp_path)
    assert store.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert (tmp_path / "state").stat().st_mode & 0o777 == 0o700
    store.close()
