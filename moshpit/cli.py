import os
import sys
from typing import Optional
import typer
from loguru import logger

from moshpit.exceptions import PlatformNotSupportedError
from moshpit.logger import setup_logger
from moshpit.config import settings

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

    # 2. Validate platform
    try:
        validate_platform()
    except PlatformNotSupportedError as e:
        logger.error(str(e))
        raise typer.Exit(code=1)

    # 3. Validate input
    try:
        validate_input(input_path)
    except typer.BadParameter as e:
        logger.error(str(e))
        raise typer.Exit(code=1)

    logger.info(f"Targeting playlist: {playlist or '[Auto-generated from input]'}")
    logger.info(f"Adding {tracks_per_artist} tracks per artist.")
    if dry_run:
        logger.info("Dry-run mode enabled. Apple Music modifications will be skipped.")

    # Ingestion stub
    logger.debug(f"Parsing input stream from: {input_path}")
    artists = ["Tool", "A Perfect Circle"]  # Dummy list for verification stub
    logger.info(f"Successfully extracted {len(artists)} artists.")

    # IPC Engine stub
    logger.debug("Connecting to JXA Apple Events Engine...")
    logger.info(f"Synchronizing playlist data for: {artists}")

    logger.info("Execution completed successfully.")


if __name__ == "__main__":
    app()
