"""Persist session verification history without page content or account identifiers."""

import os
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from amazon_tracker.discovery import DiscoveryResult
from amazon_tracker.shipments import shipment_candidate


class Store:
    """Own the initial versioned SQLite database."""

    def __init__(self, data_dir: Path) -> None:
        """Create private storage and migrate the initial schema."""
        state_dir = data_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        state_dir.chmod(0o700)
        self.connection = sqlite3.connect(state_dir / "tracker.sqlite3")
        self.connection.execute("PRAGMA journal_mode=WAL")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > 2:
            self.connection.close()
            raise RuntimeError("Database schema is newer than this application")
        with self.connection:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS session_history "
                "(id INTEGER PRIMARY KEY, state TEXT NOT NULL, "
                "reason TEXT NOT NULL, verified_at TEXT NOT NULL)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS shipments ("
                "shipment_id TEXT PRIMARY KEY, order_id TEXT, tracking_url TEXT NOT NULL, "
                "first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS discovery_runs ("
                "id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, "
                "pages_scanned INTEGER NOT NULL, "
                "complete INTEGER NOT NULL, links_seen INTEGER NOT NULL, "
                "shipments_seen INTEGER NOT NULL, unsupported_links INTEGER NOT NULL)"
            )
            self.connection.execute("PRAGMA user_version=2")
        secret_file = state_dir / "installation-secret"
        if not secret_file.exists():
            if self.connection.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]:
                self.connection.close()
                raise RuntimeError("Your installation secret is missing; restore it from backup")
            try:
                descriptor = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as secret:
                    secret.write(secrets.token_bytes(32))
                    secret.flush()
                    os.fsync(secret.fileno())
        self.secret = secret_file.read_bytes()
        if len(self.secret) != 32:
            self.connection.close()
            raise RuntimeError("Your installation secret is invalid; restore it from backup")

    def record(self, state: str, reason: str, verified_at: str) -> None:
        """Persist a sanitized result and bound historical retention."""
        with self.connection:
            self.connection.execute(
                "INSERT INTO session_history(state, reason, verified_at) VALUES (?, ?, ?)",
                (state, reason, verified_at),
            )
            self.connection.execute(
                "DELETE FROM session_history WHERE id NOT IN "
                "(SELECT id FROM session_history ORDER BY id DESC LIMIT 1000)"
            )

    def last_verified_at(self) -> str | None:
        """Return historical verification time, never a current login claim."""
        row = self.connection.execute(
            "SELECT verified_at FROM session_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return str(row[0]) if row else None

    def save_discovery(self, result: DiscoveryResult, observed_at: str) -> None:
        """Upsert distinct shipments atomically; absence never means delivered or cancelled."""
        candidates = {}
        unsupported = 0
        for link in result.links:
            candidate = shipment_candidate(link, self.secret)
            if candidate is None:
                unsupported += 1
            else:
                candidates[candidate.shipment_id] = candidate
        with self.connection:
            for candidate in candidates.values():
                self.connection.execute(
                    "INSERT INTO shipments VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(shipment_id) DO UPDATE SET "
                    "order_id=COALESCE(excluded.order_id, shipments.order_id), "
                    "tracking_url=excluded.tracking_url, last_seen_at=excluded.last_seen_at",
                    (
                        candidate.shipment_id,
                        candidate.order_id,
                        candidate.tracking_url,
                        observed_at,
                        observed_at,
                    ),
                )
            self.connection.execute(
                "INSERT INTO discovery_runs(observed_at, pages_scanned, complete, links_seen, "
                "shipments_seen, unsupported_links) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    observed_at,
                    result.pages_scanned,
                    result.complete,
                    len(result.links),
                    len(candidates),
                    unsupported,
                ),
            )
            self.connection.execute(
                "DELETE FROM discovery_runs WHERE id NOT IN "
                "(SELECT id FROM discovery_runs ORDER BY id DESC LIMIT 1000)"
            )

    def last_discovery(self) -> dict[str, Any] | None:
        """Return counts and scan coverage without exposing private tracking links."""
        row = self.connection.execute(
            "SELECT observed_at, pages_scanned, complete, links_seen, shipments_seen, "
            "unsupported_links FROM discovery_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return {
            "observed_at": row[0],
            "pages_scanned": row[1],
            "complete": bool(row[2]),
            "links_seen": row[3],
            "shipments_seen": row[4],
            "unsupported_links": row[5],
        }

    def shipments(self, *, revalidated: bool = False) -> list[dict[str, Any]]:
        """Expose candidate packages with explicit unknown delivery status and freshness."""
        rows = self.connection.execute(
            "SELECT shipment_id, order_id, last_seen_at FROM shipments ORDER BY shipment_id"
        ).fetchall()
        now = datetime.now(UTC)
        return [
            {
                "shipment_id": row[0],
                "order_id": row[1],
                "status": "unknown",
                "tracking_visibility": "unknown",
                "stops_remaining": None,
                "observed_at": row[2],
                "stale_after_seconds": 1800,
                "is_stale": not revalidated
                or (now - datetime.fromisoformat(row[2])).total_seconds() > 1800,
            }
            for row in rows
        ]

    def close(self) -> None:
        """Flush and close the database."""
        self.connection.close()
