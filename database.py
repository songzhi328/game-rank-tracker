# database.py
# SQLite database initialization and CRUD operations for Game Rank Tracker

import sqlite3
from datetime import date
from typing import List, Optional, Dict, Any

import config


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with foreign keys enabled and row_factory set to Row."""
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    """Check whether a column exists in a table."""
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row["name"] == column for row in cursor.fetchall())


def _table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    """Check whether a table exists in the database."""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cursor.fetchone() is not None


def init_db() -> None:
    """Initialize the database schema (creates tables if they don't exist)."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Main games table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS games (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT    NOT NULL,
                app_id          TEXT    NOT NULL,
                google_app_id   TEXT    DEFAULT '',
                created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
            """
        )

        # Rankings table (store-based, supports ios + google)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS rankings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id     INTEGER NOT NULL,
                store       TEXT    NOT NULL DEFAULT 'ios'
                            CHECK(store IN ('ios', 'google')),
                rank_type   TEXT    NOT NULL
                            CHECK(rank_type IN ('free', 'grossing', 'paid')),
                rank        INTEGER NOT NULL DEFAULT -1,
                region      TEXT    NOT NULL DEFAULT 'cn',
                checked_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS game_region_ids (
                game_id INTEGER NOT NULL,
                region  TEXT    NOT NULL,
                app_id  TEXT    NOT NULL,
                PRIMARY KEY (game_id, region),
                FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
            )
            """
        )

        # Migration: add google_app_id column if it doesn't exist
        if not _column_exists(cursor, "games", "google_app_id"):
            cursor.execute("ALTER TABLE games ADD COLUMN google_app_id TEXT DEFAULT ''")

        # Migration: add 'store' column to existing rankings tables if missing
        if not _column_exists(cursor, "rankings", "store"):
            cursor.execute("ALTER TABLE rankings ADD COLUMN store TEXT NOT NULL DEFAULT 'ios'")

        conn.commit()


def seed_default_games() -> None:
    """Insert default games from config if the games table is empty."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS cnt FROM games")
        row = cursor.fetchone()
        if row["cnt"] == 0:
            for game in config.DEFAULT_GAMES:
                cursor.execute(
                    "INSERT OR IGNORE INTO games (name, app_id, google_app_id) VALUES (?, ?, ?)",
                    (game["name"], game["app_id"], game.get("google_app_id", "")),
                )
                # Get the game_id for the just-inserted row
                cursor.execute(
                    "SELECT id FROM games WHERE app_id = ?", (game["app_id"],)
                )
                game_row = cursor.fetchone()
                if game_row and "region_ids" in game:
                    for region, region_app_id in game["region_ids"].items():
                        cursor.execute(
                            "INSERT OR IGNORE INTO game_region_ids (game_id, region, app_id) VALUES (?, ?, ?)",
                            (game_row["id"], region, region_app_id),
                        )
            conn.commit()


# ---------------------------------------------------------------------------
# Games CRUD
# ---------------------------------------------------------------------------

def get_all_games() -> List[Dict[str, Any]]:
    """Return all tracked games as a list of dicts, including region_ids."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, app_id, google_app_id, created_at FROM games ORDER BY id")
        games = [dict(row) for row in cursor.fetchall()]

        # Attach region_ids for each game
        for game in games:
            cursor.execute(
                "SELECT region, app_id FROM game_region_ids WHERE game_id = ?",
                (game["id"],),
            )
            game["region_ids"] = {row["region"]: row["app_id"] for row in cursor.fetchall()}

        return games


def add_game(name: str, app_id: str, google_app_id: str = "") -> Dict[str, Any]:
    """Add a new game. Returns the inserted row dict or raises if duplicate."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO games (name, app_id, google_app_id) VALUES (?, ?, ?)",
            (name.strip(), app_id.strip(), google_app_id.strip()),
        )
        conn.commit()
        game_id = cursor.lastrowid
        cursor.execute("SELECT id, name, app_id, google_app_id, created_at FROM games WHERE id = ?", (game_id,))
        game = dict(cursor.fetchone())
        game["region_ids"] = {}
        return game


def delete_game(game_id: int) -> bool:
    """Delete a game by id. Returns True if a row was deleted."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM games WHERE id = ?", (game_id,))
        conn.commit()
        return cursor.rowcount > 0


