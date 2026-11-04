"""Shared helpers for agents."""
from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from ..ledger import Ledger
from ..meta_client import ModelError
from ..state import strict_schema


def as_json(obj) -> str:
    if isinstance(obj, BaseModel):
        return obj.model_dump_json(indent=1)
    return json.dumps(obj, indent=1, default=str)


def msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def structured_call(client, ledger: Ledger, step: str, system: str, user: str, model_cls: type[BaseModel],
                    route_key: str | None = None, retries: int = 1):
    """One structured-output call, retried once with the validation error if parsing fails."""
    items = [msg("system", system), msg("user", user)]
    schema = strict_schema(model_cls)
    last_err = None
    for _ in range(retries + 1):
        resp = client.respond(step, items, schema=schema, schema_name=model_cls.__name__, route_key=route_key)
        try:
            return resp.parsed(model_cls)
        except (ValidationError, ValueError) as e:
            last_err = e
            ledger.event(step, "warning", f"invalid {model_cls.__name__}, retrying: {str(e)[:200]}")
            items += resp.output_items + [msg("user", f"Your output did not validate: {e}. Return corrected JSON only.")]
    raise ModelError(f"{step}: could not get a valid {model_cls.__name__}: {last_err}")
