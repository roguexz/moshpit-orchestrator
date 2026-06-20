import os
import pytest
from unittest import mock
from typer.testing import CliRunner

from moshpit.cli import app, validate_platform, validate_input
from moshpit.config import Settings
from moshpit.exceptions import PlatformNotSupportedError, MusicAppException
from moshpit.resolver import TrackSuggestion

runner = CliRunner()


def get_cli_default_tracks():
    import typer.main

    click_obj = typer.main.get_command(app)
    run_cmd = click_obj.get_command(None, "run")
    if run_cmd:
        for p in run_cmd.params:
            if p.name == "tracks_per_artist":
                return p.default
    return 3


@pytest.fixture(autouse=True)
def clean_env(tmp_path):
    # Force defaults to prevent host environment pollution
    db_file = tmp_path / "test_moshpit_cache.db"
    with mock.patch.dict(
        os.environ,
        {
            "MOSHPIT_LLM_BASE_URL": "http://localhost:11434",
            "MOSHPIT_LLM_MODEL": "llava",
            "MOSHPIT_LLM_TIMEOUT": "120.0",
            "MOSHPIT_OLLAMA_BASE_URL": "http://localhost:11434",
            "MOSHPIT_OLLAMA_MODEL": "llava",
            "MOSHPIT_OLLAMA_TIMEOUT": "120.0",
            "MOSHPIT_DEFAULT_TRACKS_PER_ARTIST": "3",
            "MOSHPIT_JXA_TIMEOUT": "30.0",
            "MOSHPIT_CACHE_DB_PATH": str(db_file),
        },
    ):
        yield


def test_settings_default():
    settings = Settings()
    assert settings.llm_base_url == "http://localhost:11434"
    assert settings.default_tracks_per_artist == 3


def test_settings_env_override():
    with mock.patch.dict(os.environ, {"MOSHPIT_LLM_BASE_URL": "http://ollama:11434"}):
        settings = Settings()
        assert settings.llm_base_url == "http://ollama:11434"


def test_settings_fallback_env_override():
    # Test that legacy OLLAMA env var overrides llm_base_url when llm env var is absent
    with mock.patch.dict(
        os.environ,
        {"MOSHPIT_OLLAMA_BASE_URL": "http://legacy-ollama:11434"},
        clear=True,
    ):
        settings = Settings()
        assert settings.llm_base_url == "http://legacy-ollama:11434"


@pytest.fixture
def mock_ipc_engine():
    # Mock both AppleMusicWebEngine and AppleMusicIPCEngine instantiation and methods
    with (
        mock.patch("moshpit.cli.AppleMusicWebEngine") as mock_web_class,
        mock.patch("moshpit.cli.AppleMusicIPCEngine") as mock_ipc_class,
    ):
        mock_instance = mock.Mock()
        mock_web_class.return_value = mock_instance
        mock_ipc_class.return_value = mock_instance
        # Default success response for resolved tracks
        mock_instance.append_resolved_tracks.return_value = {
            "status": "success",
            "added": 3,
            "skipped": 0,
            "failed": 0,
            "failed_tracks": [],
        }
        mock_instance.write_failure_manifest.return_value = "failure_manifest.json"
        yield mock_instance


@pytest.fixture
def mock_resolver():
    """Mock the TopTracksResolver to return deterministic results."""
    with mock.patch("moshpit.cli.TopTracksResolver") as mock_class:
        mock_instance = mock.Mock()
        mock_class.return_value = mock_instance
        # Default: return 3 tracks for any artist
        mock_instance.resolve.return_value = [
            TrackSuggestion(title="Song 1", artist="TestArtist", source="itunes_api"),
            TrackSuggestion(title="Song 2", artist="TestArtist", source="itunes_api"),
            TrackSuggestion(title="Song 3", artist="TestArtist", source="itunes_api"),
        ]
        yield mock_instance


def test_validate_platform_darwin():
    with mock.patch("sys.platform", "darwin"):
        validate_platform()


def test_validate_platform_non_darwin():
    with mock.patch("sys.platform", "linux"):
        with pytest.raises(PlatformNotSupportedError):
            validate_platform()


def test_validate_input_url():
    validate_input("https://festival.com/lineup")
    validate_input("http://festival.com/lineup")


