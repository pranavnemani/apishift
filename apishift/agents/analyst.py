"""Analyst: multi-turn tool loop over the repo that builds the ImpactGraph (nodes + data-flow edges)."""
from __future__ import annotations

import json

from .. import config
from ..meta_client import ModelError
from ..state import ChangeManifest, DiagnosticReport, ImpactGraph, strict_schema
from ..tools.repo_tools import RepoTools
from .common import as_json, msg

SYSTEM = """You are Analyst, part of APIShift, an API migration engine.
Find every place in this Python repository that must change for the given breaking API changes.

Use the tools (list_dir, grep, read_file, ast_symbols) to look; never guess a location you have
not read. Trace data flow, not just call sites: if an upstream payload is returned as a dict and
passed to other functions, every function that reads keys from it is affected even though it
never calls the API. Record those links as edges:
  calls     - from_node calls to_node
  produces  - from_node returns or passes data that to_node uses (set field to the key)
  consumes  - from_node reads a field (set field) of data produced elsewhere

Nodes are source files only (tests are read-only and are the oracle; read them to learn the
expected internal behavior). file_path is relative to the repo root, lines are the affected lines,
change_ids reference the manifest, action says what must change. Put suspected but unconfirmed
locations in unresolved.

When you are done exploring, reply with the ImpactGraph JSON and nothing else."""


def run(client, ledger, repo: RepoTools, manifest: ChangeManifest, previous: ImpactGraph | None = None,
        diagnosis: DiagnosticReport | None = None) -> ImpactGraph:
    static = f"Breaking changes (ChangeManifest):\n{as_json(manifest)}\n\nRepository files:\n{repo.list_dir('.')}"
    dynamic = ""
    if previous is not None:
        dynamic += f"\n\nYour previous ImpactGraph:\n{as_json(previous)}"
    if diagnosis is not None:
        dynamic += (f"\n\nA test run after patching failed and was routed back to you:\n{as_json(diagnosis)}\n"
                    "Extend the graph with what was missed; keep correct existing nodes.")
    items = [msg("system", SYSTEM), msg("user", static + dynamic)]
    tools = RepoTools.specs()
    schema = strict_schema(ImpactGraph)

    for turn in range(config.MAX_ANALYST_TURNS + 1):
        final_turn = turn == config.MAX_ANALYST_TURNS
        resp = client.respond("analyst", items, tools=tools, schema=schema, schema_name="ImpactGraph",
                              tool_choice="none" if final_turn else "auto")
        if not resp.function_calls:
            try:
                graph = resp.parsed(ImpactGraph)
            except ValueError as e:
                if final_turn:
                    raise ModelError(f"analyst: invalid ImpactGraph: {e}")
                items += resp.output_items + [msg("user", f"That did not validate as ImpactGraph: {e}. Reply with valid JSON.")]
                continue
            known = {c.id for c in manifest.changes}
            for n in graph.nodes:
                n.change_ids = [c for c in n.change_ids if c in known]
            ledger.event("analyst", "log",
                         f"{len(graph.nodes)} nodes in {len(graph.files())} files, {len(graph.edges)} edges",
                         files=sorted(graph.files()))
            return graph
        items += resp.output_items
        for call in resp.function_calls:
            try:
                args = json.loads(call.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            out = repo.call(call["name"], args)
            ledger.event("analyst", "tool_call", f"{call['name']}({', '.join(f'{k}={v!r}' for k, v in args.items() if v is not None)})",
                         tool=call["name"], args=args, result_preview=out[:300])
            items.append({"type": "function_call_output", "call_id": call["call_id"], "output": out})
    raise ModelError("analyst: no ImpactGraph produced")
