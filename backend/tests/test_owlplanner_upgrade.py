"""Unit tests for the owlplanner-upgrade-checker. subprocess calls are
monkeypatched throughout -- actually creating a venv and pip-installing
scipy/pandas/owlplanner on every test run would be slow, flaky, and
network-dependent; what matters here is the control flow (stop on first
failure, always clean up the scratch dir, report the right fields)."""

import subprocess
from pathlib import Path
from unittest.mock import patch

from app.system import owlplanner_upgrade as mod


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_check_for_update_reports_up_to_date_when_versions_match():
    with patch.object(mod, "fetch_latest_version", return_value=mod.get_installed_version()):
        result = mod.check_for_update()
    assert result["up_to_date"] is True
    assert result["installed_version"] == result["latest_version"]


def test_check_for_update_reports_an_available_update():
    installed = mod.get_installed_version()
    with patch.object(mod, "fetch_latest_version", return_value="9999.1.1"):
        result = mod.check_for_update()
    assert result["up_to_date"] is False
    assert result["installed_version"] == installed
    assert result["latest_version"] == "9999.1.1"


def test_test_candidate_version_fails_fast_on_venv_creation_error_and_cleans_up():
    created_dirs = []
    real_mkdtemp = __import__("tempfile").mkdtemp

    def tracking_mkdtemp(*args, **kwargs):
        d = real_mkdtemp(*args, **kwargs)
        created_dirs.append(d)
        return d

    with patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp), \
         patch("subprocess.run", return_value=_completed(returncode=1, stderr="venv boom")):
        result = mod.test_candidate_version("2099.1.1")

    assert result.passed is False
    assert result.returncode == 1
    assert "venv boom" in result.output
    assert created_dirs and not Path(created_dirs[0]).exists()  # always cleaned up


def test_test_candidate_version_stops_at_first_failing_install_step():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "venv" in cmd:
            return _completed(0)
        if cmd[-1].startswith(mod.PACKAGE):  # the owlplanner==<version> install step
            return _completed(1, stderr="no matching distribution")
        return _completed(0)

    with patch("subprocess.run", side_effect=fake_run):
        result = mod.test_candidate_version("2099.1.1")

    assert result.passed is False
    assert "no matching distribution" in result.output
    # pytest must never run if an install step failed first.
    assert not any("pytest" in str(c) for c in calls)


def test_test_candidate_version_runs_pytest_and_reports_success():
    def fake_run(cmd, **kwargs):
        return _completed(0, stdout=f"ran: {' '.join(cmd)}\n")

    with patch("subprocess.run", side_effect=fake_run):
        result = mod.test_candidate_version("2099.1.1")

    assert result.passed is True
    assert result.returncode == 0
    assert result.candidate_version == "2099.1.1"
    assert result.duration_seconds >= 0


def test_test_candidate_version_reports_pytest_failure():
    def fake_run(cmd, **kwargs):
        if cmd[1:3] == ["-m", "pytest"]:
            return _completed(1, stdout="1 failed, 93 passed")
        return _completed(0)

    with patch("subprocess.run", side_effect=fake_run):
        result = mod.test_candidate_version("2099.1.1")

    assert result.passed is False
    assert result.returncode == 1
    assert "1 failed" in result.output
