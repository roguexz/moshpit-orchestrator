import pytest
from moshpit.ingest import clean_artist_name, extract_json_block

def test_extract_json_block_markdown():
    raw_markdown = "```json\n{\n  \"artists\": [\"Tool\", \"Deftones\"]\n}\n```"
    assert extract_json_block(raw_markdown) == "{\n  \"artists\": [\"Tool\", \"Deftones\"]\n}"

def test_extract_json_block_markdown_array():
    raw_markdown = "```\n[\"Tool\", \"Deftones\"]\n```"
    assert extract_json_block(raw_markdown) == "[\"Tool\", \"Deftones\"]"

def test_extract_json_block_conversational():
    raw_text = "Here is the output: {\"artists\": [\"Tool\"]} hope this is what you wanted!"
    assert extract_json_block(raw_text) == "{\"artists\": [\"Tool\"]}"

def test_extract_json_block_array_only():
    # Covers fallback where only bracket [ and ] are found
    assert extract_json_block("Here is array: [\"Tool\"]") == "[\"Tool\"]"

def test_extract_json_block_empty():
    with pytest.raises(ValueError, match="Raw LLM output is empty"):
        extract_json_block("")

def test_extract_json_block_no_json():
    with pytest.raises(ValueError, match="No JSON object or array found"):
        extract_json_block("This text has no json data inside it.")

def test_extract_json_block_no_end_brace():
    # Covers fallback where start brace is found but no end brace/bracket is present
    with pytest.raises(ValueError, match="No matching JSON object or array end"):
        extract_json_block("Here is start { but no end")

def test_extract_json_block_invalid_boundaries():
    with pytest.raises(ValueError, match="Invalid JSON boundaries"):
        extract_json_block("} leading close then open {")

def test_clean_artist_name_basic():
    assert clean_artist_name("Tool") == "Tool"
    assert clean_artist_name("  Deftones  ") == "Deftones"

def test_clean_artist_name_billing_fluff():
    assert clean_artist_name("Tool - Main Stage Friday") == "Tool"
    assert clean_artist_name("A Perfect Circle Live") == "A Perfect Circle"
    assert clean_artist_name("Slipknot Acoustic Set") == "Slipknot"
    assert clean_artist_name("Guns N' Roses Headliner PM") == "Guns N' Roses"

def test_clean_artist_name_stylized():
    assert clean_artist_name("$UICIDEBOY$") == "Suicideboys"
    assert clean_artist_name("A$AP Rocky") == "ASAP Rocky"
    assert clean_artist_name("Ke$ha") == "Kesha"

def test_clean_artist_name_accents():
    assert clean_artist_name("Motörhead") == "Motorhead"
    assert clean_artist_name("Bérurier Noir") == "Berurier Noir"

def test_clean_artist_name_empty():
    assert clean_artist_name("") == ""
    assert clean_artist_name(None) == ""
