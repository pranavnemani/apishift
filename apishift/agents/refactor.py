"""Refactor: Muse Wonder writes search/replace edit blocks for the files in the ImpactGraph."""
from __future__ import annotations

from pathlib import Path

from ..state import ChangeManifest, DiagnosticReport, ImpactGraph, PatchSet
from .common import as_json, structured_call

SYSTEM = """You are Refactor, part of APIShift, an API migration engine. You write the code changes
that migrate a Python client from the old API version to the new one.

Output a PatchSet of search/replace edits:
- search is copied exactly from the CURRENT file text shown to you and must occur exactly once in
  that file; include enough surrounding lines to make it unique. replace is the new text.
- Only edit files listed in the ImpactGraph. Tests, conftest.py and pytest.ini are read-only: the
  existing tests define correct behavior and must pass unchanged. Never add skip or xfail markers.
- Prefer strategy ADAPTER: keep the app's internal models, function signatures and dict shapes
  unchanged, and translate the new API's requests and responses at the boundary (the client), so
  downstream code keeps working. Use INLINE only when an adapter is impossible.
- Make every change in the manifest; keep everything else byte-for-byte.
- If a previous attempt failed, the diagnosis tells you why; fix that root cause."""


def _file_block(root: Path, rel: str) -> str:
    p = root / rel
    body = p.read_text() if p.exists() else "(file does not exist)"
    return f"=== {rel} ===\n{body}"


def run(client, ledger, root: Path, manifest: ChangeManifest, graph: ImpactGraph,
        diagnosis: DiagnosticReport | None, history: list[str], test_files: list[str]) -> PatchSet:
    static = (f"ChangeManifest:\n{as_json(manifest)}\n\n"
              "Tests (read-only, for reference):\n" + "\n\n".join(_file_block(root, t) for t in test_files))
    dynamic = (f"\n\nImpactGraph:\n{as_json(graph)}\n\nCurrent text of files you may edit:\n"
               + "\n\n".join(_file_block(root, f) for f in sorted(graph.files())))
    if history:
        dynamic += "\n\nWhat happened on earlier attempts:\n" + "\n".join(f"- {h}" for h in history)
    if diagnosis is not None:
        dynamic += f"\n\nDiagnosis of the last failure:\n{as_json(diagnosis)}"
    patch = structured_call(client, ledger, "refactor", SYSTEM, static + dynamic, PatchSet)
    ledger.event("refactor", "log", f"{len(patch.edits)} edits ({patch.strategy})",
                 files=sorted({e.file_path for e in patch.edits}))
    return patch
