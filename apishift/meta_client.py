"""Client for the Meta Model API (/v1/responses), with call recording and replay.

Conversation state is managed client-side as a list of Responses-API input items, and every
request carries prompt_cache_key, so usage.input_tokens_details.cached_tokens reports the full
cached prefix each turn (previous_response_id chaining would only report the delta).

Wire rules (verification reference v2):
  - strict schemas go in text.format; response_format on /v1/responses returns HTTP 400
  - contributor models accept reasoning.effort up to "xhigh"
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from . import config
from .ledger import Ledger


class ModelError(RuntimeError):
    pass


@dataclass
class ModelResponse:
    output_items: list[dict]
    text: str
    function_calls: list[dict]
    usage: dict = field(default_factory=dict)

    def parsed(self, model_cls):
        """Parse the final text as JSON into a pydantic model."""
        raw = self.text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").split("\n", 1)[1] if "\n" in raw else raw
            raw = raw.rsplit("```", 1)[0]
        return model_cls.model_validate_json(raw)


def function_tool(name: str, description: str, parameters: dict) -> dict:
    return {"type": "function", "name": name, "description": description, "parameters": parameters}


def _request_hash(body: dict) -> str:
    stable = {k: v for k, v in body.items() if k not in ("prompt_cache_key", "prompt_cache_retention")}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _parse_responses_api(data: dict) -> ModelResponse:
    output = data.get("output", [])
    texts, calls = [], []
    for item in output:
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") in ("output_text", "text"):
                    texts.append(part.get("text", ""))
        elif item.get("type") == "function_call":
            calls.append(item)
    if not texts and isinstance(data.get("output_text"), str):
        texts.append(data["output_text"])
    u = data.get("usage") or {}
    usage = {
        "input_tokens": u.get("input_tokens", 0),
        "cached_tokens": (u.get("input_tokens_details") or {}).get("cached_tokens", 0),
        "output_tokens": u.get("output_tokens", 0),
    }
    return ModelResponse(output_items=output, text="".join(texts), function_calls=calls, usage=usage)


def _to_chat_messages(items: list[dict]) -> list[dict]:
    """Translate Responses-API input items to chat-completions messages (open-weights providers)."""
    msgs: list[dict] = []
    for it in items:
        t = it.get("type")
        if t == "function_call":
            if not msgs or msgs[-1].get("role") != "assistant" or "tool_calls" not in msgs[-1]:
                msgs.append({"role": "assistant", "content": None, "tool_calls": []})
            msgs[-1]["tool_calls"].append({"id": it["call_id"], "type": "function",
                                           "function": {"name": it["name"], "arguments": it["arguments"]}})
        elif t == "function_call_output":
            msgs.append({"role": "tool", "tool_call_id": it["call_id"], "content": it["output"]})
        elif "role" in it:
            msgs.append({"role": it["role"], "content": it["content"]})
        # reasoning items and other opaque types are dropped
    return msgs


class MetaClient:
    """Live client. Records every request/response into the ledger's calls/ folder."""

    def __init__(self, ledger: Ledger, run_id: str, api_key: str | None = None):
        self.ledger = ledger
        self.run_id = run_id
        self.api_key = api_key or os.environ.get(config.META_API_KEY_ENV)
        if not self.api_key:
            raise ModelError(f"Set {config.META_API_KEY_ENV} (or run with --replay)")
        self.seq = 0
        self.session = requests.Session()

    # -- public ---------------------------------------------------------
    def respond(self, step: str, input_items: list[dict], *, tools: list[dict] | None = None,
                schema: dict | None = None, schema_name: str | None = None,
                tool_choice: str | None = None, route_key: str | None = None) -> ModelResponse:
        route = config.ROUTES[route_key or step]
        provider = config.ANALYST_PROVIDER if (route_key or step) == "analyst" else "meta"
        if provider == "meta":
            body = self._responses_body(route, input_items, tools, schema, schema_name, tool_choice)
            url, key, parse = f"{config.META_BASE_URL}/responses", self.api_key, _parse_responses_api
            model = route.model
        else:
            body = self._chat_body(input_items, tools, schema, schema_name, tool_choice)
            url = f"{config.PROVIDER_BASE_URLS[provider]}/chat/completions"
            key = os.environ.get(config.PROVIDER_KEY_ENVS[provider], "")
            parse, model = self._parse_chat, config.OPEN_WEIGHTS_MODEL

        t0 = time.time()
        data = self._post(url, key, body)
        latency = time.time() - t0
        resp = parse(data)
        self._record(step, provider, body, data)
        self.ledger.record_usage(step, model, resp.usage,
                                 config.cost_usd(model, resp.usage["input_tokens"], resp.usage["cached_tokens"],
                                                 resp.usage["output_tokens"]), latency)
        return resp

    # -- request builders ----------------------------------------------
    def _responses_body(self, route, input_items, tools, schema, schema_name, tool_choice) -> dict:
        body = {
            "model": route.model,
            "input": input_items,
            "reasoning": {"effort": route.effort},
            "prompt_cache_key": f"apishift-{self.run_id}",
            "prompt_cache_retention": "in_memory",
        }
        if tools:
            body["tools"] = tools
            if tool_choice:
                body["tool_choice"] = tool_choice
        if schema is not None:
            body["text"] = {"format": {"type": "json_schema", "name": schema_name or "output",
                                       "strict": True, "schema": schema}}
        return body

    def _chat_body(self, input_items, tools, schema, schema_name, tool_choice) -> dict:
        body = {"model": config.OPEN_WEIGHTS_MODEL, "messages": _to_chat_messages(input_items)}
        if tools:
            body["tools"] = [{"type": "function", "function": {k: t[k] for k in ("name", "description", "parameters")}}
                             for t in tools]
            if tool_choice:
                body["tool_choice"] = tool_choice
        if schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": schema_name or "output", "strict": True, "schema": schema}}
        return body

    @staticmethod
    def _parse_chat(data: dict) -> ModelResponse:
        msg = data["choices"][0]["message"]
        calls = [{"type": "function_call", "call_id": tc["id"], "name": tc["function"]["name"],
                  "arguments": tc["function"]["arguments"]} for tc in (msg.get("tool_calls") or [])]
        items = list(calls)
        if msg.get("content"):
            items.append({"role": "assistant", "content": msg["content"]})
        u = data.get("usage") or {}
        usage = {"input_tokens": u.get("prompt_tokens", 0),
                 "cached_tokens": (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
                 "output_tokens": u.get("completion_tokens", 0)}
        return ModelResponse(output_items=items, text=msg.get("content") or "", function_calls=calls, usage=usage)

    # -- transport ------------------------------------------------------
    def _post(self, url: str, key: str, body: dict) -> dict:
        last = None
        for attempt in range(4):
            try:
                r = self.session.post(url, json=body, timeout=300,
                                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            except requests.RequestException as e:
                last = str(e)
            else:
                if r.status_code < 400:
                    return r.json()
                last = f"HTTP {r.status_code}: {r.text[:500]}"
                if r.status_code not in (408, 409, 429, 500, 502, 503, 504):
                    break
            time.sleep(2 ** attempt)
        raise ModelError(last or "request failed")

    def _record(self, step: str, provider: str, body: dict, data: dict):
        self.seq += 1
        path = self.ledger.run_dir / "calls" / f"{self.seq:04d}.json"
        path.write_text(json.dumps({"seq": self.seq, "step": step, "provider": provider,
                                    "request_hash": _request_hash(body), "request": body, "response": data},
                                   indent=1, default=str))


class ReplayClient:
    """Serves recorded responses in order. Tests and the sandbox still run for real."""

    def __init__(self, ledger: Ledger, source_run_dir: Path, speed: float = 0.0):
        self.ledger = ledger
        self.calls = sorted((Path(source_run_dir) / "calls").glob("*.json"))
        if not self.calls:
            raise ModelError(f"No recorded calls in {source_run_dir}/calls")
        self.i = 0
        self.speed = speed  # >0 re-creates recorded latency divided by speed

    def respond(self, step: str, input_items: list[dict], *, tools=None, schema=None, schema_name=None,
                tool_choice=None, route_key=None) -> ModelResponse:
        if self.i >= len(self.calls):
            raise ModelError("Replay ran out of recorded calls; the run diverged from the recording")
        rec = json.loads(self.calls[self.i].read_text())
        self.i += 1
        if rec["step"] != step:
            self.ledger.event(step, "warning", f"replay step mismatch: recorded {rec['step']}")
        parse = _parse_responses_api if rec.get("provider", "meta") == "meta" else MetaClient._parse_chat
        resp = parse(rec["response"])
        model = rec["request"].get("model", "?")
        if self.speed:
            time.sleep(0.5 / self.speed)
        cost = config.cost_usd(model, resp.usage["input_tokens"], resp.usage["cached_tokens"], resp.usage["output_tokens"])
        self.ledger.record_usage(step, model, resp.usage, cost, 0.0)
        # keep the replayed run self-contained so it can itself be replayed
        (self.ledger.run_dir / "calls" / self.calls[self.i - 1].name).write_text(self.calls[self.i - 1].read_text())
        return resp
