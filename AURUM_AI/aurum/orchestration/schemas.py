"""A small, strict JSON Schema subset for workflow input and output contracts (no third-party validator is installed).

Supported keywords: type (object, array, string, number, integer, boolean, null, or a list of these), properties,
required, additionalProperties (bool), items, enum, const, minLength, maxLength, pattern, minimum, maximum, minItems,
maxItems, description, title, default, format (recorded, not enforced). Anything else is refused when the schema is
registered, so a schema never silently means less than it says.
"""
from __future__ import annotations

import re
from typing import Any

_ALLOWED = {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "minLength", "maxLength",
            "pattern", "minimum", "maximum", "minItems", "maxItems", "description", "title", "default", "format", "$schema",
            "$id", "examples"}
_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}
MAX_DEPTH = 12


class SchemaError(ValueError):
    pass


def check_schema(schema: Any, path: str = "$", depth: int = 0) -> None:
    """Raises SchemaError if ``schema`` uses anything outside the supported subset."""
    if depth > MAX_DEPTH:
        raise SchemaError(f"{path}: schema nested too deeply")
    if not isinstance(schema, dict):
        raise SchemaError(f"{path}: a schema must be an object")
    unknown = set(schema) - _ALLOWED
    if unknown:
        raise SchemaError(f"{path}: unsupported keywords {sorted(unknown)}")
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not types or any(x not in _TYPES for x in types):
            raise SchemaError(f"{path}: unknown type {t!r}")
    props = schema.get("properties")
    if props is not None:
        if not isinstance(props, dict):
            raise SchemaError(f"{path}.properties must be an object")
        for k, v in props.items():
            check_schema(v, f"{path}.{k}", depth + 1)
    req = schema.get("required")
    if req is not None and (not isinstance(req, list) or not all(isinstance(x, str) for x in req)):
        raise SchemaError(f"{path}.required must be a list of names")
    if "items" in schema:
        check_schema(schema["items"], f"{path}[]", depth + 1)
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        raise SchemaError(f"{path}.additionalProperties must be true or false")
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except re.error as exc:
            raise SchemaError(f"{path}.pattern is not a valid regular expression: {exc}") from None


def _type_ok(value: Any, t: str) -> bool:
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    return value is None


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[dict[str, str]]:
    """Returns a list of problems ({path, problem}); empty means valid."""
    out: list[dict[str, str]] = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_type_ok(value, x) for x in types):
            return [{"path": path, "problem": f"must be {' or '.join(types)}"}]
    if "const" in schema and value != schema["const"]:
        out.append({"path": path, "problem": f"must equal {schema['const']!r}"})
    if "enum" in schema and value not in schema["enum"]:
        out.append({"path": path, "problem": f"must be one of {schema['enum']}"})
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append({"path": path, "problem": f"shorter than {schema['minLength']}"})
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append({"path": path, "problem": f"longer than {schema['maxLength']}"})
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append({"path": path, "problem": "does not match the pattern"})
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append({"path": path, "problem": f"below {schema['minimum']}"})
        if "maximum" in schema and value > schema["maximum"]:
            out.append({"path": path, "problem": f"above {schema['maximum']}"})
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append({"path": path, "problem": f"fewer than {schema['minItems']} items"})
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append({"path": path, "problem": f"more than {schema['maxItems']} items"})
        if "items" in schema:
            for i, v in enumerate(value):
                out.extend(validate(v, schema["items"], f"{path}[{i}]"))
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                out.append({"path": f"{path}.{name}", "problem": "required"})
        for k, v in value.items():
            if k in props:
                out.extend(validate(v, props[k], f"{path}.{k}"))
            elif schema.get("additionalProperties") is False:
                out.append({"path": f"{path}.{k}", "problem": "not allowed"})
    return out
