import pytest
from unittest import mock
import subprocess

from moshpit.exceptions import JXAError, MusicAppException, PlatformNotSupportedError
from moshpit.ipc import AppleMusicIPCEngine


@pytest.fixture
def mock_engine_init():
    # Helper to mock verify_environment and _initialize_playlist during __init__
    with mock.patch.object(
        AppleMusicIPCEngine, "verify_environment", return_value=None
    ):
        with mock.patch.object(
            AppleMusicIPCEngine, "_initialize_playlist", return_value=None
        ):
            yield


def test_jxa_runner_timeout(mock_engine_init):
    engine = AppleMusicIPCEngine("Test Playlist")

    # Mock subprocess.run to raise TimeoutExpired
    with mock.patch(
        "subprocess.run", side_effect=subprocess.TimeoutExpired(["osascript"], 30.0)
    ):
        with pytest.raises(JXAError, match="timed out"):
            engine._run_jxa("var a = 1;")


def test_jxa_runner_process_error(mock_engine_init):
    engine = AppleMusicIPCEngine("Test Playlist")

    # Mock subprocess.run to raise CalledProcessError
    with mock.patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(
            1, ["osascript"], stderr="Syntax Error"
        ),
    ):
        with pytest.raises(JXAError, match="process failed"):
            engine._run_jxa("var a = 1;")


def test_escape_quote(mock_engine_init):
    engine = AppleMusicIPCEngine("Test Playlist")
    assert engine._escape_quote("Guns N' Roses") == "Guns N' Roses"
    assert engine._escape_quote('The "Classic" Trio') == 'The \\"Classic\\" Trio'
    assert engine._escape_quote("Slash\\Axl") == "Slash\\\\Axl"


def test_verify_environment_platform_error():
    # Platform check fails on __init__
    with mock.patch("sys.platform", "linux"):
        with pytest.raises(PlatformNotSupportedError):
            AppleMusicIPCEngine("Test Playlist")


def test_verify_environment_success():
    # Full verification flow success on __init__
    with mock.patch("sys.platform", "darwin"):
        with mock.patch("subprocess.run") as mock_sub:
            mock_sub.return_value.returncode = 0  # pgrep matches
            with mock.patch.object(
                AppleMusicIPCEngine, "_run_jxa", return_value='{"status": "success"}'
            ):
                with mock.patch.object(
                    AppleMusicIPCEngine, "_initialize_playlist", return_value=None
                ):
                    engine = AppleMusicIPCEngine("Test Playlist")
                    assert engine.playlist_name == "Test Playlist"


def test_verify_environment_launch_music():
    # Music app pgrep fails, launch is called
    with mock.patch("sys.platform", "darwin"):
        pgrep_fail = subprocess.CalledProcessError(1, ["pgrep"])
        with mock.patch("subprocess.run", side_effect=[pgrep_fail]):
            with mock.patch.object(
                AppleMusicIPCEngine, "_run_jxa", return_value='{"status": "success"}'
            ) as mock_jxa:
                with mock.patch("time.sleep", return_value=None):  # skip sleep
                    with mock.patch.object(
                        AppleMusicIPCEngine, "_initialize_playlist", return_value=None
                    ):
                        _ = AppleMusicIPCEngine("Test Playlist")
                        # 2 JXA calls: 1 to launch Music.app, 1 to verify catalog search
                        assert mock_jxa.call_count == 2


def test_verify_environment_catalog_error():
    # Catalog search returns failed status
    with mock.patch("sys.platform", "darwin"):
        with mock.patch("subprocess.run") as mock_sub:
            mock_sub.return_value.returncode = 0
            with mock.patch.object(
                AppleMusicIPCEngine, "_run_jxa", return_value='{"status": "failed"}'
            ):
                with mock.patch.object(
                    AppleMusicIPCEngine, "_initialize_playlist", return_value=None
                ):
                    with pytest.raises(
                        MusicAppException, match="catalog is not accessible"
                    ):
                        AppleMusicIPCEngine("Test Playlist")


def test_verify_environment_catalog_invalid_json():
    # Catalog search returns invalid JSON
    with mock.patch("sys.platform", "darwin"):
        with mock.patch("subprocess.run") as mock_sub:
            mock_sub.return_value.returncode = 0
            with mock.patch.object(
                AppleMusicIPCEngine, "_run_jxa", return_value="not-json"
            ):
                with mock.patch.object(
                    AppleMusicIPCEngine, "_initialize_playlist", return_value=None
                ):
                    with pytest.raises(
                        MusicAppException, match="Invalid JSON response"
                    ):
                        AppleMusicIPCEngine("Test Playlist")


