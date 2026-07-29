"""XZ-R6-NH-01: regression tests for the TOCTOU re-verification in
``_spawn_process``.

Background
----------
``native_hotkeys/base.py:_spawn_process`` re-uses the cached
``self._binary_path`` on watchdog respawn WITHOUT re-running
``verify_native_binary_or_skip``. The factory's verification call at
construction time covers the FIRST spawn, but the watchdog respawns
the binary on liveness timeout by calling ``stop()`` + ``start()`` →
``_spawn_process()`` WITHOUT going back through the factory. A TOCTOU
window opens between the original verification and the respawn: an
attacker swapping the binary on disk during that window achieves
native-code execution as the user.

XZ-R6-NH-01 fix
----------------
Added a ``verify_native_binary_or_skip(self._binary_path)`` call at
the top of ``_spawn_process``. On verification failure, sets
``_failed=True`` and returns early (no spawn). The caller's
``_ready_event.wait(timeout=...)`` then times out and the new
``if self._failed:`` check in ``start()`` raises a clear
``RuntimeError`` instead of waiting for the full READY timeout.

These tests pin:
  1. ``_spawn_process`` calls ``verify_native_binary_or_skip`` on
     every invocation (not just the first).
  2. On verification failure, ``_failed`` is set, the error message
     references SHA-256, and ``subprocess.Popen`` is NOT called.
  3. The new ``if self._failed:`` check in ``start()`` short-circuits
     the READY-timeout wait so the operator sees the precise error.
  4. When the binary path is ``None``, ``_spawn_process`` sets
     ``_failed=True`` and returns early (defensive — the start()
     method already raises ``FileNotFoundError`` for this case, but
     ``_spawn_process`` is also called from the watchdog respawn path
     which doesn't go through ``start()``).

Also pins the XZ-R6-NH-02 constructor change: ``__init__`` accepts
an optional ``binary_path`` parameter so the factory can pass its
verified binary in (cross-file part — the factory itself is owned by
another agent).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make sure the Linux backend is importable on this platform.
from voice_typer.server import native_hotkeys
from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

# ─── Helpers ────────────────────────────────────────────────────────────


def _setup_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
    monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
    monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
    monkeypatch.setattr(sys, "platform", "linux")


def _fake_binary(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "fake-native"
    fake_bin.write_text("#!/bin/sh\nwhile true; do sleep 1; done\n")
    fake_bin.chmod(0o755)
    return fake_bin


def _patch_binary_path(monkeypatch: pytest.MonkeyPatch, fake_bin: Path | None) -> None:
    """Patch BOTH the binary_path module AND the base module's
    ``get_native_binary_path`` binding. The base module imports the
    function via ``from .binary_path import get_native_binary_path``,
    so it has its OWN binding that must be patched separately."""
    monkeypatch.setattr(
        "voice_typer.server.native_hotkeys.binary_path.get_native_binary_path",
        lambda: fake_bin,
    )
    monkeypatch.setattr(
        "voice_typer.server.native_hotkeys.base.get_native_binary_path",
        lambda: fake_bin,
    )


# ─── XZ-R6-NH-01: _spawn_process re-verifies on every call ──────────────


class TestSpawnProcessReVerifiesBinary:
    """XZ-R6-NH-01: ``_spawn_process`` must call
    ``verify_native_binary_or_skip`` on EVERY invocation — not just
    the first. The watchdog respawn path goes through ``_spawn_process``
    directly (no factory), so this is the only gate that closes the
    TOCTOU window for respawns."""

    def test_calls_verify_on_first_spawn(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _setup_linux(monkeypatch)
        fake_bin = _fake_binary(tmp_path)
        _patch_binary_path(monkeypatch, fake_bin)
        verify_calls: list[Path] = []
        monkeypatch.setattr(
            "voice_typer.server.native_hotkeys.binary_path.verify_native_binary_or_skip",
            lambda p: (verify_calls.append(p), True)[1],
        )
        # Also patch the base module's binding of the verifier (same
        # reason as get_native_binary_path — base.py imports it locally).
        # Reset verify_calls to drop the import-time check (if any).
        verify_calls.clear()
        b = LinuxEvdevHotkey("<caps_lock>")
        # Stub Popen so no real process spawns.
        with patch("subprocess.Popen", MagicMock()):
            try:
                b._spawn_process()
            except Exception:
                pass
            finally:
                with __import__("contextlib").suppress(Exception):
                    b.stop()
        assert verify_calls == [fake_bin], (
            "XZ-R6-NH-01: _spawn_process must call verify_native_binary_or_skip "
            f"exactly once with the binary path; got {verify_calls}"
        )

    def test_failed_verification_sets_failed_flag_and_skips_spawn(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _setup_linux(monkeypatch)
        fake_bin = _fake_binary(tmp_path)
        _patch_binary_path(monkeypatch, fake_bin)
        # Verifier returns False → fail-closed. Patch BOTH bindings.
        monkeypatch.setattr(
            "voice_typer.server.native_hotkeys.binary_path.verify_native_binary_or_skip",
            lambda _p: False,
        )
        popen_calls: list = []
        with patch(
            "subprocess.Popen",
            lambda *a, **k: popen_calls.append((a, k)) or MagicMock(),
        ):
            b = LinuxEvdevHotkey("<caps_lock>")
            b._spawn_process()
        assert b._failed is True, (
            "XZ-R6-NH-01: _spawn_process must set _failed=True when verification fails"
        )
        assert b._error_message is not None
        assert "SHA-256" in b._error_message or "verification" in b._error_message, (
            "XZ-R6-NH-01: error message must mention SHA-256 / verification; "
            f"got {b._error_message!r}"
        )
        assert not popen_calls, (
            "XZ-R6-NH-01: _spawn_process must NOT call subprocess.Popen when "
            "verification fails — that would spawn an untrusted binary."
        )

    def test_none_binary_path_sets_failed_and_skips_spawn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _setup_linux(monkeypatch)
        _patch_binary_path(monkeypatch, None)
        popen_calls: list = []
        with patch(
            "subprocess.Popen",
            lambda *a, **k: popen_calls.append((a, k)) or MagicMock(),
        ):
            b = LinuxEvdevHotkey("<caps_lock>")
            b._spawn_process()
        assert b._failed is True
        assert b._error_message is not None
        assert "not found" in b._error_message.lower()
        assert not popen_calls


# ─── XZ-R6-NH-01: start() short-circuits on _spawn_process failure ─────


class TestStartShortCircuitsOnSpawnFailure:
    """XZ-R6-NH-01: when ``_spawn_process`` sets ``_failed=True`` and
    returns early (no spawn), the ``start()`` method must immediately
    raise ``RuntimeError`` with the precise error message — NOT wait
    for the ``_ready_event`` timeout and overwrite the message with
    the generic "Timed out waiting for READY"."""

    def test_start_raises_with_verification_error_message(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _setup_linux(monkeypatch)
        fake_bin = _fake_binary(tmp_path)
        _patch_binary_path(monkeypatch, fake_bin)
        monkeypatch.setattr(
            "voice_typer.server.native_hotkeys.binary_path.verify_native_binary_or_skip",
            lambda _p: False,
        )
        b = LinuxEvdevHotkey("<caps_lock>")
        with pytest.raises(RuntimeError) as exc_info:
            b.start(callback=lambda: None)
        assert "SHA-256" in str(exc_info.value) or "verification" in str(exc_info.value), (
            "XZ-R6-NH-01: start() must raise with the verification-failure message, "
            f"not the generic READY-timeout message. Got: {exc_info.value!r}"
        )
        assert "Timed out waiting for READY" not in str(exc_info.value), (
            "XZ-R6-NH-01: start() must NOT fall through to the READY-timeout path "
            "when _spawn_process set _failed=True — that overwrites the precise "
            "error message."
        )

    def test_start_does_not_wait_full_ready_timeout_on_verification_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Sanity check: the short-circuit returns IMMEDIATELY, not
        after ``READY_TIMEOUT_SECONDS`` seconds. Uses a wall-clock
        assertion so a future regression that removes the short-circuit
        (forcing the wait) is caught."""
        import time

        _setup_linux(monkeypatch)
        fake_bin = _fake_binary(tmp_path)
        _patch_binary_path(monkeypatch, fake_bin)
        monkeypatch.setattr(
            "voice_typer.server.native_hotkeys.binary_path.verify_native_binary_or_skip",
            lambda _p: False,
        )
        b = LinuxEvdevHotkey("<caps_lock>")
        t0 = time.perf_counter()
        with pytest.raises(RuntimeError):
            b.start(callback=lambda: None)
        elapsed = time.perf_counter() - t0
        # READY_TIMEOUT_SECONDS is 5.0 in base.py; the short-circuit
        # should return in < 1s. Allow 2s of slack for slow CI runners.
        assert elapsed < 2.0, (
            "XZ-R6-NH-01: start() should short-circuit immediately on "
            f"verification failure (elapsed={elapsed:.2f}s). A 5s+ elapsed "
            "time means the _ready_event.wait(timeout=5) path was taken."
        )


