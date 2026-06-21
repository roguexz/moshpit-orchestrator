import json
import re
import time
import unicodedata
from dataclasses import dataclass, asdict
from typing import List, Optional

import requests
from loguru import logger

from moshpit.config import settings
from moshpit.dedup import normalize_track_title, get_track_preference_score
from moshpit.ingest.normalizer import extract_json_block


@dataclass
class TrackSuggestion:
    """A resolved track suggestion from one of the discovery sources."""

    title: str
    artist: str
    source: str  # "itunes_api" | "itunes_lookup" | "llm"

    def to_dict(self) -> dict:
        return asdict(self)


class TopTracksResolver:
    """
    Resolves an artist name to a list of top track names using a tiered
    fallback chain:
      1. iTunes Search API (song search by artist name)
      2. iTunes Lookup API (find artist ID → lookup songs by ID)
      3. LLM fallback (ask local LLM for well-known songs)
    """

    ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
    ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"

    # User-Agent for web requests
    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self, config=settings, storefront: Optional[str] = None):
        self.config = config
        self._delay = config.resolver_delay
        self._storefront = storefront if storefront else config.storefront

    def resolve(self, artist: str, count: int = 20) -> List[TrackSuggestion]:
        """
        Resolves top tracks for the given artist using a tiered fallback chain.
        Returns up to `count` TrackSuggestion objects.
        """
        # Tier 1: iTunes Search API — song search (fast, usually good results)
        tracks = self._resolve_itunes_search(artist, count)
        tracks = self._deduplicate_suggestions(tracks)
        if len(tracks) >= count:
            return tracks[:count]

        # Tier 2: iTunes Lookup API — find artist ID, then lookup songs by ID
        # This can surface different songs than the search API
        if len(tracks) < count:
            lookup_tracks = self._resolve_itunes_lookup(artist, count)
            if lookup_tracks:
                lookup_tracks = self._deduplicate_suggestions(lookup_tracks)
                tracks = self._merge_tracks(tracks, lookup_tracks, count)
                if len(tracks) >= count:
                    return tracks[:count]

        # Tier 3: LLM fallback — ask local LLM for well-known tracks
        if len(tracks) < count:
            llm_tracks = self._resolve_llm(artist, count - len(tracks))
            if llm_tracks:
                llm_tracks = self._deduplicate_suggestions(llm_tracks)
                tracks = self._merge_tracks(tracks, llm_tracks, count)

        return tracks[:count]

    def _resolve_itunes_search(self, artist: str, count: int) -> List[TrackSuggestion]:
        """Tier 1: Query the iTunes Search API for songs matching the artist name."""
        try:
            params = {
                "term": artist,
                "entity": "song",
                "limit": str(
                    min(max(count * 3, 50), 200)
                ),  # overfetch to allow version deduplication
                "country": self._storefront,
            }
            resp = requests.get(
                self.ITUNES_SEARCH_URL,
                params=params,
                headers={"User-Agent": self.USER_AGENT},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.debug(
                    f"iTunes Search API returned status {resp.status_code} for '{artist}'"
                )
                return []

            data = resp.json()
            tracks: List[TrackSuggestion] = []
            seen_titles: set = set()

            for item in data.get("results", []):
                item_artist = item.get("artistName", "")
                track_name = item.get("trackName", "")

                if not track_name:
                    continue

                # Fuzzy artist match
                if not self._artist_match(artist, item_artist):
                    continue

                title_lower = track_name.lower()
                if title_lower in seen_titles:
                    continue
                seen_titles.add(title_lower)

                tracks.append(
                    TrackSuggestion(
                        title=track_name,
                        artist=item_artist,
                        source="itunes_api",
                    )
                )

            logger.debug(
                f"iTunes Search API resolved {len(tracks)} tracks for '{artist}'"
            )
            time.sleep(self._delay)
            return tracks

        except Exception as e:
            logger.debug(f"iTunes Search API resolution failed for '{artist}': {e}")
            return []

    def _resolve_itunes_lookup(self, artist: str, count: int) -> List[TrackSuggestion]:
        """Tier 2: Find artist ID via search, then use the lookup API for additional songs.

        The lookup API can surface different songs than the search API,
        particularly for artists whose name overlaps with common words.
        """
        try:
            # Step 1: Find the artist ID
            artist_id = self._find_artist_id(artist)
            if not artist_id:
                logger.debug(f"iTunes Lookup: could not find artist ID for '{artist}'")
                return []

            # Step 2: Lookup songs by artist ID
            params = {
                "id": str(artist_id),
                "entity": "song",
                "limit": str(
                    min(max(count * 3, 50), 200)
                ),  # overfetch to allow version deduplication
                "country": self._storefront,
            }
            resp = requests.get(
                self.ITUNES_LOOKUP_URL,
                params=params,
                headers={"User-Agent": self.USER_AGENT},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.debug(
                    f"iTunes Lookup API returned status {resp.status_code} for artist ID {artist_id}"
                )
                return []

            data = resp.json()
            tracks: List[TrackSuggestion] = []
            seen_titles: set = set()

            for item in data.get("results", []):
                # Skip the artist record itself (first result is the artist metadata)
                if item.get("wrapperType") != "track":
                    continue

                track_name = item.get("trackName", "")
                item_artist = item.get("artistName", "")

                if not track_name:
                    continue

                title_lower = track_name.lower()
                if title_lower in seen_titles:
                    continue
                seen_titles.add(title_lower)

                tracks.append(
                    TrackSuggestion(
                        title=track_name,
                        artist=item_artist,
                        source="itunes_lookup",
                    )
                )

            logger.debug(
                f"iTunes Lookup API resolved {len(tracks)} tracks for '{artist}' (ID: {artist_id})"
            )
            time.sleep(self._delay)
            return tracks

        except Exception as e:
            logger.debug(f"iTunes Lookup API resolution failed for '{artist}': {e}")
            return []

    def _find_artist_id(self, artist: str) -> Optional[int]:
        """Search the iTunes API for a musicArtist entity to find the artist's numeric ID."""
        try:
            params = {
                "term": artist,
                "entity": "musicArtist",
                "limit": "5",
                "country": self._storefront,
            }
            resp = requests.get(
                self.ITUNES_SEARCH_URL,
                params=params,
                headers={"User-Agent": self.USER_AGENT},
                timeout=10,
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            for item in data.get("results", []):
                candidate = item.get("artistName", "")
                if self._artist_match(artist, candidate):
                    return item.get("artistId")

            return None

        except Exception:
            return None

    def _resolve_llm(self, artist: str, count: int) -> List[TrackSuggestion]:
        """Tier 3: Fall back to the local LLM for top track suggestions."""
        try:
            from moshpit.ingest.base import BaseIngester

            class _LLMHelper(BaseIngester):
                def extract_artists(self, input_path: str) -> list[str]:
                    return []

            helper = _LLMHelper(self.config)
            prompt = (
                f"Identify the top {count} most popular or representative songs "
                f"of the artist or band '{artist}'. "
                f"Respond with a JSON object containing a 'songs' key mapping "
                f"to a list of strings (the song names). "
                f"Do not include any explanation or markdown formatting other "
                f"than the JSON block."
            )
            raw_resp = helper.query_llm(prompt)
            data = json.loads(extract_json_block(raw_resp))

            tracks: List[TrackSuggestion] = []
            if data and "songs" in data:
                for song in data["songs"]:
                    tracks.append(
                        TrackSuggestion(
                            title=str(song),
                            artist=artist,
                            source="llm",
                        )
                    )
            logger.debug(f"LLM resolved {len(tracks)} tracks for '{artist}'")
            return tracks

        except Exception as e:
            logger.debug(f"LLM resolution failed for '{artist}': {e}")
            return []

    @staticmethod
    def _normalize_artist_name(name: str) -> str:
        """
        Cleans and normalizes an artist name:
        1. Lowercases and trims.
        2. Removes parenthesized/bracketed metadata (e.g. (Band)).
        3. Removes accents/diacritics.
        4. Standardizes '&' to 'and'.
        5. Removes non-alphanumeric characters except spaces.
        6. Strips leading 'the '.
        """
        if not name:
            return ""
        n = name.lower().strip()
        n = re.sub(r"\s*\([^)]*\)", "", n)
        n = re.sub(r"\s*\[[^\]]*\]", "", n)
        n = "".join(
            c for c in unicodedata.normalize("NFKD", n) if not unicodedata.combining(c)
        )
        n = n.replace("&", "and")
        n = re.sub(r"[^a-z0-9\s]", "", n)
        n = re.sub(r"\s+", " ", n).strip()
        if n.startswith("the "):
            n = n[4:].strip()
        return n

    @staticmethod
    def _artist_match(query: str, candidate: str) -> bool:
        """
        Fuzzy artist name match:
        1. Compares normalized exact match (with or without spaces).
        2. If not matched, splits candidate by collaboration separators
           and checks if the query matches any of the split parts.
        """
        q_norm = TopTracksResolver._normalize_artist_name(query)
        c_norm = TopTracksResolver._normalize_artist_name(candidate)
        if not q_norm or not c_norm:
            return False

        # Check for exact normalized match
        if q_norm == c_norm or q_norm.replace(" ", "") == c_norm.replace(" ", ""):
            return True

        # Check for collaboration matches by splitting candidate
        # e.g., "Doobie & Krash Minati" -> ["Doobie", "Krash Minati"]
        separators = re.compile(
            r"\s*(?:,|&|\b(?:and|feat\.?|featuring|with|vs\.?|x)\b)\s*", re.IGNORECASE
        )
        parts = separators.split(candidate)
        if len(parts) > 1:
            for part in parts:
                p_norm = TopTracksResolver._normalize_artist_name(part)
                if q_norm == p_norm or q_norm.replace(" ", "") == p_norm.replace(
                    " ", ""
                ):
                    return True

        return False

    @staticmethod
    def _merge_tracks(
        primary: List[TrackSuggestion],
        secondary: List[TrackSuggestion],
        limit: int,
    ) -> List[TrackSuggestion]:
        """Merge two track lists, deduplicating by normalized title."""
        seen = {normalize_track_title(t.title) for t in primary}
        merged = list(primary)
        for track in secondary:
            if len(merged) >= limit:
                break
            norm = normalize_track_title(track.title)
            if norm not in seen:
                seen.add(norm)
                merged.append(track)
        return merged

    def _deduplicate_suggestions(
        self, tracks: List[TrackSuggestion]
    ) -> List[TrackSuggestion]:
        """
        Deduplicates track suggestions by normalized title, picking the version
        with the highest preference score, and preserving original relative order.
        """
        groups: dict[str, List[tuple[int, TrackSuggestion]]] = (
            {}
        )  # norm_title -> List[(index, track)]
        for idx, track in enumerate(tracks):
            norm_title = normalize_track_title(track.title)
            if norm_title not in groups:
                groups[norm_title] = []
            groups[norm_title].append((idx, track))

        unique_tracks = []
        for norm_title, group in groups.items():
            # Sort by preference score descending, then original index ascending (negated index for reverse)
            sorted_group = sorted(
                group,
                key=lambda item: (
                    get_track_preference_score(item[1].title),
                    -item[0],
                ),
                reverse=True,
            )
            # Keep the best track (first one in the sorted group)
            best_track_idx, best_track = sorted_group[0]
            unique_tracks.append((best_track_idx, best_track))

        # Sort selected tracks by their original index to preserve overall order
        unique_tracks.sort(key=lambda item: item[0])
        return [track for _, track in unique_tracks]
