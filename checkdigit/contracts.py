"""
contracts.py
============
Loads the shared contract data under ../contracts (enumerations, finding codes,
entity schema, examples) so the Python service uses the same values as the
TypeScript site. A small validator covers the JSON Schema subset those files
use; tests on both sides validate the same examples.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

CONTRACTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "contracts")


def _load(name: str) -> Any:
    with open(os.path.join(CONTRACTS_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


ENUMS: Dict[str, Any] = _load("enums.json")
FINDINGS: List[Dict[str, Any]] = _load("findings.json")["findings"]
SCHEMA: Dict[str, Any] = _load("entities.schema.json")
EXAMPLES: Dict[str, Any] = _load("examples.json")

_BY_CODE = {f["code"]: f for f in FINDINGS}


def finding(code: str) -> Dict[str, Any]:
    if code not in _BY_CODE:
        raise KeyError(f"unknown finding code {code!r}")
    return dict(_BY_CODE[code])


def make_finding(code: str, *, id: str, location: Dict[str, Any] | None = None,
                 detail: str = "", rule_id: str = "", rule_version: str = "") -> Dict[str, Any]:
    """A finding document (entities.schema.json#/$defs/finding) for a known code."""
    base = finding(code)
    out = {"id": id, "code": code, "severity": base["severity"],
           "blocking_scope": base["blocking_scope"],
           "explanation": base["explanation"] + (f" {detail}" if detail else ""),
           "recovery": base["recovery"]}
    if location is not None:
        out["location"] = location
    if rule_id:
        out["rule_id"] = rule_id
    if rule_version:
        out["rule_version"] = rule_version
    return out


def is_enum_value(name: str, value: Any) -> bool:
    values = ENUMS.get(name)
    return isinstance(values, list) and value in values


def _matches_type(t: str, v: Any) -> bool:
    return {
        "object": lambda: isinstance(v, dict),
        "array": lambda: isinstance(v, list),
        "string": lambda: isinstance(v, str),
        "integer": lambda: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda: isinstance(v, bool),
        "null": lambda: v is None,
    }.get(t, lambda: False)()


def validate(node: Dict[str, Any], value: Any, path: str = "$") -> List[str]:
    """Return every violation of `node` (a schema fragment) by `value`."""
    errors: List[str] = []
    ref = node.get("$ref")
    if isinstance(ref, str):
        m = re.match(r"^#/\$defs/(.+)$", ref)
        if not m:
            return [f"{path}: unsupported $ref {ref}"]
        target = SCHEMA["$defs"].get(m.group(1))
        if target is None:
            return [f"{path}: missing $def {m.group(1)}"]
        return validate(target, value, path)

    types = node.get("type")
    if types is not None:
        allowed = types if isinstance(types, list) else [types]
        if not any(_matches_type(t, value) for t in allowed):
            return [f"{path}: expected {'|'.join(allowed)}, got {type(value).__name__}"]
    if "const" in node and value != node["const"]:
        errors.append(f"{path}: must equal {node['const']!r}")
    if "enum" in node and value not in node["enum"]:
        errors.append(f"{path}: {value!r} not in enum")
    x_enum = node.get("x-enum")
    if isinstance(x_enum, str):
        if not isinstance(ENUMS.get(x_enum), list):
            errors.append(f"{path}: x-enum {x_enum} is not defined in enums.json")
        elif value not in ENUMS[x_enum]:
            errors.append(f"{path}: {value!r} not in enum {x_enum}")
    if isinstance(value, str):
        if "pattern" in node and not re.search(node["pattern"], value):
            errors.append(f"{path}: does not match {node['pattern']}")
        if "minLength" in node and len(value) < node["minLength"]:
            errors.append(f"{path}: shorter than {node['minLength']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in node and value < node["minimum"]:
            errors.append(f"{path}: below minimum {node['minimum']}")
    if isinstance(value, list) and "items" in node:
        for i, item in enumerate(value):
            errors.extend(validate(node["items"], item, f"{path}[{i}]"))
    if isinstance(value, dict):
        for key in node.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required {key}")
        props = node.get("properties", {})
        for key, sub in props.items():
            if key in value:
                errors.extend(validate(sub, value[key], f"{path}.{key}"))
        extra = node.get("additionalProperties")
        if extra is False:
            for key in value:
                if key not in props:
                    errors.append(f"{path}: unexpected property {key}")
        elif isinstance(extra, dict):
            for key, v in value.items():
                if key not in props:
                    errors.extend(validate(extra, v, f"{path}.{key}"))
    return errors


def validate_entity(name: str, document: Any) -> List[str]:
    node = SCHEMA["$defs"].get(name)
    if node is None:
        raise KeyError(f"no entity {name!r} in entities.schema.json")
    return validate(node, document)
