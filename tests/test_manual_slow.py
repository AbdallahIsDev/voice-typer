"""TASK-013: slow-wrapped versions of the manual diagnostic scripts.

The scripts under ``tests/manual/`` were originally designed to be run
by hand (``python tests/manual/runtime_proof.py``). This module wraps
each one in a ``@pytest.mark.slow`` test so they can also run in CI
behind the ``--slow`` flag (see ``tests/conftest.py``).

Design rules
------------

1. **Skip, don't fail, in CI**: heavy / hardware-only deps are gated
   with ``pytest.importorskip`` (for numpy), an
   ``importlib.util.find_spec`` check (for ``faster_whisper`` —
   ``importorskip`` is fooled by the autouse ``mock_heavy_imports``
   fixture which installs a MagicMock in ``sys.modules``), or a
   ``sys.platform`` check (for the Win32-only
   ``runtime_test_runner.py``).
2. **Don't duplicate coverage**: each manual script is already
   partially covered by the regular pytest suite (see
   ``tests/manual/README.md``). These slow tests exist to catch
   regressions in the *scripts themselves* (e.g. a refactor breaking
   ``runtime_proof.py``'s exit-code contract) and to give developers a
   single ``pytest --slow tests/test_manual_slow.py`` command for the
   end-to-end paths.
3. **Preserve script execution**: every script under ``tests/manual/``
   is still runnable as ``python tests/manual/<name>.py`` — we only
   import their ``run()``/``main()`` callables here, we never rewrite
   the script bodies.

Running
-------

Default (slow tests skipped)::

    pytest tests/test_manual_slow.py -v

Opt in to the slow tests::

    pytest tests/test_manual_slow.py -v --slow
"""

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
    """Load a script file as an importable module.

    ``tests/manual/`` is intentionally NOT a Python package (no
    ``__init__.py``) — these are scripts, not a library. ``spec_from_file_location``
    lets us load a single script as a module without adding the
    package marker.
    """
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None, (
        f"could not build import spec for {script_path}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ─── Deprecated-script contract ────────────────────────────────────────
# ``diagnose_f2.py`` and ``cublas_fallback.py`` were written against the
# deleted ``voice_typer.app`` (Electron) module and now exit with a
# deprecation message. The slow test verifies that contract — if a
# future refactor accidentally removes the deprecation notice, this
# test fails immediately.


@pytest.mark.slow
def test_diagnose_f2_deprecated_contract(capsys: pytest.CaptureFixture[str]) -> None:
    """``diagnose_f2.run()`` must return exit code 2 and print a
    deprecation notice pointing at the modern replacement tests.
    """
    script = MANUAL_DIR / "diagnose_f2.py"
    module = _load_script_module(script, "manual_diagnose_f2")
    exit_code = module.run()
    assert exit_code == 2, f"expected exit code 2, got {exit_code}"
    captured = capsys.readouterr()
    assert "DEPRECATED" in captured.err, (
        "diagnose_f2.run() must print a DEPRECATED notice on stderr; "
        f"got stderr={captured.err!r}"
    )
    assert "test_round8_e2e" in captured.err, (
        "diagnose_f2.run() must point users at the modern replacement tests"
    )


@pytest.mark.slow
def test_cublas_fallback_deprecated_contract(capsys: pytest.CaptureFixture[str]) -> None:
    """``cublas_fallback.run()`` must return exit code 2 and print a
    deprecation notice pointing at ``TestFallbackChain``.
    """
    script = MANUAL_DIR / "cublas_fallback.py"
    module = _load_script_module(script, "manual_cublas_fallback")
    exit_code = module.run()
    assert exit_code == 2, f"expected exit code 2, got {exit_code}"
    captured = capsys.readouterr()
    assert "DEPRECATED" in captured.err, (
        "cublas_fallback.run() must print a DEPRECATED notice on stderr; "
        f"got stderr={captured.err!r}"
    )
    assert "TestFallbackChain" in captured.err, (
        "cublas_fallback.run() must point users at TestFallbackChain"
    )


# ─── runtime_proof.py end-to-end smoke ─────────────────────────────────
# ``runtime_proof.py`` exercises the real ``TranscriptionEngine`` with
# synthetic audio. It needs numpy (top-level import) and faster_whisper
# (lazy import inside ``TranscriptionEngine.load()``). We run it as a
# subprocess so it gets a clean Python interpreter — without the
# autouse ``mock_heavy_imports`` fixture that would otherwise mock
# ``faster_whisper`` and make the test trivially pass.


def _real_module_available(modname: str) -> bool:
    """Return True iff ``modname`` is REALLY importable on disk.

    ``importlib.util.find_spec`` ignores ``sys.modules`` (which the
    autouse ``mock_heavy_imports`` fixture populates with MagicMocks
    for sounddevice / faster_whisper / pynput / pystray / PIL /
    pyperclip), so this is the correct way to ask "is the real
    package installed?".
    """
    try:
        return importlib.util.find_spec(modname) is not None
    except (ImportError, ValueError):
        # find_spec can raise ValueError for malformed module names or
        # ImportError if a parent package's __init__ blows up. Either
        # way, treat as "not available".
        return False


@pytest.mark.slow
def test_runtime_proof_smoke() -> None:
    """Run ``runtime_proof.py`` end-to-end and assert it exits cleanly.

    Exit codes (defined by the script's ``__main__`` block):
      - 0: transcription succeeded, state fully recovered (Outcome A)
      - 1: transcription failed but recovery worked (Outcome B)
      - 2: unexpected crash (this is the only failing exit code)

    The script needs ``numpy`` (top-level) and ``faster_whisper``
    (lazy). We skip if either is missing — running this in a CI
    matrix without a real Whisper model would be noise, not a signal.
    """
    pytest.importorskip("numpy", reason="runtime_proof.py imports numpy at top level")
    if not _real_module_available("faster_whisper"):
        pytest.skip("faster_whisper not installed — runtime_proof needs a real model")
    if not _real_module_available("voice_typer"):
        pytest.skip("voice_typer not installed — run `pip install -e .` first")

    script = MANUAL_DIR / "runtime_proof.py"
    assert script.exists(), f"runtime_proof.py not found at {script}"

    # The script's own watchdog times out at 60s and waits up to 90s
    # for the transcription thread to join — give a generous margin.
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=180,
    )

    # Exit code 2 = crash; anything else is acceptable for this smoke test.
    assert result.returncode != 2, (
        f"runtime_proof.py crashed (exit 2):\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    # The script should always log its outcome line.
    combined = result.stdout + result.stderr
    assert "RUNTIME PROOF RESULTS" in combined, (
        "runtime_proof.py did not reach its results summary — "
        "unexpected early exit.\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


# ─── runtime_test_runner.py smoke ──────────────────────────────────────
# ``runtime_test_runner.py`` is Windows-only (uses ``ctypes.windll``)
# and starts the REAL Voice Typer app as a subprocess. It is NOT
# runnable in CI (no display, no microphone, no app install). We only
# verify it parses + (on Windows) imports cleanly.


@pytest.mark.slow
def test_runtime_test_runner_parses() -> None:
    """``runtime_test_runner.py`` must parse without syntax errors.

    This is the cheapest invariant we can check on non-Windows hosts
    (where the script's top-level ``import ctypes.wintypes`` would
    raise ImportError). On Windows we additionally verify it imports
    cleanly — but we never invoke ``main()`` because that would
    launch the real app and send simulated F2 keypresses.
    """
    script = MANUAL_DIR / "runtime_test_runner.py"
    source = script.read_text()
    ast.parse(source)  # raises SyntaxError on failure
    # Quick sanity: the script still exposes the ``run`` alias added
    # in TASK-013 so this test module has a stable import target.
    assert "run = main" in source, (
        "runtime_test_runner.py must expose ``run = main`` so the slow "
        "test wrapper has a stable callable name"
    )

    # On Windows, also verify the module imports without error. We
    # deliberately do NOT call ``main()`` here — it spawns the real
    # app and simulates F2 keypresses, which is incompatible with
    # headless CI.
    if sys.platform != "win32":
        pytest.skip("runtime_test_runner.py uses ctypes.windll (Windows-only)")
    module = _load_script_module(script, "manual_runtime_test_runner")
    assert callable(module.run), "runtime_test_runner.run must be callable"
    assert callable(module.main), "runtime_test_runner.main must be callable"
