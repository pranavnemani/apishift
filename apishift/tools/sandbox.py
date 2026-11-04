"""Run the target repo's tests against the mock API and return a structured TestReport.

Two backends behind one function, run_tests():
  docker (default): --network=none, read-only root, resource limits, tests mounted read-only
  local (--unsafe-local): plain subprocesses on this machine, for development only
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from .. import config
from ..state import TestFailure, TestReport
from .guardrails import is_protected

MARKER = "===APISHIFT_JUNIT==="


class SandboxError(RuntimeError):
    pass


def parse_junit(xml_text: str, exit_code: int, duration: float, stdout_tail: str = "") -> TestReport:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return TestReport(collected=0, passed=0, failed=0, errors=1, skipped=0, duration_s=duration,
                          exit_code=exit_code or 1, stdout_tail=stdout_tail[-4000:],
                          failures=[TestFailure(nodeid="<session>", message="pytest produced no report",
                                                traceback=stdout_tail[-4000:])])
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    failures, total, failed, errors, skipped = [], 0, 0, 0, 0
    for s in suites:
        for tc in s.findall("testcase"):
            total += 1
            cls, name = tc.get("classname", ""), tc.get("name", "")
            nodeid = (cls.replace(".", "/") + ".py::" + name) if cls else name
            for tag in ("failure", "error"):
                el = tc.find(tag)
                if el is not None:
                    failed += tag == "failure"
                    errors += tag == "error"
                    failures.append(TestFailure(nodeid=nodeid, message=(el.get("message") or "")[:500],
                                                traceback=(el.text or "")[-3000:]))
            if tc.find("skipped") is not None:
                skipped += 1
    passed = total - failed - errors - skipped
    return TestReport(collected=total, passed=passed, failed=failed, errors=errors, skipped=skipped,
                      duration_s=round(duration, 3), exit_code=exit_code, failures=failures,
                      stdout_tail=stdout_tail[-4000:])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_health(port: int, timeout: float = 15.0):
    import urllib.request
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5)
            return
        except Exception:
            time.sleep(0.1)
    raise SandboxError("mock server did not start")


def run_local(workspace: Path, mock_server: Path, mock_version: str) -> TestReport:
    port = _free_port()
    mock = subprocess.Popen([sys.executable, str(mock_server), "--version", mock_version, "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_health(port)
        with tempfile.TemporaryDirectory() as td:
            xml_path = Path(td) / "report.xml"
            env = {**os.environ, "BASE_URL": f"http://127.0.0.1:{port}", "PYTHONDONTWRITEBYTECODE": "1"}
            t0 = time.time()
            try:
                proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                       f"--junitxml={xml_path}"], cwd=workspace, env=env, capture_output=True,
                                      text=True, timeout=config.SANDBOX_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                return TestReport(collected=0, passed=0, failed=0, errors=1, skipped=0,
                                  duration_s=config.SANDBOX_TIMEOUT_S, exit_code=124,
                                  failures=[TestFailure(nodeid="<session>", message="TIMEOUT",
                                                        traceback="test run exceeded the time limit")])
            xml = xml_path.read_text() if xml_path.exists() else ""
            return parse_junit(xml, proc.returncode, time.time() - t0, proc.stdout + proc.stderr)
    finally:
        mock.terminate()
        mock.wait(timeout=5)


def run_docker(workspace: Path, mock_server: Path, mock_version: str) -> TestReport:
    if not shutil.which("docker"):
        raise SandboxError("Docker not found. Install Docker, or use --unsafe-local for development only.")
    workspace = Path(workspace).resolve()
    name = f"apishift-{uuid.uuid4().hex[:8]}"
    cmd = ["docker", "run", "--rm", "--name", name, "--network=none", "--read-only",
           "--memory=1g", "--cpus=1", "--pids-limit=256", "--user", "10001:10001",
           "--tmpfs", "/tmp:size=64m,mode=1777",
           "-v", f"{workspace}:/workspace:rw",
           "-v", f"{Path(mock_server).resolve()}:/mock/mock_server.py:ro",
           "-e", f"MOCK_VERSION={mock_version}", "-e", f"MOCK_PORT={config.MOCK_PORT}",
           "-e", f"BASE_URL=http://127.0.0.1:{config.MOCK_PORT}", "-e", "PYTHONDONTWRITEBYTECODE=1",
           "-w", "/workspace"]
    # every protected path (tests/, conftest.py, pytest.ini, ...) is re-mounted read-only on top
    for p in sorted(workspace.iterdir()):
        rel = p.name + ("/x" if p.is_dir() else "")
        if is_protected(rel) or is_protected(p.name):
            cmd += ["-v", f"{p}:/workspace/{p.name}:ro"]
    cmd += [config.SANDBOX_IMAGE, "/opt/apishift/run-tests.sh"]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=config.SANDBOX_TIMEOUT_S + 15)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True)
        return TestReport(collected=0, passed=0, failed=0, errors=1, skipped=0,
                          duration_s=config.SANDBOX_TIMEOUT_S, exit_code=124,
                          failures=[TestFailure(nodeid="<session>", message="TIMEOUT",
                                                traceback="sandbox exceeded the time limit")])
    out = proc.stdout
    if MARKER in out:
        logs, xml = out.split(MARKER, 1)
        try:
            code = int(logs.strip().splitlines()[-1].split("=", 1)[1])
        except (IndexError, ValueError):
            code = proc.returncode
    else:
        logs, xml, code = out, "", proc.returncode or 1
    return parse_junit(xml.strip(), code, time.time() - t0, logs + proc.stderr)


def run_tests(workspace: Path, mock_server: Path, mock_version: str = "v2", backend: str = "docker") -> TestReport:
    if backend == "local":
        return run_local(workspace, mock_server, mock_version)
    return run_docker(workspace, mock_server, mock_version)
