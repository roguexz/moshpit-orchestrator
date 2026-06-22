import os
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

from playwright.sync_api import (
    sync_playwright,
    Page,
    BrowserContext,
    TimeoutError,
)
from moshpit.exceptions import JXAError, MusicAppException

from loguru import logger


class AppleMusicWebEngine:
    """
    Playwright-based engine for Apple Music Web Player.
    Bypasses the JXA local library limitation by interacting with the DOM.
    """

    def __init__(self, target_playlist: str, storefront: str = "us"):
        self.target_playlist = target_playlist
        self.storefront = storefront

        # Start playwright once for the lifetime of this engine
        self.playwright = sync_playwright().start()
        self.context = self._ensure_authenticated(self.playwright)

    def close(self):
        """Clean up the browser and playwright instance."""
        if hasattr(self, "context"):
            self.context.close()
        if hasattr(self, "playwright"):
            self.playwright.stop()

    def _ensure_authenticated(self, p) -> BrowserContext:
        """
        Loads the persistent profile if it exists. If not, or if invalid, launches a headed browser
        for the user to log in manually, then saves the profile.
        """
        import sys

        home = os.path.expanduser("~")
        if sys.platform == "darwin":
            cache_dir = os.path.join(home, "Library", "Caches", "moshpit-mauler")
        else:
            cache_dir = os.path.join(home, ".cache", "moshpit-mauler")

        user_data_dir = os.path.join(cache_dir, "playwright_profile")
        os.makedirs(user_data_dir, exist_ok=True)

        # Try to load existing session headlessly
        logger.debug(f"Loading Playwright persistent context from {user_data_dir}")
        context = p.chromium.launch_persistent_context(
            user_data_dir,
            headless=True,
            args=[
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(f"https://music.apple.com/{self.storefront}/home")

        # Wait to see if we are logged in. The 'Sign In' button shouldn't exist.
        try:
            sign_in_hidden = True
            try:
                # If this succeeds, it means the sign in button is visible -> not logged in
                page.wait_for_selector("button:has-text('Sign In')", timeout=3000)
                sign_in_hidden = False
            except TimeoutError:
                pass

            if sign_in_hidden:
                logger.debug("Session is valid.")
                return context
            else:
                logger.warning(
                    "Session is invalid or expired. Re-authentication required."
                )
                context.close()
        except Exception as e:
            logger.warning(f"Error validating session: {e}")
            try:
                context.close()
            except Exception:
                pass

        # If we reach here, we need to authenticate
        logger.info(
            "No valid session found. Launching browser for manual Apple Music login."
        )
        print("\n" + "=" * 60)
        print("ACTION REQUIRED: PLEASE LOG IN TO APPLE MUSIC")
        print("A browser window will open. Please log in with your Apple ID.")
        print(
            "Once logged in and the 'Sign In' button disappears, the script will continue."
        )
        print("=" * 60 + "\n")

        context = p.chromium.launch_persistent_context(
            user_data_dir,
            headless=False,
            args=[
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(f"https://music.apple.com/{self.storefront}/home")

        # Wait for the user to log in. We wait for the Sign In button to disappear.
        try:
            # First wait for it to appear (if it hasn't already)
            page.wait_for_selector("button:has-text('Sign In')", timeout=10000)
            logger.info("Waiting for you to log in...")
            # Now wait for it to be hidden
            page.wait_for_selector(
                "button:has-text('Sign In')", state="hidden", timeout=300000
            )  # 5 mins max
            logger.info("Sign In successful. Profile saved.")
        except Exception as e:
            logger.error(f"Timeout or error during login: {e}")
            context.close()
            raise Exception("Authentication failed or timed out.")

        return context

    def _get_existing_tracks(self) -> set:
        """
        Uses JXA to check if the playlist exists, creates it if not, and
        fetches the list of track names already in the playlist to ensure idempotent additions.
        """
        import subprocess

        jxa = f"""
        (() => {{
            var Music = Application("Music");
            var targetName = "{self.target_playlist}";
            var playlists = Music.userPlaylists;
            var playlist = null;
            for (var i = 0; i < playlists.length; i++) {{
                if (playlists[i].name() === targetName) {{
                    playlist = playlists[i];
                    break;
                }}
            }}
            if (!playlist) {{
                playlist = Music.UserPlaylist({{name: targetName}}).make();
                return JSON.stringify([]);
            }}
            var tracksSpec = playlist.tracks;
            var names = [];
            var artists = [];
            try {{
                names = tracksSpec.name();
                artists = tracksSpec.artist();
            }} catch(e) {{}}
            var out = [];
            for (var i = 0; i < names.length; i++) {{
                try {{
                    out.push(names[i].toLowerCase() + "|" + artists[i].toLowerCase());
                }} catch(e) {{}}
            }}
            return JSON.stringify(out);
        }})();
        """
        try:
            result = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", jxa],
                capture_output=True,
                text=True,
                check=True,
            )
            import json

            return set(json.loads(result.stdout))
        except Exception as e:
            logger.warning(
                f"Could not fetch existing tracks for idempotency check: {e}"
            )
            if hasattr(e, "stderr") and e.stderr:
                logger.warning(f"JXA Stderr: {e.stderr}")
            return set()

    def append_resolved_tracks(
        self, artist_name: str, tracks: List[Any], delay: float = 0.5
    ) -> Dict[str, Any]:
        """
        Searches for each track via Apple Music Web, adds it to the target playlist.
        Returns a summary dict with counts of added, skipped, and failed tracks.
        """
        added = 0
        skipped = 0
        failed = 0
        failed_tracks: List[Dict[str, str]] = []

        existing_tracks = self._get_existing_tracks()

        page = self.context.pages[0] if self.context.pages else self.context.new_page()

        # Go to Apple Music
        page.goto(f"https://music.apple.com/{self.storefront}/home")

        for track in tracks:
            title = track.title if hasattr(track, "title") else track.get("title", "")
            artist = (
                track.artist
                if hasattr(track, "artist")
                else track.get("artist", artist_name)
            )

            track_id = f"{title.lower()}|{artist.lower()}"
            if track_id in existing_tracks:
                logger.info(f"  → Skipped (already in playlist): '{title}' by {artist}")
                skipped += 1
                continue

            search_term = f"{artist} {title}"
            logger.debug(f"Searching web for: {search_term}")

            try:
                res = self._add_track(page, search_term)
                status = res.get("status", "error")

                if status == "added":
                    added += 1
                    logger.info(f"  ✓ Added: '{title}' by {artist}")
                    time.sleep(delay)
                elif status == "skipped":
                    skipped += 1
                    logger.debug(f"  → Skipped: '{title}' by {artist}")
                else:
                    failed += 1
                    failed_tracks.append(
                        {
                            "title": title,
                            "artist": artist,
                            "reason": res.get("message", ""),
                        }
                    )
                    logger.debug(
                        f"  ✗ Not found: '{title}' by {artist} — {res.get('message', '')}"
                    )
            except Exception as e:
                failed += 1
                failed_tracks.append(
                    {"title": title, "artist": artist, "reason": str(e)}
                )
                logger.debug(f"  ✗ Error adding '{title}' by {artist}: {e}")

        if failed_tracks and added == 0:
            logger.warning(
                f"No tracks could be added for '{artist_name}' ({failed} not found in catalog)."
            )

        return {
            "artist": artist_name,
            "added": added,
            "skipped": skipped,
            "failed": failed,
            "failed_tracks": failed_tracks,
        }

    def _add_track(self, page: Page, search_term: str) -> Dict[str, Any]:
        from urllib.parse import quote

        url = f"https://music.apple.com/{self.storefront}/search?term={quote(search_term)}"
        page.goto(url)

        try:
            # First find the track row, wait for it to be visible, and hover over it to make the more-button visible
            track_row = page.locator("[data-testid='track-lockup']").first
            track_row.wait_for(state="visible", timeout=10000)
            track_row.hover()
            page.wait_for_timeout(500)

            # Now find the more-button inside the hovered track row
            more_button = track_row.locator("[data-testid='more-button']").first
            more_button.wait_for(state="visible", timeout=5000)
            more_button.click()

            # Now wait for the context menu item "Add to Playlist"
            add_to_playlist = page.locator("text='Add to Playlist'").first
            add_to_playlist.wait_for(state="visible", timeout=5000)

            # Hover over "Add to Playlist" to open the sub-menu of playlists
            add_to_playlist.hover()
            page.wait_for_timeout(1000)  # give time for submenu to pop

            # Find the specific playlist by its title attribute (which is robust for context menus)
            playlist_item = page.locator(
                f"button[title='{self.target_playlist}']"
            ).first

            try:
                playlist_item.wait_for(state="visible", timeout=5000)
                playlist_item.click()
            except TimeoutError:
                logger.info(
                    f"Playlist '{self.target_playlist}' not found in web player menu. "
                    "Creating it on the fly..."
                )
                try:
                    # Click the "New Playlist" button
                    new_playlist_btn = page.locator("text='New Playlist'").first
                    new_playlist_btn.wait_for(state="visible", timeout=5000)
                    new_playlist_btn.click()

                    # Wait for the Playlist Title input to appear and type the name
                    title_input = page.locator(
                        "input[placeholder='Playlist Title']"
                    ).first
                    title_input.wait_for(state="visible", timeout=5000)
                    title_input.fill(self.target_playlist)

                    # Click the submit/Create button
                    create_btn = page.locator("button:has-text('Create')").first
                    create_btn.wait_for(state="visible", timeout=5000)
                    create_btn.click()

                    # Wait for the input to disappear to confirm creation/addition is done
                    title_input.wait_for(state="hidden", timeout=10000)
                except Exception as create_err:
                    return {
                        "status": "not_found",
                        "message": f"Playlist '{self.target_playlist}' not found in sub-menu, "
                        f"and failed to create on the fly: {create_err}",
                    }

            # Wait a sec for the action to complete or for a popup to appear
            page.wait_for_timeout(1000)

            # Handle possible "Already in Playlist" popup if force-refresh misses JXA cache
            try:
                # The popup usually has a button saying "Add" or "Add Anyway"
                # We target the last visible button with this text
                duplicate_btn = (
                    page.locator(
                        "button:has-text('Add'), button:has-text('Add Anyway')"
                    )
                    .locator("visible=true")
                    .last
                )
                # It might already be visible or might take a moment
                duplicate_btn.wait_for(state="visible", timeout=2000)
                duplicate_btn.click()
                page.wait_for_timeout(1000)
            except TimeoutError:
                pass

            return {"status": "added", "message": "Successfully added via Web Player"}

        except TimeoutError:
            return {
                "status": "not_found",
                "message": "Could not find track in search results or context menu timeout.",
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
            "playlist_name": self.target_playlist,
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
        import subprocess

        try:
            result = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", wrapper],
                capture_output=True,
                text=True,
                check=True,
                timeout=30.0,
            )
            return result.stdout.strip()
        except Exception as e:
            raise JXAError(f"osascript execution failed: {e}")

    def get_playlist_tracks(self) -> List[Dict[str, Any]]:
        """
        Retrieves all tracks currently in the target playlist.
        Each track is a dictionary containing: id, databaseID, name, artist, and album.
        """
        escaped_playlist = self._escape_quote(self.target_playlist)
        jxa_query = f"""
        var playlists = Music.userPlaylists;
        var targetPlaylist = null;
        for (var i = 0; i < playlists.length; i++) {{
            if (playlists[i].name() === "{escaped_playlist}") {{
                targetPlaylist = playlists[i];
                break;
            }}
        }}
        if (!targetPlaylist) {{
            return JSON.stringify({{status: "error", message: "Playlist not found"}});
        }}
        var tracksSpec = targetPlaylist.tracks;
        var ids = tracksSpec.id();
        var dbIds = tracksSpec.databaseID();
        var names = tracksSpec.name();
        var artists = tracksSpec.artist();
        var albums = tracksSpec.album();
        var out = [];
        for (var j = 0; j < ids.length; j++) {{
            out.push({{
                id: ids[j],
                databaseID: dbIds[j],
                name: names[j],
                artist: artists[j],
                album: albums[j]
            }});
        }}
        return JSON.stringify({{status: "success", tracks: out}});
        """
        response_raw = self._run_jxa(jxa_query)
        if not response_raw:
            raise MusicAppException("Empty response while reading playlist tracks.")
        try:
            data = json.loads(response_raw)
            if data.get("status") == "error":
                raise MusicAppException(
                    f"Failed to read playlist tracks: {data.get('message')}"
                )
            return data.get("tracks", [])
        except json.JSONDecodeError:
            raise MusicAppException(
                f"Invalid JSON returned from JXA track query: {response_raw}"
            )

    def delete_tracks_by_id(self, track_ids: List[Any]) -> int:
        """
        Deletes tracks from the target playlist matching the provided unique track IDs.
        Returns the number of deleted tracks.
        """
        if not track_ids:
            return 0
        escaped_playlist = self._escape_quote(self.target_playlist)
        js_track_ids = json.dumps(track_ids)
        jxa_mutation = f"""
        var playlists = Music.userPlaylists;
        var targetPlaylist = null;
        for (var i = 0; i < playlists.length; i++) {{
            if (playlists[i].name() === "{escaped_playlist}") {{
                targetPlaylist = playlists[i];
                break;
            }}
        }}
        if (!targetPlaylist) {{
            return JSON.stringify({{status: "error", message: "Playlist not found"}});
        }}
        var tracks = targetPlaylist.tracks();
        var ids = [];
        try {{
            ids = targetPlaylist.tracks.id();
        }} catch (e) {{}}
        var count = 0;
        var toDelete = {js_track_ids};
        if (ids.length === 0 && tracks.length > 0) {{
            for (var j = tracks.length - 1; j >= 0; j--) {{
                try {{
                    var trackId = tracks[j].id();
                    if (toDelete.indexOf(trackId) !== -1) {{
                        tracks[j].delete();
                        count++;
                    }}
                }} catch (e) {{}}
            }}
        }} else {{
            for (var j = ids.length - 1; j >= 0; j--) {{
                if (toDelete.indexOf(ids[j]) !== -1) {{
                    try {{
                        tracks[j].delete();
                        count++;
                    }} catch (e) {{}}
                }}
            }}
        }}
        return JSON.stringify({{status: "success", count: count}});
        """
        response_raw = self._run_jxa(jxa_mutation)
        if not response_raw:
            raise MusicAppException("Empty response while deleting tracks.")
        try:
            data = json.loads(response_raw)
            if data.get("status") == "error":
                raise MusicAppException(
                    f"Failed to delete playlist tracks: {data.get('message')}"
                )
            return data.get("count", 0)
        except json.JSONDecodeError:
            raise MusicAppException(
                f"Invalid JSON returned from JXA deletion: {response_raw}"
            )
