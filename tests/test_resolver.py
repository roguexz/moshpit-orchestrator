import pytest
from unittest import mock

from moshpit.resolver import TopTracksResolver, TrackSuggestion


@pytest.fixture
def resolver():
    """Create a TopTracksResolver with a short delay for testing."""
    mock_config = mock.Mock()
    mock_config.resolver_delay = 0.0
    mock_config.storefront = "us"
    mock_config.llm_base_url = "http://localhost:11434"
    mock_config.llm_model = "llava"
    mock_config.llm_timeout = 5.0
    return TopTracksResolver(config=mock_config)


class TestTrackSuggestion:
    def test_to_dict(self):
        track = TrackSuggestion(title="Enter Sandman", artist="Metallica", source="itunes_api")
        d = track.to_dict()
        assert d == {"title": "Enter Sandman", "artist": "Metallica", "source": "itunes_api"}

    def test_dataclass_fields(self):
        track = TrackSuggestion(title="One", artist="Metallica", source="llm")
        assert track.title == "One"
        assert track.artist == "Metallica"
        assert track.source == "llm"


class TestiTunesSearchAPI:
    def test_successful_resolution(self, resolver):
        mock_response = {
            "resultCount": 3,
            "results": [
                {"trackName": "Enter Sandman", "artistName": "Metallica", "wrapperType": "track"},
                {"trackName": "Master of Puppets", "artistName": "Metallica", "wrapperType": "track"},
                {"trackName": "Nothing Else Matters", "artistName": "Metallica", "wrapperType": "track"},
            ],
        }
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = mock_response

            tracks = resolver._resolve_itunes_search("Metallica", 3)
            assert len(tracks) == 3
            assert tracks[0].title == "Enter Sandman"
            assert tracks[0].source == "itunes_api"
            assert tracks[0].artist == "Metallica"

    def test_filters_non_matching_artists(self, resolver):
        mock_response = {
            "resultCount": 3,
            "results": [
                {"trackName": "Enter Sandman", "artistName": "Metallica", "wrapperType": "track"},
                {"trackName": "Random Song", "artistName": "Some Other Band", "wrapperType": "track"},
                {"trackName": "Nothing Else Matters", "artistName": "Metallica", "wrapperType": "track"},
            ],
        }
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = mock_response

            tracks = resolver._resolve_itunes_search("Metallica", 10)
            assert len(tracks) == 2
            assert all(t.artist == "Metallica" for t in tracks)

    def test_deduplicates_titles(self, resolver):
        mock_response = {
            "resultCount": 3,
            "results": [
                {"trackName": "Enter Sandman", "artistName": "Metallica", "wrapperType": "track"},
                {"trackName": "enter sandman", "artistName": "Metallica", "wrapperType": "track"},
                {"trackName": "Enter Sandman", "artistName": "Metallica", "wrapperType": "track"},
            ],
        }
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = mock_response

            tracks = resolver._resolve_itunes_search("Metallica", 10)
            assert len(tracks) == 1

    def test_handles_api_error(self, resolver):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 500

            tracks = resolver._resolve_itunes_search("Metallica", 3)
            assert tracks == []

    def test_handles_network_exception(self, resolver):
        with mock.patch("requests.get", side_effect=Exception("Network error")):
            tracks = resolver._resolve_itunes_search("Metallica", 3)
            assert tracks == []

    def test_skips_empty_track_names(self, resolver):
        mock_response = {
            "resultCount": 2,
            "results": [
                {"trackName": "", "artistName": "Metallica", "wrapperType": "track"},
                {"trackName": "Enter Sandman", "artistName": "Metallica", "wrapperType": "track"},
            ],
        }
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = mock_response

            tracks = resolver._resolve_itunes_search("Metallica", 10)
            assert len(tracks) == 1
            assert tracks[0].title == "Enter Sandman"


class TestiTunesLookupAPI:
    def test_successful_lookup(self, resolver):
        # Mock _find_artist_id
        with mock.patch.object(resolver, "_find_artist_id", return_value=3996865):
            mock_response = {
                "resultCount": 3,
                "results": [
                    {"wrapperType": "artist", "artistName": "Metallica"},
                    {"wrapperType": "track", "trackName": "Fuel", "artistName": "Metallica"},
                    {"wrapperType": "track", "trackName": "The Memory Remains", "artistName": "Metallica"},
                ],
            }
            with mock.patch("requests.get") as mock_get:
                mock_get.return_value.status_code = 200
                mock_get.return_value.json.return_value = mock_response

                tracks = resolver._resolve_itunes_lookup("Metallica", 5)
                assert len(tracks) == 2
                assert tracks[0].title == "Fuel"
                assert tracks[0].source == "itunes_lookup"

    def test_skips_artist_records(self, resolver):
        with mock.patch.object(resolver, "_find_artist_id", return_value=12345):
            mock_response = {
                "resultCount": 2,
                "results": [
                    {"wrapperType": "artist", "artistName": "TestArtist"},
                    {"wrapperType": "collection", "collectionName": "Some Album"},
                ],
            }
            with mock.patch("requests.get") as mock_get:
                mock_get.return_value.status_code = 200
                mock_get.return_value.json.return_value = mock_response

                tracks = resolver._resolve_itunes_lookup("TestArtist", 5)
                assert len(tracks) == 0

    def test_no_artist_id_found(self, resolver):
        with mock.patch.object(resolver, "_find_artist_id", return_value=None):
            tracks = resolver._resolve_itunes_lookup("Unknown Artist", 5)
            assert tracks == []

    def test_lookup_api_error(self, resolver):
        with mock.patch.object(resolver, "_find_artist_id", return_value=12345):
            with mock.patch("requests.get") as mock_get:
                mock_get.return_value.status_code = 503

                tracks = resolver._resolve_itunes_lookup("Metallica", 5)
                assert tracks == []


