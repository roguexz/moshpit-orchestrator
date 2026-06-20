import os
import sys
import time
from typing import Optional, List, Any
from urllib.parse import urlparse
import typer
from loguru import logger

from moshpit.exceptions import PlatformNotSupportedError
from moshpit.logger import setup_logger
from moshpit.config import settings
from moshpit.ingest import WebScraperIngester, VisualIngester, clean_artist_name
from moshpit.resolver import TopTracksResolver
from moshpit.ipc import AppleMusicWebEngine, AppleMusicIPCEngine
from moshpit.cache import MoshpitCache
from datetime import datetime, timezone

app = typer.Typer(help="Moshpit Orchestrator: Apple Music Playlist Generator")


@app.callback()
def main():
    """
    Moshpit Orchestrator: Local VLM & IPC Automation Engine.
    """
    pass


def validate_platform():
    """Verifies that the script is running on macOS."""
    if sys.platform != "darwin":
        raise PlatformNotSupportedError(
            "Moshpit Orchestrator requires macOS (darwin) to automate Music.app."
        )


def validate_input(input_path: str):
    """Checks that the input path is either a valid URL or an existing file."""
    if input_path.startswith("http://") or input_path.startswith("https://"):
        return
    if not os.path.exists(input_path):
        raise typer.BadParameter(
            f"Input path '{input_path}' does not exist and is not a valid URL."
        )


