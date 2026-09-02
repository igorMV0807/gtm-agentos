from functools import lru_cache

from pydantic import AnyHttpUrl, EmailStr, Field, SecretStr
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
    n8n_webhook_url: AnyHttpUrl | None = None
    n8n_webhook_secret: SecretStr | None = None
    n8n_timeout_seconds: float = Field(default=10.0, ge=1.0, le=30.0)
    n8n_signature_max_age_seconds: int = Field(default=300, ge=30, le=900)
    crm_provider: str = "hubspot"
    hubspot_access_token: SecretStr | None = None
    resend_api_key: SecretStr | None = None
    email_test_recipient: EmailStr | None = None
    resend_from_email: str = "GTM AgentOS <onboarding@resend.dev>"
    operator_api_key: SecretStr | None = None
    operator_session_max_age_seconds: int = Field(
        default=43200, ge=900, le=86400
    )
    portfolio_mode: bool = False
    ai_pricing_json: str | None = None

    # Internal timeout defaults stay out of .env.example; operators normally need
    # only provider identities, URLs, keys, and the bounded public tuning fields.
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

    def require_n8n(self) -> tuple[str, str]:
        if self.n8n_webhook_url is None or self.n8n_webhook_secret is None:
            raise ApplicationConfigurationError(
                "N8N_WEBHOOK_URL and N8N_WEBHOOK_SECRET must be configured"
            )
        host = (self.n8n_webhook_url.host or "").lower()
        if (
            self.n8n_webhook_url.username
            or self.n8n_webhook_url.password
            or self.n8n_webhook_url.query
            or self.n8n_webhook_url.fragment
        ):
            raise ApplicationConfigurationError(
                "N8N_WEBHOOK_URL cannot contain credentials, query, or fragment"
            )
        if self.n8n_webhook_url.scheme != "https" and host not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ApplicationConfigurationError(
                "N8N_WEBHOOK_URL must use HTTPS unless it targets localhost"
            )
        secret = self.n8n_webhook_secret.get_secret_value()
        if len(secret.encode("utf-8")) < 16:
            raise ApplicationConfigurationError(
                "N8N_WEBHOOK_SECRET must contain at least 16 bytes"
            )
        return str(self.n8n_webhook_url), secret

    def require_crm(self) -> tuple[str, str]:
        provider = self.crm_provider.strip().lower()
        if provider != "hubspot":
            raise ApplicationConfigurationError(
                f"Unsupported CRM_PROVIDER: {provider or '<empty>'}"
            )
        if (
            self.hubspot_access_token is None
            or not self.hubspot_access_token.get_secret_value().strip()
        ):
            raise ApplicationConfigurationError(
                "HUBSPOT_ACCESS_TOKEN must be configured before using HubSpot"
            )
        return provider, self.hubspot_access_token.get_secret_value()

    def require_email(self) -> tuple[str, str, str]:
        if (
            self.resend_api_key is None
            or not self.resend_api_key.get_secret_value().strip()
            or self.email_test_recipient is None
        ):
            raise ApplicationConfigurationError(
                "RESEND_API_KEY and EMAIL_TEST_RECIPIENT must be configured"
            )
        sender = self.resend_from_email.strip()
        if not sender:
            raise ApplicationConfigurationError(
                "RESEND_FROM_EMAIL cannot be empty"
            )
        return (
            self.resend_api_key.get_secret_value(),
            str(self.email_test_recipient),
            sender,
        )

    def require_operator_key(self) -> str:
        if self.operator_api_key is None:
            raise ApplicationConfigurationError(
                "OPERATOR_API_KEY must be configured"
            )
        key = self.operator_api_key.get_secret_value().strip()
        if len(key.encode("utf-8")) < 32:
            raise ApplicationConfigurationError(
                "OPERATOR_API_KEY must contain at least 32 bytes"
            )
        return key


@lru_cache
def get_settings() -> Settings:
    return Settings()
