"""Mask likely secrets before any file content is sent to a model."""
import re

PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA****************"),
    (re.compile(r"\b(sk|rk|pk)[-_][A-Za-z0-9_-]{16,}"), r"\1-****"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "gh*_****"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "<private key redacted>"),
    (re.compile(r"(?i)((?:api[_-]?key|secret|token|password|passwd)\s*[:=]\s*)(['\"])[^'\"]{6,}\2"), r"\1\2****\2"),
]


def redact(text: str) -> str:
    for pat, repl in PATTERNS:
        text = pat.sub(repl, text)
    return text
