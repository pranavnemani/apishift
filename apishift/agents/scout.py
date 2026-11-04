"""Scout: classify the deterministic spec diff into breaking changes with migration rules."""
from ..state import ChangeManifest, RawDiff
from .common import as_json, structured_call

SYSTEM = """You are Scout, part of APIShift, an API migration engine.
You receive a deterministic diff between two OpenAPI specs (RawDiff) and the upstream changelog.
Group the raw entries into breaking changes a client codebase must handle.

Rules:
- Every change cites the RawDiff entry ids it comes from in raw_diff_refs. Never invent a change
  that no entry supports; the changelog only helps you interpret entries.
- Pair related entries: a removed query param plus an added one with the same meaning is one
  PARAM_RENAME; a removed field plus an added field at a new path is one SCHEMA_RESHAPE.
- Categories: ENDPOINT_RENAME, PARAM_RENAME, PARAM_MUTATION, SCHEMA_RESHAPE, TYPE_CHANGE,
  AUTH_HEADER (any newly required header), STATUS_CODE, REMOVAL.
- migration_rule is one concrete instruction for Python client code, e.g.
  "read payload['wind']['speed_mph'] where code read payload['wind_mph']".
- Ids are c1, c2, ... in order. new_target is null only for removals.
- Additive, non-breaking entries (an optional new field) are not changes unless code must react."""


def run(client, ledger, raw: RawDiff, changelog: str) -> ChangeManifest:
    user = f"RawDiff:\n{as_json(raw)}\n\nChangelog:\n{changelog or '(none provided)'}"
    manifest = structured_call(client, ledger, "scout", SYSTEM, user, ChangeManifest)
    known = {e.id for e in raw.entries}
    cited = set()
    for c in manifest.changes:
        bad = [r for r in c.raw_diff_refs if r not in known]
        if bad:
            ledger.event("scout", "warning", f"{c.id} cites unknown diff entries {bad}; dropped")
            c.raw_diff_refs = [r for r in c.raw_diff_refs if r in known]
        cited.update(c.raw_diff_refs)
    uncovered = sorted(known - cited, key=lambda x: int(x[1:]))
    if uncovered:
        ledger.event("scout", "log", f"diff entries not mapped to a change (treated as non-breaking): {uncovered}")
    return manifest