def get_suggested_tracks(artist: str, count: int = 3) -> list[str]:
    """Retrieves suggested top tracks for an artist from iTunes Search API, falling back to local LLM."""
    import requests
    import json

    # 1. Try iTunes Search API
    try:
        url = "https://itunes.apple.com/search"
        params = {"term": artist, "entity": "song", "limit": str(count)}
        resp = requests.get(url, params=params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            songs = []
            for item in data.get("results", []):
                item_artist = item.get("artistName", "").lower()
                if artist.lower() in item_artist or item_artist in artist.lower():
                    track_name = item.get("trackName")
                    if track_name and track_name not in songs:
                        songs.append(track_name)
            if songs:
                return songs
    except Exception as e:
        logger.debug(f"iTunes API suggestion failed for artist '{artist}': {e}")

    # 2. Fallback to local LLM
    try:
        from moshpit.ingest.base import BaseIngester
        from moshpit.ingest.normalizer import extract_json_block

        class SuggestionHelper(BaseIngester):
            def extract_artists(self, input_path: str) -> list[str]:
                return []

        helper = SuggestionHelper(settings)
        prompt = (
            f"Identify the top {count} most popular or representative songs of the artist or band '{artist}'. "
            f"Respond with a JSON object containing a 'songs' key mapping to a list of strings (the song names). "
            f"Do not include any explanation or markdown formatting other than the JSON block."
        )
        raw_resp = helper.query_llm(prompt)
        data = json.loads(extract_json_block(raw_resp))
        if data and "songs" in data:
            return [str(s) for s in data["songs"]]
    except Exception as e:
        logger.debug(f"LLM suggestion fallback failed for artist '{artist}': {e}")

    return []


@app.command()
def run(
    input_path: str = typer.Argument(
        ..., help="Path to local image, text file, or schedule URL"
    ),
    playlist: Optional[str] = typer.Option(
        None,
        "--playlist",
        "-p",
        help="Name of the target Apple Music playlist. If omitted, a name will be generated.",
    ),
    tracks_per_artist: int = typer.Option(
        settings.default_tracks_per_artist,
        "--tracks-per-artist",
        "-t",
        help="Number of top tracks to add per artist.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Resolve tracks but do not modify Apple Music library."
    ),
    storefront: str = typer.Option(
        "us",
        "--storefront",
        "-s",
        help="The Apple Music storefront to use (e.g. us, in).",
    ),
    print_artists: bool = typer.Option(
        False,
        "--print-artists",
        help="Only extract and print the list of artists to stdout, then exit.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable debug logging output.",
    ),
    force_refresh: bool = typer.Option(
        False,
        "--force-refresh",
        "-f",
        help="Force refresh and bypass cache for playlist sync and artist search.",
    ),
):
    """
    Extract artists from the input and generate an Apple Music playlist containing their top tracks.
    """
    # 1. Initialize logging level
    log_level = "DEBUG" if verbose else "INFO"
    setup_logger(level=log_level)

    logger.info("Initializing Moshpit Orchestrator runtime environment...")

    # 2. Validate platform and input
    if not print_artists:
        try:
            validate_platform()
        except PlatformNotSupportedError as e:
            logger.error(str(e))
            raise typer.Exit(code=1)

    try:
        validate_input(input_path)
    except typer.BadParameter as e:
        logger.error(str(e))
        raise typer.Exit(code=1)

    # 3. Determine and run extraction
    artists = []
    try:
        if input_path.startswith("http://") or input_path.startswith("https://"):
            logger.info(f"Using web scraper ingestion for: {input_path}")
            scraper = WebScraperIngester()
            artists = scraper.extract_artists(input_path)
        else:
            _, ext = os.path.splitext(input_path.lower())
            if ext in [".png", ".jpg", ".jpeg", ".webp", ".gif", ".tiff", ".bmp"]:
                logger.info(f"Using visual vision-LLM ingestion for: {input_path}")
                visual = VisualIngester()
                artists = visual.extract_artists(input_path)
            else:
                logger.info(f"Using local text file ingestion for: {input_path}")
                with open(input_path, "r", encoding="utf-8") as f:
                    for line in f:
                        cleaned = clean_artist_name(line)
                        if cleaned:
                            artists.append(cleaned)
    except Exception as e:
        logger.error(f"Ingestion extraction failed: {e}")
        raise typer.Exit(code=1)

    if not artists:
        logger.error("No valid artist names could be extracted from the input source.")
        raise typer.Exit(code=1)

    # Deduplicate extracted artist names while preserving order
    artists = list(dict.fromkeys(artists))

    if print_artists:
        for artist in artists:
            print(artist)
        return

    # 4. Generate default playlist name if not provided
    target_playlist = playlist
    if not target_playlist:
        if input_path.startswith("http://") or input_path.startswith("https://"):
            parsed = urlparse(input_path)
            domain = parsed.netloc.replace("www.", "")
            name_base = domain.split(".")[0].capitalize()
            target_playlist = f"{name_base} Playlist"
        else:
            base_file = os.path.basename(input_path)
            name_base, _ = os.path.splitext(base_file)
            target_playlist = f"{name_base.capitalize()} Playlist"

    logger.info(f"Targeting Apple Music playlist: '{target_playlist}'")

    # Check playlist sync cache
    cache = MoshpitCache()
    if not force_refresh:
        last_sync = cache.get_playlist_last_sync(target_playlist)
        if last_sync:
            if last_sync.tzinfo is None:
                last_sync = last_sync.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - last_sync
            if delta.total_seconds() < 24 * 3600:
                logger.info(
                    f"Playlist '{target_playlist}' was updated less than 24 hours ago. "
                    "Skipping update. Use --force-refresh to override."
                )
                return

    # 5. Initialize IPC engine
    try:
        engine = AppleMusicWebEngine(target_playlist, storefront=storefront)
    except Exception as e:
        logger.error(f"Failed to initialize Apple Music connection: {e}")
        raise typer.Exit(code=1)

    if dry_run:
        logger.info(
            "Dry-run mode enabled. Simulating track synchronization... (Will resolve tracks but skip playlist addition)"
        )

    # 6. Synchronization Loop
    resolver = TopTracksResolver()
    unresolved_matches = []
    total_added = 0
    total_skipped = 0
    logger.info(f"Starting track synchronization for {len(artists)} artists...")

    for artist in artists:
        logger.info(f"Processing artist: '{artist}'...")
        try:
            # Check artist search cache
            resolved_tracks: Optional[List[Any]] = None
            if not force_refresh:
                cached = cache.get_artist_cache(artist)
                if cached:
                    last_search = cached["last_searched_at"]
                    if last_search.tzinfo is None:
                        last_search = last_search.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - last_search).days < 7:
                        status = cached.get("status")
                        results = cached.get("tracks", [])
                        if status == "success" and results:
                            from moshpit.resolver import TrackSuggestion

                            resolved_tracks = [TrackSuggestion(**t) for t in results]
                            logger.info(
                                f"Loaded {len(resolved_tracks)} tracks from local cache for '{artist}'"
                            )
                        elif status == "not_found":
                            logger.warning(
                                f"Artist '{artist}' was previously unresolved (cached). Skipping."
                            )
                            unresolved_matches.append(
                                {
                                    "artist": artist,
                                    "reason": "NOT_FOUND",
                                    "details": "No tracks could be resolved (cached).",
                                    "suggested_top_tracks": (
                                        [
                                            (
                                                r.get("title", r)
                                                if isinstance(r, dict)
                                                else r
                                            )
                                            for r in results
                                        ]
                                        if results
                                        else []
                                    ),
                                }
                            )
                            continue

            if resolved_tracks is None:
                logger.info(f"Resolving top tracks for '{artist}'...")
                tracks = resolver.resolve(artist, tracks_per_artist)
                if tracks:
                    resolved_tracks = [t.to_dict() for t in tracks]
                    logger.info(
                        f"Resolved {len(resolved_tracks)} tracks for '{artist}' (sources: {', '.join(set(t.source for t in tracks))})"
                    )
                    cache.update_artist_cache(artist, "success", resolved_tracks)
                else:
                    logger.warning(f"No tracks resolved for artist '{artist}'.")
                    cache.update_artist_cache(artist, "not_found", [])
                    unresolved_matches.append(
                        {
                            "artist": artist,
                            "reason": "NOT_FOUND",
                            "details": "Resolver chain returned zero tracks.",
                            "suggested_top_tracks": [],
                        }
                    )
                    continue

            if resolved_tracks:
                if dry_run:
                    logger.info(
                        f"[DRY-RUN] Would add {len(resolved_tracks)} tracks for '{artist}' to playlist."
                    )
                    continue

                # Add resolved tracks to playlist via engine (per-track)
                logger.info(
                    f"Adding {len(resolved_tracks)} resolved tracks for '{artist}' to playlist..."
                )
                from moshpit.resolver import TrackSuggestion

                if isinstance(resolved_tracks[0], dict):
                    suggestions = [TrackSuggestion(**t) for t in resolved_tracks]
                else:
                    suggestions = resolved_tracks

                result = engine.append_resolved_tracks(
                    artist, suggestions, delay=settings.resolver_delay
                )
                added = result.get("added", 0)
                skipped = result.get("skipped", 0)
                failed = result.get("failed", 0)
                total_added += added
                total_skipped += skipped

                if added > 0:
                    logger.info(
                        f"Successfully added {added} tracks for '{artist}' "
                        f"({skipped} skipped, {failed} not found in catalog)."
                    )
                elif skipped > 0 and failed == 0:
                    logger.info(
                        f"All {skipped} tracks for '{artist}' already in playlist (idempotent)."
                    )
                else:
                    logger.warning(
                        f"No tracks could be added for '{artist}' ({failed} not found in catalog)."
                    )
                    failed_tracks = result.get("failed_tracks", [])
                    unresolved_matches.append(
                        {
                            "artist": artist,
                            "reason": "CATALOG_MISS",
                            "details": f"Resolved tracks but {failed} failed catalog search.",
                            "suggested_top_tracks": [
                                ft.get("title", "") for ft in failed_tracks
                            ],
                        }
                    )
        except Exception as e:
            logger.error(f"Unexpected error matching artist '{artist}': {e}")
            unresolved_matches.append(
                {"artist": artist, "reason": "SYSTEM_ERROR", "details": str(e)}
            )

        # Rate-limiting delay to mitigate events thread thrashing
        time.sleep(0.5)

    # 7. Telemetry output
    if unresolved_matches:
        filepath = engine.write_failure_manifest(unresolved_matches, len(artists))
        if filepath:
            logger.warning(
                f"Synchronization completed with issues. Telemetry written to: {filepath}"
            )
        else:
            logger.warning(
                "Synchronization completed with issues. Failed to write failure manifest."
            )
    else:
        # If failure manifest exists from previous run, remove it
        if os.path.exists("failure_manifest.json"):
            try:
                os.remove("failure_manifest.json")
            except OSError:
                pass
        logger.info(
            "Synchronization completed successfully with zero unresolved matches!"
        )

    # Clean up engine resources (Playwright browser session)
    if hasattr(engine, "close"):
        engine.close()

    # Update playlist sync cache
    cache.update_playlist_sync(target_playlist)


