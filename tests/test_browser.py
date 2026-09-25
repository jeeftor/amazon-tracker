"""Real Chromium tests using intercepted synthetic pages, never your Amazon account."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from playwright.async_api import Route

from amazon_tracker.browser import BrowserManager
from amazon_tracker.config import ORDERS_URL, Settings
from amazon_tracker.session import inspect_session

pytestmark = pytest.mark.browser


@pytest.fixture
async def manager(tmp_path: Path) -> AsyncIterator[BrowserManager]:
    """Launch an isolated browser profile and intercept all external requests."""
    browser = BrowserManager(Settings(data_dir=tmp_path, browser_headless=True))
    await browser.start()
    assert browser.context is not None
    await browser.context.route("**/*", lambda route: route.fulfill(body="<html></html>"))
    try:
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
