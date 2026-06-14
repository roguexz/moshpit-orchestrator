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
        """Queries the Apple Music Catalog and duplicates top tracks to target playlist."""
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
