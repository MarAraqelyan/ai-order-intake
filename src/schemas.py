"""
Pydantic models for everything that crosses the model boundary.

  SearchCatalogArgs  - arguments the model passes to the search_catalog tool
  DraftLine          - one proposed order line in the model's final answer
  DraftOutput        - the model's final answer

The same shape is also written as a plain JSON schema (OUTPUT_JSON_SCHEMA) and sent
to the API as a strict response_format, so the model is constrained to it. Pydantic
then re-validates the answer locally: correct JSON is required, but correct JSON
alone is never treated as a correct order (see src/validation.py for fact checks).
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator


class SearchCatalogArgs(BaseModel):
    """Arguments for the search_catalog tool. Unknown keys are rejected."""
    model_config = ConfigDict(extra="forbid")

    query: StrictStr = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def query_not_blank(self):
        if not self.query.strip():
            raise ValueError("query must not be blank")
        return self


class DraftLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Optional[StrictStr]
    # StrictInt rejects booleans, floats, and numeric strings. ge=1 rejects 0 and negatives.
    quantity: Optional[StrictInt] = Field(ge=1)
    source_quote: StrictStr
    is_unresolved: StrictBool
    clarification_reason: Optional[StrictStr]

    @model_validator(mode="after")
    def resolved_lines_are_complete(self):
        if not self.is_unresolved:
            if self.sku is None or self.quantity is None:
                raise ValueError("a resolved line (is_unresolved=false) needs both sku and quantity")
            if not self.source_quote.strip():
                raise ValueError("a resolved line needs a non-empty source_quote")
        return self


class DraftOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["draft", "needs-clarification"]
    lines: list[DraftLine] = Field(min_length=1)
    clarification_message: Optional[StrictStr]


# Strict JSON schema sent as response_format. Kept by hand so it is easy to read;
# tests/test_schemas.py checks that it lists the same fields as the Pydantic models.
OUTPUT_JSON_SCHEMA = {
    "name": "order_draft",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "lines", "clarification_message"],
        "properties": {
            "status": {"type": "string", "enum": ["draft", "needs-clarification"]},
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["sku", "quantity", "source_quote", "is_unresolved",
                                 "clarification_reason"],
                    "properties": {
                        "sku": {"type": ["string", "null"]},
                        "quantity": {"type": ["integer", "null"]},
                        "source_quote": {"type": "string"},
                        "is_unresolved": {"type": "boolean"},
                        "clarification_reason": {"type": ["string", "null"]},
                    },
                },
            },
            "clarification_message": {"type": ["string", "null"]},
        },
    },
}
