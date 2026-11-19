"""MCP tool schemas made into ones Gemini takes (docs/uniloom-plan.md, section 5)."""
from src.uniloom.schema import for_gemini


def test_a_nullable_field_keeps_its_type_and_says_null_clears_it():
    schema = for_gemini({"type": "object", "properties": {
        "sourceProposalId": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "A proposal"},
    }})
    field = schema["properties"]["sourceProposalId"]
    assert field["type"] == "string" and "anyOf" not in field
    assert "null" in field["description"]


def test_a_type_list_with_null_becomes_one_type():
    schema = for_gemini({"type": "object", "properties": {"parent": {"type": ["string", "null"]}}})
    assert schema["properties"]["parent"]["type"] == "string"


def test_what_gemini_refuses_is_taken_out():
    schema = for_gemini({
        "$schema": "http://json-schema.org/draft-07/schema#", "type": "object", "additionalProperties": False,
        "title": "save_itemArguments",
        "properties": {"title": {"type": "string", "minLength": 1, "maxLength": 200, "default": "x",
                                 "title": "Title", "pattern": "^.+$"}},
        "required": ["title"],
    })
    assert schema == {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}


def test_enums_arrays_and_nested_objects_are_kept():
    schema = for_gemini({"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["FEATURE", "SLICE"]},
        "blockedBy": {"type": "array", "items": {"type": "string"}},
        "entry": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    }})
    assert schema["properties"]["kind"]["enum"] == ["FEATURE", "SLICE"]
    assert schema["properties"]["blockedBy"]["items"] == {"type": "string"}
    assert schema["properties"]["entry"]["required"] == ["text"]


def test_a_const_becomes_a_one_value_enum():
    schema = for_gemini({"type": "object", "properties": {"kind": {"const": "SPEC"}}})
    assert schema["properties"]["kind"] == {"type": "string", "enum": ["SPEC"]}


def test_references_are_written_out():
    schema = for_gemini({
        "type": "object", "$defs": {"Key": {"type": "string", "description": "Item key"}},
        "properties": {"key": {"$ref": "#/$defs/Key"}},
    })
    assert schema["properties"]["key"] == {"type": "string", "description": "Item key"}


def test_a_choice_of_different_types_takes_the_first_and_says_so():
    schema = for_gemini({"type": "object", "properties": {
        "criterion": {"anyOf": [{"type": "integer"}, {"type": "string"}], "description": "Number or text"},
    }})
    field = schema["properties"]["criterion"]
    assert field["type"] == "integer" and "string" in field["description"]


def test_a_tool_with_no_input_gets_an_empty_object():
    assert for_gemini(None) == {"type": "object", "properties": {}}
    assert for_gemini({"type": "object"}) == {"type": "object", "properties": {}}


def test_required_only_names_fields_that_exist():
    schema = for_gemini({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a", "b"]})
    assert schema["required"] == ["a"]
