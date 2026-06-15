from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configuration settings loaded from environment variables with defaults.
    All properties are prefixable with 'MOSHPIT_' (e.g. MOSHPIT_LLM_BASE_URL).
    """

    llm_base_url: str = Field(
        default="http://localhost:11434",
        validation_alias=AliasChoices(
            "moshpit_llm_base_url", "moshpit_ollama_base_url"
        ),
    )
    llm_model: str = Field(
        default="llava",
        validation_alias=AliasChoices("moshpit_llm_model", "moshpit_ollama_model"),
    )
    llm_timeout: float = Field(
        default=120.0,
        validation_alias=AliasChoices("moshpit_llm_timeout", "moshpit_ollama_timeout"),
    )
    default_tracks_per_artist: int = 20
    jxa_timeout: float = 30.0
    storefront: str = "us"
    resolver_delay: float = 1.0

    model_config = SettingsConfigDict(
        env_prefix="MOSHPIT_",
        case_sensitive=False,
    )


settings = Settings()
