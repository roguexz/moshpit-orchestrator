import re
import unicodedata

# A static map for specific stylized band/artist names
STYLIZED_ARTIST_MAP = {
    "uicideboy": "Suicideboys",
    "uicideboys": "Suicideboys",
}

# Added days of the week to strip billing day details (e.g., "Friday")
BILLING_FLUFF_PATTERN = re.compile(
    r"\b(main stage|stage|headliner|noon|pm|am|set|live|special guest|acoustic|vip|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE
)

def clean_artist_name(name: str) -> str:
    """
    Cleans and normalizes an artist name string for search matching.
    
    1. Removes billing fluff, stage names, and set times.
    2. Maps known stylized names.
    3. Substitutes general stylized characters (e.g. $ -> S/s contextually).
    4. Normalizes unicode accents (e.g. é -> e).
    5. Trims and formats spacing.
    """
    if not name:
        return ""

    # Strip surrounding whitespace and quotes
    cleaned = name.strip().strip("'\"")

    # 1. Remove billing fluff/stage designations
    cleaned = BILLING_FLUFF_PATTERN.sub("", cleaned)

    # Clean double spaces or dangling hyphens/separators that might remain after stripping billing fluff
    cleaned = re.sub(r"\s+-\s+", " ", cleaned)  # Hyphens separating billing details
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.rstrip("-:,; ")

    # 2. Check stylized map (case-insensitive key comparison)
    # Remove symbols for key lookup to match e.g. "$uicideboy$"
    lookup_key = re.sub(r"[^a-zA-Z0-9]", "", cleaned).lower()
    if lookup_key in STYLIZED_ARTIST_MAP:
        return STYLIZED_ARTIST_MAP[lookup_key]

    # 3. Replace general stylized characters (e.g., $ -> S or s based on casing context)
    def replace_dollar(match):
        pos = match.start()
        if pos > 0 and match.string[pos - 1].islower():
            return "s"
        return "S"

    cleaned = re.sub(r"\$", replace_dollar, cleaned)

    # 4. Accent normalization (remove diacritics)
    cleaned = "".join(
        c for c in unicodedata.normalize("NFKD", cleaned)
        if not unicodedata.combining(c)
    )

    # 5. Clean up any remaining extra spacing or empty strings
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    
    return cleaned