@app.command("analyze")
def analyze_playlist(
    playlist: str = typer.Option(
        ..., "--playlist", "-p", help="Name of the target Apple Music playlist"
    ),
    list_artists: bool = typer.Option(
        False, "--list-artists", help="List all unique artist names in the playlist"
    ),
    list_duplicates: bool = typer.Option(
        False,
        "--list-duplicates",
        help="List all duplicate track versions in the playlist",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable debug logging output"
    ),
):
    """
    Analyze an Apple Music playlist, showing stats, unique artists, or duplicate track versions.
    """
    log_level = "DEBUG" if verbose else "INFO"
    setup_logger(level=log_level)
    try:
        validate_platform()
    except PlatformNotSupportedError as e:
        logger.error(str(e))
        raise typer.Exit(code=1)

    try:
        engine = AppleMusicIPCEngine(playlist)
    except Exception as e:
        logger.error(f"Failed to initialize Apple Music connection: {e}")
        raise typer.Exit(code=1)

    try:
        logger.info(f"Scanning playlist '{playlist}' for tracks...")
        tracks = engine.get_playlist_tracks()

        # 1. Total tracks
        total_tracks = len(tracks)

        # 2. Unique artists
        artists = sorted(
            list({t.get("artist", "").strip() for t in tracks if t.get("artist")})
        )

        # 3. Duplicate versions
        from moshpit.dedup import identify_duplicates

        duplicates = identify_duplicates(tracks)

        # Default behavior: Print high-level overview if no specific list options are selected
        if not list_artists and not list_duplicates:
            typer.echo(f"Playlist '{playlist}' analysis summary:")
            typer.echo(f"  - Total tracks: {total_tracks}")
            typer.echo(f"  - Unique artists: {len(artists)}")
            typer.echo(f"  - Duplicate track versions found: {len(duplicates)}")
            typer.echo("Use --list-artists or --list-duplicates to see detailed lists.")
            return

        if list_artists:
            if not artists:
                typer.echo(
                    f"No artists found in playlist '{playlist}' (or playlist is empty)."
                )
            else:
                typer.echo(f"Unique artists in playlist '{playlist}':")
                for artist in artists:
                    typer.echo(artist)

        if list_duplicates:
            if not duplicates:
                typer.echo(
                    f"No duplicate tracks/versions found in playlist '{playlist}'."
                )
            else:
                typer.echo(
                    f"Duplicate/alternative track versions found in playlist '{playlist}':"
                )
                for dup in duplicates:
                    typer.echo(
                        f"  - '{dup.get('name')}' by {dup.get('artist')} (Album: {dup.get('album')})"
                    )
    except Exception as e:
        logger.error(f"Failed to analyze playlist: {e}")
        raise typer.Exit(code=1)
    finally:
        if hasattr(engine, "close"):
            engine.close()


