"""TASK-013: slow-wrapped versions of the manual diagnostic scripts."""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

MANUAL_DIR = Path(__file__).resolve().parent / "manual"


def _load_script_module(script_path: Path, module_name: str) -> ModuleType:
    """Load a script file as an importable module."""
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None, f"could not build import spec for {script_path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ─── Deprecated-script contract ────────────────────────────────────────


@pytest.mark.slow
def test_diagnose_f2_deprecated_contract(capsys: pytest.CaptureFixture[str]) -> None:
    """deprecation notice pointing at the modern replacement tests."""
    script = MANUAL_DIR / "diagnose_f2.py"
    module = _load_script_module(script, "manual_diagnose_f2")
    exit_code = module.run()
    assert exit_code == 2, f"expected exit code 2, got {exit_code}"
    captured = capsys.readouterr()
    assert "DEPRECATED" in captured.err, (
        f"diagnose_f2.run() must print a DEPRECATED notice on stderr; got stderr={captured.err!r}"
    )
    assert "test_e2e_smoke" in captured.err, "diagnose_f2.run() must point users at the modern replacement tests"


@pytest.mark.slow
def test_cublas_fallback_deprecated_contract(capsys: pytest.CaptureFixture[str]) -> None:
    """deprecation notice pointing at ``TestFallbackChain``."""
    script = MANUAL_DIR / "cublas_fallback.py"
    module = _load_script_module(script, "manual_cublas_fallback")
    exit_code = module.run()
    assert exit_code == 2, f"expected exit code 2, got {exit_code}"
    captured = capsys.readouterr()
    assert "DEPRECATED" in captured.err, (
        f"cublas_fallback.run() must print a DEPRECATED notice on stderr; got stderr={captured.err!r}"
    )
    assert "TestFallbackChain" in captured.err, "cublas_fallback.run() must point users at TestFallbackChain"


def _real_module_available(modname: str) -> bool:
    """Return True iff ``modname`` is REALLY importable on disk."""
    try:
        return importlib.util.find_spec(modname) is not None
    except (ImportError, ValueError):
        return False


@pytest.mark.slow
def test_runtime_proof_smoke() -> None:
    """Run ``runtime_proof.py`` end-to-end and assert it exits cleanly."""
    pytest.importorskip("numpy", reason="runtime_proof.py imports numpy at top level")
    if not _real_module_available("faster_whisper"):
        pytest.skip("faster_whisper not installed, runtime_proof needs a real model")
    if not _real_module_available("voice_typer"):
        pytest.skip("voice_typer not installed, run `pip install -e .` first")

    script = MANUAL_DIR / "runtime_proof.py"
    assert script.exists(), f"runtime_proof.py not found at {script}"

    # The script's own watchdog times out at 60s and waits up to 90s
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=180,
    )

    # Exit code 2 = crash; anything else is acceptable for this smoke test.
    assert result.returncode != 2, (
        f"runtime_proof.py crashed (exit 2):\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    # The script should always log its outcome line.
    combined = result.stdout + result.stderr
    assert "RUNTIME PROOF RESULTS" in combined, (
        "runtime_proof.py did not reach its results summary, "
        "unexpected early exit.\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


# ``runtime_test_runner.py`` is Windows-only (uses ``ctypes.windll``)


@pytest.mark.slow
def test_runtime_test_runner_parses() -> None:
    """``runtime_test_runner.py`` must parse without syntax errors."""
    script = MANUAL_DIR / "runtime_test_runner.py"
    source = script.read_text()
    ast.parse(source)  # raises SyntaxError on failure
    # Quick sanity: the script still exposes the ``run`` alias added
    assert "run = main" in source, (
        "runtime_test_runner.py must expose ``run = main`` so the slow test wrapper has a stable callable name"
    )


@pytest.mark.slow
@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only: runtime_test_runner.py uses ctypes.windll (Win32 API) at import time",
)
def test_runtime_test_runner_imports_on_windows() -> None:
    """On Windows, ``runtime_test_runner.py`` must import without error."""
    script = MANUAL_DIR / "runtime_test_runner.py"
    module = _load_script_module(script, "manual_runtime_test_runner")
    assert callable(module.run), "runtime_test_runner.run must be callable"
    assert callable(module.main), "runtime_test_runner.main must be callable"
