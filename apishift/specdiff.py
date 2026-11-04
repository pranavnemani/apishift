"""Deterministic OpenAPI v1 -> v2 diff. No LLM: every change the Scout classifies traces back here."""
from __future__ import annotations

from pathlib import Path

import yaml

from .state import RawDiff, RawDiffEntry

METHODS = ("get", "post", "put", "patch", "delete")


def _resolve(node, root):
    if isinstance(node, dict) and "$ref" in node:
        cur = root
        for part in node["$ref"].lstrip("#/").split("/"):
            cur = cur[part]
        return _resolve(cur, root)
    return node


def _operations(spec: dict) -> dict[str, dict]:
    ops = {}
    for path, item in (spec.get("paths") or {}).items():
        shared = item.get("parameters", [])
        for m in METHODS:
            if m in item:
                op = item[m]
                key = op.get("operationId") or f"{m.upper()} {path}"
                ops[key] = {"method": m.upper(), "path": path, "op": op,
                            "params": [_resolve(p, spec) for p in shared + op.get("parameters", [])]}
    return ops


def _flatten(schema: dict, root: dict, prefix: str = "") -> dict[str, str]:
    schema = _resolve(schema or {}, root)
    out = {}
    if schema.get("type") == "object" or "properties" in schema:
        for name, sub in (schema.get("properties") or {}).items():
            path = f"{prefix}.{name}" if prefix else name
            sub = _resolve(sub, root)
            out[path] = sub.get("type", "object")
            out.update(_flatten(sub, root, path))
    elif schema.get("type") == "array":
        out.update(_flatten(schema.get("items", {}), root, f"{prefix}[]"))
    return out


def _success_schema(op: dict, root: dict) -> dict:
    for code in ("200", "201"):
        resp = _resolve((op.get("responses") or {}).get(code, {}), root)
        content = (resp.get("content") or {}).get("application/json") or {}
        if content.get("schema"):
            return content["schema"]
    return {}


def diff_specs(v1_path: str | Path, v2_path: str | Path) -> RawDiff:
    v1 = yaml.safe_load(Path(v1_path).read_text())
    v2 = yaml.safe_load(Path(v2_path).read_text())
    o1, o2 = _operations(v1), _operations(v2)
    entries: list[RawDiffEntry] = []

    def add(kind, operation, detail, old=None, new=None):
        entries.append(RawDiffEntry(id=f"d{len(entries) + 1}", kind=kind, operation=operation,
                                    detail=detail, old=old, new=new))

    for key in sorted(set(o1) - set(o2)):
        a = o1[key]
        add("OPERATION_REMOVED", key, f"{a['method']} {a['path']} has no v2 counterpart",
            old=f"{a['method']} {a['path']}")
    for key in sorted(set(o2) - set(o1)):
        b = o2[key]
        add("OPERATION_ADDED", key, f"{b['method']} {b['path']} is new in v2", new=f"{b['method']} {b['path']}")

    for key in sorted(set(o1) & set(o2)):
        a, b = o1[key], o2[key]
        if (a["method"], a["path"]) != (b["method"], b["path"]):
            add("OPERATION_MOVED", key, "operation moved to a new method/path",
                old=f"{a['method']} {a['path']}", new=f"{b['method']} {b['path']}")

        p1 = {(p["in"], p["name"]): p for p in a["params"]}
        p2 = {(p["in"], p["name"]): p for p in b["params"]}
        for k in sorted(set(p1) - set(p2)):
            add("PARAM_REMOVED", key, f"{k[0]} parameter '{k[1]}' removed", old=k[1])
        for k in sorted(set(p2) - set(p1)):
            p = p2[k]
            if k[0] == "header" and p.get("required"):
                enum = (_resolve(p.get("schema", {}), v2).get("enum") or [None])[0]
                add("HEADER_REQUIRED", key, f"header '{k[1]}' is now required"
                    + (f" with value '{enum}'" if enum is not None else ""), new=f"{k[1]}: {enum}" if enum else k[1])
            else:
                add("PARAM_ADDED", key, f"{k[0]} parameter '{k[1]}' added"
                    + (" (required)" if p.get("required") else ""), new=k[1])

        f1 = _flatten(_success_schema(a["op"], v1), v1)
        f2 = _flatten(_success_schema(b["op"], v2), v2)
        for f in sorted(set(f1) - set(f2)):
            add("FIELD_REMOVED", key, f"response field '{f}' removed", old=f)
        for f in sorted(set(f2) - set(f1)):
            add("FIELD_ADDED", key, f"response field '{f}' added ({f2[f]})", new=f)
        for f in sorted(set(f1) & set(f2)):
            if f1[f] != f2[f]:
                add("FIELD_TYPE_CHANGED", key, f"response field '{f}' changed type", old=f1[f], new=f2[f])

        s1, s2 = set((a["op"].get("responses") or {})), set((b["op"].get("responses") or {}))
        for s in sorted(s2 - s1):
            add("STATUS_ADDED", key, f"status {s} can now be returned", new=s)
        for s in sorted(s1 - s2):
            add("STATUS_REMOVED", key, f"status {s} is no longer returned", old=s)

    return RawDiff(api_name=v2.get("info", {}).get("title", "API"),
                   source_version=str(v1.get("info", {}).get("version", "v1")),
                   target_version=str(v2.get("info", {}).get("version", "v2")),
                   entries=entries)
