"""CLI.

  python -m apishift migrate --demo --unsafe-local            # weather-app demo, local runner
  python -m apishift migrate --repo R --from v1.yaml --to v2.yaml --changelog C --mock-server M
  python -m apishift migrate --demo --replay runs/<run-id>    # re-run from recorded model calls
  python -m apishift dashboard                                # FastAPI + UI on http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"


def build_run(args, runs_dir: Path, run_id: str | None = None, api_key: str | None = None,
              listeners=None):
    from .ledger import Ledger
    from .meta_client import MetaClient, ReplayClient
    from .orchestrator import Orchestrator, RunConfig

    if args.demo:
        repo = DEMO / "weather-app"
        spec_from, spec_to = repo / "openapi_v1.yaml", repo / "openapi_v2.yaml"
        changelog, mock = repo / "CHANGELOG.md", DEMO / "mock_server.py"
    else:
        repo, spec_from, spec_to = Path(args.repo), Path(getattr(args, "from")), Path(args.to)
        changelog = Path(args.changelog) if args.changelog else None
        mock = Path(args.mock_server)
    run_id = run_id or args.run_id or time.strftime("run-%Y%m%d-%H%M%S")
    ledger = Ledger(runs_dir / run_id)
    for fn in listeners or []:
        ledger.listeners.append(fn)
    client = (ReplayClient(ledger, Path(args.replay)) if args.replay
              else MetaClient(ledger, run_id, api_key=api_key))
    cfg = RunConfig(repo=repo, spec_from=spec_from, spec_to=spec_to, mock_server=mock, changelog=changelog,
                    backend="local" if args.unsafe_local else "docker", max_heals=args.max_heals,
                    open_pr=args.open_pr)
    return Orchestrator(cfg, ledger, client), ledger


def cmd_migrate(args) -> int:
    from .meta_client import ModelError
    runs_dir = Path(args.runs_dir)
    if args.unsafe_local:
        print("WARNING: --unsafe-local runs generated code on this machine without isolation. Development only.",
              file=sys.stderr)

    def printer(ev):
        if ev["kind"] in ("stage", "log", "tool_call", "warning", "rejected", "violation", "tests", "end", "start"):
            print(f"[{ev['t']:7.1f}s] {ev['step']:<10} {ev['kind']:<9} {ev.get('status', '')} {ev['message']}")

    try:
        orch, ledger = build_run(args, runs_dir, listeners=[printer])
        result = orch.run()
    except ModelError as e:
        print(f"model error: {e}", file=sys.stderr)
        return 2
    print(f"\n{result.status}: {result.after.passed}/{result.after.collected} tests passing after "
          f"{result.attempts} attempt(s). Cost ${ledger.totals['cost_usd']:.3f}.")
    print(f"PR body: {ledger.run_dir / 'artifacts' / 'PR.md'}")
    if result.pr_url:
        print(f"PR: {result.pr_url}")
    return 0 if result.status == "READY" else 1


def cmd_dashboard(args) -> int:
    import uvicorn

    from .dashboard.server import create_app
    uvicorn.run(create_app(Path(args.runs_dir)), host=args.host, port=args.port)
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="apishift", description="Contract-driven API migration agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("migrate", help="migrate a repo to a new API version")
    m.add_argument("--demo", action="store_true", help="use the bundled weather-app demo")
    m.add_argument("--repo")
    m.add_argument("--from", dest="from")
    m.add_argument("--to")
    m.add_argument("--changelog")
    m.add_argument("--mock-server", help="path to a mock server script supporting --version v1|v2 --port N")
    m.add_argument("--run-id")
    m.add_argument("--runs-dir", default=str(ROOT / "runs"))
    m.add_argument("--replay", help="run dir whose recorded model calls to replay")
    m.add_argument("--unsafe-local", action="store_true", help="run tests without Docker (development only)")
    m.add_argument("--open-pr", action="store_true", help="push a branch and open a GitHub PR with gh")
    m.add_argument("--max-heals", type=int, default=3)
    m.set_defaults(func=cmd_migrate)
    d = sub.add_parser("dashboard", help="serve the FastAPI dashboard")
    d.add_argument("--runs-dir", default=str(ROOT / "runs"))
    d.add_argument("--host", default="127.0.0.1")
    d.add_argument("--port", type=int, default=8000)
    d.set_defaults(func=cmd_dashboard)
    return ap


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.cmd == "migrate" and not args.demo and not all([args.repo, getattr(args, "from"), args.to, args.mock_server]):
        parser().error("migrate needs --demo, or --repo, --from, --to and --mock-server")
    return args.func(args)