def test_initialize_playlist_error():
    # verify_environment passes, but _initialize_playlist fails during init
    with mock.patch.object(
        AppleMusicIPCEngine, "verify_environment", return_value=None
    ):
        with mock.patch.object(
            AppleMusicIPCEngine,
            "_run_jxa",
            return_value='{"status": "error", "message": "Failed to create"}',
        ):
            with pytest.raises(
                MusicAppException, match="Failed to initialize playlist"
            ):
                AppleMusicIPCEngine("Test Playlist")


def test_append_top_tracks_success(mock_engine_init):
    engine = AppleMusicIPCEngine("Test Playlist")

    mock_jxa_response = '{"status": "success", "count": 3, "ids": [123, 456, 789]}'
    with mock.patch.object(
        AppleMusicIPCEngine, "_run_jxa", return_value=mock_jxa_response
    ) as mock_jxa:
        res = engine.append_top_tracks("Tool")
        assert res["status"] == "success"
        assert res["count"] == 3
        assert res["ids"] == [123, 456, 789]
        mock_jxa.assert_called_once()


def test_append_top_tracks_not_found(mock_engine_init):
    engine = AppleMusicIPCEngine("Test Playlist")

    mock_jxa_response = '{"status": "not_found", "message": "No search results"}'
    with mock.patch.object(
        AppleMusicIPCEngine, "_run_jxa", return_value=mock_jxa_response
    ):
        res = engine.append_top_tracks("Unknown Artist")
        assert res["status"] == "not_found"


def test_append_top_tracks_empty_response(mock_engine_init):
    engine = AppleMusicIPCEngine("Test Playlist")
    with mock.patch.object(AppleMusicIPCEngine, "_run_jxa", return_value=""):
        res = engine.append_top_tracks("Tool")
        assert res["status"] == "error"


def test_write_failure_manifest(mock_engine_init, tmp_path):
    engine = AppleMusicIPCEngine("Test Playlist")

    unresolved = [
        {
            "artist": "Unknown Artist",
            "reason": "NOT_FOUND",
            "details": "No songs matched",
        }
    ]

    with mock.patch("builtins.open", mock.mock_open()) as mock_file:
        filepath = engine.write_failure_manifest(unresolved, total_submitted=5)
        assert filepath == "failure_manifest.json"

        # Verify JSON dumping was called
        mock_file.assert_called_once_with("failure_manifest.json", "w")


def test_jxa_runner_success(mock_engine_init):
    engine = AppleMusicIPCEngine("Test Playlist")
    mock_proc = mock.Mock()
    mock_proc.stdout = "Successful JXA stdout"
    with mock.patch("subprocess.run", return_value=mock_proc):
        assert engine._run_jxa("return 'success';") == "Successful JXA stdout"


def test_verify_environment_catalog_empty_response():
    with mock.patch("sys.platform", "darwin"):
        with mock.patch("subprocess.run") as mock_sub:
            mock_sub.return_value.returncode = 0
            with mock.patch.object(AppleMusicIPCEngine, "_run_jxa", return_value=""):
                with mock.patch.object(
                    AppleMusicIPCEngine, "_initialize_playlist", return_value=None
                ):
                    with pytest.raises(
                        MusicAppException, match="Failed to verify catalog search"
                    ):
                        AppleMusicIPCEngine("Test Playlist")


def test_verify_environment_catalog_jxa_error():
    with mock.patch("sys.platform", "darwin"):
        with mock.patch("subprocess.run") as mock_sub:
            mock_sub.return_value.returncode = 0
            with mock.patch.object(
                AppleMusicIPCEngine,
                "_run_jxa",
                return_value='{"status": "error", "message": "Internal compilation error"}',
            ):
                with mock.patch.object(
                    AppleMusicIPCEngine, "_initialize_playlist", return_value=None
                ):
                    with pytest.raises(
                        MusicAppException,
                        match="Catalog check error: Internal compilation error",
                    ):
                        AppleMusicIPCEngine("Test Playlist")


def test_initialize_playlist_empty_response():
    with mock.patch.object(
        AppleMusicIPCEngine, "verify_environment", return_value=None
    ):
        with mock.patch.object(AppleMusicIPCEngine, "_run_jxa", return_value=""):
            with pytest.raises(
                MusicAppException, match="Empty response while initializing"
            ):
                AppleMusicIPCEngine("Test Playlist")


