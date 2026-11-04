"""The state machine: Spec Diff -> Scout -> Analyst -> Refactor -> guardrails -> sandbox -> heal loop -> PR.

The orchestrator is the only component with state. Agents return typed artifacts; guardrails and
the test oracle decide what counts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .agents import analyst, pr_writer, refactor, scout, triage, validator
from .ledger import Ledger
from .specdiff import diff_specs
from .state import DiagnosticReport, ImpactGraph, PatchSet, TestReport
from .tools import git_tools, guardrails, sandbox
from .tools.repo_tools import RepoTools


@dataclass
class RunConfig:
    repo: Path
    spec_from: Path
    spec_to: Path
    mock_server: Path
    changelog: Path | None = None
    backend: str = "docker"           # docker | local
    max_heals: int = config.MAX_HEAL_ATTEMPTS
    open_pr: bool = False


@dataclass
class RunResult:
    status: str                       # READY | NEEDS_HUMAN | FAILED
    attempts: int
    before: TestReport | None = None
    after: TestReport | None = None
    pr_url: str | None = None
    notes: list[str] = field(default_factory=list)


def _signature(r: TestReport) -> str:
    return "|".join(sorted(f.nodeid for f in r.failures))


def _local_diagnosis(attempt: int, kind: str, detail: str) -> DiagnosticReport:
    return DiagnosticReport(attempt=attempt, failed_tests=[], failure_type=kind, root_cause=detail,
                            route_to="ANALYST" if "not in the impact graph" in detail else "REFACTOR",
                            directive=detail, target_files=[], confidence=1.0)


class Orchestrator:
    def __init__(self, cfg: RunConfig, ledger: Ledger, client):
        self.cfg, self.ledger, self.client = cfg, ledger, client
        self.blocked_test_edits = 0

    def stage(self, name: str, status: str, message: str = "", **data):
        self.ledger.event(name, "stage", message, status=status, **data)

    def tests(self, ws: Path, version: str, label: str) -> TestReport:
        self.stage("sandbox", "running", f"pytest against {version} mock ({label})")
        r = sandbox.run_tests(ws, self.cfg.mock_server, version, self.cfg.backend)
        self.ledger.event("sandbox", "tests", f"{label}: {r.passed}/{r.collected} passed",
                          label=label, version=version, report=r.model_dump())
        return r

    def run(self) -> RunResult:
        cfg, L = self.cfg, self.ledger
        L.event("run", "start", f"migrate {cfg.repo}", repo=str(cfg.repo), backend=cfg.backend)
        ws = git_tools.prepare_workspace(cfg.repo, L.run_dir)
        repo = RepoTools(ws)
        fp0 = guardrails.tests_fingerprint(ws)
        test_files = [str(p.relative_to(ws)) for p in sorted(ws.rglob("*.py"))
                      if guardrails.is_protected(str(p.relative_to(ws)))]

        # 0. establish the oracle: tests pass on the old API and fail on the new one
        on_old = self.tests(ws, "v1", "baseline on old API")
        if not on_old.green:
            L.event("sandbox", "warning", "tests do not pass on the old API; results will be weaker evidence")
        before = self.tests(ws, "v2", "before migration")
        self.stage("sandbox", "done")
        if before.green:
            L.event("run", "end", "tests already pass against the new API; nothing to migrate", status="READY")
            return RunResult("READY", 0, before, before, notes=["nothing to migrate"])

        # 1. deterministic diff
        self.stage("specdiff", "running")
        raw = diff_specs(cfg.spec_from, cfg.spec_to)
        L.artifact("raw_diff.json", raw)
        self.stage("specdiff", "done", f"{len(raw.entries)} raw differences", count=len(raw.entries))

        # 2. scout
        self.stage("scout", "running")
        changelog = cfg.changelog.read_text() if cfg.changelog and cfg.changelog.exists() else ""
        manifest = scout.run(self.client, L, raw, changelog)
        L.artifact("change_manifest.json", manifest)
        self.stage("scout", "done", f"{len(manifest.changes)} breaking changes",
                   changes=[c.model_dump() for c in manifest.changes])

        # 3. analyst
        self.stage("analyst", "running")
        graph: ImpactGraph = analyst.run(self.client, L, repo, manifest)
        L.artifact("impact_graph_1.json", graph)
        self.stage("analyst", "done", f"{len(graph.files())} files affected", graph=graph.model_dump())

        # 4. patch / test / heal
        diagnosis: DiagnosticReport | None = None
        history: list[str] = []
        prev_sig, after, green, attempt = None, before, False, 0
        rejections_all: list[str] = []
        guard_log: dict[str, bool] = {"tests unchanged": True, "no skip markers": True,
                                      "edits inside impact graph": True, "test count unchanged": True}
        while attempt <= cfg.max_heals:
            attempt += 1
            self.stage("refactor", "running", f"attempt {attempt}", attempt=attempt)
            patch: PatchSet = refactor.run(self.client, L, ws, manifest, graph, diagnosis, history, test_files)
            L.artifact(f"patchset_{attempt}.json", patch)
            res = guardrails.apply_patch(patch, graph, ws)
            rejections = [f"{e.file_path}: {why}" for e, why in res.rejected]
            rejections_all += rejections
            for e, why in res.rejected:
                if "read-only" in why:
                    self.blocked_test_edits += 1  # attempted and blocked; tests stay unchanged
                if "skip" in why:
                    guard_log["no skip markers"] = False
                if "impact graph" in why:
                    guard_log["edits inside impact graph"] = False
                L.event("guardrails", "rejected", f"{e.file_path}: {why}", file=e.file_path, reason=why)
            self.stage("refactor", "done", f"{len(res.applied)} edits applied, {len(res.rejected)} rejected")
            L.event("refactor", "diff", "workspace diff", diff=git_tools.diff(ws))

            if res.apply_errors or not res.applied:
                detail = "; ".join(res.apply_errors or rejections or ["patch contained no applicable edits"])
                kind = "PATCH_APPLY_FAILURE" if res.apply_errors else "GUARDRAIL_VIOLATION"
                diagnosis = _local_diagnosis(attempt, kind, detail)
                history.append(f"attempt {attempt}: {kind}: {detail[:300]}")
                L.event("guardrails", "log", f"{kind}: {detail[:300]}")
                if diagnosis.route_to == "ANALYST":
                    graph = self._reanalyze(repo, manifest, graph, diagnosis, attempt)
                continue

            after = self.tests(ws, "v2", f"attempt {attempt}")
            if guardrails.tests_fingerprint(ws) != fp0:
                guard_log["tests unchanged"] = False
                L.event("guardrails", "violation", "protected test files changed during the run")
                break
            # judged per attempt: a broken early patch (0 collected) must not block a later, correct one
            count_ok = not (after.collected != on_old.collected and on_old.collected)
            guard_log["test count unchanged"] = count_ok
            if not count_ok:
                L.event("guardrails", "violation", f"collected {after.collected} tests, expected {on_old.collected}")
            if after.green and guard_log["test count unchanged"]:
                green = True
                self.stage("sandbox", "done", f"all {after.collected} tests pass")
                break
            self.stage("sandbox", "failed", f"{after.failed + after.errors} failing")
            if attempt > cfg.max_heals:
                break

            sig = _signature(after)
            self.stage("triage", "running", f"heal {attempt}/{cfg.max_heals}")
            diff_text = git_tools.diff(ws)
            d = triage.run(self.client, L, escalate=False, attempt=attempt, manifest=manifest, graph=graph,
                           report=after, diff_text=diff_text, rejections=rejections)
            if d.confidence < config.TRIAGE_ESCALATION_CONFIDENCE or sig == prev_sig:
                L.event("triage", "log", "escalating to Validator (Wonder)")
                d = validator.run(self.client, L, attempt=attempt, manifest=manifest, graph=graph,
                                  report=after, diff_text=diff_text, rejections=rejections, prior=d)
            L.artifact(f"diagnosis_{attempt}.json", d)
            self.stage("triage", "done", f"{d.failure_type} -> {d.route_to}", diagnosis=d.model_dump())
            history.append(f"attempt {attempt}: {after.failed + after.errors} failing; {d.failure_type}: {d.root_cause}")
            diagnosis, prev_sig = d, sig
            if d.route_to == "ANALYST":
                graph = self._reanalyze(repo, manifest, graph, d, attempt)

        status = "READY" if green else "NEEDS_HUMAN"
        result = RunResult(status, attempt, before, after)
        self._deliver(result, manifest, graph, guard_log, rejections_all)
        L.event("run", "end", f"{status} after {attempt} attempt(s)", status=status, attempts=attempt,
                totals=L.totals, pr_url=result.pr_url)
        return result

    def _reanalyze(self, repo, manifest, graph, diagnosis, attempt) -> ImpactGraph:
        self.stage("analyst", "running", "extending the impact graph")
        new = analyst.run(self.client, self.ledger, repo, manifest, previous=graph, diagnosis=diagnosis)
        added = sorted(new.files() - graph.files())
        self.ledger.artifact(f"impact_graph_{attempt + 1}.json", new)
        self.stage("analyst", "done", f"graph now covers {len(new.files())} files"
                   + (f" (added {', '.join(added)})" if added else ""), graph=new.model_dump(), added=added)
        return new

    def _deliver(self, result: RunResult, manifest, graph, guard_log, rejections):
        L, ws = self.ledger, self.ledger.run_dir / "workspace"
        self.stage("pr_writer", "running")
        diff_text = git_tools.diff(ws)
        L.artifact("final.diff", diff_text)
        green = result.status == "READY"
        summary = pr_writer.run(self.client, L, manifest, graph, diff_text, result.before, result.after, green)
        rows = [("Tests and test config read-only", guard_log["tests unchanged"],
                 ("fingerprint unchanged" + (f"; {self.blocked_test_edits} attempted test edit(s) blocked"
                                             if self.blocked_test_edits else ""))
                 if guard_log["tests unchanged"] else "protected files changed during the run"),
                ("No skip/xfail markers added", guard_log["no skip markers"], "checked on every edit"),
                ("Edits limited to the impact graph", guard_log["edits inside impact graph"],
                 f"{len(rejections)} edit(s) rejected" if rejections else "all edits in scope"),
                ("Same tests collected as on the old API", guard_log["test count unchanged"],
                 f"{result.after.collected} collected")]
        body = pr_writer.render_body(summary, manifest, graph, diff_text, result.before, result.after, rows,
                                     L.totals, result.attempts, green)
        body_path = L.artifact("PR.md", body)
        self.stage("pr_writer", "done", summary.title, title=summary.title, draft=not green)
        if self.cfg.open_pr:
            try:
                result.pr_url = git_tools.open_pr(self.cfg.repo, diff_text, f"apishift/{L.run_dir.name}",
                                                  summary.title, body_path, draft=not green)
                L.event("pr_writer", "log", f"opened {result.pr_url}")
            except Exception as e:
                result.notes.append(str(e))
                L.event("pr_writer", "warning", f"could not open PR: {e}")
