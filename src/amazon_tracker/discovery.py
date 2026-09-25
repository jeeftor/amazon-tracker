"""Collect tracking links from rendered orders, without recording products or addresses."""

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Page


@dataclass(frozen=True)
class DiscoveryResult:
    """A bounded orders-page scan; links remain internal."""

    links: list[str]
    pages_scanned: int
    complete: bool


async def tracking_links(page: Page) -> list[str]:
    """Read only visible tracking anchors, never whole order cards or page HTML."""
    links: list[str] = await page.locator("a[href]").evaluate_all(
        """anchors => anchors.filter(a =>
          /^(track package|track shipment)$/i.test(a.innerText.trim()) &&
          a.getClientRects().length > 0
        ).map(a => a.href)"""
    )
    return list(dict.fromkeys(links))


async def next_orders_page(page: Page) -> str | None:
    """Accept only an Amazon orders pagination link; do not follow arbitrary navigation."""
    links = page.get_by_role("link", name=re.compile(r"^Next\b", re.IGNORECASE))
    for index in range(await links.count()):
        link = links.nth(index)
        if not await link.is_visible():
            continue
        href = await link.get_attribute("href")
        if not href:
            continue
        candidate = urljoin(page.url, href)
        parsed = urlsplit(candidate)
        if (
            parsed.scheme == "https"
            and parsed.netloc in {"www.amazon.com", "amazon.com"}
            and parsed.path.rstrip("/")
            in {"/your-orders/orders", "/gp/css/order-history", "/gp/your-account/order-history"}
        ):
            return candidate
    return None
