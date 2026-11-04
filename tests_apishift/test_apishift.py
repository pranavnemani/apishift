"""APIShift's own tests (the demo app's tests live in demo/weather-app/tests)."""
import json
from pathlib import Path

from apishift.ledger import Ledger
from apishift.meta_client import ReplayClient
from apishift.orchestrator import Orchestrator, RunConfig
from apishift.specdiff import diff_specs
from apishift.state import ChangeManifest, Edit, ImpactGraph, ImpactNode, PatchSet, strict_schema
from apishift.tools import guardrails
from apishift.tools.redact import redact
from apishift.tools.repo_tools import RepoTools
from apishift.tools.sandbox import parse_junit
from tests_apishift.fake_client import FakeClient

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "demo" / "weather-app"


def cfg():
    return RunConfig(repo=APP, spec_from=APP / "openapi_v1.yaml", spec_to=APP / "openapi_v2.yaml",
                     mock_server=ROOT / "demo" / "mock_server.py", changelog=APP / "CHANGELOG.md", backend="local")


def test_specdiff_finds_every_breaking_change():
    kinds = [e.kind for e in diff_specs(APP / "openapi_v1.yaml", APP / "openapi_v2.yaml").entries]
    assert "OPERATION_MOVED" in kinds and "HEADER_REQUIRED" in kinds
    assert kinds.count("FIELD_REMOVED") == 3 and "PARAM_REMOVED" in kinds


def test_strict_schemas_are_closed_and_fully_required():
    def check(node):
        if isinstance(node, dict):
            assert "$ref" not in node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node.get("properties", {}))
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)
    for m in (ChangeManifest, ImpactGraph, PatchSet):
        check(strict_schema(m))


def test_guardrails_block_tests_scope_and_skips(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    graph = ImpactGraph(nodes=[ImpactNode(id="n", file_path="src/a.py", symbol="x", lines=[1], change_ids=[],
                                          action="")], edges=[], unresolved=[])
    patch = PatchSet(strategy="ADAPTER", notes="", edits=[
        Edit(file_path="tests/test_a.py", search="", replace="", change_ids=[], rationale=""),
        Edit(file_path="src/b.py", search="", replace="y", change_ids=[], rationale=""),
        Edit(file_path="src/a.py", search="x = 1", replace="import pytest\npytest.skip('no')", change_ids=[], rationale=""),
        Edit(file_path="../evil.py", search="", replace="", change_ids=[], rationale="")])
    res = guardrails.apply_patch(patch, graph, tmp_path)
    assert not res.applied and len(res.rejected) == 4
    assert (tmp_path / "src" / "a.py").read_text() == "x = 1\n"


def test_edits_apply_atomically(tmp_path):
    (tmp_path / "a.py").write_text("a\na\nb\n")
    errs = guardrails.apply_edits([Edit(file_path="a.py", search="b", replace="B", change_ids=[], rationale=""),
                                   Edit(file_path="a.py", search="a", replace="A", change_ids=[], rationale="")], tmp_path)
    assert errs and "matched 2 times" in errs[0]
    assert (tmp_path / "a.py").read_text() == "a\na\nb\n"


def test_repo_tools_jail_and_redaction(tmp_path):
    (tmp_path / "k.py").write_text('API_KEY = "abcdef123456"\n')
    (tmp_path / ".env").write_text("SECRET=1")
    t = RepoTools(tmp_path)
    assert "escapes" in t.call("read_file", {"path": "../x"})
    assert ".env" in t.call("read_file", {"path": ".env"})  # error message, not content
    assert "abcdef123456" not in t.read_file("k.py")
    assert "abcdef" not in redact('token = "abcdef123456"')


def test_junit_parse_counts():
    xml = ('<testsuites><testsuite><testcase classname="tests.test_a" name="t1"/>'
           '<testcase classname="tests.test_a" name="t2"><failure message="boom">tb</failure></testcase>'
           '</testsuite></testsuites>')
    r = parse_junit(xml, 1, 0.1)
    assert (r.collected, r.passed, r.failed) == (2, 1, 1)
    assert r.failures[0].nodeid == "tests/test_a.py::t2"


def test_end_to_end_heals_and_replays(tmp_path):
    ledger = Ledger(tmp_path / "run1")
    result = Orchestrator(cfg(), ledger, FakeClient(ledger)).run()
    assert result.status == "READY", result
    assert result.attempts == 2 and result.after.passed == 14
    events = [json.loads(l) for l in (tmp_path / "run1" / "events.jsonl").read_text().splitlines()]
    assert any(e["kind"] == "rejected" and "read-only" in e["message"] for e in events)
    assert any(e["step"] == "analyst" and e.get("added") == ["src/service.py"] for e in events)
    pr = (tmp_path / "run1" / "artifacts" / "PR.md").read_text()
    assert "1 attempted test edit(s) blocked" in pr
    # the user's demo repo is never modified
    assert "/v1/weather" in (APP / "src" / "client.py").read_text()

    # replay the recorded calls: same outcome, no API key
    ledger2 = Ledger(tmp_path / "run2")
    result2 = Orchestrator(cfg(), ledger2, ReplayClient(ledger2, tmp_path / "run1")).run()
    assert result2.status == "READY" and result2.after.passed == 14


def test_strict_schema_keeps_fields_named_title():
    from apishift.state import PRSummary, strict_schema
    s = strict_schema(PRSummary)
    assert set(s["properties"]) == {"title", "summary", "reviewer_notes"}
    assert s["required"] == ["title", "summary", "reviewer_notes"]


def test_apply_edits_rejects_syntax_errors_atomically(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    errs = guardrails.apply_edits([Edit(file_path="a.py", search="x = 1", replace="x = 10", change_ids=[], rationale="t"),
                                   Edit(file_path="b.py", search="y = 2", replace="y = 'oops", change_ids=[], rationale="t")], tmp_path)
    assert errs and "syntax error" in errs[0]
    assert (tmp_path / "a.py").read_text() == "x = 1\n"  # nothing written, not even the valid edit
    assert (tmp_path / "b.py").read_text() == "y = 2\n"
