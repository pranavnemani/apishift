"""Typed artifacts passed between steps, plus strict-mode JSON schema export."""
from __future__ import annotations

import copy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- Spec Diff (deterministic) ----------
class RawDiffEntry(Strict):
    id: str
    kind: Literal["OPERATION_REMOVED", "OPERATION_ADDED", "OPERATION_MOVED", "PARAM_REMOVED", "PARAM_ADDED",
                  "HEADER_REQUIRED", "FIELD_REMOVED", "FIELD_ADDED", "FIELD_TYPE_CHANGED", "STATUS_ADDED",
                  "STATUS_REMOVED"]
    operation: str
    detail: str
    old: str | None = None
    new: str | None = None


class RawDiff(Strict):
    api_name: str
    source_version: str
    target_version: str
    entries: list[RawDiffEntry]


# ---------- Scout ----------
Category = Literal["ENDPOINT_RENAME", "PARAM_RENAME", "PARAM_MUTATION", "SCHEMA_RESHAPE", "TYPE_CHANGE",
                   "AUTH_HEADER", "STATUS_CODE", "REMOVAL"]


class Change(Strict):
    id: str
    category: Category
    old_target: str
    new_target: str | None
    description: str
    migration_rule: str
    raw_diff_refs: list[str]


class ChangeManifest(Strict):
    api_name: str
    source_version: str
    target_version: str
    changes: list[Change]


# ---------- Analyst ----------
class ImpactNode(Strict):
    id: str
    file_path: str
    symbol: str
    lines: list[int]
    change_ids: list[str]
    action: str


class ImpactEdge(Strict):
    from_node: str
    to_node: str
    kind: Literal["calls", "produces", "consumes"]
    field: str | None


class ImpactGraph(Strict):
    nodes: list[ImpactNode]
    edges: list[ImpactEdge]
    unresolved: list[str]

    def files(self) -> set[str]:
        return {n.file_path for n in self.nodes}


# ---------- Refactor ----------
class Edit(Strict):
    file_path: str
    search: str
    replace: str
    change_ids: list[str]
    rationale: str


class PatchSet(Strict):
    strategy: Literal["ADAPTER", "INLINE"]
    edits: list[Edit]
    notes: str


# ---------- Triage / Validator ----------
FailureType = Literal["ASSERTION_ERROR", "KEY_ERROR", "ATTRIBUTE_ERROR", "TYPE_ERROR", "VALIDATION_ERROR",
                      "HTTP_STATUS", "IMPORT_ERROR", "SYNTAX_ERROR", "TIMEOUT", "PATCH_APPLY_FAILURE",
                      "GUARDRAIL_VIOLATION", "OTHER"]


class DiagnosticReport(Strict):
    attempt: int
    failed_tests: list[str]
    failure_type: FailureType
    root_cause: str
    route_to: Literal["REFACTOR", "ANALYST"]
    directive: str
    target_files: list[str]
    confidence: float = Field(ge=0, le=1)


# ---------- PR Writer ----------
class PRSummary(Strict):
    title: str
    summary: str
    reviewer_notes: list[str]


# ---------- Sandbox ----------
class TestFailure(Strict):
    __test__ = False
    nodeid: str
    message: str
    traceback: str


class TestReport(Strict):
    __test__ = False  # not a pytest test class
    collected: int
    passed: int
    failed: int
    errors: int
    skipped: int
    duration_s: float
    exit_code: int
    failures: list[TestFailure]
    stdout_tail: str = ""

    @property
    def green(self) -> bool:
        return self.exit_code == 0 and self.failed == 0 and self.errors == 0 and self.collected > 0


# ---------- strict schema export ----------
def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema rewritten for strict structured outputs:
    $refs inlined, every object closed, every property required, no titles/defaults."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                return walk(copy.deepcopy(defs[name]))
            out = {}
            for k, v in node.items():
                if k in ("title", "default"):
                    continue
                if k == "properties" and isinstance(v, dict):
                    # keys here are field names (a field may be called "title"); only the values are schemas
                    out[k] = {name: walk(sub) for name, sub in v.items()}
                    continue
                out[k] = walk(v)
            if out.get("type") == "object" or "properties" in out:
                out["type"] = "object"
                out["additionalProperties"] = False
                out["required"] = list(out.get("properties", {}).keys())
            # strict mode does not accept numeric bounds
            for bound in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
                out.pop(bound, None)
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(raw)
