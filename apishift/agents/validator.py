"""Validator: the Wonder-backed escalation path of Triage (same contract, deeper model)."""
from __future__ import annotations

from . import triage


def run(client, ledger, **kwargs):
    return triage.run(client, ledger, escalate=True, **kwargs)
