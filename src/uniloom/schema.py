"""
MCP tool schemas made into ones Gemini takes. Gemini's function declarations accept a
subset of JSON Schema (an OpenAPI 3.0 style object): no $ref, no type lists, no
additionalProperties, and anyOf only in narrow cases. Uniloom's schemas come from zod and
use all of those, so each one is rewritten to the plain shape here. What can't be said
in the schema, such as "null clears it", goes into the field's description.
"""
from typing import Any, Dict, Optional

# Only type, description, enum, items, properties, required and these formats are kept.
# Limits, patterns, defaults and titles are dropped: Uniloom checks them anyway and says what was wrong
_FORMATS = {"date-time", "enum"}
_MAX_DEPTH = 12


def _resolve(node: Any, root: dict, depth: int) -> Any:
    """Writes out a local $ref such as #/$defs/Key. Unknown or remote refs become a plain string."""
    if not isinstance(node, dict) or "$ref" not in node or depth > _MAX_DEPTH:
        return node
    target: Any = root
    for part in str(node["$ref"]).lstrip("#/").split("/"):
        target = target.get(part) if isinstance(target, dict) else None
    if not isinstance(target, dict):
        return {"type": "string"}
    merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
    return _resolve(merged, root, depth + 1)


def _with_note(node: dict, note: str) -> dict:
    description = node.get("description", "")
    node["description"] = f"{description} ({note})" if description else note
    return node


def _convert(node: Any, root: dict, depth: int) -> Dict[str, Any]:
    node = _resolve(node, root, depth)
    if not isinstance(node, dict) or depth > _MAX_DEPTH:
        return {"type": "string"}
    note = ""

    choices = node.get("anyOf") or node.get("oneOf")
    if isinstance(choices, list) and choices:
        variants = [_resolve(c, root, depth) for c in choices]
        real = [v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")]
        if len(real) < len(variants):
            note = "null clears it"
        chosen = real[0] if real else {"type": "string"}
        others = sorted({str(v.get("type")) for v in real[1:] if isinstance(v, dict) and v.get("type")}
                        - {str(chosen.get("type"))})
        if others:
            note = "; ".join(filter(None, [note, f"can also be {' or '.join(others)}"]))
        outer = {k: v for k, v in node.items() if k not in ("anyOf", "oneOf")}
        node = {**chosen, **outer}
        if "description" not in outer and "description" in chosen:
            node["description"] = chosen["description"]

    if "const" in node:
        value = node["const"]
        node = {**node, "enum": [value]}
        node.setdefault("type", "string" if isinstance(value, str) else "number")

    kind = node.get("type")
    if isinstance(kind, list):
        real_types = [t for t in kind if t != "null"]
        if len(real_types) < len(kind):
            note = note or "null clears it"
        kind = real_types[0] if real_types else "string"
    if not kind:
        kind = "object" if "properties" in node else "array" if "items" in node else "string"

    out: Dict[str, Any] = {"type": kind}
    if isinstance(node.get("description"), str) and node["description"]:
        out["description"] = node["description"]
    if isinstance(node.get("enum"), list) and node["enum"]:
        out["enum"] = [str(v) for v in node["enum"]] if kind == "string" else node["enum"]
    if node.get("format") in _FORMATS and kind == "string":
        out["format"] = node["format"]
    if kind == "array":
        out["items"] = _convert(node.get("items", {"type": "string"}), root, depth + 1)
    if kind == "object":
        found = node.get("properties")
        properties: Dict[str, Any] = found if isinstance(found, dict) else {}
        out["properties"] = {name: _convert(value, root, depth + 1) for name, value in properties.items()}
        required = [name for name in node.get("required") or [] if name in out["properties"]]
        if required:
            out["required"] = required
    if note:
        _with_note(out, note)
    return out


def for_gemini(schema: Optional[dict]) -> Dict[str, Any]:
    """The tool's input schema as Gemini accepts it, always an object at the top."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    converted = _convert(schema, schema, 0)
    if converted.get("type") != "object":
        return {"type": "object", "properties": {}}
    converted.pop("description", None)
    converted.setdefault("properties", {})
    return converted


__all__ = ["for_gemini"]
