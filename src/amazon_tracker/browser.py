"""One browser context, a process lock, and exclusive interactive ownership."""

import asyncio
import fcntl
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import IO, Literal

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from amazon_tracker.config import ORDERS_URL, Settings
from amazon_tracker.discovery import DiscoveryResult, next_orders_page, tracking_links
from amazon_tracker.session import SessionResult, inspect_session


class BrowserBusy(Exception):
    """Your browser is already owned by another operation."""


class BrowserUnavailable(Exception):
    """Your browser could not start or disconnected."""


class SessionRequired(Exception):
    """Discovery stopped because your Amazon session requires attention."""

    def __init__(self, result: SessionResult) -> None:
        """Carry only sanitized session state to the control plane."""
        self.result = result
        super().__init__(result.reason)


class BrowserManager:
    """Keep profile ownership across browser failures until orderly shutdown."""

    def __init__(self, settings: Settings) -> None:
        """Initialize ownership without launching a browser."""
        self.settings = settings
        self.context: BrowserContext | None = None
        self.playwright: Playwright | None = None
        self.profile_lock: IO[bytes] | None = None
        self.mutex = asyncio.Lock()
        self.mode: Literal["idle", "automation", "interactive", "restarting"] = "idle"
        self.interactive_until: float = 0
        self.error: str | None = None
        self.pages: dict[str, Page] = {}
        self.interactive_page: Page | None = None
        self.discovery_starts: deque[float] = deque()
        self.last_discovery_navigation: float = 0

    @property
    def running(self) -> bool:
        """Report whether the persistent context is still available."""
        return self.context is not None

    def _lock_profile(self) -> Path:
        """Fail closed if another tracker owns the profile; never unlink locks."""
        profile = self.settings.data_dir / "browser-profile"
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        profile.chmod(0o700)
        if self.profile_lock is None:
            handle = (profile / ".tracker.lock").open("a+b")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                raise BrowserUnavailable("profile_in_use") from None
            self.profile_lock = handle
        return profile

    async def start(self) -> None:
        """Start stock Chromium and keep errors free of URLs or profile contents."""
        if self.running:
            return
        try:
            profile = self._lock_profile()
            if self.playwright is None:
                self.playwright = await async_playwright().start()
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(profile),
                headless=self.settings.browser_headless,
                executable_path=self.settings.browser_executable,
                chromium_sandbox=self.settings.chromium_sandbox,
                ignore_default_args=["--disable-dev-shm-usage"],
                locale="en-US",
                viewport={"width": 1280, "height": 800},
                accept_downloads=False,
                timeout=30000,
            )
            self.context.on("close", self._disconnected)
            self.context.set_default_timeout(15000)
            self.context.set_default_navigation_timeout(30000)
            # Keep one page alive: closing the final headed tab exits Chromium.
            existing = self.context.pages
            self.pages["admin"] = existing[0] if existing else await self.context.new_page()
            await self.pages["admin"].goto("about:blank")
            for page in existing[1:]:
                await page.close()
            self.error = None
        except Exception as exc:
            self.error = (
                "profile_in_use" if str(exc) == "profile_in_use" else "browser_start_failed"
            )
            raise BrowserUnavailable(self.error) from None

    def _disconnected(self, context: BrowserContext) -> None:
        """Clear stale page handles after a browser crash or close."""
        self.context = None
        self.pages.clear()
        self.interactive_page = None
        self.mode = "idle"
        self.interactive_until = 0
        self.error = "browser_disconnected"

    async def page(self, name: str) -> Page:
        """Reuse a named page in the single context."""
        if self.context is None:
            raise BrowserUnavailable("browser_not_running")
        page = self.pages.get(name)
        if page is None or page.is_closed():
            page = await self.context.new_page()
            self.pages[name] = page
        return page

    def expire_interactive(self) -> bool:
        """Release an abandoned interactive lease without claiming login success."""
        if self.mode == "interactive" and time.monotonic() >= self.interactive_until:
            self.mode = "idle"
            self.interactive_until = 0
            return True
        return False

    @asynccontextmanager
    async def ownership(self, *, interactive_allowed: bool = False) -> AsyncIterator[None]:
        """Reject concurrent navigations and automation during human control."""
        self.expire_interactive()
        if self.mutex.locked() or (self.mode == "interactive" and not interactive_allowed):
            raise BrowserBusy("browser_busy")
        async with self.mutex:
            previous = self.mode
            self.mode = "automation"
            try:
                yield
            finally:
                if self.mode == "automation":
                    self.mode = previous if self.running else "idle"

    async def open_login(self) -> None:
        """Give your interactive browser priority and open Amazon orders once."""
        async with self.ownership(interactive_allowed=True):
            await self.start()
            page = self.interactive_page
            if page is None or page.is_closed():
                page = await self.page("admin")
            self.interactive_page = page
            self.mode = "interactive"
            self.interactive_until = time.monotonic() + self.settings.interactive_timeout_seconds
            await page.bring_to_front()
            if page.url == "about:blank":
                await page.goto(ORDERS_URL, wait_until="domcontentloaded")

    async def verify(self) -> SessionResult:
        """Check a protected page in a separate tab, preserving your login form."""
        async with self.ownership(interactive_allowed=True):
            await self.start()
            page = await self.page("verification")
            await page.goto(ORDERS_URL, wait_until="domcontentloaded")
            result = await inspect_session(page)
            if result.state == "authenticated":
                self.mode = "idle"
                self.interactive_until = 0
                self.interactive_page = None
            elif result.state in {"challenge", "needs_login"}:
                self.mode = "interactive"
                self.interactive_page = page
                self.interactive_until = (
                    time.monotonic() + self.settings.interactive_timeout_seconds
                )
                await page.bring_to_front()
            return result

    async def end_interactive(self) -> None:
        """Release human ownership without changing Amazon authentication state."""
        async with self.ownership(interactive_allowed=True):
            self.mode = "idle"
            self.interactive_until = 0

    async def discover(self) -> DiscoveryResult:
        """Scan bounded orders pages with pacing, session checks, and exclusive ownership."""
        async with self.ownership():
            await self.start()
            page = await self.page("orders")
            url: str | None = ORDERS_URL
            visited: set[str] = set()
            links: list[str] = []
            for _ in range(self.settings.discovery_max_pages):
                if url is None or url in visited:
                    break
                await self._pace_discovery()
                visited.add(url)
                await page.goto(url, wait_until="domcontentloaded")
                session = await inspect_session(page)
                if session.state != "authenticated":
                    self.mode = "interactive"
                    self.interactive_page = page
                    self.interactive_until = (
                        time.monotonic() + self.settings.interactive_timeout_seconds
                    )
                    await page.bring_to_front()
                    raise SessionRequired(session)
                links.extend(await tracking_links(page))
                url = await next_orders_page(page)
            return DiscoveryResult(list(dict.fromkeys(links)), len(visited), url is None)

    async def _pace_discovery(self) -> None:
        """Space full orders navigations by at least thirty seconds."""
        delay = self.last_discovery_navigation + 30 - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        now = time.monotonic()
        while self.discovery_starts and self.discovery_starts[0] <= now - 3600:
            self.discovery_starts.popleft()
        if len(self.discovery_starts) >= 12:
            raise BrowserBusy("discovery_hourly_budget_reached")
        self.discovery_starts.append(now)
        self.last_discovery_navigation = now

    async def restart(self) -> None:
        """Restart only when human ownership has been released."""
        async with self.ownership():
            self.mode = "restarting"
            try:
                if self.context:
                    await self.context.close()
                await self.start()
            finally:
                self.mode = "idle"

    async def close(self) -> None:
        """Stop Chromium before releasing the operating-system profile lock."""
        try:
            if self.context:
                await self.context.close()
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
        finally:
            if self.profile_lock:
                self.profile_lock.close()
                self.profile_lock = None
