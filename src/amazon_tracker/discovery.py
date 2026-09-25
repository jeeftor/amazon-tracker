"""Collect tracking links from rendered orders, without recording products or addresses."""

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Page

from amazon_tracker.delivery import DeliveryStatus, parse_delivery_status


@dataclass(frozen=True)
class DiscoveryResult:
    """A bounded orders-page scan; links remain internal."""

    links: list[str]
    pages_scanned: int
    complete: bool
    statuses: dict[str, DeliveryStatus] = field(default_factory=dict)


async def tracking_observations(page: Page) -> dict[str, DeliveryStatus]:
    """Associate a visible status with its own package, never an entire split order."""
    entries: list[dict[str, str]] = await page.locator("a[href]").evaluate_all(
        """anchors => {
          const visible = n => n.getClientRects().length > 0 &&
            getComputedStyle(n).visibility === 'visible';
          const tracking = a => visible(a) &&
            /^(track package|track shipment)$/i.test(a.innerText.trim());
          return anchors.filter(tracking).map(a => {
            const box = a.closest('.delivery-box');
            if (!box) return {url: a.href, label: ''};
            const labels = [...box.querySelectorAll('.delivery-box__primary-text')]
              .filter(n => visible(n) && n.closest('.delivery-box') === box);
            const links = [...box.querySelectorAll('a[href]')]
              .filter(n => tracking(n) && n.closest('.delivery-box') === box);
            const unambiguous = labels.length === 1 &&
              new Set(links.map(n => n.href)).size === 1;
            return {url: a.href, label: unambiguous ? labels[0].innerText : ''};
          });
        }"""
    )
    observations: dict[str, DeliveryStatus] = {}
    for entry in entries:
        status = parse_delivery_status(entry["label"])
        url = entry["url"]
        if url in observations and observations[url] != status:
            status = DeliveryStatus()
        observations[url] = status
    return observations


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
