import json
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.exceptions import ApplicationConfigurationError


class ModelPrice(BaseModel):
    input_per_million_usd: Decimal | None = Field(default=None, ge=0)
    output_per_million_usd: Decimal | None = Field(default=None, ge=0)
    total_per_million_usd: Decimal | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid")


class AIPricingCatalog:
    """Operator-supplied prices; empty by default so costs are never invented."""

    def __init__(self, prices: dict[str, ModelPrice] | None = None) -> None:
        self._prices = prices or {}

    @classmethod
    def from_json(cls, raw: str | None) -> "AIPricingCatalog":
        if not raw or not raw.strip():
            return cls()
        try:
            decoded = json.loads(raw)
            if not isinstance(decoded, dict):
                raise TypeError("pricing config must be an object")
            prices = {
                str(key): ModelPrice.model_validate(value)
                for key, value in decoded.items()
            }
        except (ValueError, TypeError, ValidationError) as exc:
            raise ApplicationConfigurationError(
                "AI_PRICING_JSON is invalid"
            ) from exc
        return cls(prices)

    def estimate(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
    ) -> Decimal | None:
        price = self._prices.get(f"{provider}:{model}")
        if price is None:
            return None
        million = Decimal(1_000_000)
        if price.total_per_million_usd is not None and total_tokens is not None:
            return (Decimal(total_tokens) * price.total_per_million_usd / million).quantize(
                Decimal("0.00000001")
            )
        if (
            price.input_per_million_usd is None
            or price.output_per_million_usd is None
            or input_tokens is None
            or output_tokens is None
        ):
            return None
        return (
            Decimal(input_tokens) * price.input_per_million_usd
            + Decimal(output_tokens) * price.output_per_million_usd
        ).__truediv__(million).quantize(Decimal("0.00000001"))
