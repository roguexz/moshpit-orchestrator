import os
import sys
from datetime import datetime, timezone

from moshpit.cache import MoshpitCache


def test_cache_init(tmp_path):
    db_file = tmp_path / "cache.db"
    cache = MoshpitCache(db_path=str(db_file))
    assert cache.db_path == str(db_file)

    # Connect to verify tables exist
    with cache._connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        assert "playlist_sync" in tables
        assert "artist_cache" in tables


def test_cache_os_specific_path(monkeypatch):
    # Set env override to empty so path resolution logic is tested
    monkeypatch.delenv("MOSHPIT_CACHE_DB_PATH", raising=False)

    # Mock OS platform to darwin and verify caches path
    monkeypatch.setattr(sys, "platform", "darwin")
    cache = MoshpitCache()
    expected_darwin_dir = os.path.expanduser("~/Library/Caches/moshpit-mauler")
    assert cache.db_path.startswith(expected_darwin_dir)

    # Mock OS platform to linux and verify caches path
    monkeypatch.setattr(sys, "platform", "linux")
    cache2 = MoshpitCache()
    expected_linux_dir = os.path.expanduser("~/.cache/moshpit-mauler")
    assert cache2.db_path.startswith(expected_linux_dir)


def test_playlist_sync_cache(tmp_path):
    db_file = tmp_path / "cache.db"
    cache = MoshpitCache(db_path=str(db_file))

    # Initial sync time is None
    assert cache.get_playlist_last_sync("Test Playlist") is None

    # Update sync time
    cache.update_playlist_sync("Test Playlist")
    last_sync = cache.get_playlist_last_sync("Test Playlist")
    assert last_sync is not None
    assert isinstance(last_sync, datetime)

    # Timezone check
    if last_sync.tzinfo is None:
        last_sync = last_sync.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - last_sync
    assert delta.total_seconds() < 10.0  # recently updated


def test_artist_search_cache(tmp_path):
    db_file = tmp_path / "cache.db"
    cache = MoshpitCache(db_path=str(db_file))

    # Initial search value is None
    assert cache.get_artist_cache("Unknown Artist") is None

    # Cache successful search
    cache.update_artist_cache("Flobots", "success", [])
    cached = cache.get_artist_cache("Flobots")
    assert cached is not None
    assert cached["status"] == "success"
    assert cached["results"] == []
    assert isinstance(cached["last_searched_at"], datetime)

    # Cache failed/unresolved search with recommendations
    suggestions = ["Song A", "Song B"]
    cache.update_artist_cache("Dua Lipa", "not_found", suggestions)
    cached2 = cache.get_artist_cache("Dua Lipa")
    assert cached2 is not None
    assert cached2["status"] == "not_found"
    assert cached2["results"] == suggestions


def test_cache_env_path_override(monkeypatch, tmp_path):
    db_file = tmp_path / "env_override.db"
    monkeypatch.setenv("MOSHPIT_CACHE_DB_PATH", str(db_file))
    cache = MoshpitCache()
    assert cache.db_path == str(db_file)
