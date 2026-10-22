"""
Checks whether a newer owlplanner release exists on PyPI, and can test-drive
it in a fully isolated, throwaway virtualenv -- never touching the running
app's actual installed packages -- by running the real backend test suite
against it.

Nothing here ever modifies requirements.txt, the running environment, or any
Docker image. Applying a verified-safe upgrade stays a manual step (bump the
pin, rebuild, redeploy) by design: this is a financial-planning tool, and
auto-swapping in a new version of the library that computes tax/pension math
without a human looking at what changed is a bigger risk than the convenience
is worth.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from importlib.metadata import version as installed_version
from pathlib import Path

PACKAGE = "owlplanner"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"

# These are the paths the Dockerfile actually copies the backend to --
# fixed, not derived, since this only ever runs inside that container.
APP_ROOT = Path("/app")
REQUIREMENTS = APP_ROOT / "requirements.txt"
DEV_REQUIREMENTS = APP_ROOT / "requirements-dev.txt"
TESTS_DIR = APP_ROOT / "tests"

_OUTPUT_TAIL_CHARS = 8_000


def fetch_latest_version(timeout: float = 10.0) -> str:
    with urllib.request.urlopen(PYPI_URL, timeout=timeout) as resp:  # noqa: S310 -- fixed, hardcoded PyPI URL
        data = json.load(resp)
    return data["info"]["version"]


def get_installed_version() -> str:
    return installed_version(PACKAGE)


def check_for_update() -> dict:
    installed = get_installed_version()
    latest = fetch_latest_version()
    return {"installed_version": installed, "latest_version": latest, "up_to_date": installed == latest}


@dataclass
class UpgradeTestResult:
    candidate_version: str
    passed: bool
    returncode: int
    output: str
    duration_seconds: float


def test_candidate_version(candidate_version: str, timeout: float = 900.0) -> UpgradeTestResult:
    """Installs `candidate_version` into a scratch virtualenv and runs the
    real backend test suite against it, then discards the venv regardless of
    outcome -- this only ever reports pass/fail."""
    scratch = Path(tempfile.mkdtemp(prefix="owlplanner_upgrade_"))
    start = time.monotonic()
    output_parts: list[str] = []
    try:
        venv_dir = scratch / "venv"
        venv_proc = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)], capture_output=True, text=True, timeout=timeout,
        )
        output_parts.append(venv_proc.stdout + venv_proc.stderr)
        if venv_proc.returncode != 0:
            return _result(candidate_version, False, venv_proc.returncode, output_parts, start)

        venv_python = str(venv_dir / "bin" / "python")
        install_cmds = [
            [venv_python, "-m", "pip", "install", "-r", str(REQUIREMENTS)],
            [venv_python, "-m", "pip", "install", "-r", str(DEV_REQUIREMENTS)],
            [venv_python, "-m", "pip", "install", f"{PACKAGE}=={candidate_version}"],
        ]
        for cmd in install_cmds:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            output_parts.append(proc.stdout + proc.stderr)
            if proc.returncode != 0:
                return _result(candidate_version, False, proc.returncode, output_parts, start)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(APP_ROOT)
        test_proc = subprocess.run(
            [venv_python, "-m", "pytest", str(TESTS_DIR), "-q"],
            capture_output=True, text=True, timeout=timeout, cwd=str(APP_ROOT), env=env,
        )
        output_parts.append(test_proc.stdout + test_proc.stderr)
        return _result(candidate_version, test_proc.returncode == 0, test_proc.returncode, output_parts, start)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _result(candidate_version: str, passed: bool, returncode: int, output_parts: list[str], start: float) -> UpgradeTestResult:
    return UpgradeTestResult(
        candidate_version=candidate_version,
        passed=passed,
        returncode=returncode,
        output="\n".join(output_parts)[-_OUTPUT_TAIL_CHARS:],
        duration_seconds=time.monotonic() - start,
    )
