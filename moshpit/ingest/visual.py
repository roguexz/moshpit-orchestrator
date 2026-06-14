import base64
import os
from moshpit.exceptions import MoshpitException
from moshpit.ingest.base import BaseIngester


class VisualIngester(BaseIngester):
    """
    Ingester that reads local images (e.g. concert poster lineups)
    and uses a local Vision-LLM to extract artist names.
    """

    def extract_artists(self, image_path: str) -> list[str]:
        """
        Loads the image, base64 encodes it, and queries the local LLM to extract artists.
        """
        if not os.path.exists(image_path):
            raise MoshpitException(
                f"Concert poster image not found at path: {image_path}"
            )

        try:
            with open(image_path, "rb") as image_file:
                image_data = image_file.read()
                image_b64 = base64.b64encode(image_data).decode("utf-8")
        except IOError as e:
            raise MoshpitException(f"Failed to read image file at {image_path}: {e}")

        # Build prompt
        prompt = (
            "Identify and extract all band, artist, or performer names visible "
            "on this concert poster. You MUST return the list strictly formatted "
            'in JSON matching: {"artists": ["Artist Name 1", "Artist Name 2"]}.'
        )

        raw_llm_output = self.query_llm(prompt, image_b64=image_b64)
        return self.parse_and_validate_artists(raw_llm_output)