def get_game_by_app_id(app_id: str) -> Optional[Dict[str, Any]]:
    """Return a game row by its App Store ID, or None."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, app_id, google_app_id, created_at FROM games WHERE app_id = ?",
            (app_id,),
        )
        row = cursor.fetchone()
        if row:
            game = dict(row)
            cursor.execute(
                "SELECT region, app_id FROM game_region_ids WHERE game_id = ?",
                (game["id"],),
            )
            game["region_ids"] = {r["region"]: r["app_id"] for r in cursor.fetchall()}
            return game
        return None


def update_google_app_id(game_id: int, google_app_id: str) -> None:
    """Update the Google Play app ID for a game."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE games SET google_app_id = ? WHERE id = ?",
            (google_app_id.strip(), game_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Region-specific App ID management
# ---------------------------------------------------------------------------

def get_region_app_id(game_id: int, region: str) -> str:
    """
    Get the app_id for a game in a specific region.
    Falls back to the game's default app_id if no region-specific ID is configured.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT app_id FROM game_region_ids WHERE game_id = ? AND region = ?",
            (game_id, region),
        )
        row = cursor.fetchone()
        if row:
            return row["app_id"]
        # Fallback to default app_id
        cursor.execute("SELECT app_id FROM games WHERE id = ?", (game_id,))
        fallback = cursor.fetchone()
        return fallback["app_id"] if fallback else ""


def get_region_app_ids(game_id: int) -> Dict[str, str]:
    """Get all region-specific app_ids for a game. Returns {region: app_id}."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT region, app_id FROM game_region_ids WHERE game_id = ?",
            (game_id,),
        )
        return {row["region"]: row["app_id"] for row in cursor.fetchall()}


def set_region_app_ids(game_id: int, region_ids: Dict[str, str]) -> None:
    """
    Set region-specific app_ids for a game.
    Upserts all entries; uses INSERT OR REPLACE to handle updates.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        for region, app_id in region_ids.items():
            if not app_id.strip():
                cursor.execute(
                    "DELETE FROM game_region_ids WHERE game_id = ? AND region = ?",
                    (game_id, region),
                )
            else:
                cursor.execute(
                    "INSERT OR REPLACE INTO game_region_ids (game_id, region, app_id) VALUES (?, ?, ?)",
                    (game_id, region, app_id.strip()),
                )
        conn.commit()


# ---------------------------------------------------------------------------
# Rankings
# ---------------------------------------------------------------------------

def save_ranking(game_id: int, rank_type: str, rank: int, region: str = "cn", store: str = "ios") -> None:
    """Insert a ranking record with the current local timestamp."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO rankings (game_id, store, rank_type, rank, region, checked_at)
            VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
            """,
            (game_id, store, rank_type, rank, region),
        )
        conn.commit()


def get_rankings_for_game(
    game_id: int, region: str = "cn", store: str = "ios", limit: int = 30
) -> Dict[str, List[Dict[str, Any]]]:
    """Return the last `limit` ranking records for a game in a region+store, keyed by rank_type."""
    with get_connection() as conn:
        cursor = conn.cursor()
        result: Dict[str, List[Dict[str, Any]]] = {"free": [], "grossing": [], "paid": []}
        valid_types = ("free", "grossing", "paid")
        for rank_type in valid_types:
            cursor.execute(
                """
                SELECT rank, checked_at
                FROM rankings
                WHERE game_id = ? AND rank_type = ? AND region = ? AND store = ?
                ORDER BY checked_at DESC
                LIMIT ?
                """,
                (game_id, rank_type, region, store, limit),
            )
            rows = [dict(r) for r in cursor.fetchall()]
            result[rank_type] = list(reversed(rows))
        return result


def get_latest_rankings() -> Dict[int, Dict[str, Dict[str, Dict[str, int]]]]:
    """
    Return the most recent rank for every game+region+store.

    Returns:
        {game_id: {region: {store: {rank_type: rank}}}}
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT r.game_id, r.store, r.region, r.rank_type, r.rank
            FROM rankings r
            WHERE r.id IN (
                SELECT MAX(id) FROM rankings GROUP BY game_id, store, region, rank_type
            )
            """
        )
        latest: Dict[int, Dict[str, Dict[str, Dict[str, int]]]] = {}
        for row in cursor.fetchall():
            game_id = row["game_id"]
            region = row["region"]
            store = row["store"]
            if game_id not in latest:
                latest[game_id] = {}
            if region not in latest[game_id]:
                latest[game_id][region] = {}
            if store not in latest[game_id][region]:
                latest[game_id][region][store] = {}
            latest[game_id][region][store][row["rank_type"]] = row["rank"]
        return latest


def has_checked_today(region: Optional[str] = None) -> bool:
    """Return True if there is at least one ranking record for today (local time)."""
    today_str = date.today().isoformat()
    with get_connection() as conn:
        cursor = conn.cursor()
        if region:
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM rankings WHERE checked_at LIKE ? AND region = ?",
                (f"{today_str}%", region),
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM rankings WHERE checked_at LIKE ?",
                (f"{today_str}%",),
            )
        row = cursor.fetchone()
        return row["cnt"] > 0


def get_last_checked_at(region: Optional[str] = None, store: Optional[str] = None) -> Optional[str]:
    """Return the most recent checked_at timestamp, optionally filtered by region and/or store."""
    with get_connection() as conn:
        cursor = conn.cursor()
        conditions = []
        params = []
        if region:
            conditions.append("region = ?")
            params.append(region)
        if store:
            conditions.append("store = ?")
            params.append(store)
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        cursor.execute(
            f"SELECT MAX(checked_at) AS last FROM rankings WHERE {where_clause}",
            params,
        )
        row = cursor.fetchone()
        return row["last"]
