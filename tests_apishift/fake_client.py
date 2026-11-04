"""Scripted stand-in for the Meta API, used only by APIShift's own tests.

It returns raw Responses-API JSON so parsing, recording and replay are exercised exactly as live.
Attempt 1 deliberately patches only the client, so the heal loop (Triage -> Analyst -> Refactor) runs.
"""
import json
from pathlib import Path

from apishift.meta_client import _parse_responses_api, _request_hash

MANIFEST = {
    "api_name": "WeatherAPI", "source_version": "1.0", "target_version": "2.0",
    "changes": [
        {"id": "c1", "category": "ENDPOINT_RENAME", "old_target": "GET /v1/weather", "new_target": "GET /v2/current",
         "description": "endpoint moved", "migration_rule": "call /v2/current", "raw_diff_refs": ["d1"]},
        {"id": "c2", "category": "PARAM_RENAME", "old_target": "city", "new_target": "location",
         "description": "query param renamed", "migration_rule": "send location=", "raw_diff_refs": ["d2", "d4"]},
        {"id": "c3", "category": "AUTH_HEADER", "old_target": "(none)", "new_target": "X-API-Version: 2",
         "description": "header required", "migration_rule": "send X-API-Version: 2", "raw_diff_refs": ["d3"]},
        {"id": "c4", "category": "SCHEMA_RESHAPE", "old_target": "city,temp,wind_mph",
         "new_target": "location,temperature.current,wind.speed_mph", "description": "nested response",
         "migration_rule": "map nested fields back to the internal shape",
         "raw_diff_refs": ["d5", "d6", "d7", "d8", "d10", "d13"]},
    ],
}

NODE_CLIENT = {"id": "n1", "file_path": "src/client.py", "symbol": "WeatherClient", "lines": [17, 18, 19, 20, 21, 29],
               "change_ids": ["c1", "c2", "c3", "c4"], "action": "call v2 and map the response"}
NODE_SERVICE = {"id": "n2", "file_path": "src/service.py", "symbol": "report", "lines": [9, 10],
                "change_ids": ["c4"], "action": "stop passing the raw upstream payload downstream"}
GRAPH_1 = {"nodes": [NODE_CLIENT], "edges": [], "unresolved": []}
GRAPH_2 = {"nodes": [NODE_CLIENT, NODE_SERVICE],
           "edges": [{"from_node": "n1", "to_node": "n2", "kind": "produces", "field": "wind_mph"}], "unresolved": []}

PATCH_1 = {"strategy": "ADAPTER", "notes": "translate at the client boundary", "edits": [
    {"file_path": "src/client.py",
     "search": '            f"{self.base_url}/v1/weather",\n            params={"city": city},\n',
     "replace": '            f"{self.base_url}/v2/current",\n            params={"location": city},\n'
                '            headers={"X-API-Version": "2"},\n',
     "change_ids": ["c1", "c2", "c3"], "rationale": "v2 endpoint, param and header"},
    {"file_path": "src/client.py",
     "search": "        return WeatherReading.model_validate(self.fetch_raw(city))",
     "replace": "        data = self.fetch_raw(city)\n        return WeatherReading(city=data[\"location\"], "
                "temp=data[\"temperature\"][\"current\"], wind_mph=data[\"wind\"][\"speed_mph\"])",
     "change_ids": ["c4"], "rationale": "map nested v2 fields to the internal model"},
    {"file_path": "tests/test_service.py", "search": "def test_report_heat", "replace": "def _disabled",
     "change_ids": [], "rationale": "a test edit the guardrails must block"},
]}
PATCH_2 = {"strategy": "ADAPTER", "notes": "service consumes the adapted model", "edits": [
    {"file_path": "src/service.py",
     "search": "    payload = client.fetch_raw(city)\n    reading = WeatherReading.model_validate(payload)",
     "replace": "    reading = client.current(city)\n    payload = {\"temp\": reading.temp, \"wind_mph\": reading.wind_mph}",
     "change_ids": ["c4"], "rationale": "alerts read the internal shape"},
]}
DIAG = {"attempt": 1, "failed_tests": ["tests/test_service.py::test_report_high_wind"], "failure_type": "VALIDATION_ERROR",
        "root_cause": "service.py validates the raw v2 payload and passes it to alerts.evaluate",
        "route_to": "ANALYST", "directive": "add service.report as a consumer of the client payload",
        "target_files": ["src/service.py"], "confidence": 0.85}
PR = {"title": "Migrate WeatherAPI client from v1 to v2", "summary": "Adapter at the client boundary.",
      "reviewer_notes": ["Check temperature.unit is always F"]}


def _text(obj, usage=(9000, 6000, 800)):
    return {"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(obj)}]}],
            "usage": {"input_tokens": usage[0], "input_tokens_details": {"cached_tokens": usage[1]},
                      "output_tokens": usage[2]}}


def _call(name, args, cid):
    return {"output": [{"type": "function_call", "call_id": cid, "name": name, "arguments": json.dumps(args)}],
            "usage": {"input_tokens": 5000, "input_tokens_details": {"cached_tokens": 4000}, "output_tokens": 60}}


class FakeClient:
    def __init__(self, ledger):
        self.ledger = ledger
        self.counts = {}
        self.seq = 0

    def _script(self, step, n):
        if step == "scout":
            return _text(MANIFEST)
        if step == "analyst":
            return {0: _call("grep", {"pattern": "v1/weather|wind_mph", "glob": None}, "c1"),
                    1: _text(GRAPH_1), 2: _call("ast_symbols", {"path": "src/service.py"}, "c2")}.get(n, _text(GRAPH_2))
        if step == "refactor":
            return _text(PATCH_1 if n == 0 else PATCH_2, (30000, 20000, 5000))
        if step in ("triage", "validator"):
            return _text(DIAG)
        if step == "pr_writer":
            return _text(PR)
        raise AssertionError(step)

    def respond(self, step, input_items, *, tools=None, schema=None, schema_name=None, tool_choice=None, route_key=None):
        n = self.counts.get(step, 0)
        self.counts[step] = n + 1
        data = self._script(step, n)
        resp = _parse_responses_api(data)
        self.seq += 1
        body = {"model": "fake", "input": input_items}
        (Path(self.ledger.run_dir) / "calls" / f"{self.seq:04d}.json").write_text(json.dumps(
            {"seq": self.seq, "step": step, "provider": "meta", "request_hash": _request_hash(body),
             "request": {"model": "muse-wonder-1.0" if step == "refactor" else "muse-spark-1.3-contributor"},
             "response": data}))
        self.ledger.record_usage(step, "fake", resp.usage, 0.0, 0.0)
        return resp
