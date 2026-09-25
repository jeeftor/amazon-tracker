"""Conservative US English session checks; selectors await real-account validation."""

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from playwright.async_api import Page

SessionState = Literal["unknown", "authenticated", "needs_login", "challenge", "checking"]


@dataclass(frozen=True)
class SessionResult:
    """A verification result containing no account data."""

    state: SessionState
    reason: str


async def inspect_session(page: Page) -> SessionResult:
    """Require affirmative orders and signed-in evidence; reject challenges first."""
    url = urlsplit(page.url)
    if url.hostname not in {"amazon.com", "www.amazon.com"}:
        return SessionResult("unknown", "unexpected_page")
    challenge = page.locator(
        '#captchacharacters, #auth-mfa-otpcode, input[name="cvf_captcha_input"], '
        'form[action*="validateCaptcha"], #cvf-page-content'
    )
    if "/ap/cvf" in url.path or "/errors/validateCaptcha" in url.path or await challenge.count():
        return SessionResult("challenge", "human_verification_required")
    if "/ap/signin" in url.path or await page.locator("#ap_email, #ap_password").count():
        return SessionResult("needs_login", "signin_required")
    orders = page.locator("#yourOrders, #ordersContainer, .your-orders-content-container")
    signed_in = page.locator('a[href*="/gp/flex/sign-out.html"], #nav-item-signout')
    if "orders" in url.path.lower() and await orders.count() and await signed_in.count():
        return SessionResult("authenticated", "orders_and_signout_present")
    return SessionResult("unknown", "authenticated_page_not_confirmed")
