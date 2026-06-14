from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configuration settings loaded from environment variables with defaults.
    All properties are prefixable with 'MOSHPIT_' (e.g. MOSHPIT_OLLAMA_BASE_URL).
    """

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llava"
    ollama_timeout: float = 120.0
    default_tracks_per_artist: int = 3
    jxa_timeout: float = 30.0

    model_config = SettingsConfigDict(
        env_prefix="MOSHPIT_",
        case_sensitive=False,
    )


settings = Settings()
