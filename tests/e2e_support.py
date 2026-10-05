"""Run browser scenarios against a real, started web app.

Playwright here is the Node package already present on the machine (no Python
dependency is added), so a scenario is a ``tests/e2e/*.mjs`` script.  pytest owns
the server and the scripted stand-in for the model; the script owns the browser
and prints one ``E2E_RESULT {json}`` line that the test asserts on.

When Node, Playwright or Chromium is missing the scenarios are *skipped*, never
silently passed, and the skip reason says which one is absent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

E2E_DIR = Path(__file__).resolve().parent / "e2e"
RESULT_PREFIX = "E2E_RESULT "


def _playwright_module() -> str | None:
    override = os.environ.get("PLAYWRIGHT_MODULE")
    if override:
        return override if Path(override).exists() else None
    cli = shutil.which("playwright")
    if cli is None:
        return None
    candidate = Path(cli).resolve().parent / "index.mjs"
    return str(candidate) if candidate.exists() else None


def _chromium() -> str | None:
    override = os.environ.get("CHROMIUM_PATH")
    if override:
        return override if Path(override).exists() else None
    for name in ("chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def environment_problem() -> str | None:
    if shutil.which("node") is None:
        return "node is not installed"
    if _playwright_module() is None:
        return "the Node playwright package was not found"
    if _chromium() is None:
        return "no Chromium executable was found"
    return None


requires_browser = pytest.mark.skipif(
    environment_problem() is not None,
    reason=f"browser scenarios need Node + Playwright + Chromium: {environment_problem()}")


def run_scenario(name: str, app, *, token: str, extra: dict | None = None,
                 timeout: int = 120) -> dict:
    """Run ``tests/e2e/<name>.mjs`` against ``app`` and return its result."""
    env = {
        **os.environ,
        "BASE": f"http://127.0.0.1:{app.port}",
        "TOKEN": token,
        "PLAYWRIGHT_MODULE": _playwright_module() or "",
        "CHROMIUM_PATH": _chromium() or "",
        "SCENARIO": json.dumps(extra or {}),
    }
    process = subprocess.run(["node", str(E2E_DIR / f"{name}.mjs")], env=env,
                             capture_output=True, text=True, timeout=timeout)
    for line in reversed(process.stdout.splitlines()):
        if line.startswith(RESULT_PREFIX):
            return json.loads(line[len(RESULT_PREFIX):])
    raise AssertionError(
        f"scenario {name} printed no result (exit {process.returncode}).\n"
        f"stdout:\n{process.stdout[-2000:]}\nstderr:\n{process.stderr[-2000:]}")
