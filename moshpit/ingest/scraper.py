from bs4 import BeautifulSoup
import requests
from moshpit.exceptions import MoshpitException
from moshpit.ingest.base import BaseIngester


class WebScraperIngester(BaseIngester):
    """
    Ingester that scrapes a web page, cleans the HTML structure,
    and queries an LLM to extract artist names.
    """

    def extract_artists(self, url: str) -> list[str]:
        """
        Fetches the web page, cleans the layout, and extracts artists using the local LLM.
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        try:
            response = requests.get(
                url, headers=headers, timeout=self.config.llm_timeout
            )
            response.raise_for_status()
        except requests.RequestException as e:
            raise MoshpitException(f"Failed to fetch webpage at {url}: {e}")

        # Clean HTML content
        soup = BeautifulSoup(response.text, "html.parser")

        # Remove script and style elements
        for element in soup(["script", "style", "noscript", "header", "footer", "nav"]):
            element.decompose()

        cleaned_text = soup.get_text(separator=" ")
        # Clean up excessive spacing
        cleaned_text = " ".join(cleaned_text.split())

        if not cleaned_text.strip():
            raise MoshpitException(
                f"Webpage at {url} yielded no readable text content."
            )

        # Build prompt
        prompt = (
            "Identify and extract all music artist names, bands, and performers "
            "listed on the following concert event page details. "
            "You MUST return the list strictly formatted in JSON matching: "
            '{"artists": ["Artist Name 1", "Artist Name 2"]}.\n\n'
            f"Event details:\n{cleaned_text}"
        )

        raw_llm_output = self.query_llm(prompt)
        return self.parse_and_validate_artists(raw_llm_output)
