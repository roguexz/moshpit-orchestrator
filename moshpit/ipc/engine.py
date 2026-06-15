from datetime import datetime
import json
import subprocess
import time
from typing import Any, Dict, List, Optional
from loguru import logger

from moshpit.config import settings
from moshpit.exceptions import JXAError, MusicAppException, PlatformNotSupportedError


class AppleMusicIPCEngine:
    """
    macOS Apple Music JXA IPC automation engine.
    Checks environment capabilities, initializes playlists, queries catalogs,
    and duplicates tracks using osascript subprocess commands.
    """

    def __init__(self, playlist_name: str, config=settings):
        self.playlist_name = playlist_name
        self.config = config
        self.verify_environment()
        self._initialize_playlist()

    def _escape_quote(self, text: str) -> str:
        """Safely escapes backslashes and double quotes in strings for JXA template insertion."""
        return text.replace("\\", "\\\\").replace('"', '\\"')

    def _run_jxa(self, script_body: str) -> Optional[str]:
        """
        Executes JavaScript Apple Automation script inside an IIFE wrapper,
        returning stdout from the process.
        """
        wrapper = f"""
        (function() {{
            var Music = Application('Music');
            try {{
                {script_body}
            }} catch (err) {{
                return JSON.stringify({{status: "error", message: err.message}});
            }}
        }})()
        """
        try:
            result = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", wrapper],
                capture_output=True,
                text=True,
                check=True,
                timeout=self.config.jxa_timeout,
            )
            return result.stdout.strip()
        except subprocess.TimeoutExpired as e:
            raise JXAError(
                f"osascript execution timed out after {self.config.jxa_timeout}s: {e}"
            )
        except subprocess.CalledProcessError as e:
            raise JXAError(
                f"osascript process failed: {e.stderr.strip() or e.stdout.strip()}"
            )

    def verify_environment(self):
        """
        Runs multi-tier environment capability checks.
        Verifies OS platform, Music app process state, and subscription catalog access.
        """
        # 1. Platform Verification
        # Check system platform is Darwin
        import sys

        if sys.platform != "darwin":
            raise PlatformNotSupportedError("macOS is required for JXA automation.")

        # 2. Check Music.app Running State
        try:
            # Check if Music process is active
            subprocess.run(["pgrep", "-x", "Music"], check=True, capture_output=True)
            is_running = True
        except (subprocess.CalledProcessError, FileNotFoundError):
            is_running = False

        if not is_running:
            logger.info("Music.app is not running. Launching...")
            self._run_jxa("Music.launch(); return JSON.stringify({status: 'success'});")
            time.sleep(1.0)

        # 3. Catalog Access Verification
        jxa_check = """
        var playlist = Music.playlists()[0];
        try {
            var searchResults = Music.search(playlist, {for: "Tool"});
            return JSON.stringify({status: "success"});
        } catch (err) {
            return JSON.stringify({status: "error", message: err.message});
        }
        """
        response_raw = self._run_jxa(jxa_check)
        if not response_raw:
            raise MusicAppException("Failed to verify catalog search (empty response).")

        try:
            res_data = json.loads(response_raw)
            if res_data.get("status") == "error":
                raise MusicAppException(
                    f"Catalog check error: {res_data.get('message')}"
                )
            if res_data.get("status") != "success":
                raise MusicAppException(
                    "Apple Music shared catalog is not accessible. "
                    "Ensure you have an active Apple Music subscription and internet connection."
                )
        except json.JSONDecodeError:
            raise MusicAppException(
                f"Invalid JSON response during catalog verification: {response_raw}"
            )

    def _initialize_playlist(self):
        """Creates the destination playlist if it does not already exist."""
        escaped_name = self._escape_quote(self.playlist_name)
        jxa_init = f"""
        var playlists = Music.userPlaylists;
        var exists = false;
        var targetPlaylist;
        
        for (var i = 0; i < playlists.length; i++) {{
            if (playlists[i].name() === "{escaped_name}") {{
                exists = true;
                targetPlaylist = playlists[i];
                break;
            }}
        }}
        
        if (!exists) {{
            targetPlaylist = Music.UserPlaylist({{name: "{escaped_name}"}}).make();
        }}
        return JSON.stringify({{status: "success", playlist: "{escaped_name}"}});
        """
        response_raw = self._run_jxa(jxa_init)
        if not response_raw:
            raise MusicAppException(
                "Empty response while initializing target playlist."
            )

        try:
            data = json.loads(response_raw)
            if data.get("status") == "error":
                raise MusicAppException(
                    f"Failed to initialize playlist: {data.get('message')}"
                )
        except json.JSONDecodeError:
            raise MusicAppException(
                f"Invalid JSON returned from JXA initialization: {response_raw}"
            )

    def append_top_tracks(
        self, artist_name: str, tracks_per_artist: int = 3
    ) -> Dict[str, Any]:
        """Queries the Apple Music Catalog and duplicates top tracks to target playlist.

        Note: This is the legacy method that searches by artist name only.
        Prefer append_resolved_tracks() with pre-resolved track names for
        better catalog hit rates.
        """
        escaped_artist = self._escape_quote(artist_name)
        escaped_playlist = self._escape_quote(self.playlist_name)

        jxa_mutation = f"""
        var playlist = Music.playlists()[0];
        var searchResults = Music.search(playlist, {{for: "{escaped_artist}"}});
        if (searchResults && searchResults.length > 0) {{
            var targetPlaylist = Music.userPlaylists.byName("{escaped_playlist}");
            var targetTracks = targetPlaylist.tracks();
            var existingDbIds = {{}};
            for (var j = 0; j < targetTracks.length; j++) {{
                existingDbIds[targetTracks[j].databaseID()] = true;
            }}
            
            var tracksAdded = 0;
            var addedIds = [];
            var matchedArtist = false;
            
            for (var i = 0; i < searchResults.length; i++) {{
                var song = searchResults[i];
                var songArtist = song.artist().toLowerCase();
                var targetArtist = "{escaped_artist.lower()}";
                
                if (songArtist.includes(targetArtist) || targetArtist.includes(songArtist)) {{
                    matchedArtist = true;
                    if (tracksAdded >= {tracks_per_artist}) continue;
                    
                    if (existingDbIds[song.databaseID()]) {{
                        continue; // Skip track if it is already in the playlist
                    }}
                    song.duplicate({{to: targetPlaylist}});
                    existingDbIds[song.databaseID()] = true; // Mark as added to prevent in-run duplication
                    tracksAdded++;
                    addedIds.push(song.id());
                }}
            }}
            
            if (matchedArtist) {{
                return JSON.stringify({{status: "success", count: tracksAdded, ids: addedIds}});
            }} else {{
                return JSON.stringify({{status: "not_found", message: "No tracks matching artist name boundaries."}});
            }}
        }} else {{
            return JSON.stringify({{status: "not_found", message: "No search results found in catalog."}});
        }}
        """
        response_raw = self._run_jxa(jxa_mutation)
        if not response_raw:
            return {"status": "error", "message": "Empty response from JXA automation"}

        try:
            return json.loads(response_raw)
        except json.JSONDecodeError:
            return {
                "status": "error",
                "message": f"Invalid JSON returned: {response_raw}",
            }

    def add_track_by_name(
        self, song_title: str, artist_name: str
    ) -> Dict[str, Any]:
        """Searches Apple Music catalog for a specific song by title+artist and adds it to the playlist.

        Uses a multi-strategy search to maximize catalog hit rates:
          1. Song title only (most specific — avoids stopword noise from artist names)
          2. Combined "artist songTitle" (for disambiguation)
          3. Artist name only (broadest, last resort)
        Each strategy verifies both artist AND title match before adding.
        Returns a dict with 'status' key: 'success', 'not_found', 'skipped', or 'error'.
        """
        escaped_artist = self._escape_quote(artist_name)
        escaped_title = self._escape_quote(song_title)
        escaped_playlist = self._escape_quote(self.playlist_name)

        jxa_script = f"""
        var playlist = Music.playlists()[0];
        var targetPlaylist = Music.userPlaylists.byName("{escaped_playlist}");
        var targetTracks = targetPlaylist.tracks();
        var existingDbIds = {{}};
        for (var j = 0; j < targetTracks.length; j++) {{
            existingDbIds[targetTracks[j].databaseID()] = true;
        }}

        var targetArtist = "{escaped_artist.lower()}";
        var targetTitle = "{escaped_title.lower()}";

        // Helper: scan search results for an artist+title match
        function findMatch(results) {{
            if (!results || results.length === 0) return null;
            for (var i = 0; i < results.length; i++) {{
                var song = results[i];
                var songArtist = song.artist().toLowerCase();
                var songName = song.name().toLowerCase();

                var artistMatch = songArtist.includes(targetArtist) || targetArtist.includes(songArtist);
                var titleMatch = songName.includes(targetTitle) || targetTitle.includes(songName);

                if (artistMatch && titleMatch) {{
                    return song;
                }}
            }}
            return null;
        }}

        // Strategy 1: Search by song title only (most specific, avoids stopword noise)
        var match = findMatch(Music.search(playlist, {{for: "{escaped_title}"}}));

        // Strategy 2: Search by combined "artist songTitle"
        if (!match) {{
            match = findMatch(Music.search(playlist, {{for: "{escaped_artist} {escaped_title}"}}));
        }}

        // Strategy 3: Search by artist name only (broadest)
        if (!match) {{
            match = findMatch(Music.search(playlist, {{for: "{escaped_artist}"}}));
        }}

        if (!match) {{
            return JSON.stringify({{
                status: "not_found",
                message: "No catalog match for: {escaped_artist} - {escaped_title}"
            }});
        }}

        // Idempotency: skip if already in the playlist
        if (existingDbIds[match.databaseID()]) {{
            return JSON.stringify({{
                status: "skipped",
                message: "Track already in playlist",
                track: match.name(),
                artist: match.artist()
            }});
        }}

        match.duplicate({{to: targetPlaylist}});
        return JSON.stringify({{
            status: "success",
            track: match.name(),
            artist: match.artist(),
            id: match.id()
        }});
        """
        response_raw = self._run_jxa(jxa_script)
        if not response_raw:
            return {"status": "error", "message": "Empty response from JXA automation"}

        try:
            return json.loads(response_raw)
        except json.JSONDecodeError:
            return {
                "status": "error",
                "message": f"Invalid JSON returned: {response_raw}",
            }

    def append_resolved_tracks(
        self, artist_name: str, tracks: list, delay: float = 0.5
    ) -> Dict[str, Any]:
        """Iterates through resolved TrackSuggestion objects and adds each to the playlist.

        Returns a summary dict with counts of added, skipped, and failed tracks.
        The `tracks` parameter accepts a list of TrackSuggestion objects (or dicts
        with 'title' and 'artist' keys).
        """
        added = 0
        skipped = 0
        failed = 0
        failed_tracks: List[Dict[str, str]] = []

        for track in tracks:
            # Support both TrackSuggestion objects and plain dicts
            title = track.title if hasattr(track, "title") else track.get("title", "")
            artist = (
                track.artist if hasattr(track, "artist") else track.get("artist", artist_name)
            )

            if not title:
                continue

            try:
                result = self.add_track_by_name(title, artist)
                status = result.get("status", "error")

                if status == "success":
                    added += 1
                    logger.info(
                        f"  ✓ Added: '{result.get('track', title)}' by {result.get('artist', artist)}"
                    )
                elif status == "skipped":
                    skipped += 1
                    logger.debug(
                        f"  → Skipped (already in playlist): '{title}' by {artist}"
                    )
                else:
                    failed += 1
                    failed_tracks.append(
                        {"title": title, "artist": artist, "reason": result.get("message", "")}
                    )
                    logger.debug(
                        f"  ✗ Not found: '{title}' by {artist} — {result.get('message', '')}"
                    )
            except Exception as e:
                failed += 1
                failed_tracks.append(
                    {"title": title, "artist": artist, "reason": str(e)}
                )
                logger.debug(f"  ✗ Error adding '{title}' by {artist}: {e}")

            time.sleep(delay)

        return {
            "status": "success" if added > 0 else "not_found",
            "added": added,
            "skipped": skipped,
            "failed": failed,
            "failed_tracks": failed_tracks,
        }

    def write_failure_manifest(
        self, unresolved_matches: List[Dict[str, Any]], total_submitted: int
    ) -> str:
        """Generates failure_manifest.json documenting unresolved artist matching results."""
        successful_matches = total_submitted - len(unresolved_matches)

        # Local ISO timestamp with timezone offset
        timestamp = datetime.now().astimezone().isoformat()

        manifest_data = {
            "timestamp": timestamp,
            "playlist_name": self.playlist_name,
            "run_statistics": {
                "total_artists_submitted": total_submitted,
                "successfully_matched": successful_matches,
                "failed_matches": len(unresolved_matches),
            },
            "unresolved_exceptions": unresolved_matches,
        }

        filepath = "failure_manifest.json"
        try:
            with open(filepath, "w") as f:
                json.dump(manifest_data, f, indent=2)
            return filepath
        except IOError as e:
            logger.error(f"Failed to write failure manifest: {e}")
            return ""
