import logging
from math import isfinite
from time import perf_counter
from typing import Literal, Protocol

import httpx

from app.core.config import Settings
from app.core.exceptions import (
    EmbeddingInvalidResponseError,
    EmbeddingProviderError,
    EmbeddingTimeoutError,
)
from app.services.ai_usage_service import AIUsageTracker


logger = logging.getLogger(__name__)
EmbeddingInputType = Literal["query", "document"]


class EmbeddingProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_text(
        self, text: str, *, input_type: EmbeddingInputType
    ) -> list[float]: ...

    def embed_batch(
        self, texts: list[str], *, input_type: EmbeddingInputType
    ) -> list[list[float]]: ...


class VoyageEmbeddingProvider:
    provider_name = "voyage"
    _endpoint = "https://api.voyageai.com/v1/embeddings"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimension: int,
        timeout_seconds: float,
        client: httpx.Client | None = None,
        usage_tracker: AIUsageTracker | None = None,
    ) -> None:
        self._model = model
        self._dimension = dimension
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._usage_tracker = usage_tracker

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_text(
        self, text: str, *, input_type: EmbeddingInputType
    ) -> list[float]:
        return self.embed_batch([text], input_type=input_type)[0]

    def embed_batch(
        self, texts: list[str], *, input_type: EmbeddingInputType
    ) -> list[list[float]]:
        if not texts or len(texts) > 1000 or any(not text.strip() for text in texts):
            raise EmbeddingInvalidResponseError("Embedding input is empty or too large")

        started = perf_counter()
        try:
            response = self._client.post(
                self._endpoint,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "input": texts,
                    "model": self._model,
                    "input_type": input_type,
                    "truncation": False,
                    "output_dimension": self._dimension,
                    "output_dtype": "float",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise EmbeddingTimeoutError("Voyage request timed out") from exc
        except httpx.HTTPError as exc:
            raise EmbeddingProviderError("Voyage API request failed") from exc
        except ValueError as exc:
            raise EmbeddingInvalidResponseError("Voyage returned invalid JSON") from exc

        embeddings = self._parse_embeddings(payload, expected_count=len(texts))
        if self._usage_tracker is not None:
            usage = payload.get("usage") if isinstance(payload, dict) else None
            total_tokens = (
                usage.get("total_tokens") if isinstance(usage, dict) else None
            )
            if (
                isinstance(total_tokens, bool)
                or not isinstance(total_tokens, int)
                or total_tokens < 0
            ):
                total_tokens = None
            self._usage_tracker.record(
                provider=self.provider_name,
                model=self.model,
                operation=(
                    "embedding_document"
                    if input_type == "document"
                    else "embedding_query"
                ),
                total_tokens=total_tokens,
                latency_ms=max(0, round((perf_counter() - started) * 1000)),
            )
        logger.info(
            "embedding_generated",
            extra={
                "provider": self.provider_name,
                "model": self.model,
                "dimension": self.dimension,
                "embedding_count": len(embeddings),
                "input_type": input_type,
            },
        )
        return embeddings

    def _parse_embeddings(
        self, payload: object, *, expected_count: int
    ) -> list[list[float]]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise EmbeddingInvalidResponseError("Voyage response has no data list")

        indexed: list[tuple[int, list[float]]] = []
        for position, item in enumerate(payload["data"]):
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise EmbeddingInvalidResponseError("Voyage response has no embedding")
            raw_embedding = item["embedding"]
            if len(raw_embedding) != self._dimension:
                raise EmbeddingInvalidResponseError(
                    "Voyage embedding dimension does not match configuration"
                )
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(float(value))
                for value in raw_embedding
            ):
                raise EmbeddingInvalidResponseError(
                    "Voyage embedding contains invalid values"
                )
            index = item.get("index", position)
            if not isinstance(index, int):
                raise EmbeddingInvalidResponseError("Voyage embedding index is invalid")
            indexed.append((index, [float(value) for value in raw_embedding]))

        indexed.sort(key=lambda item: item[0])
        if len(indexed) != expected_count or [item[0] for item in indexed] != list(
            range(expected_count)
        ):
            raise EmbeddingInvalidResponseError(
                "Voyage returned an incomplete embedding batch"
            )
        return [embedding for _, embedding in indexed]


def build_embedding_provider(
    settings: Settings, *, usage_tracker: AIUsageTracker | None = None
) -> EmbeddingProvider:
    provider, model, api_key, dimension = settings.require_embedding()
    if provider == "voyage":
        return VoyageEmbeddingProvider(
            api_key=api_key,
            model=model,
            dimension=dimension,
            timeout_seconds=settings.embedding_timeout_seconds,
            usage_tracker=usage_tracker,
        )
    raise EmbeddingProviderError(f"No adapter configured for provider {provider}")
