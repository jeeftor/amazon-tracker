"""Shipment identity from tracking links, independent of an order's item count."""

import hashlib
import hmac
import json
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit, urlunsplit


@dataclass(frozen=True)
class ShipmentCandidate:
    """Keep the source tracking link private and expose only installation-specific IDs."""

    shipment_id: str
    order_id: str | None
    tracking_url: str


def public_id(secret: bytes, domain: str, parts: list[str]) -> str:
    """Hash a namespaced identity without exposing raw Amazon identifiers."""
    message = json.dumps([domain, *parts], separators=(",", ":")).encode()
    digest = hmac.new(secret, message, hashlib.sha256).hexdigest()[:16]
    return f"{domain}_{digest}"


def shipment_candidate(url: str, secret: bytes) -> ShipmentCandidate | None:
    """Require a shipment key; an order identifier alone cannot identify a package."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"www.amazon.com", "amazon.com"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc not in {"www.amazon.com", "amazon.com"}
    ):
        return None
    query = parse_qs(parsed.query)
    if any(len(values) != 1 for values in query.values()):
        return None
    order = query.get("orderId", [None])[0]
    shipment = query.get("shipmentId", [None])[0]
    tracking = query.get("trackingId", [None])[0]
    package = query.get("packageIndex", [None])[0]
    if shipment:
        parts = ["shipment", shipment]
    elif tracking:
        parts = ["tracking", tracking]
    elif order and package is not None and package.isascii() and package.isdecimal():
        parts = ["order-package", order, str(int(package))]
    else:
        return None
    return ShipmentCandidate(
        shipment_id=public_id(secret, "shp", parts),
        order_id=public_id(secret, "ord", [order]) if order else None,
        tracking_url=urlunsplit(parsed._replace(fragment="")),
    )
