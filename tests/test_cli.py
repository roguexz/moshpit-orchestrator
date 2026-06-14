import os
import pytest
from unittest import mock
from typer.testing import CliRunner

from moshpit.cli import app, validate_platform, validate_input
from moshpit.config import Settings
from moshpit.exceptions import PlatformNotSupportedError

runner = CliRunner()


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
        # Should not raise exception
        validate_platform()


def test_validate_platform_non_darwin():
    with mock.patch("sys.platform", "linux"):
        with pytest.raises(PlatformNotSupportedError):
            validate_platform()


def test_validate_input_url():
    # Should not raise exception
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


def test_cli_run_success(tmp_path):
    temp_file = tmp_path / "lineup.txt"
    temp_file.write_text("Tool")

    with mock.patch("sys.platform", "darwin"):
        result = runner.invoke(app, ["run", str(temp_file)])
        assert result.exit_code == 0
        assert "Initializing Moshpit Orchestrator" in result.output
        assert "Successfully extracted" in result.output


def test_cli_run_platform_failure():
    with mock.patch("sys.platform", "linux"):
        result = runner.invoke(app, ["run", "https://festival.com"])
        assert result.exit_code == 1


def test_cli_run_input_failure():
    with mock.patch("sys.platform", "darwin"):
        result = runner.invoke(app, ["run", "nonexistent.txt"])
        assert result.exit_code == 1


def test_cli_run_dry_run(tmp_path):
    temp_file = tmp_path / "lineup.txt"
    temp_file.write_text("Tool")

    with mock.patch("sys.platform", "darwin"):
        result = runner.invoke(app, ["run", str(temp_file), "--dry-run"])
        assert result.exit_code == 0
        assert "Dry-run mode enabled" in result.output