@app.command("prune")
def prune_playlist(
    playlist: str = typer.Option(
        ..., "--playlist", "-p", help="Name of the target Apple Music playlist"
    ),
    artist: Optional[str] = typer.Option(
        None,
        "--artist",
        help="Remove all songs by this artist instead of pruning duplicates",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate execution without modifying the playlist",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable debug logging output"
    ),
):
    """
    Prune duplicate track versions, or remove all tracks by a specific artist from a playlist.
    """
    log_level = "DEBUG" if verbose else "INFO"
    setup_logger(level=log_level)
    try:
        validate_platform()
    except PlatformNotSupportedError as e:
        logger.error(str(e))
        raise typer.Exit(code=1)

    try:
        engine = AppleMusicIPCEngine(playlist)
    except Exception as e:
        logger.error(f"Failed to initialize Apple Music connection: {e}")
        raise typer.Exit(code=1)

    try:
        # Check if we are removing a specific artist or pruning duplicates
        if artist:
            logger.info(f"Scanning playlist '{playlist}' for tracks by '{artist}'...")
            tracks = engine.get_playlist_tracks()
            if not tracks:
                typer.echo(f"Playlist '{playlist}' is empty or does not exist.")
                return

            target_lower = artist.lower().strip()
            to_remove = []
            for track in tracks:
                track_artist = track.get("artist", "").lower().strip()
                if target_lower in track_artist or track_artist in target_lower:
                    to_remove.append(track)

            if not to_remove:
                typer.echo(
                    f"No tracks by artist '{artist}' found in playlist '{playlist}'."
                )
                return

            typer.echo(f"Found {len(to_remove)} tracks by '{artist}' to remove:")
            for track in to_remove:
                typer.echo(f"  - '{track.get('name')}' (Album: {track.get('album')})")

            if dry_run:
                typer.echo("[DRY-RUN] Simulating deletion. No tracks were removed.")
                return

            track_ids = [
                track["id"]
                for track in to_remove
                if "id" in track and track["id"] is not None
            ]
            typer.echo(f"Removing {len(track_ids)} tracks from playlist...")
            deleted_count = engine.delete_tracks_by_id(track_ids)
            typer.echo(
                f"Successfully removed {deleted_count} tracks from playlist '{playlist}'."
            )
        else:
            logger.info(f"Scanning playlist '{playlist}' for duplicate tracks...")
            tracks = engine.get_playlist_tracks()
            if not tracks:
                typer.echo(f"Playlist '{playlist}' is empty or does not exist.")
                return

            from moshpit.dedup import identify_duplicates

            duplicates = identify_duplicates(tracks)

            if not duplicates:
                typer.echo(
                    f"No duplicate tracks/versions found in playlist '{playlist}'."
                )
                return

            typer.echo(
                f"Found {len(duplicates)} duplicate/alternative track versions to remove:"
            )
            for dup in duplicates:
                typer.echo(
                    f"  - '{dup.get('name')}' by {dup.get('artist')} (Album: {dup.get('album')})"
                )

            if dry_run:
                typer.echo("[DRY-RUN] Simulating deletion. No tracks were removed.")
                return

            track_ids = [
                dup["id"] for dup in duplicates if "id" in dup and dup["id"] is not None
            ]
            typer.echo(f"Removing {len(track_ids)} tracks from playlist...")
            deleted_count = engine.delete_tracks_by_id(track_ids)
            typer.echo(
                f"Successfully removed {deleted_count} tracks from playlist '{playlist}'."
            )
    except Exception as e:
        logger.error(f"Failed to prune playlist: {e}")
        raise typer.Exit(code=1)
    finally:
        if hasattr(engine, "close"):
            engine.close()


if __name__ == "__main__":
    app()