def test_initialize_playlist_invalid_json():
    with mock.patch.object(
        AppleMusicIPCEngine, "verify_environment", return_value=None
    ):
        with mock.patch.object(
            AppleMusicIPCEngine, "_run_jxa", return_value="not-json"
        ):
            with pytest.raises(
                MusicAppException, match="Invalid JSON returned from JXA initialization"
            ):
                AppleMusicIPCEngine("Test Playlist")


def test_append_top_tracks_invalid_json(mock_engine_init):
    engine = AppleMusicIPCEngine("Test Playlist")
    with mock.patch.object(AppleMusicIPCEngine, "_run_jxa", return_value="not-json"):
        res = engine.append_top_tracks("Tool")
        assert res["status"] == "error"
        assert "Invalid JSON returned" in res["message"]


def test_write_failure_manifest_io_error(mock_engine_init):
    engine = AppleMusicIPCEngine("Test Playlist")
    with mock.patch("builtins.open", side_effect=IOError("Write permission denied")):
        filepath = engine.write_failure_manifest([], total_submitted=0)
        assert filepath == ""


def test_get_playlist_tracks_success(mock_engine_init):
    engine = AppleMusicIPCEngine("Test Playlist")
    mock_jxa_response = '{"status": "success", "tracks": [{"id": 1001, "databaseID": 123, "name": "Time", "artist": "Pink Floyd", "album": "Dark Side"}]}'
    with mock.patch.object(
        AppleMusicIPCEngine, "_run_jxa", return_value=mock_jxa_response
    ) as mock_jxa:
        tracks = engine.get_playlist_tracks()
        assert len(tracks) == 1
        assert tracks[0]["id"] == 1001
        assert tracks[0]["databaseID"] == 123
        assert tracks[0]["name"] == "Time"
        mock_jxa.assert_called_once()


def test_delete_tracks_by_id_success(mock_engine_init):
    engine = AppleMusicIPCEngine("Test Playlist")
    mock_jxa_response = '{"status": "success", "count": 2}'
    with mock.patch.object(
        AppleMusicIPCEngine, "_run_jxa", return_value=mock_jxa_response
    ) as mock_jxa:
        count = engine.delete_tracks_by_id([1001, 1002])
        assert count == 2
        mock_jxa.assert_called_once()


def test_sync_from_playlist_success(mock_engine_init):
    engine = AppleMusicIPCEngine("Destination Playlist")
    mock_jxa_response = '{"status": "success", "dry_run": false, "before_count": 2, "after_count": 3, "before_tracks": [], "after_tracks": []}'
    with mock.patch.object(
        AppleMusicIPCEngine, "_run_jxa", return_value=mock_jxa_response
    ) as mock_jxa:
        res = engine.sync_from_playlist("Source Playlist", dry_run=False)
        assert res["status"] == "success"
        assert res["dry_run"] is False
        assert res["before_count"] == 2
        assert res["after_count"] == 3
        mock_jxa.assert_called_once()


def test_sync_from_playlist_dry_run(mock_engine_init):
    engine = AppleMusicIPCEngine("Destination Playlist")
    mock_jxa_response = '{"status": "success", "dry_run": true, "before_count": 2, "after_count": 3, "before_tracks": [], "after_tracks": []}'
    with mock.patch.object(
        AppleMusicIPCEngine, "_run_jxa", return_value=mock_jxa_response
    ) as mock_jxa:
        res = engine.sync_from_playlist("Source Playlist", dry_run=True)
        assert res["status"] == "success"
        assert res["dry_run"] is True
        assert res["before_count"] == 2
        assert res["after_count"] == 3
        mock_jxa.assert_called_once()


def test_sync_from_playlist_error(mock_engine_init):
    engine = AppleMusicIPCEngine("Destination Playlist")
    mock_jxa_response = '{"status": "error", "message": "Source playlist not found."}'
    with mock.patch.object(
        AppleMusicIPCEngine, "_run_jxa", return_value=mock_jxa_response
    ):
        with pytest.raises(MusicAppException, match="Failed to synchronize playlists"):
            engine.sync_from_playlist("Source Playlist")


def test_sync_from_playlist_invalid_json(mock_engine_init):
    engine = AppleMusicIPCEngine("Destination Playlist")
    with mock.patch.object(
        AppleMusicIPCEngine, "_run_jxa", return_value="invalid-json"
    ):
        with pytest.raises(
            MusicAppException, match="Invalid JSON returned from JXA sync"
        ):
            engine.sync_from_playlist("Source Playlist")
