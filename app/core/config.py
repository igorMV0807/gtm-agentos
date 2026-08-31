from functools import lru_cache

from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.exceptions import ApplicationConfigurationError


class Settings(BaseSettings):
    """Runtime configuration loaded only from environment variables or .env."""

    supabase_url: AnyHttpUrl | None = None
    supabase_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    llm_provider: str = "anthropic"
    llm_model: str | None = None

    # Internal defaults intentionally stay out of .env.example to keep its public
    # configuration surface limited to the variables requested for Phase 1.
    llm_timeout_seconds: float = 30.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def require_database(self) -> tuple[str, str]:
        if self.supabase_url is None or self.supabase_key is None:
            raise ApplicationConfigurationError(
                "SUPABASE_URL and SUPABASE_KEY must be configured"
            )
        return str(self.supabase_url).rstrip("/"), self.supabase_key.get_secret_value()

    def require_llm(self) -> tuple[str, str, str]:
        provider = self.llm_provider.strip().lower()
        if provider != "anthropic":
            raise ApplicationConfigurationError(
                f"Unsupported LLM_PROVIDER: {provider or '<empty>'}"
            )
        if self.anthropic_api_key is None or not self.llm_model:
            raise ApplicationConfigurationError(
                "ANTHROPIC_API_KEY and LLM_MODEL must be configured"
            )
        return (
            provider,
            self.llm_model.strip(),
            self.anthropic_api_key.get_secret_value(),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

