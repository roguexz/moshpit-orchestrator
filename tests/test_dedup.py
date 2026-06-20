from moshpit.dedup import (
    normalize_track_title,
    get_track_preference_score,
    identify_duplicates,
)


def test_normalize_track_title():
    assert normalize_track_title("Comfortably Numb") == "comfortably numb"
    assert normalize_track_title("Comfortably Numb (Live)") == "comfortably numb"
    assert normalize_track_title("Comfortably Numb - Live") == "comfortably numb"
    assert (
        normalize_track_title("Comfortably Numb (Live at Pompeii)")
        == "comfortably numb"
    )
    assert (
        normalize_track_title("Comfortably Numb [Acoustic Version]")
        == "comfortably numb"
    )
    assert normalize_track_title("Comfortably Numb - Remix") == "comfortably numb"
    assert (
        normalize_track_title("Comfortably Numb (2011 Remaster)") == "comfortably numb"
    )
    assert (
        normalize_track_title("Comfortably Numb (Deluxe Edition)") == "comfortably numb"
    )
    assert normalize_track_title("") == ""


def test_get_track_preference_score():
    assert get_track_preference_score("Comfortably Numb") == 100
    assert get_track_preference_score("Comfortably Numb (Live)") == 50
    assert get_track_preference_score("Comfortably Numb - Remix") == 60
    assert get_track_preference_score("Comfortably Numb [Acoustic]") == 70
    assert get_track_preference_score("Comfortably Numb (Instrumental)") == 40
    assert get_track_preference_score("Comfortably Numb (2011 Remaster)") == 95


def test_identify_duplicates_exact():
    # Identical songs (different database IDs)
    tracks = [
        {"databaseID": 1, "name": "Time", "artist": "Pink Floyd"},
        {"databaseID": 2, "name": "Time", "artist": "Pink Floyd"},
    ]
    dups = identify_duplicates(tracks)
    assert len(dups) == 1
    assert dups[0]["databaseID"] == 2  # Keeps the first occurrence


def test_identify_duplicates_versions():
    # Studio vs Live vs Remix
    tracks = [
        {"databaseID": 1, "name": "Comfortably Numb (Live)", "artist": "Pink Floyd"},
        {"databaseID": 2, "name": "Comfortably Numb", "artist": "Pink Floyd"},
        {"databaseID": 3, "name": "Comfortably Numb (Remix)", "artist": "Pink Floyd"},
    ]
    dups = identify_duplicates(tracks)
    # Should keep databaseID=2 (Comfortably Numb) and delete 1 and 3
    assert len(dups) == 2
    deleted_ids = [d["databaseID"] for d in dups]
    assert 1 in deleted_ids
    assert 3 in deleted_ids
    assert 2 not in deleted_ids


def test_identify_duplicates_different_artists():
    # Same title, different artists (not duplicates)
    tracks = [
        {"databaseID": 1, "name": "Time", "artist": "Pink Floyd"},
        {"databaseID": 2, "name": "Time", "artist": "David Bowie"},
    ]
    dups = identify_duplicates(tracks)
    assert len(dups) == 0


def test_identify_duplicates_no_dups():
    tracks = [
        {"databaseID": 1, "name": "Time", "artist": "Pink Floyd"},
        {"databaseID": 2, "name": "Money", "artist": "Pink Floyd"},
    ]
    dups = identify_duplicates(tracks)
    assert len(dups) == 0
