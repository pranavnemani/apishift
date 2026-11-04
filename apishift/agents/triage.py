"""Triage (Spark) diagnoses a failed test run; Validator (Wonder) takes over when Triage is unsure."""
from __future__ import annotations

from ..state import ChangeManifest, DiagnosticReport, ImpactGraph, TestReport
from .common import as_json, structured_call

SYSTEM = """You are {role}, part of APIShift, an API migration engine.
A patch was applied and the repository's tests ran against the new API. Diagnose the failure.

- failure_type: the dominant error class (KEY_ERROR, VALIDATION_ERROR, HTTP_STATUS, ...).
- root_cause: one or two sentences naming the file and the line of reasoning.
- route_to ANALYST when the fix belongs in a file or a data-flow link that is NOT in the
  ImpactGraph (a missed consumer or call site); route_to REFACTOR when the right files are in the
  graph but the patch was wrong or incomplete.
- directive: a concrete instruction for that agent. target_files: files that need changes.
- Tests are read-only and correct by definition; never suggest changing them.
- confidence: your honest probability (0-1) that this diagnosis is right."""


def _failures_text(report: TestReport, limit: int = 6) -> str:
    parts = [f"collected={report.collected} passed={report.passed} failed={report.failed} errors={report.errors}"]
    for f in report.failures[:limit]:
        parts.append(f"--- {f.nodeid}\n{f.message}\n{f.traceback[-1500:]}")
    if len(report.failures) > limit:
        parts.append(f"... and {len(report.failures) - limit} more failures")
    return "\n".join(parts)


def run(client, ledger, *, escalate: bool, attempt: int, manifest: ChangeManifest, graph: ImpactGraph,
        report: TestReport, diff_text: str, rejections: list[str], prior: DiagnosticReport | None = None) -> DiagnosticReport:
    step = "validator" if escalate else "triage"
    user = (f"Attempt: {attempt}\n\nChangeManifest:\n{as_json(manifest)}\n\nImpactGraph:\n{as_json(graph)}\n\n"
            f"Diff applied so far:\n{diff_text[-6000:]}\n\nTest results:\n{_failures_text(report)}")
    if rejections:
        user += "\n\nEdits rejected by guardrails:\n" + "\n".join(f"- {r}" for r in rejections)
    if prior is not None:
        user += f"\n\nTriage's first diagnosis (low confidence or repeated failure):\n{as_json(prior)}"
    d = structured_call(client, ledger, step, SYSTEM.format(role="Validator" if escalate else "Triage"), user,
                        DiagnosticReport)
    d.attempt = attempt
    ledger.event(step, "log", f"{d.failure_type} -> {d.route_to} (confidence {d.confidence:.2f}): {d.root_cause}")
    return d
