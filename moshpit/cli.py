import os
import sys
import time
from typing import Optional
from urllib.parse import urlparse
import typer
from loguru import logger

from moshpit.exceptions import PlatformNotSupportedError
from moshpit.logger import setup_logger
from moshpit.config import settings
from moshpit.ingest import WebScraperIngester, VisualIngester, clean_artist_name
from moshpit.ipc import AppleMusicIPCEngine

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
        False,
        "--dry-run",
        help="Extract artists and search catalog, but do not modify the playlist.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable debug logging output.",
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

    # 5. Initialize IPC engine
    try:
        engine = AppleMusicIPCEngine(target_playlist)
    except Exception as e:
        logger.error(f"Failed to initialize Apple Music connection: {e}")
        raise typer.Exit(code=1)

    if dry_run:
        logger.info("Dry-run mode enabled. Simulating track synchronization...")
        for artist in artists:
            logger.info(f"[DRY-RUN] Would search and add tracks for artist: {artist}")
        logger.info("Dry-run execution completed successfully.")
        return

    # 6. Synchronization Loop
    unresolved_matches = []
    logger.info(f"Starting track synchronization for {len(artists)} artists...")

    for artist in artists:
        logger.info(f"Processing artist: '{artist}'...")
        try:
            res = engine.append_top_tracks(artist, tracks_per_artist)
            if res.get("status") == "success":
                logger.info(
                    f"Successfully added {res.get('count')} tracks for '{artist}'."
                )
            elif res.get("status") == "not_found":
                logger.warning(
                    f"No catalog matches found for artist '{artist}': {res.get('message')}"
                )
                unresolved_matches.append(
                    {
                        "artist": artist,
                        "reason": "NOT_FOUND",
                        "details": res.get(
                            "message", "No matching songs found in shared catalog."
                        ),
                    }
                )
            else:
                logger.error(f"JXA error for artist '{artist}': {res.get('message')}")
                unresolved_matches.append(
                    {
                        "artist": artist,
                        "reason": "IPC_EXCEPTION",
                        "details": res.get(
                            "message", "Error returned from JXA engine."
                        ),
                    }
                )
        except Exception as e:
            logger.error(f"Unexpected error matching artist '{artist}': {e}")
            unresolved_matches.append(
                {"artist": artist, "reason": "IPC_EXCEPTION", "details": str(e)}
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


if __name__ == "__main__":
    app()
