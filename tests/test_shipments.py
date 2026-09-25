"""Check split-package identity, durable discovery, and public-data boundaries."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from amazon_tracker.discovery import DiscoveryResult
from amazon_tracker.shipments import shipment_candidate
from amazon_tracker.storage import Store

BASE = "https://www.amazon.com/gp/your-account/ship-track?orderId=synthetic-order"


def test_split_order_has_distinct_stable_shipment_ids() -> None:
    """One order with two package indexes must never collapse into one entity."""
    first = shipment_candidate(BASE + "&packageIndex=0&ref=first", b"test-secret")
    second = shipment_candidate(BASE + "&packageIndex=1", b"test-secret")
    repeated = shipment_candidate(BASE + "&packageIndex=0&ref=changed", b"test-secret")
    assert first is not None and second is not None and repeated is not None
    assert first.shipment_id != second.shipment_id
    assert first.order_id == second.order_id
    assert first.shipment_id == repeated.shipment_id
    assert "synthetic-order" not in first.shipment_id


@pytest.mark.parametrize(
    "url",
    [
        BASE,
        BASE + "&packageIndex=",
        BASE + "&packageIndex=1&packageIndex=2",
        BASE.replace("www.amazon.com", "amazon.com.attacker.invalid") + "&packageIndex=0",
        BASE.replace("https:", "http:") + "&packageIndex=0",
    ],
)
def test_ambiguous_or_untrusted_identity_is_rejected(url: str) -> None:
    """An order alone, conflicting query fields, or an external link is insufficient."""
    assert shipment_candidate(url, b"test-secret") is None


def test_discovery_survives_restart_and_never_exposes_tracking_links(tmp_path: Path) -> None:
    """Repeated scans and process restarts preserve IDs without public raw identifiers."""
    observed = datetime.now(UTC).isoformat()
    result = DiscoveryResult([BASE + "&packageIndex=0", BASE + "&packageIndex=1", BASE], 2, True)
    store = Store(tmp_path)
    store.save_discovery(result, observed)
    original = store.shipments(revalidated=True)
    assert len(original) == 2
    assert not any(shipment["is_stale"] for shipment in original)
    assert "synthetic-order" not in str(original)
    assert "https://" not in str(original)
    assert store.last_discovery()["unsupported_links"] == 1
    store.close()
    restored = Store(tmp_path)
    assert all(shipment["is_stale"] for shipment in restored.shipments())
    restored.save_discovery(result, observed)
    assert restored.shipments(revalidated=True) == original
    restored.save_discovery(DiscoveryResult([], 1, False), observed)
    assert len(restored.shipments()) == 2
    assert (tmp_path / "state" / "installation-secret").stat().st_mode & 0o777 == 0o600
    restored.close()


def test_missing_secret_with_saved_shipments_fails_closed(tmp_path: Path) -> None:
    """Losing the secret must not silently assign every package a new identity."""
    store = Store(tmp_path)
    store.save_discovery(
        DiscoveryResult([BASE + "&packageIndex=0"], 1, True), datetime.now(UTC).isoformat()
    )
    store.close()
    (tmp_path / "state" / "installation-secret").unlink()
    with pytest.raises(RuntimeError, match="secret is missing"):
        Store(tmp_path)


def test_version_one_migrates_without_losing_session_history(tmp_path: Path) -> None:
    """An existing login-only installation gains discovery without data loss."""
    state = tmp_path / "state"
    state.mkdir()
    db = sqlite3.connect(state / "tracker.sqlite3")
    db.execute(
        "CREATE TABLE session_history (id INTEGER PRIMARY KEY, state TEXT NOT NULL, "
        "reason TEXT NOT NULL, verified_at TEXT NOT NULL)"
    )
    db.execute("INSERT INTO session_history VALUES (1, 'authenticated', 'test', 'saved-time')")
    db.execute("PRAGMA user_version=1")
    db.commit()
    db.close()
    store = Store(tmp_path)
    assert store.last_verified_at() == "saved-time"
    assert store.shipments() == []
    assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 3
    store.close()
