import os
import sys
import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class MoshpitCache:
    """
    SQLite-based cache manager for playlists and artist search results.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.environ.get("MOSHPIT_CACHE_DB_PATH")

        if db_path is None:
            # Resolve OS-specific cache directory
            home = os.path.expanduser("~")
            if sys.platform == "darwin":
                cache_dir = os.path.join(
                    home, "Library", "Caches", "moshpit-mauler"
                )
            else:
                cache_dir = os.path.join(home, ".cache", "moshpit-mauler")

            # Ensure the directory exists
            os.makedirs(cache_dir, exist_ok=True)
            self.db_path = os.path.join(cache_dir, "moshpit_cache.db")
        else:
            self.db_path = db_path

        self._init_db()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS playlist_sync (
                    playlist_name TEXT PRIMARY KEY,
                    last_synced_at TEXT NOT NULL
                )
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS artist_cache (
                    artist_name TEXT PRIMARY KEY,
                    last_searched_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    results_json TEXT NOT NULL
                )
                """)
            conn.commit()

    def get_playlist_last_sync(self, playlist_name: str) -> Optional[datetime]:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT last_synced_at FROM playlist_sync WHERE playlist_name = ?",
                (playlist_name,),
            )
            row = cursor.fetchone()
            if row:
                try:
                    return datetime.fromisoformat(row[0])
                except ValueError:
                    return None
            return None

    def update_playlist_sync(self, playlist_name: str):
        now_str = datetime.now(timezone.utc).isoformat()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO playlist_sync (playlist_name, last_synced_at)
                VALUES (?, ?)
                ON CONFLICT(playlist_name) DO UPDATE SET last_synced_at = excluded.last_synced_at
                """,
                (playlist_name, now_str),
            )
            conn.commit()

    def get_artist_cache(self, artist_name: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT last_searched_at, status, results_json FROM artist_cache WHERE artist_name = ?",
                (artist_name,),
            )
            row = cursor.fetchone()
            if row:
                try:
                    last_searched = datetime.fromisoformat(row[0])
                    return {
                        "last_searched_at": last_searched,
                        "status": row[1],
                        "results": json.loads(row[2]),
                    }
                except (ValueError, json.JSONDecodeError):
                    return None
            return None

    def update_artist_cache(self, artist_name: str, status: str, results: Any):
        now_str = datetime.now(timezone.utc).isoformat()
        results_str = json.dumps(results)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO artist_cache (artist_name, last_searched_at, status, results_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(artist_name) DO UPDATE SET
                    last_searched_at = excluded.last_searched_at,
                    status = excluded.status,
                    results_json = excluded.results_json
                """,
                (artist_name, now_str, status, results_str),
            )
            conn.commit()
