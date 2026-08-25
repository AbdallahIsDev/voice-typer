"""Regression tests for ``scripts/build/generate_beeps.py --check``.

The original ``--check`` only verified that the freshly-generated START
and STOP URLs were distinct from each other — it did NOT read
``sound-manager.ts``, so a stale or accidentally-collapsed pair of
constants committed to the source file would pass the regression guard
(false assurance). These tests pin the stricter behavior:

* ``--check`` passes when the committed constants match the freshly
  generated URLs (smoke test against the real source file).
* ``--check`` fails when the two committed constants are byte-for-byte
  identical (the original regression this script exists to prevent).
* ``--check`` fails when either committed constant drifts from the
  freshly generated URL (catches a half-applied ``--write`` or a
  hand-edit that only updates one of the two constants).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Resolve the script path relative to the repo root (tests/ is one
# level below the root; scripts/build/generate_beeps.py is two levels
# below the root).
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "build" / "generate_beeps.py"


def _load_generate_beeps():
    """Load ``generate_beeps.py`` as an isolated module.

    The script lives under ``scripts/build/`` which is not a Python
    package, so we use ``importlib.util`` instead of a regular import.
    Each call returns a fresh module instance so monkeypatching
    ``SOUND_MANAGER_PATH`` in one test does not bleed into another.
    """
    spec = importlib.util.spec_from_file_location("generate_beeps_under_test", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_sound_manager(path: Path, start_url: str, stop_url: str) -> None:
    """Write a minimal sound-manager.ts with the two constants.

    The format mirrors the real source file: the constant declaration
    spans two lines (``const NAME =`` on one line, the data URL string
    on the next, indented with a tab). This exercises the multi-line
    tolerance of the regex in ``_read_sound_manager_urls``.
    """
    path.write_text(
        "/* preamble */\n"
        f'const START_BEEP_WAV =\n\t"{start_url}";\n'
        f'const STOP_BEEP_WAV =\n\t"{stop_url}";\n'
        "/* epilogue */\n",
        encoding="utf-8",
    )


def test_check_passes_on_real_sound_manager(capsys):
    """Smoke test: ``--check`` exits 0 against the real, healthy source file.

    This guards against the regression where the regex in
    ``_read_sound_manager_urls`` stops matching the committed
    sound-manager.ts layout (e.g. someone reformats the constant
    declaration in a way the pattern no longer accepts).
    """
    mod = _load_generate_beeps()
    original_argv = sys.argv
    sys.argv = ["generate_beeps.py", "--check"]
    try:
        rc = mod.main()
    finally:
        sys.argv = original_argv
    out = capsys.readouterr()
    assert rc == 0, (
        f"--check should pass on the real sound-manager.ts; got rc={rc}.\nstderr:\n{out.err}\nstdout:\n{out.out}"
    )


def test_check_passes_when_constants_match_generated(monkeypatch, tmp_path, capsys):
    """``--check`` exits 0 when a fake sound-manager.ts carries the
    freshly-generated URLs in the multi-line layout."""
    mod = _load_generate_beeps()
    start_url = mod.generate_start_url()
    stop_url = mod.generate_stop_url()
    fake_sm = tmp_path / "sound-manager.ts"
    _write_sound_manager(fake_sm, start_url, stop_url)
    monkeypatch.setattr(mod, "SOUND_MANAGER_PATH", fake_sm)

    original_argv = sys.argv
    sys.argv = ["generate_beeps.py", "--check"]
    try:
        rc = mod.main()
    finally:
        sys.argv = original_argv
    assert rc == 0


def test_check_fails_when_constants_are_identical(monkeypatch, tmp_path, capsys):
    """``--check`` exits 1 when the two committed constants are byte-for-byte
    identical — the regression this script exists to prevent.

    The generated URLs are still distinct (so the generator itself is
    healthy); the regression is in the *source file*, which the old
    --check never read. The new --check must catch this.
    """
    mod = _load_generate_beeps()
    # Use a single bogus URL for BOTH constants. It does not match the
    # freshly generated output, but the first failure mode we hit
    # should be the "identical to each other" check (the script checks
    # that before checking drift).
    bogus = "data:audio/wav;base64,AAAA"
    fake_sm = tmp_path / "sound-manager.ts"
    _write_sound_manager(fake_sm, bogus, bogus)
    monkeypatch.setattr(mod, "SOUND_MANAGER_PATH", fake_sm)

    original_argv = sys.argv
    sys.argv = ["generate_beeps.py", "--check"]
    try:
        rc = mod.main()
    finally:
        sys.argv = original_argv
    err = capsys.readouterr().err
    assert rc == 1, f"--check should fail when committed constants are identical; got rc={rc}.\nstderr:\n{err}"
    assert "identical" in err.lower(), f"stderr should mention 'identical'; got:\n{err}"


def test_check_fails_when_constants_drift(monkeypatch, tmp_path, capsys):
    """``--check`` exits 1 when the committed constants are distinct from
    each other but do not match the freshly generated URLs (e.g. a
    half-applied ``--write`` or a hand-edit that only touched one
    constant)."""
    mod = _load_generate_beeps()
    # Two distinct bogus URLs that do NOT match the freshly generated
    # output — exercises the "drift" branch (not the "identical" branch).
    bogus_start = "data:audio/wav;base64,AAAA"
    bogus_stop = "data:audio/wav;base64,BBBB"
    fake_sm = tmp_path / "sound-manager.ts"
    _write_sound_manager(fake_sm, bogus_start, bogus_stop)
    monkeypatch.setattr(mod, "SOUND_MANAGER_PATH", fake_sm)

    original_argv = sys.argv
    sys.argv = ["generate_beeps.py", "--check"]
    try:
        rc = mod.main()
    finally:
        sys.argv = original_argv
    err = capsys.readouterr().err
    assert rc == 1, (
        f"--check should fail when committed constants drift from the generated URLs; got rc={rc}.\nstderr:\n{err}"
    )
    # The error message should mention "match" or "drift" — both
    # branches of the failure-message wording are acceptable.
    assert "match" in err.lower() or "drift" in err.lower(), f"stderr should mention match/drift; got:\n{err}"


def test_check_fails_when_sound_manager_missing(monkeypatch, tmp_path, capsys):
    """``--check`` exits 1 when sound-manager.ts is missing entirely."""
    mod = _load_generate_beeps()
    missing = tmp_path / "does-not-exist.ts"
    monkeypatch.setattr(mod, "SOUND_MANAGER_PATH", missing)

    original_argv = sys.argv
    sys.argv = ["generate_beeps.py", "--check"]
    try:
        rc = mod.main()
    finally:
        sys.argv = original_argv
    err = capsys.readouterr().err
    assert rc == 1
    assert "not found" in err.lower()


def test_read_sound_manager_urls_returns_full_data_urls():
    """``_read_sound_manager_urls`` reconstructs the full ``data:audio/wav;base64,...``
    strings from the committed constants (not just the base64 payload)."""
    mod = _load_generate_beeps()
    start_url, stop_url = mod._read_sound_manager_urls()
    assert start_url.startswith("data:audio/wav;base64,")
    assert stop_url.startswith("data:audio/wav;base64,")
    assert start_url != stop_url


if __name__ == "__main__":
    # Allow running this test file directly: ``python tests/test_beeps_check_regression.py``
    sys.exit(pytest.main([__file__, "-v", "--no-cov"]))
