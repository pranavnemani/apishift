"""Guardrails enforced in code: what a patch may touch, and whether it applies cleanly.

A green test run only counts if the agent could not have made it green by cheating.
"""
from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..state import Edit, ImpactGraph, PatchSet


@dataclass
class GuardrailResult:
    applied: list[Edit] = field(default_factory=list)
    rejected: list[tuple[Edit, str]] = field(default_factory=list)
    apply_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.apply_errors and bool(self.applied)


def is_protected(rel_path: str) -> bool:
    rel_path = rel_path.lstrip("./")
    return any(fnmatch.fnmatch(rel_path, g) for g in config.PROTECTED_GLOBS)


def adds_skip_marker(edit: Edit) -> bool:
    return any(m in edit.replace and m not in edit.search for m in config.SKIP_MARKERS)


def tests_fingerprint(root: Path) -> str:
    """Hash of every protected file, checked before and after each run."""
    h = hashlib.sha256()
    root = Path(root)
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_file() and "__pycache__" not in rel and ".pytest_cache" not in rel and is_protected(rel):
            h.update(rel.encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def screen(patch: PatchSet, graph: ImpactGraph, root: Path) -> tuple[list[Edit], list[tuple[Edit, str]]]:
    """Split edits into allowed and rejected (with the reason)."""
    allowed, rejected = [], []
    in_scope = graph.files()
    root = Path(root).resolve()
    for e in patch.edits:
        target = (root / e.file_path).resolve()
        if root not in target.parents:
            rejected.append((e, "path escapes the repository"))
        elif is_protected(e.file_path):
            rejected.append((e, "tests and test config are read-only"))
        elif e.file_path not in in_scope:
            rejected.append((e, f"{e.file_path} is not in the impact graph; ask the Analyst to add it"))
        elif adds_skip_marker(e):
            rejected.append((e, "adding skip/xfail markers is not allowed"))
        else:
            allowed.append(e)
    return allowed, rejected


def apply_edits(edits: list[Edit], root: Path) -> list[str]:
    """Apply search/replace edits atomically: every search must match exactly once, or nothing changes."""
    root = Path(root)
    staged: dict[Path, str] = {}
    errors = []
    for e in edits:
        p = root / e.file_path
        text = staged.get(p, p.read_text() if p.exists() else None)
        if text is None:
            if e.search == "":
                staged[p] = e.replace
                continue
            errors.append(f"{e.file_path}: file does not exist")
            continue
        if e.search == "":
            errors.append(f"{e.file_path}: empty search on an existing file")
            continue
        n = text.count(e.search)
        if n != 1:
            snippet = e.search[:120].replace("\n", "\\n")
            errors.append(f"{e.file_path}: search text matched {n} times (must be exactly 1): '{snippet}'")
            continue
        staged[p] = text.replace(e.search, e.replace, 1)
    for p, text in staged.items():
        if p.suffix == ".py":
            try:
                compile(text, str(p), "exec")
            except SyntaxError as exc:
                errors.append(f"{p.relative_to(root)}: edit would leave a syntax error "
                              f"(line {exc.lineno}: {exc.msg}); nothing was written")
    if errors:
        return errors
    for p, text in staged.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return []


def apply_patch(patch: PatchSet, graph: ImpactGraph, root: Path) -> GuardrailResult:
    allowed, rejected = screen(patch, graph, root)
    res = GuardrailResult(rejected=rejected)
    if allowed:
        errs = apply_edits(allowed, root)
        if errs:
            res.apply_errors = errs
        else:
            res.applied = allowed
    return res
