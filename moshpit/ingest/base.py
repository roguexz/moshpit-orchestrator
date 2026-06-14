from abc import ABC, abstractmethod
from typing import Any, List, Optional
import requests
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from loguru import logger
from moshpit.config import settings
from moshpit.exceptions import MoshpitException
from moshpit.ingest.normalizer import extract_json_block
from moshpit.ingest.sanitation import clean_artist_name


class ArtistSchema(BaseModel):
    """Schema to enforce the structure of extracted artist lists."""

    artists: List[str] = Field(..., min_length=1)


class BaseIngester(ABC):
    """
    Base class for ingestion pipelines that parse unstructured documents
    and extract a validated list of artist names.
    """

    def __init__(self, config=settings):
        self.config = config

    @abstractmethod
    def extract_artists(self, input_path: str) -> List[str]:
        """Extracts artist names from the given source path."""
        pass

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def query_llm(self, prompt: str, image_b64: Optional[str] = None) -> str:
        """
        Sends a query request to the local OpenAI-compatible API endpoint.
        Uses tenacity for exponential backoff retries on request failure.
        """
        url = f"{self.config.llm_base_url.rstrip('/')}/v1/chat/completions"

        if image_b64:
            content_payload: Any = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                },
                {"type": "text", "text": prompt},
            ]
        else:
            content_payload = prompt

        payload = {
            "model": self.config.llm_model,
            "messages": [{"role": "user", "content": content_payload}],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }

        # Truncate base64 strings in debug logs to avoid terminal flooding
        log_content_list: Any = []
        if isinstance(content_payload, list):
            for item in content_payload:
                if item["type"] == "image_url":
                    url_str = item["image_url"]["url"]
                    truncated_url = (
                        url_str[:30] + "...[truncated]..." + url_str[-20:]
                        if len(url_str) > 50
                        else url_str
                    )
                    log_content_list.append(
                        {"type": "image_url", "image_url": {"url": truncated_url}}
                    )
                else:
                    log_content_list.append(item)
        else:
            log_content_list = content_payload

        logger.debug(f"Sending LLM request to: {url}")
        logger.debug(f"LLM request model: {payload['model']}")
        logger.debug(f"LLM request messages: {log_content_list}")
        logger.debug(f"LLM response_format: {payload.get('response_format')}")

        try:
            response = requests.post(url, json=payload, timeout=self.config.llm_timeout)
            logger.debug(f"LLM response status code: {response.status_code}")
            headers_dict: Any = None
            try:
                headers_dict = dict(response.headers)
            except Exception:
                headers_dict = getattr(response, "headers", None)
            logger.debug(f"LLM response headers: {headers_dict}")
            logger.debug(f"LLM response text: {response.text}")

            response.raise_for_status()
            response_json = response.json()
            choices = response_json.get("choices", [])
            if not choices:
                raise MoshpitException("API returned an empty choices list.")

            llm_response = choices[0].get("message", {}).get("content", "")
            if not llm_response:
                raise MoshpitException("API returned an empty message content string.")
            return llm_response
        except requests.RequestException as e:
            logger.debug(f"RequestException encountered: {e}")
            if "response" in locals() and response is not None:
                logger.debug(f"Failed response status code: {response.status_code}")
                logger.debug(f"Failed response text: {response.text}")
            raise MoshpitException(f"Failed to query local LLM API: {e}")

    def parse_and_validate_artists(self, raw_llm_output: str) -> List[str]:
        """
        Extracts, cleans, and validates the artist list from the raw LLM output.
        """
        # 1. Extract the JSON block
        json_str = extract_json_block(raw_llm_output)

        # 2. Parse using Pydantic
        try:
            validated = ArtistSchema.model_validate_json(json_str)
        except Exception as e:
            raise MoshpitException(
                f"Failed to validate LLM response against ArtistSchema: {e}"
            )

        # 3. Sanitize each artist name and drop empty results
        sanitized_artists = []
        for artist in validated.artists:
            cleaned = clean_artist_name(artist)
            if cleaned:
                sanitized_artists.append(cleaned)

        if not sanitized_artists:
            raise MoshpitException(
                "Sanitation pipeline resolved zero valid artists from LLM response."
            )

        return sanitized_artists
