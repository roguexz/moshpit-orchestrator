import re
from typing import List, Dict, Any

# Version keywords we want to strip from titles to identify matches
VERSION_KEYWORDS = (
    r"live|remix|acoustic|remaster|version|edit|deluxe|mix|studio|mono|stereo|"
    r"radio|explicit|clean|anniversary|lp|single|instrumental"
)

# Regex to match version indicators in parentheses or brackets (e.g. "(Live at Pompeii)", "(2011 Remaster)", "[Acoustic Version]")
VERSION_PARENTHESES_PATTERN = re.compile(
    rf"\s*[\(\[][^\)\]]*(?:{VERSION_KEYWORDS})[^\)\]]*[\)\]]", re.IGNORECASE
)

# Regex to match trailing hyphens with version indicators (e.g. " - Live", " - Remix")
VERSION_HYPHEN_PATTERN = re.compile(rf"\s*-\s*(?:{VERSION_KEYWORDS}).*$", re.IGNORECASE)


def normalize_track_title(title: str) -> str:
    """Normalizes a track title to identify duplicates/different versions."""
    if not title:
        return ""
    normalized = title.lower()
    normalized = VERSION_PARENTHESES_PATTERN.sub("", normalized)
    normalized = VERSION_HYPHEN_PATTERN.sub("", normalized)
    # Strip double spaces and surrounding whitespace
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def get_track_preference_score(title: str) -> int:
    """Assigns a preference score to a track title (higher is preferred)."""
    title_lower = title.lower()
    score = 100

    if "live" in title_lower:
        score -= 50
    if "remix" in title_lower or " mix" in title_lower:
        score -= 40
    if "acoustic" in title_lower:
        score -= 30
    if "edit" in title_lower:
        score -= 10
    if "instrumental" in title_lower:
        score -= 60
    if "remaster" in title_lower:
        # Remasters are fine, but keep it slightly below original
        score -= 5

    return score


def identify_duplicates(tracks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Identifies duplicate tracks within a playlist list of tracks.

    Each track should be a dict with: 'databaseID', 'name', 'artist'.
    Returns a list of track dicts that should be deleted.
    """
    # Group tracks by artist and normalized title
    groups: Dict[tuple, List[tuple]] = (
        {}
    )  # (artist, norm_title) -> List[(index, track)]

    for idx, track in enumerate(tracks):
        artist = track.get("artist", "").strip().lower()
        title = track.get("name", "").strip()
        norm_title = normalize_track_title(title)

        key = (artist, norm_title)
        if key not in groups:
            groups[key] = []
        groups[key].append((idx, track))

    duplicates = []

    for key, group_tracks in groups.items():
        if len(group_tracks) <= 1:
            continue

        # We sort by preference score descending, and then by their original index ascending
        # (to keep first occurrence if scores tie).
        # Since Python's sort is stable, sorting by index can be done by sorting with (score, -index) descending.
        sorted_tracks = sorted(
            group_tracks,
            key=lambda item: (
                get_track_preference_score(item[1].get("name", "")),
                -item[0],
            ),
            reverse=True,
        )

        # Keep the best track (first one in sorted list)
        best_track_idx, best_track = sorted_tracks[0]

        # The rest are duplicates to delete
        for idx, track in sorted_tracks[1:]:
            duplicates.append(track)

    return duplicates
