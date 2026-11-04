"""Run ledger: events.jsonl, typed artifacts and recorded model calls under runs/<run-id>/.

The dashboard and replay mode both read from here, so what you see is what happened.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from pydantic import BaseModel


class Ledger:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        (self.run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "calls").mkdir(parents=True, exist_ok=True)
        self.events_path = self.run_dir / "events.jsonl"
        self.listeners: list[Callable[[dict], None]] = []
        self.totals = {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}
        self._t0 = time.time()

    def event(self, step: str, kind: str, message: str = "", **data) -> dict:
        ev = {"t": round(time.time() - self._t0, 3), "ts": time.time(), "step": step, "kind": kind,
              "message": message, **data}
        with self.events_path.open("a") as f:
            f.write(json.dumps(ev, default=str) + "\n")
        for fn in self.listeners:
            try:
                fn(ev)
            except Exception:
                pass
        return ev

    def artifact(self, name: str, obj) -> Path:
        path = self.run_dir / "artifacts" / name
        if isinstance(obj, BaseModel):
            path.write_text(obj.model_dump_json(indent=2))
        elif isinstance(obj, (dict, list)):
            path.write_text(json.dumps(obj, indent=2, default=str))
        else:
            path.write_text(str(obj))
        self.event("ledger", "artifact", name, path=str(path.relative_to(self.run_dir)))
        return path

    def record_usage(self, step: str, model: str, usage: dict, cost: float, latency_s: float):
        self.totals["input_tokens"] += usage.get("input_tokens", 0)
        self.totals["cached_tokens"] += usage.get("cached_tokens", 0)
        self.totals["output_tokens"] += usage.get("output_tokens", 0)
        self.totals["cost_usd"] += cost
        self.totals["calls"] += 1
        self.event(step, "model_call", f"{model} {latency_s:.1f}s ${cost:.4f}", model=model, usage=usage,
                   cost_usd=round(cost, 6), latency_s=round(latency_s, 3), totals=dict(self.totals))
