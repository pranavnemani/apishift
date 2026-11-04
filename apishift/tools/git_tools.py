"""Workspace setup, diffs and PR delivery.

Each run works on its own copy of the target repo (runs/<id>/workspace) with a baseline commit,
so the user's checkout is never modified and the final diff is exact.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules")


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def prepare_workspace(repo: Path, run_dir: Path) -> Path:
    ws = Path(run_dir) / "workspace"
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(repo, ws, ignore=IGNORE)
    _git(ws, "init", "-q")
    _git(ws, "-c", "user.email=apishift@local", "-c", "user.name=APIShift", "add", "-A")
    _git(ws, "-c", "user.email=apishift@local", "-c", "user.name=APIShift", "commit", "-q", "-m", "baseline")
    return ws


def diff(ws: Path) -> str:
    _git(ws, "add", "-A")
    return _git(ws, "diff", "--cached", "HEAD")


def changed_files(ws: Path) -> list[str]:
    _git(ws, "add", "-A")
    return [l for l in _git(ws, "diff", "--cached", "--name-only", "HEAD").splitlines() if l]


def open_pr(repo: Path, patch_text: str, branch: str, title: str, body_path: Path, draft: bool) -> str:
    """Apply the run's diff on a new branch of the real repo (via a git worktree), push, and open a PR."""
    repo = Path(repo).resolve()
    top = Path(_git(repo, "rev-parse", "--show-toplevel").strip())
    sub = repo.relative_to(top)
    wt = top.parent / f".apishift-wt-{branch.replace('/', '-')}"
    _git(top, "worktree", "add", "-q", "-b", branch, str(wt), "HEAD")
    try:
        args = ["apply", "--index"] + ([f"--directory={sub}"] if str(sub) != "." else []) + ["-"]
        r = subprocess.run(["git", *args], cwd=wt, input=patch_text, text=True, capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(f"git apply failed: {r.stderr}")
        _git(wt, "commit", "-q", "-m", title)
        _git(wt, "push", "-q", "-u", "origin", branch)
        cmd = ["gh", "pr", "create", "--title", title, "--body-file", str(body_path), "--head", branch]
        if draft:
            cmd += ["--draft", "--label", "needs-human"]
        out = subprocess.run(cmd, cwd=wt, capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError(f"gh pr create failed: {out.stderr}")
        return out.stdout.strip()
    finally:
        _git(top, "worktree", "remove", "--force", str(wt), check=False)
