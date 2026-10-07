"""The strict JSON schema sent to the API must describe the same fields as the Pydantic models."""
from src.schemas import OUTPUT_JSON_SCHEMA, DraftLine, DraftOutput


def test_top_level_fields_match():
    schema = OUTPUT_JSON_SCHEMA["schema"]
    assert set(schema["properties"]) == set(DraftOutput.model_fields)
    assert set(schema["required"]) == set(DraftOutput.model_fields)


def test_line_fields_match():
    items = OUTPUT_JSON_SCHEMA["schema"]["properties"]["lines"]["items"]
    assert set(items["properties"]) == set(DraftLine.model_fields)
    assert set(items["required"]) == set(DraftLine.model_fields)
    assert items["additionalProperties"] is False
