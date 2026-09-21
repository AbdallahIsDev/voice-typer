"""Normalized boolean workflow-input gates (FV-61).

GitHub compares a boolean input differently depending on which event produced
it (actions/runner#3571): a ``workflow_call`` boolean matches the literal
``true``, a direct ``workflow_dispatch`` checkbox arrives as the STRING
``'true'``. A gate written in one form only is silently inert on the other
event, which is exactly the "ships unsigned while every gate shows green"
class TX-23 / C-CI-11 exist to prevent (``tauri-build.yml`` passes
``fromJSON(github.event.inputs.sign)``, so the orchestrator path is the
boolean form).

These tests pin the fix: each platform build job normalizes the flag ONCE in
job-level ``env`` (``SIGN_ENABLED``, both forms OR-ed, yielding a string), and
every signing / attestation gate compares that env value instead of
``inputs.sign``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

NORMALIZER = "${{ inputs.sign == true || inputs.sign == 'true' }}"
ENV_GATE = "env.SIGN_ENABLED == 'true'"

# The gates that must remain wired (count is part of the contract: a dropped
# gate is a dropped guard, and the count keeps a rewrite from silently
# losing one).
EXPECTED_ENV_GATES = {
    "tauri-windows-build.yml": 6,
    "tauri-macos-build.yml": 5,
    "tauri-linux-build.yml": 2,
}

# Jobs (per workflow file) that run the sign/attestation gate set.
GATE_JOBS = {
    "tauri-windows-build.yml": "tauri-windows-build",
    "tauri-macos-build.yml": "build-tauri-universal",
    "tauri-linux-build.yml": "build",
}


def _load(filename: str) -> dict:
    return yaml.safe_load((WORKFLOWS / filename).read_text(encoding="utf-8"))


def _job_env(filename: str) -> dict:
    data = _load(filename)
    return data["jobs"][GATE_JOBS[filename]].get("env") or {}


def _job_steps(filename: str) -> list[dict]:
    data = _load(filename)
    return data["jobs"][GATE_JOBS[filename]]["steps"]


@pytest.mark.parametrize("filename", sorted(EXPECTED_ENV_GATES))
def test_sign_flag_is_normalized_in_job_env(filename: str) -> None:
    """Both dispatch forms must map to one string flag in job-level env."""
    assert _job_env(filename).get("SIGN_ENABLED") == NORMALIZER, (
        f"{filename}: the sign flag must be normalized once in job-level env as {NORMALIZER!r} "
        "(true matches workflow_call booleans, 'true' matches direct dispatch strings)"
    )


@pytest.mark.parametrize("filename", sorted(EXPECTED_ENV_GATES))
def test_gates_compare_the_normalized_env_flag(filename: str) -> None:
    """Every gate compares env.SIGN_ENABLED, never the raw boolean input."""
    gates = [s for s in _job_steps(filename) if ENV_GATE in str(s.get("if", ""))]
    assert len(gates) == EXPECTED_ENV_GATES[filename], (
        f"{filename}: expected {EXPECTED_ENV_GATES[filename]} gates on {ENV_GATE!r}, "
        f"found {len(gates)}: {[s.get('name') for s in gates]}"
    )


@pytest.mark.parametrize("filename", sorted(EXPECTED_ENV_GATES))
def test_no_gate_compares_the_raw_input_to_a_boolean_string(filename: str) -> None:
    """The inert single-form comparison must not reappear in any gate.

    The normalizer itself legitimately contains ``inputs.sign == 'true'`` as the
    second half of its OR, so the check looks for the single-form usage inside
    an ``if:`` condition instead.
    """
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("if:") and re.search(r"inputs\.sign\s*==\s*'true'", line)
    ]
    assert offenders == [], (
        f"{filename}: boolean input compared to the string 'true' — inert on the workflow_call "
        f"path (actions/runner#3571). Use env.SIGN_ENABLED instead: {offenders}"
    )


@pytest.mark.parametrize("filename", sorted(EXPECTED_ENV_GATES))
def test_normalizer_is_declared_exactly_once(filename: str) -> None:
    """A second declaration would let the two flag definitions drift apart."""
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert text.count(NORMALIZER) == 1, (
        f"{filename}: SIGN_ENABLED must be declared exactly once (found {text.count(NORMALIZER)})"
    )
