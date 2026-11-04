"""FastAPI backend for the dashboard.

REST:  GET  /api/runs                          list runs with status
       GET  /api/runs/{id}/events              every ledger event (used for replay playback)
       GET  /api/runs/{id}/artifacts/{name}    PR.md, final.diff, JSON artifacts
       POST /api/runs                          start a run: {"mode": "live"|"replay", "source_run": id}
WS:    /ws/runs/{id}                           stream events as they are written

The UI only talks to these endpoints; it never imports agent code.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")
STATIC = Path(__file__).parent / "static"


class StartRun(BaseModel):
    mode: str = "replay"              # live | replay
    source_run: str | None = None     # run to replay


def _events(run_dir: Path) -> list[dict]:
    p = run_dir / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _summary(run_dir: Path) -> dict:
    evs = _events(run_dir)
    end = next((e for e in reversed(evs) if e["kind"] == "end" and e["step"] == "run"), None)
    start = evs[0]["ts"] if evs else run_dir.stat().st_mtime
    return {"id": run_dir.name, "started": start, "status": (end or {}).get("status", "RUNNING" if evs else "EMPTY"),
            "attempts": (end or {}).get("attempts"), "cost_usd": ((end or {}).get("totals") or {}).get("cost_usd"),
            "recorded_calls": len(list((run_dir / "calls").glob("*.json"))) if (run_dir / "calls").exists() else 0}


def create_app(runs_dir: Path) -> FastAPI:
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="APIShift")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    lock = threading.Lock()

    def run_path(run_id: str) -> Path:
        if not SAFE.match(run_id):
            raise HTTPException(400, "bad run id")
        p = runs_dir / run_id
        if not p.is_dir():
            raise HTTPException(404, "no such run")
        return p

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/runs")
    def list_runs():
        runs = [_summary(p) for p in runs_dir.iterdir() if p.is_dir() and SAFE.match(p.name)]
        return sorted(runs, key=lambda r: r["started"], reverse=True)

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: str):
        return _events(run_path(run_id))

    @app.get("/api/runs/{run_id}/artifacts/{name}")
    def artifact(run_id: str, name: str):
        if not SAFE.match(name):
            raise HTTPException(400, "bad name")
        p = run_path(run_id) / "artifacts" / name
        if not p.is_file():
            raise HTTPException(404, "no such artifact")
        return PlainTextResponse(p.read_text())

    @app.post("/api/runs")
    def start(req: StartRun, x_meta_key: str | None = Header(default=None)):
        from ..main import build_run
        if req.mode not in ("live", "replay"):
            raise HTTPException(400, "mode must be live or replay")
        replay = None
        if req.mode == "replay":
            if not req.source_run:
                raise HTTPException(400, "source_run is required for replay")
            replay = str(run_path(req.source_run))
        elif not (x_meta_key or os.environ.get("META_API_KEY")):
            raise HTTPException(400, "live runs need META_API_KEY on the server or an X-Meta-Key header")
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "a run is already in progress")
        run_id = time.strftime("run-%Y%m%d-%H%M%S") + ("-replay" if replay else "-live")
        args = SimpleNamespace(demo=True, replay=replay, run_id=run_id, max_heals=3, open_pr=False,
                               unsafe_local=os.environ.get("APISHIFT_BACKEND", "docker") == "local")
        try:
            orch, _ = build_run(args, runs_dir, run_id=run_id, api_key=x_meta_key)
        except Exception as e:
            lock.release()
            raise HTTPException(400, str(e))

        def work():
            try:
                orch.run()
            except Exception as e:  # surface failures in the event stream
                orch.ledger.event("run", "end", f"FAILED: {e}", status="FAILED")
            finally:
                lock.release()

        threading.Thread(target=work, daemon=True).start()
        return {"run_id": run_id}

    @app.websocket("/ws/runs/{run_id}")
    async def stream(ws: WebSocket, run_id: str):
        await ws.accept()
        if not SAFE.match(run_id):
            await ws.close(code=1008)
            return
        path = runs_dir / run_id / "events.jsonl"
        sent = 0
        try:
            for _ in range(600):  # wait up to 60s for the run to start writing
                if path.exists():
                    break
                await asyncio.sleep(0.1)
            while True:
                lines = path.read_text().splitlines() if path.exists() else []
                for line in lines[sent:]:
                    ev = json.loads(line)
                    await ws.send_json(ev)
                    if ev["kind"] == "end" and ev["step"] == "run":
                        await ws.close()
                        return
                sent = len(lines)
                await asyncio.sleep(0.2)
        except WebSocketDisconnect:
            return

    return app
