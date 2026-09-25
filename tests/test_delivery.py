"""Verify delivered facts from shipment-scoped order cards without live map data."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from amazon_tracker.delivery import DeliveryStatus, parse_delivery_status
from amazon_tracker.discovery import DiscoveryResult
from amazon_tracker.storage import Store


def test_delivered_dates_are_validated_without_inventing_a_year() -> None:
    """Only a complete delivered label is affirmative delivery evidence."""
    assert parse_delivery_status("Delivered September 12") == DeliveryStatus(
        "delivered", "September 12"
    )
    assert parse_delivery_status("Delivered today") == DeliveryStatus("delivered", "today")
    for text in (
        "Not delivered September 12",
        "Delivered September 99",
        "Delivered February 30",
        "Your parcel will be delivered September 12",
        "Delivered September 12 to Private Person",
        "Arriving tomorrow",
    ):
        assert parse_delivery_status(text) == DeliveryStatus()


def test_delivery_survives_restart_and_missing_labels(tmp_path: Path) -> None:
    """Unknown observations cannot erase a known delivery or pretend to revalidate it."""
    link = "https://www.amazon.com/gp/your-account/ship-track?shipmentId=synthetic"
    observed = datetime.now(UTC).isoformat()
    store = Store(tmp_path)
    store.save_discovery(
        DiscoveryResult([link], 1, True, {link: DeliveryStatus("delivered", "September 12")}),
        observed,
    )
    original = store.shipments(revalidated=True)[0]
    assert original["status"] == "delivered"
    assert original["delivery_date_label"] == "September 12"
    assert original["status_observed_at"] == observed
    assert not original["is_stale"]
    store.close()

    store = Store(tmp_path)
    assert store.shipments()[0]["status"] == "delivered"
    assert store.shipments()[0]["is_stale"]
    later = datetime.now(UTC).isoformat()
    store.save_discovery(DiscoveryResult([link], 1, True, {link: DeliveryStatus()}), later)
    shipment = store.shipments(revalidated=True)[0]
    assert shipment["status"] == "delivered"
    assert shipment["status_observed_at"] == observed
    assert shipment["status_checked_at"] == later
    assert shipment["is_stale"]
    assert shipment["stops_remaining"] is None
    store.close()


def test_version_two_migration_preserves_ids_and_private_urls(tmp_path: Path) -> None:
    """Add delivery fields to an existing discovery database without rewriting its IDs."""
    state = tmp_path / "state"
    state.mkdir()
    (state / "installation-secret").write_bytes(b"s" * 32)
    with sqlite3.connect(state / "tracker.sqlite3") as connection:
        connection.execute(
            "CREATE TABLE shipments (shipment_id TEXT PRIMARY KEY, order_id TEXT, "
            "tracking_url TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO shipments VALUES (?, ?, ?, ?, ?)",
            (
                "shp_saved",
                "ord_saved",
                "private-url",
                "2026-09-01T00:00:00+00:00",
                "2026-09-01T00:00:00+00:00",
            ),
        )
        connection.execute("PRAGMA user_version=2")
    store = Store(tmp_path)
    shipment = store.shipments()[0]
    assert shipment["shipment_id"] == "shp_saved"
    assert shipment["status"] == "unknown"
    assert shipment["status_checked_at"] is None
    assert (
        store.connection.execute("SELECT tracking_url FROM shipments").fetchone()[0]
        == "private-url"
    )
    store.close()


def test_failed_migration_does_not_leave_partially_added_columns(tmp_path: Path) -> None:
    """A later migration error must roll back earlier ALTER statements too."""
    state = tmp_path / "state"
    state.mkdir()
    path = state / "tracker.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE shipments (shipment_id TEXT PRIMARY KEY, delivery_date_label TEXT)"
        )
        connection.execute("PRAGMA user_version=2")
    with pytest.raises(sqlite3.OperationalError, match="duplicate column"):
        Store(tmp_path)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(shipments)")}
        assert "delivery_status" not in columns
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