def test_validate_input_file_exists(tmp_path):
    temp_file = tmp_path / "lineup.txt"
    temp_file.write_text("Tool\nSlipknot")
    validate_input(str(temp_file))


def test_validate_input_file_not_exists():
    import typer

    with pytest.raises(typer.BadParameter):
        validate_input("nonexistent_file.txt")


def test_cli_run_url_scraper(mock_ipc_engine, mock_resolver):
    with mock.patch("sys.platform", "darwin"):
        # Mock WebScraperIngester
        with mock.patch("moshpit.cli.WebScraperIngester") as mock_scraper_class:
            mock_scraper = mock_scraper_class.return_value
            mock_scraper.extract_artists.return_value = ["Tool", "Deftones"]

            with mock.patch("time.sleep", return_value=None):
                result = runner.invoke(
                    app, ["run", "https://aftershockfestival.com/lineup"]
                )
                assert result.exit_code == 0
                # Resolver should be called for each artist
                default_tracks = get_cli_default_tracks()
                assert mock_resolver.resolve.call_count == 2
                mock_resolver.resolve.assert_any_call("Tool", default_tracks)
                mock_resolver.resolve.assert_any_call("Deftones", default_tracks)
                # Engine should add resolved tracks for each artist
                assert mock_ipc_engine.append_resolved_tracks.call_count == 2


def test_cli_run_visual_ingester(mock_ipc_engine, mock_resolver, tmp_path):
    image_file = tmp_path / "flyer.jpg"
    image_file.write_bytes(b"image")

    with mock.patch("sys.platform", "darwin"):
        # Mock VisualIngester
        with mock.patch("moshpit.cli.VisualIngester") as mock_visual_class:
            mock_visual = mock_visual_class.return_value
            mock_visual.extract_artists.return_value = ["Tool"]

            with mock.patch("time.sleep", return_value=None):
                result = runner.invoke(app, ["run", str(image_file)])
                assert result.exit_code == 0
                default_tracks = get_cli_default_tracks()
                mock_resolver.resolve.assert_called_with("Tool", default_tracks)
                mock_ipc_engine.append_resolved_tracks.assert_called_once()


def test_cli_run_text_file(mock_ipc_engine, mock_resolver, tmp_path):
    text_file = tmp_path / "artists.txt"
    text_file.write_text(
        "Tool - Live\n$UICIDEBOY$\n\n  \n"
    )  # empty line and billing fluff

    with mock.patch("sys.platform", "darwin"):
        with mock.patch("time.sleep", return_value=None):
            result = runner.invoke(app, ["run", str(text_file)])
            assert result.exit_code == 0
            default_tracks = get_cli_default_tracks()
            mock_resolver.resolve.assert_any_call("Tool", default_tracks)
            mock_resolver.resolve.assert_any_call("Suicideboys", default_tracks)


def test_cli_run_dry_run(mock_ipc_engine, mock_resolver):
    # dry run should resolve tracks but not touch IPC engine
    with mock.patch("sys.platform", "darwin"):
        with mock.patch("moshpit.cli.WebScraperIngester") as mock_scraper_class:
            mock_scraper = mock_scraper_class.return_value
            mock_scraper.extract_artists.return_value = ["Tool"]

            result = runner.invoke(app, ["run", "https://rock.com", "--dry-run"])
            assert result.exit_code == 0
            assert "Dry-run mode enabled" in result.output
            # Resolver should be called for dry-run
            mock_resolver.resolve.assert_called_once()
            # IPC engine should NOT be initialized in dry-run
            mock_ipc_engine.append_resolved_tracks.assert_not_called()


def test_cli_run_ingestion_failure():
    with mock.patch("sys.platform", "darwin"):
        with mock.patch(
            "moshpit.cli.WebScraperIngester", side_effect=Exception("Scraper Down")
        ):
            result = runner.invoke(app, ["run", "https://rock.com"])
            assert result.exit_code == 1


def test_cli_run_no_artists_extracted():
    with mock.patch("sys.platform", "darwin"):
        with mock.patch("moshpit.cli.WebScraperIngester") as mock_scraper_class:
            mock_scraper = mock_scraper_class.return_value
            mock_scraper.extract_artists.return_value = []

            result = runner.invoke(app, ["run", "https://rock.com"])
            assert result.exit_code == 1