class TestFindArtistId:
    def test_finds_matching_artist(self, resolver):
        mock_response = {
            "resultCount": 2,
            "results": [
                {"artistName": "Metallica", "artistId": 3996865},
                {"artistName": "Metallica Cover Band", "artistId": 99999},
            ],
        }
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = mock_response

            artist_id = resolver._find_artist_id("Metallica")
            assert artist_id == 3996865

    def test_falls_back_to_first_result(self, resolver):
        mock_response = {
            "resultCount": 1,
            "results": [
                {"artistName": "XYZ Completely Different", "artistId": 11111},
            ],
        }
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = mock_response

            artist_id = resolver._find_artist_id("Metallica")
            assert artist_id == 11111

    def test_no_results(self, resolver):
        mock_response = {"resultCount": 0, "results": []}
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = mock_response

            artist_id = resolver._find_artist_id("Nonexistent Artist")
            assert artist_id is None

    def test_api_failure(self, resolver):
        with mock.patch("requests.get", side_effect=Exception("Timeout")):
            artist_id = resolver._find_artist_id("Metallica")
            assert artist_id is None


class TestLLMFallback:
    def test_successful_llm_resolution(self, resolver):
        mock_llm_response = '{"songs": ["Enter Sandman", "Master of Puppets", "One"]}'
        with mock.patch(
            "moshpit.ingest.base.BaseIngester.query_llm",
            return_value=mock_llm_response,
        ):
            tracks = resolver._resolve_llm("Metallica", 3)
            assert len(tracks) == 3
            assert tracks[0].title == "Enter Sandman"
            assert tracks[0].source == "llm"

    def test_llm_failure(self, resolver):
        with mock.patch(
            "moshpit.ingest.base.BaseIngester.query_llm",
            side_effect=Exception("LLM offline"),
        ):
            tracks = resolver._resolve_llm("Metallica", 3)
            assert tracks == []


class TestArtistMatch:
    def test_exact_match(self):
        assert TopTracksResolver._artist_match("Metallica", "Metallica")

    def test_case_insensitive(self):
        assert TopTracksResolver._artist_match("metallica", "Metallica")

    def test_substring_match(self):
        assert TopTracksResolver._artist_match("Tool", "Tool (Band)")
        assert TopTracksResolver._artist_match("Tool (Band)", "Tool")

    def test_no_match(self):
        assert not TopTracksResolver._artist_match("Metallica", "Megadeth")


class TestMergeTracks:
    def test_merges_without_duplicates(self):
        primary = [
            TrackSuggestion(title="Enter Sandman", artist="Metallica", source="itunes_api"),
        ]
        secondary = [
            TrackSuggestion(title="Enter Sandman", artist="Metallica", source="llm"),
            TrackSuggestion(title="One", artist="Metallica", source="llm"),
        ]
        merged = TopTracksResolver._merge_tracks(primary, secondary, 10)
        assert len(merged) == 2
        titles = [t.title for t in merged]
        assert "Enter Sandman" in titles
        assert "One" in titles

    def test_respects_limit(self):
        primary = [
            TrackSuggestion(title="Song1", artist="A", source="itunes_api"),
        ]
        secondary = [
            TrackSuggestion(title="Song2", artist="A", source="llm"),
            TrackSuggestion(title="Song3", artist="A", source="llm"),
        ]
        merged = TopTracksResolver._merge_tracks(primary, secondary, 2)
        assert len(merged) == 2


class TestResolveOrchestration:
    def test_full_tiered_fallback(self, resolver):
        """When tier 1 returns fewer tracks than needed, tier 2 and 3 are tried."""
        with mock.patch.object(
            resolver, "_resolve_itunes_search", return_value=[
                TrackSuggestion(title="Song1", artist="TestArtist", source="itunes_api"),
            ]
        ):
            with mock.patch.object(
                resolver, "_resolve_itunes_lookup", return_value=[
                    TrackSuggestion(title="Song2", artist="TestArtist", source="itunes_lookup"),
                ]
            ):
                with mock.patch.object(
                    resolver, "_resolve_llm", return_value=[
                        TrackSuggestion(title="Song3", artist="TestArtist", source="llm"),
                    ]
                ):
                    tracks = resolver.resolve("TestArtist", 3)
                    assert len(tracks) == 3
                    sources = {t.source for t in tracks}
                    assert "itunes_api" in sources
                    assert "itunes_lookup" in sources
                    assert "llm" in sources

    def test_tier1_sufficient(self, resolver):
        """When tier 1 returns enough tracks, tiers 2 and 3 are not called."""
        with mock.patch.object(
            resolver, "_resolve_itunes_search", return_value=[
                TrackSuggestion(title=f"Song{i}", artist="TestArtist", source="itunes_api")
                for i in range(5)
            ]
        ):
            with mock.patch.object(resolver, "_resolve_itunes_lookup") as mock_lookup:
                with mock.patch.object(resolver, "_resolve_llm") as mock_llm:
                    tracks = resolver.resolve("TestArtist", 5)
                    assert len(tracks) == 5
                    mock_lookup.assert_not_called()
                    mock_llm.assert_not_called()

    def test_all_tiers_fail(self, resolver):
        """When all tiers fail, an empty list is returned."""
        with mock.patch.object(resolver, "_resolve_itunes_search", return_value=[]):
            with mock.patch.object(resolver, "_resolve_itunes_lookup", return_value=[]):
                with mock.patch.object(resolver, "_resolve_llm", return_value=[]):
                    tracks = resolver.resolve("Unknown", 3)
                    assert tracks == []
