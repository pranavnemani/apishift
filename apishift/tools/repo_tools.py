"""Read-only, path-jailed repository tools exposed to the Analyst agent."""
from __future__ import annotations

import ast
import fnmatch
import re
from pathlib import Path

from .redact import redact

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", ".mypy_cache"}
MAX_OUTPUT = 12_000


class JailError(ValueError):
    pass


class RepoTools:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    # -- jail -----------------------------------------------------------
    def _resolve(self, rel: str) -> Path:
        p = (self.root / (rel or ".")).resolve()
        if p != self.root and self.root not in p.parents:
            raise JailError(f"path escapes the repository: {rel}")
        if p.name.startswith(".env"):
            raise JailError(".env files are never read")
        return p

    def _rel(self, p: Path) -> str:
        return str(p.relative_to(self.root))

    def _files(self, glob: str | None = None):
        for p in sorted(self.root.rglob("*")):
            if any(part in SKIP_DIRS for part in p.relative_to(self.root).parts):
                continue
            if p.is_file() and not p.name.startswith(".env"):
                if glob is None or fnmatch.fnmatch(self._rel(p), glob):
                    yield p

    # -- tools ----------------------------------------------------------
    def list_dir(self, path: str = ".") -> str:
        base = self._resolve(path)
        lines = [self._rel(p) for p in self._files() if base == self.root or base in p.parents]
        return "\n".join(lines) or "(empty)"

    def read_file(self, path: str, start: int = 1, end: int | None = None) -> str:
        p = self._resolve(path)
        if not p.is_file():
            return f"error: {path} is not a file"
        lines = p.read_text(errors="replace").splitlines()
        end = min(end or len(lines), len(lines))
        body = "\n".join(f"{i:4d}| {lines[i - 1]}" for i in range(max(start, 1), end + 1))
        return redact(body)[:MAX_OUTPUT]

    def grep(self, pattern: str, glob: str | None = None) -> str:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"error: bad regex: {e}"
        hits = []
        for p in self._files(glob):
            try:
                for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{self._rel(p)}:{i}: {line.strip()}")
            except OSError:
                continue
            if len(hits) > 200:
                hits.append("... truncated at 200 matches")
                break
        return redact("\n".join(hits) or "no matches")[:MAX_OUTPUT]

    def ast_symbols(self, path: str) -> str:
        """Functions/classes with line ranges, plus string-keyed subscripts and attribute reads per function."""
        p = self._resolve(path)
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError as e:
            return f"error: {e}"
        out = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "def"
                keys, calls = set(), set()
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Subscript) and isinstance(sub.slice, ast.Constant) \
                            and isinstance(sub.slice.value, str):
                        keys.add(sub.slice.value)
                    if isinstance(sub, ast.Call):
                        f = sub.func
                        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                        if name:
                            calls.add(name)
                line = f"{kind} {node.name} lines {node.lineno}-{getattr(node, 'end_lineno', node.lineno)}"
                if keys:
                    line += f" | dict keys read: {sorted(keys)}"
                if calls and kind == "def":
                    line += f" | calls: {sorted(calls)}"
                out.append(line)
        return "\n".join(out) or "no symbols"

    # -- tool specs for the model -----------------------------------------
    @staticmethod
    def specs() -> list[dict]:
        from ..meta_client import function_tool
        return [
            function_tool("list_dir", "List files under a directory of the repository.",
                          {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
                           "additionalProperties": False}),
            function_tool("read_file", "Read a file with line numbers. Optional 1-based start/end lines.",
                          {"type": "object", "properties": {"path": {"type": "string"},
                                                            "start": {"type": ["integer", "null"]},
                                                            "end": {"type": ["integer", "null"]}},
                           "required": ["path", "start", "end"], "additionalProperties": False}),
            function_tool("grep", "Regex search across repository files. Optional glob like 'src/*.py'.",
                          {"type": "object", "properties": {"pattern": {"type": "string"},
                                                            "glob": {"type": ["string", "null"]}},
                           "required": ["pattern", "glob"], "additionalProperties": False}),
            function_tool("ast_symbols", "Python functions/classes in a file with line ranges, dict keys read and calls made.",
                          {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
                           "additionalProperties": False}),
        ]

    def call(self, name: str, args: dict) -> str:
        try:
            if name == "list_dir":
                return self.list_dir(args.get("path") or ".")
            if name == "read_file":
                return self.read_file(args["path"], args.get("start") or 1, args.get("end"))
            if name == "grep":
                return self.grep(args["pattern"], args.get("glob"))
            if name == "ast_symbols":
                return self.ast_symbols(args["path"])
            return f"error: unknown tool {name}"
        except (JailError, KeyError, OSError) as e:
            return f"error: {e}"
