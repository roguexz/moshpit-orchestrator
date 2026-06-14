import re

JSON_BLOCK_PATTERN = re.compile(
    r"```(?:json)?\s*([\{\[].*?[\}\]])\s*```",
    re.DOTALL | re.IGNORECASE
)

def extract_json_block(raw_output: str) -> str:
    """
    Extracts a clean JSON string from raw LLM outputs, which may be wrapped
    in markdown fences or contain conversational prefixes/suffixes.
    """
    if not raw_output:
        raise ValueError("Raw LLM output is empty.")

    # 1. Try to extract from markdown code blocks
    match = JSON_BLOCK_PATTERN.search(raw_output)
    if match:
        return match.group(1).strip()

    # 2. Fall back to brace/bracket recovery
    first_brace = raw_output.find("{")
    first_bracket = raw_output.find("[")

    if first_brace == -1 and first_bracket == -1:
        raise ValueError("No JSON object or array found in LLM output.")
    elif first_brace == -1:
        start = first_bracket
    elif first_bracket == -1:
        start = first_brace
    else:
        start = min(first_brace, first_bracket)

    last_brace = raw_output.rfind("}")
    last_bracket = raw_output.rfind("]")

    if last_brace == -1 and last_bracket == -1:
        raise ValueError("No matching JSON object or array end in LLM output.")
    elif last_brace == -1:
        end = last_bracket
    elif last_bracket == -1:
        end = last_brace
    else:
        end = max(last_brace, last_bracket)

    if start > end:
        raise ValueError("Invalid JSON boundaries detected in LLM output.")

    return raw_output[start:end + 1].strip()