def test_cli_run_connection_failure(mock_resolver):
    # AppleMusicWebEngine instantiation throws error
    with mock.patch("sys.platform", "darwin"):
        with mock.patch("moshpit.cli.WebScraperIngester") as mock_scraper_class:
            mock_scraper = mock_scraper_class.return_value
            mock_scraper.extract_artists.return_value = ["Tool"]
            with mock.patch(
                "moshpit.cli.AppleMusicWebEngine",
                side_effect=MusicAppException("Cannot connect"),
            ):
                result = runner.invoke(app, ["run", "https://rock.com"])
                assert result.exit_code == 1


def test_cli_run_sync_with_errors(mock_ipc_engine, mock_resolver, tmp_path):
    # Test tracking failed matches and manifest writing
    text_file = tmp_path / "list.txt"
    text_file.write_text("Tool\nGlitchArtist\nErrorArtist")

    # Mock resolver: return tracks for all artists
    mock_resolver.resolve.return_value = [
        TrackSuggestion(title="Song1", artist="TestArtist", source="itunes_api"),
    ]

    # Mock engine responses: success, all-failed, exception
    mock_ipc_engine.append_resolved_tracks.side_effect = [
        {
            "status": "success",
            "added": 1,
            "skipped": 0,
            "failed": 0,
            "failed_tracks": [],
        },
        {
            "status": "not_found",
            "added": 0,
            "skipped": 0,
            "failed": 1,
            "failed_tracks": [
                {"title": "Song1", "artist": "GlitchArtist", "reason": "No matches"}
            ],
        },
        Exception("JXA syntax error"),
    ]

    with mock.patch("sys.platform", "darwin"):
        # Mock time.sleep to run quickly
        with mock.patch("time.sleep", return_value=None):
            result = runner.invoke(app, ["run", str(text_file)])
            assert result.exit_code == 0

            # Verify write_failure_manifest was called
            mock_ipc_engine.write_failure_manifest.assert_called_once()
            called_args = mock_ipc_engine.write_failure_manifest.call_args[0]
            unresolved = called_args[0]
            assert len(unresolved) == 2
            assert unresolved[0]["artist"] == "GlitchArtist"
            assert unresolved[0]["reason"] == "CATALOG_MISS"
            assert unresolved[1]["artist"] == "ErrorArtist"
            assert unresolved[1]["reason"] == "SYSTEM_ERROR"


def test_cli_run_sync_unexpected_exception(mock_ipc_engine, mock_resolver, tmp_path):
    # Test tracking unexpected runtime exceptions inside loop
    text_file = tmp_path / "list.txt"
    text_file.write_text("Tool")

    mock_ipc_engine.append_resolved_tracks.side_effect = Exception(
        "Unexpected OS Error"
    )

    with mock.patch("sys.platform", "darwin"):
        with mock.patch("time.sleep", return_value=None):
            result = runner.invoke(app, ["run", str(text_file)])
            assert result.exit_code == 0
            mock_ipc_engine.write_failure_manifest.assert_called_once()


def test_cli_run_platform_failure():
    with mock.patch(
        "moshpit.cli.validate_platform",
        side_effect=PlatformNotSupportedError("OS check fails"),
    ):
        result = runner.invoke(app, ["run", "https://rock.com"])
        assert result.exit_code == 1


def test_cli_run_input_failure():
    import typer

    with mock.patch("moshpit.cli.validate_platform", return_value=None):
        with mock.patch(
            "moshpit.cli.validate_input",
            side_effect=typer.BadParameter("Input path does not exist"),
        ):
            result = runner.invoke(app, ["run", "nonexistent.txt"])
            assert result.exit_code == 1


