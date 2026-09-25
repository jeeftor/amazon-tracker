"""Persist session verification history without page content or account identifiers."""

import sqlite3
from pathlib import Path


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
        if version > 1:
            self.connection.close()
            raise RuntimeError("Database schema is newer than this application")
        with self.connection:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS session_history "
                "(id INTEGER PRIMARY KEY, state TEXT NOT NULL, "
                "reason TEXT NOT NULL, verified_at TEXT NOT NULL)"
            )
            self.connection.execute("PRAGMA user_version=1")

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

    def close(self) -> None:
        """Flush and close the database."""
        self.connection.close()