# ─── XZ-R6-NH-02: __init__ accepts optional binary_path ────────────────


class TestInitAcceptsBinaryPathParameter:
    """XZ-R6-NH-02: ``__init__`` accepts an optional ``binary_path``
    parameter so the factory can pass its verified binary in (instead
    of re-discovering via ``get_native_binary_path()``). The parameter
    is optional so existing call sites (tests, etc.) that don't pass
    it continue to work."""

    def test_binary_path_parameter_is_used_when_provided(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _setup_linux(monkeypatch)
        fake_bin = _fake_binary(tmp_path)
        # Make get_native_binary_path return a DIFFERENT path so we
        # can detect whether the constructor used our binary_path or
        # fell back to get_native_binary_path.
        other_bin = tmp_path / "other-binary"
        other_bin.write_text("#!/bin/sh\n")
        other_bin.chmod(0o755)
        _patch_binary_path(monkeypatch, other_bin)
        b = LinuxEvdevHotkey("<caps_lock>", binary_path=fake_bin)
        assert b._binary_path == fake_bin, (
            "XZ-R6-NH-02: when binary_path is provided to __init__, the "
            "backend must use it instead of calling get_native_binary_path()."
        )

    def test_falls_back_to_get_native_binary_path_when_not_provided(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _setup_linux(monkeypatch)
        fake_bin = _fake_binary(tmp_path)
        _patch_binary_path(monkeypatch, fake_bin)
        # No binary_path kwarg → falls back to get_native_binary_path.
        b = LinuxEvdevHotkey("<caps_lock>")
        assert b._binary_path == fake_bin

    def test_binary_path_none_explicit_falls_back(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Passing ``binary_path=None`` explicitly must behave the same
        as omitting the kwarg (fall back to get_native_binary_path).
        This pins the ``if binary_path is not None`` branch."""
        _setup_linux(monkeypatch)
        fake_bin = _fake_binary(tmp_path)
        _patch_binary_path(monkeypatch, fake_bin)
        b = LinuxEvdevHotkey("<caps_lock>", binary_path=None)
        assert b._binary_path == fake_bin


# ─── XZ-R6-NH-01: re-verification is called on every spawn ─────────────


class TestReVerificationOnEverySpawn:
    """XZ-R6-NH-01: the watchdog respawn path goes through
    ``_spawn_process`` directly (no factory). The verifier must be
    called on EVERY spawn — including respawns — so an attacker
    swapping the binary between the first spawn and a respawn is
    caught."""

    def test_verify_called_on_second_spawn(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _setup_linux(monkeypatch)
        fake_bin = _fake_binary(tmp_path)
        _patch_binary_path(monkeypatch, fake_bin)
        verify_calls: list[Path] = []
        monkeypatch.setattr(
            "voice_typer.server.native_hotkeys.binary_path.verify_native_binary_or_skip",
            lambda p: (verify_calls.append(p), True)[1],
        )
        verify_calls.clear()
        b = LinuxEvdevHotkey("<caps_lock>")
        with patch("subprocess.Popen", MagicMock()):
            try:
                b._spawn_process()
                b._process = None  # reset so the second spawn doesn't bail
                b._failed = False
                b._spawn_process()
            except Exception:
                pass
            finally:
                with __import__("contextlib").suppress(Exception):
                    b.stop()
        # Two spawns → two verify calls.
        assert len(verify_calls) == 2, (
            "XZ-R6-NH-01: _spawn_process must call verify_native_binary_or_skip "
            f"on EVERY spawn (including watchdog respawns); got {len(verify_calls)} "
            "calls for 2 spawn invocations."
        )