def test_cli_run_sync_with_manifest_write_failure(
    mock_ipc_engine, mock_resolver, tmp_path
):
    text_file = tmp_path / "list.txt"
    text_file.write_text("Tool\nFailedArtist")

    # Mock resolver returns different results per artist
    mock_resolver.resolve.return_value = [
        TrackSuggestion(title="Song1", artist="TestArtist", source="itunes_api"),
    ]

    mock_ipc_engine.append_resolved_tracks.side_effect = [
        {
            "status": "success",
            "added": 1,
            "skipped": 0,
            "failed": 0,
            "failed_tracks": [],
        },
        {
            "status": "not_found",
            "added": 0,
            "skipped": 0,
            "failed": 1,
            "failed_tracks": [
                {"title": "Song1", "artist": "FailedArtist", "reason": "No matches"}
            ],
        },
    ]
    mock_ipc_engine.write_failure_manifest.return_value = (
        ""  # simulate manifest write failure
    )

    with mock.patch("sys.platform", "darwin"):
        with mock.patch("time.sleep", return_value=None):
            result = runner.invoke(app, ["run", str(text_file)])
            assert result.exit_code == 0
            mock_ipc_engine.write_failure_manifest.assert_called_once()


def test_cli_run_sync_remove_previous_manifest(
    mock_ipc_engine, mock_resolver, tmp_path
):
    # Setup test file
    text_file = tmp_path / "list.txt"
    text_file.write_text("Tool")

    # Create dummy manifest file in current directory to verify removal
    with open("failure_manifest.json", "w") as f:
        f.write("{}")
    assert os.path.exists("failure_manifest.json")

    try:
        with mock.patch("sys.platform", "darwin"):
            with mock.patch("time.sleep", return_value=None):
                result = runner.invoke(app, ["run", str(text_file)])
                assert result.exit_code == 0
                # Success should result in manifest removal
                assert not os.path.exists("failure_manifest.json")
    finally:
        # Cleanup in case test fails
        if os.path.exists("failure_manifest.json"):
            os.remove("failure_manifest.json")


def test_cli_run_print_artists(tmp_path):
    text_file = tmp_path / "list.txt"
    text_file.write_text("Tool\nDeftones\n")

    # Bypasses validate_platform so we can mock a non-darwin platform (e.g. linux)
    with mock.patch("sys.platform", "linux"):
        result = runner.invoke(app, ["run", str(text_file), "--print-artists"])
        assert result.exit_code == 0
        assert "Tool\nDeftones\n" in result.output


def test_cli_run_duplicate_artists_deduplicated(tmp_path):
    text_file = tmp_path / "list.txt"
    text_file.write_text("Tool\nDeftones\nTool\n")

    with mock.patch("sys.platform", "linux"):
        result = runner.invoke(app, ["run", str(text_file), "--print-artists"])
        assert result.exit_code == 0
        # Should print each unique artist exactly once at the end of output
        assert result.output.endswith("Tool\nDeftones\n")


def test_cli_run_resolver_returns_empty(mock_ipc_engine, mock_resolver, tmp_path):
    """When the resolver returns no tracks for an artist, it should be logged as unresolved."""
    text_file = tmp_path / "list.txt"
    text_file.write_text("Unknown Artist")

    mock_resolver.resolve.return_value = []

    with mock.patch("sys.platform", "darwin"):
        with mock.patch("time.sleep", return_value=None):
            result = runner.invoke(app, ["run", str(text_file)])
            assert result.exit_code == 0
            # IPC engine's append_resolved_tracks should NOT be called
            mock_ipc_engine.append_resolved_tracks.assert_not_called()
            # But failure manifest should be written
            mock_ipc_engine.write_failure_manifest.assert_called_once()


def test_cli_run_idempotent_skip(mock_ipc_engine, mock_resolver, tmp_path):
    """When all tracks are already in the playlist (skipped), no errors should be logged."""
    text_file = tmp_path / "list.txt"
    text_file.write_text("Tool")

    mock_ipc_engine.append_resolved_tracks.return_value = {
        "status": "success",
        "added": 0,
        "skipped": 3,
        "failed": 0,
        "failed_tracks": [],
    }

    with mock.patch("sys.platform", "darwin"):
        with mock.patch("time.sleep", return_value=None):
            result = runner.invoke(app, ["run", str(text_file)])
            assert result.exit_code == 0
            # No failure manifest should be written for idempotent runs
            mock_ipc_engine.write_failure_manifest.assert_not_called()


