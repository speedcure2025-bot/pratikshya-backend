from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ProductInteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    product_id: str = Field(alias="productId", min_length=1, max_length=36, pattern=r"^[A-Za-z0-9_-]+$")
    event_type: Literal["VIEW", "CLICK"] = Field(alias="eventType")
    idempotency_key: UUID | None = Field(default=None, alias="idempotencyKey")
