"""Real Chromium tests using intercepted synthetic pages, never your Amazon account."""

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from playwright.async_api import Route

from amazon_tracker.browser import BrowserManager, BrowserUnavailable
from amazon_tracker.config import ORDERS_URL, Settings
from amazon_tracker.session import inspect_session

pytestmark = pytest.mark.browser


@pytest.fixture
async def manager(tmp_path: Path) -> AsyncIterator[BrowserManager]:
    """Launch an isolated browser profile and intercept all external requests."""
    browser = BrowserManager(Settings(data_dir=tmp_path, browser_headless=True))
    try:
        try:
            await browser.start()
        except BrowserUnavailable as error:
            # This profile is synthetic; preserve launch diagnostics only in test failures.
            pytest.fail(f"Isolated fixture browser failed to start: {error.__context__}")
        assert browser.context is not None
        await browser.context.route("**/*", lambda route: route.fulfill(body="<html></html>"))
        yield browser
    finally:
        await browser.close()


@pytest.mark.parametrize(
    ("path", "markup", "expected"),
    [
        (
            "/your-orders/orders",
            '<div id="yourOrders"></div><a id="nav-item-signout">Sign out</a>',
            "authenticated",
        ),
        ("/your-orders/orders", '<div id="yourOrders"></div>', "unknown"),
        ("/your-orders/orders", '<a id="nav-item-signout">Sign out</a>', "unknown"),
        (
            "/your-orders/orders",
            "<h1>Your Orders</h1><form><button>Search Orders</button></form>",
            "authenticated",
        ),
        ("/your-orders/orders", "<h1>Your Orders</h1>", "unknown"),
        (
            "/your-orders/orders",
            "<h1 hidden>Your Orders</h1><button hidden>Search Orders</button>",
            "unknown",
        ),
        ("/ap/signin", '<input id="ap_email">', "needs_login"),
        ("/ap/signin", '<input id="auth-mfa-otpcode">', "challenge"),
        ("/your-orders/orders", '<input id="captchacharacters">', "challenge"),
        ("/ap/cvf", "<h1>Verification required</h1>", "challenge"),
        (
            "/your-orders/orders",
            "<p>Continued access by an unauthorized AI agent</p><button>Continue</button>",
            "challenge",
        ),
        ("/your-orders/orders", "<h1>Something went wrong</h1>", "unknown"),
    ],
)
async def test_session_classification(
    manager: BrowserManager, path: str, markup: str, expected: str
) -> None:
    """Positive login requires both protected-page and signed-in evidence."""
    page = await manager.page("test")
    await page.goto("https://www.amazon.com" + path)
    await page.set_content(markup)
    assert (await inspect_session(page)).state == expected


async def test_login_verification_releases_lease(manager: BrowserManager) -> None:
    """Your login page remains open while verification uses a separate tab."""
    assert manager.context is not None

    async def orders(route: Route) -> None:
        """Serve only synthetic account evidence."""
        await route.fulfill(body='<div id="yourOrders"></div><a id="nav-item-signout">Sign out</a>')

    await manager.context.route("**/your-orders/**", orders)
    await manager.open_login()
    assert manager.mode == "interactive"
    assert (await manager.verify()).state == "authenticated"
    assert manager.mode == "idle"
    assert not manager.pages["admin"].is_closed()


async def test_cookie_persists_across_browser_restart(manager: BrowserManager) -> None:
    """A persistent cookie survives a full context close and reopen."""
    assert manager.context is not None
    await manager.context.add_cookies(
        [
            {
                "name": "synthetic_session",
                "value": "test-only",
                "domain": "www.amazon.com",
                "path": "/",
                "expires": 2147483647,
                "secure": True,
                "httpOnly": True,
            }
        ]
    )
    await manager.restart()
    assert manager.context is not None
    cookies = await manager.context.cookies(ORDERS_URL)
    assert any(
        cookie["name"] == "synthetic_session" and cookie["value"] == "test-only"
        for cookie in cookies
    )


async def test_open_login_returns_to_pending_challenge(manager: BrowserManager) -> None:
    """Reopening human control preserves and focuses your unfinished challenge."""
    assert manager.context is not None

    async def challenge(route: Route) -> None:
        """Serve a synthetic verification form without contacting Amazon."""
        await route.fulfill(content_type="text/html", body='<input id="auth-mfa-otpcode">')

    await manager.open_login()
    admin = manager.pages["admin"]
    await manager.context.route("**/your-orders/**", challenge)
    assert (await manager.verify()).state == "challenge"
    verification = manager.pages["verification"]
    await verification.locator("#auth-mfa-otpcode").fill("synthetic-unfinished-input")
    with (
        patch.object(admin, "bring_to_front", new_callable=AsyncMock) as admin_focus,
        patch.object(verification, "bring_to_front", new_callable=AsyncMock) as challenge_focus,
    ):
        await manager.open_login()
        challenge_focus.assert_awaited_once()
        admin_focus.assert_not_awaited()
    assert await verification.locator("#auth-mfa-otpcode").input_value() == (
        "synthetic-unfinished-input"
    )

    await verification.close()
    await manager.open_login()
    assert manager.pages["admin"] is admin
    assert manager.mode == "interactive"


async def test_discovery_follows_pagination_and_preserves_split_shipments(
    manager: BrowserManager,
) -> None:
    """Scan visible tracking links across orders pages without copying product text."""
    assert manager.context is not None

    async def orders(route: Route) -> None:
        """Serve two orders pages for a single split order."""
        second = "startIndex=10" in route.request.url
        package = 1 if second else 0
        next_link = "" if second else '<a href="/your-orders/orders?startIndex=10">Next →</a>'
        await route.fulfill(
            content_type="text/html",
            body=(
                "<h1>Your Orders</h1><button>Search Orders</button>"
                '<a href="/gp/your-account/ship-track?orderId=synthetic'
                f'&amp;packageIndex={package}">'
                f"Track package</a>{next_link}"
                '<a hidden href="/private">Track package</a><p>Private product text</p>'
            ),
        )

    await manager.context.route("**/your-orders/**", orders)
    with patch.object(manager, "_pace_discovery", new_callable=AsyncMock) as pacing:
        result = await manager.discover()
        assert pacing.await_count == 2
    assert result.pages_scanned == 2
    assert result.complete
    assert len(result.links) == 2
    assert "packageIndex=0" in result.links[0]
    assert "packageIndex=1" in result.links[1]
    assert "Private product" not in str(result)

    manager.settings.discovery_max_pages = 1
    with patch.object(manager, "_pace_discovery", new_callable=AsyncMock):
        partial = await manager.discover()
    assert not partial.complete
    assert partial.pages_scanned == 1