def test_cli_analyze_summary(mock_ipc_engine):
    mock_ipc_engine.get_playlist_tracks.return_value = [
        {
            "id": 1001,
            "databaseID": 1,
            "name": "Time",
            "artist": "Pink Floyd",
            "album": "Dark Side",
        },
        {
            "id": 1002,
            "databaseID": 2,
            "name": "Time (Live)",
            "artist": "Pink Floyd",
            "album": "Live",
        },
        {
            "id": 1003,
            "databaseID": 3,
            "name": "Sober",
            "artist": "Tool",
            "album": "Undertow",
        },
    ]
    with mock.patch("sys.platform", "darwin"):
        result = runner.invoke(app, ["analyze", "-p", "Test Playlist"])
        assert result.exit_code == 0
        assert "Playlist 'Test Playlist' analysis summary:" in result.stdout
        assert "Total tracks: 3" in result.stdout
        assert "Unique artists: 2" in result.stdout
        assert "Duplicate track versions found: 1" in result.stdout


def test_cli_analyze_list_artists(mock_ipc_engine):
    mock_ipc_engine.get_playlist_tracks.return_value = [
        {
            "id": 1001,
            "databaseID": 1,
            "name": "Time",
            "artist": "Pink Floyd",
            "album": "Dark Side",
        },
        {
            "id": 1002,
            "databaseID": 2,
            "name": "Sober",
            "artist": "Tool",
            "album": "Undertow",
        },
    ]
    with mock.patch("sys.platform", "darwin"):
        result = runner.invoke(
            app, ["analyze", "-p", "Test Playlist", "--list-artists"]
        )
        assert result.exit_code == 0
        assert "Unique artists in playlist 'Test Playlist':" in result.stdout
        assert "Pink Floyd" in result.stdout
        assert "Tool" in result.stdout


def test_cli_analyze_list_duplicates(mock_ipc_engine):
    mock_ipc_engine.get_playlist_tracks.return_value = [
        {
            "id": 1001,
            "databaseID": 1,
            "name": "Comfortably Numb",
            "artist": "Pink Floyd",
            "album": "Wall",
        },
        {
            "id": 1002,
            "databaseID": 2,
            "name": "Comfortably Numb (Live)",
            "artist": "Pink Floyd",
            "album": "Live",
        },
    ]
    with mock.patch("sys.platform", "darwin"):
        result = runner.invoke(
            app, ["analyze", "-p", "Test Playlist", "--list-duplicates"]
        )
        assert result.exit_code == 0
        assert (
            "Duplicate/alternative track versions found in playlist 'Test Playlist':"
            in result.stdout
        )
        assert "Comfortably Numb (Live)" in result.stdout


def test_cli_prune_duplicates(mock_ipc_engine):
    mock_ipc_engine.get_playlist_tracks.return_value = [
        {
            "id": 1001,
            "databaseID": 1,
            "name": "Comfortably Numb",
            "artist": "Pink Floyd",
            "album": "Wall",
        },
        {
            "id": 1002,
            "databaseID": 2,
            "name": "Comfortably Numb (Live)",
            "artist": "Pink Floyd",
            "album": "Live",
        },
    ]
    mock_ipc_engine.delete_tracks_by_id.return_value = 1
    with mock.patch("sys.platform", "darwin"):
        result = runner.invoke(app, ["prune", "-p", "Test Playlist"])
        assert result.exit_code == 0
        assert "Successfully removed 1 tracks" in result.stdout
        mock_ipc_engine.delete_tracks_by_id.assert_called_once_with([1002])


def test_cli_prune_artist(mock_ipc_engine):
    mock_ipc_engine.get_playlist_tracks.return_value = [
        {
            "id": 1001,
            "databaseID": 1,
            "name": "Time",
            "artist": "Pink Floyd",
            "album": "Dark Side",
        },
        {
            "id": 1002,
            "databaseID": 2,
            "name": "Sober",
            "artist": "Tool",
            "album": "Undertow",
        },
    ]
    mock_ipc_engine.delete_tracks_by_id.return_value = 1
    with mock.patch("sys.platform", "darwin"):
        result = runner.invoke(
            app, ["prune", "-p", "Test Playlist", "--artist", "Pink Floyd"]
        )
        assert result.exit_code == 0
        assert "Successfully removed 1 tracks" in result.stdout
        mock_ipc_engine.delete_tracks_by_id.assert_called_once_with([1001])
