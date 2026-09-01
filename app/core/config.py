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
    embedding_provider: str = "voyage"
    embedding_model: str = "voyage-4"
    embedding_api_key: SecretStr | None = None
    embedding_dimension: int = 1024
    rag_top_k: int = 5
    rag_similarity_threshold: float = 0.40
    rag_chunk_size_words: int = 160
    rag_chunk_overlap_words: int = 24

    # Internal defaults intentionally stay out of .env.example to keep its public
    # configuration surface limited to the variables requested for Phase 1.
    llm_timeout_seconds: float = 30.0
    embedding_timeout_seconds: float = 30.0

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

    def require_embedding(self) -> tuple[str, str, str, int]:
        provider = self.embedding_provider.strip().lower()
        model = self.embedding_model.strip()
        if provider != "voyage":
            raise ApplicationConfigurationError(
                f"Unsupported EMBEDDING_PROVIDER: {provider or '<empty>'}"
            )
        if model != "voyage-4" or self.embedding_dimension != 1024:
            raise ApplicationConfigurationError(
                "Phase 3 requires voyage-4 with 1024-dimensional embeddings"
            )
        if self.embedding_api_key is None:
            raise ApplicationConfigurationError(
                "EMBEDDING_API_KEY must be configured"
            )
        if not 1 <= self.rag_top_k <= 20:
            raise ApplicationConfigurationError("RAG_TOP_K must be between 1 and 20")
        if not 0.0 <= self.rag_similarity_threshold <= 1.0:
            raise ApplicationConfigurationError(
                "RAG_SIMILARITY_THRESHOLD must be between 0 and 1"
            )
        if (
            self.rag_chunk_size_words < 20
            or self.rag_chunk_overlap_words < 0
            or self.rag_chunk_overlap_words >= self.rag_chunk_size_words
        ):
            raise ApplicationConfigurationError(
                "RAG chunk size and overlap configuration is invalid"
            )
        return (
            provider,
            model,
            self.embedding_api_key.get_secret_value(),
            self.embedding_dimension,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
