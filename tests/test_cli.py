import os
import pytest
from unittest import mock
from typer.testing import CliRunner

from moshpit.cli import app, validate_platform, validate_input
from moshpit.config import Settings
from moshpit.exceptions import PlatformNotSupportedError, MusicAppException

runner = CliRunner()


@pytest.fixture
def mock_ipc_engine():
    # Mock AppleMusicIPCEngine instantiation and methods
    with mock.patch("moshpit.cli.AppleMusicIPCEngine") as mock_class:
        mock_instance = mock.Mock()
        mock_class.return_value = mock_instance
        # Default success response for tracks
        mock_instance.append_top_tracks.return_value = {"status": "success", "count": 3}
        mock_instance.write_failure_manifest.return_value = "failure_manifest.json"
        yield mock_instance


def test_settings_default():
    settings = Settings()
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.default_tracks_per_artist == 3


def test_settings_env_override():
    with mock.patch.dict(
        os.environ, {"MOSHPIT_OLLAMA_BASE_URL": "http://ollama:11434"}
    ):
        settings = Settings()
        assert settings.ollama_base_url == "http://ollama:11434"


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


def test_cli_run_url_scraper(mock_ipc_engine):
    with mock.patch("sys.platform", "darwin"):
        # Mock WebScraperIngester
        with mock.patch("moshpit.cli.WebScraperIngester") as mock_scraper_class:
            mock_scraper = mock_scraper_class.return_value
            mock_scraper.extract_artists.return_value = ["Tool", "Deftones"]

            result = runner.invoke(
                app, ["run", "https://aftershockfestival.com/lineup"]
            )
            assert result.exit_code == 0
            # Check generated playlist name: Domain capitalized
            mock_ipc_engine.append_top_tracks.assert_any_call("Tool", 3)
            mock_ipc_engine.append_top_tracks.assert_any_call("Deftones", 3)


def test_cli_run_visual_ingester(mock_ipc_engine, tmp_path):
    image_file = tmp_path / "flyer.jpg"
    image_file.write_bytes(b"image")

    with mock.patch("sys.platform", "darwin"):
        # Mock VisualIngester
        with mock.patch("moshpit.cli.VisualIngester") as mock_visual_class:
            mock_visual = mock_visual_class.return_value
            mock_visual.extract_artists.return_value = ["Tool"]

            result = runner.invoke(app, ["run", str(image_file)])
            assert result.exit_code == 0
            mock_ipc_engine.append_top_tracks.assert_called_with("Tool", 3)


def test_cli_run_text_file(mock_ipc_engine, tmp_path):
    text_file = tmp_path / "artists.txt"
    text_file.write_text(
        "Tool - Live\n$UICIDEBOY$\n\n  \n"
    )  # empty line and billing fluff

    with mock.patch("sys.platform", "darwin"):
        result = runner.invoke(app, ["run", str(text_file)])
        assert result.exit_code == 0
        mock_ipc_engine.append_top_tracks.assert_any_call("Tool", 3)
        mock_ipc_engine.append_top_tracks.assert_any_call("Suicideboys", 3)


def test_cli_run_dry_run(mock_ipc_engine):
    # dry run should bypass ipc operations and not fail
    with mock.patch("sys.platform", "darwin"):
        with mock.patch("moshpit.cli.WebScraperIngester") as mock_scraper_class:
            mock_scraper = mock_scraper_class.return_value
            mock_scraper.extract_artists.return_value = ["Tool"]

            result = runner.invoke(app, ["run", "https://rock.com", "--dry-run"])
            assert result.exit_code == 0
            assert "Dry-run mode enabled" in result.output


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


def test_cli_run_connection_failure():
    # AppleMusicIPCEngine instantiation throws error
    with mock.patch("sys.platform", "darwin"):
        with mock.patch("moshpit.cli.WebScraperIngester") as mock_scraper_class:
            mock_scraper = mock_scraper_class.return_value
            mock_scraper.extract_artists.return_value = ["Tool"]
            with mock.patch(
                "moshpit.cli.AppleMusicIPCEngine",
                side_effect=MusicAppException("Cannot connect"),
            ):
                result = runner.invoke(app, ["run", "https://rock.com"])
                assert result.exit_code == 1


def test_cli_run_sync_with_errors(mock_ipc_engine, tmp_path):
    # Test tracking failed matches and manifest writing
    text_file = tmp_path / "list.txt"
    text_file.write_text("Tool\nGlitchArtist\nErrorArtist")

    # Mock tracks results: success, not_found, error
    mock_ipc_engine.append_top_tracks.side_effect = [
        {"status": "success", "count": 2},
        {"status": "not_found", "message": "No matches"},
        {"status": "error", "message": "JXA syntax error"},
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
            assert unresolved[0]["reason"] == "NOT_FOUND"
            assert unresolved[1]["artist"] == "ErrorArtist"
            assert unresolved[1]["reason"] == "IPC_EXCEPTION"


def test_cli_run_sync_unexpected_exception(mock_ipc_engine, tmp_path):
    # Test tracking unexpected runtime exceptions inside loop
    text_file = tmp_path / "list.txt"
    text_file.write_text("Tool")

    mock_ipc_engine.append_top_tracks.side_effect = Exception("Unexpected OS Error")

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


def test_cli_run_sync_with_manifest_write_failure(mock_ipc_engine, tmp_path):
    text_file = tmp_path / "list.txt"
    text_file.write_text("Tool\nFailedArtist")

    mock_ipc_engine.append_top_tracks.side_effect = [
        {"status": "success", "count": 2},
        {"status": "not_found", "message": "No matches"},
    ]
    mock_ipc_engine.write_failure_manifest.return_value = (
        ""  # simulate manifest write failure
    )

    with mock.patch("sys.platform", "darwin"):
        with mock.patch("time.sleep", return_value=None):
            result = runner.invoke(app, ["run", str(text_file)])
            assert result.exit_code == 0
            mock_ipc_engine.write_failure_manifest.assert_called_once()


def test_cli_run_sync_remove_previous_manifest(mock_ipc_engine, tmp_path):
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
