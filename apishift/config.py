"""Model routing, endpoints, prices and limits. One place to change a model."""
import os
from dataclasses import dataclass

META_BASE_URL = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1")
META_API_KEY_ENV = "META_API_KEY"

# Optional open-weights route for the Analyst (OpenAI-compatible providers).
ANALYST_PROVIDER = os.environ.get("ANALYST_PROVIDER", "meta")  # meta | baseten | together | fireworks
OPEN_WEIGHTS_MODEL = "meta-models/muse-spark-open-1026"
PROVIDER_BASE_URLS = {
    "baseten": os.environ.get("BASETEN_BASE_URL", "https://inference.baseten.co/v1"),
    "together": os.environ.get("TOGETHER_BASE_URL", "https://api.together.xyz/v1"),
    "fireworks": os.environ.get("FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1"),
}
PROVIDER_KEY_ENVS = {"baseten": "BASETEN_API_KEY", "together": "TOGETHER_API_KEY", "fireworks": "FIREWORKS_API_KEY"}

SPARK = "muse-spark-1.3-contributor"
SPARK_STANDARD = "muse-spark-1.3"
WONDER = "muse-wonder-1.0"


@dataclass(frozen=True)
class Route:
    model: str
    effort: str  # contributor models cap at "xhigh"; "max" only on standard Spark / Wonder


ROUTES = {
    "scout": Route(SPARK, "medium"),
    "analyst": Route(SPARK, "high"),
    "refactor": Route(WONDER, "max"),
    "triage": Route(SPARK, "medium"),
    "validator": Route(WONDER, "high"),
    "pr_writer": Route(SPARK, "low"),
}

# USD per 1M tokens: (input, cached input, output). From the hackathon verification reference v2.
PRICES = {
    SPARK: (0.10, 0.002, 0.20),
    SPARK_STANDARD: (1.25, 0.15, 4.25),
    WONDER: (4.00, 0.20, 20.00),
}

MAX_HEAL_ATTEMPTS = 3
MAX_ANALYST_TURNS = 12
TRIAGE_ESCALATION_CONFIDENCE = 0.6
SANDBOX_TIMEOUT_S = 60
SANDBOX_IMAGE = os.environ.get("APISHIFT_SANDBOX_IMAGE", "apishift-sandbox:latest")
MOCK_PORT = 8088

# Files a patch may never touch: the tests are the oracle.
PROTECTED_GLOBS = ["tests/*", "tests/**/*", "conftest.py", "**/conftest.py", "pytest.ini",
                   "pyproject.toml", "setup.cfg", "tox.ini"]
SKIP_MARKERS = ["pytest.mark.skip", "pytest.mark.xfail", "pytest.skip(", "pytest.xfail(", "unittest.skip"]


def cost_usd(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    p_in, p_cached, p_out = PRICES.get(model, (0.0, 0.0, 0.0))
    uncached = max(input_tokens - cached_tokens, 0)
    return (uncached * p_in + cached_tokens * p_cached + output_tokens * p_out) / 1_000_000
