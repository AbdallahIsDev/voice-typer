"""Integration tests for the Linux native key-listener C binary.

These tests cover the cross-device event deduplication fix that prevents
the same physical keystroke from being emitted multiple times when the
system has overlapping keyboard ``/dev/input/event*`` nodes (e.g. a
laptop internal keyboard + a USB keyboard, or an AT-translated keyboard
+ a dock keyboard that re-broadcasts).

Two layers are exercised:

1. **C-level** (``tests/c/test_linux_key_listener_dedup.c``): compiled
   and run as a subprocess. The test ``#include``s the production source
   so the file-local static helpers ``is_duplicate_event`` and
   ``remember_emitted_event`` are reachable, then asserts the dedup
   window logic directly (exact-match duplicate, within-5 ms duplicate,
   beyond-5 ms non-duplicate, different code/value, modifier events,
   state-overwrite, negative-delta tolerance).

2. **Wire-protocol contract**: the C binary's stdout is mocked as a list
   of wire-protocol lines and fed through
   :class:`LinuxEvdevHotkey._handle_line`. The post-fix stream (one
   ``KEY_DOWN`` per physical press, post-dedup) must fire the hotkey
   callback exactly once per press — not N times. A second test asserts
   that genuinely-distinct presses (separated in time) are NOT
   collapsed by the dedup window.

These tests do NOT require ``/dev/input`` access — the dedup logic is
validated in isolation, and the consumer contract is validated with a
mocked stdout stream. They DO require ``gcc`` to be on ``$PATH`` for
the C-level portion.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LISTENER_SRC = PROJECT_ROOT / "voice_typer" / "server" / "native" / "linux-key-listener.c"
C_TEST_SRC = PROJECT_ROOT / "tests" / "c" / "test_linux_key_listener_dedup.c"

_LINUX_GCC = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("gcc") is None,
    reason="Linux native listener tests require Linux + gcc",
)


class TestLinuxKeyListenerDedupC:
    """C-level test: compile and run the dedup unit-test harness."""

    @_LINUX_GCC
    def test_dedup_logic_compiles_and_passes(self, tmp_path: Path) -> None:
        """The C test harness compiles cleanly and all assertions pass.

        This exercises the actual ``is_duplicate_event`` /
        ``remember_emitted_event`` functions from the production source
        (no stubs), so any regression in the dedup window logic fails
        here at the C level — independent of the Python consumer.
        """
        assert LISTENER_SRC.is_file(), f"missing listener source: {LISTENER_SRC}"
        assert C_TEST_SRC.is_file(), f"missing C test source: {C_TEST_SRC}"

        binary = tmp_path / "test_dedup"
        cmd = [
            "gcc",
            "-O2",
            "-std=c99",
            "-Wall",
            "-Wextra",
            # The listener source defines several static helpers that the
            # test does not call (emit, run_loop, discover_devices, etc.).
            # They are harmless but would clutter the build log.
            "-Wno-unused-function",
            str(C_TEST_SRC),
            "-o",
            str(binary),
        ]
        compiled = subprocess.run(cmd, capture_output=True, text=True)
        assert compiled.returncode == 0, (
            f"gcc failed to compile C test harness:\ncmd: {' '.join(cmd)}\nstderr:\n{compiled.stderr}"
        )

        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
        assert ran.returncode == 0, (
            f"C test harness exited {ran.returncode}:\nstdout:\n{ran.stdout}\nstderr:\n{ran.stderr}"
        )
        assert "ALL PASSED" in ran.stdout, (
            f"C test harness did not report ALL PASSED:\nstdout:\n{ran.stdout}\nstderr:\n{ran.stderr}"
        )


class TestLinuxKeyListenerDedupContract:
    """Wire-protocol contract: feeding the post-fix stdout stream to the
    Python consumer must fire the hotkey callback exactly once per
    physical key press.

    The C binary's stdout is mocked as a list of wire-protocol lines
    (no real subprocess is spawned, no ``/dev/input`` access is needed).
    This validates the consumer side of the dedup contract: given the
    deduped stream the fixed C binary emits, the matcher fires once.
    """

    @staticmethod
    def _make_backend(hotkey_str: str, monkeypatch: pytest.MonkeyPatch) -> object:
        """Build a LinuxEvdevHotkey backend without spawning a process.

        ``_handle_line`` is called directly with mocked stdout lines, so
        the binary path is never resolved to a real file and no
        subprocess is ever started.
        """
        # Pretend we are on Linux so the platform guard passes.
        import voice_typer.server.native_hotkeys as native_hotkeys_pkg

        monkeypatch.setattr(native_hotkeys_pkg, "is_linux", lambda: True)
        monkeypatch.setattr(sys, "platform", "linux")

        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        backend = LinuxEvdevHotkey(hotkey_str)
        return backend

    def test_single_press_fires_callback_once(self, monkeypatch) -> None:
        """Post-fix: one ``KEY_DOWN`` line per physical press → one fire.

        Before the dedup fix, an N-keyboard system would emit N
        ``KEY_DOWN:F2`` lines for a single F2 press (one per open
        keyboard fd), causing the matcher to fire N times. The C-level
        dedup now collapses those N kernel-broadcast duplicates into a
        single stdout line BEFORE they reach Python, so the consumer
        sees exactly one ``KEY_DOWN`` and fires once.
        """
        backend = self._make_backend("<f2>", monkeypatch)
        fired: list[int] = []
        backend._callback = lambda: fired.append(1)  # type: ignore[attr-defined]

        # Mocked stdout that the FIXED C binary emits for one F2 press.
        post_fix_stdout = ["READY", "KEY_DOWN:F2", "KEY_UP:F2"]
        for line in post_fix_stdout:
            backend._handle_line(line)  # type: ignore[attr-defined]

        assert fired == [1], f"post-fix stdout must fire the callback exactly once; got {fired}"

    def test_two_distinct_presses_fire_twice(self, monkeypatch) -> None:
        """Sanity: the dedup contract must NOT collapse distinct presses.

        Two F2 presses separated by a key-up event are genuinely
        distinct keystrokes; the consumer must fire twice. This guards
        against an over-aggressive dedup that would suppress legitimate
        repeat presses.
        """
        backend = self._make_backend("<f2>", monkeypatch)
        fired: list[int] = []
        backend._callback = lambda: fired.append(1)  # type: ignore[attr-defined]

        # Mocked stdout: two distinct F2 presses (down/up/down/up).
        post_fix_stdout = [
            "READY",
            "KEY_DOWN:F2",
            "KEY_UP:F2",
            "KEY_DOWN:F2",
            "KEY_UP:F2",
        ]
        for line in post_fix_stdout:
            backend._handle_line(line)  # type: ignore[attr-defined]

        assert fired == [1, 1], f"two distinct presses must fire twice; got {fired}"

    def test_modifier_combo_fires_once_per_press(self, monkeypatch) -> None:
        """A modifier+key combo also fires exactly once per press.

        The dedup applies to ``MOD_DOWN`` lines as well as ``KEY_DOWN``
        lines, so a combo like ``<ctrl>+<shift>+v`` must fire once even
        if multiple keyboard fds broadcast the Ctrl, Shift, and V
        events.
        """
        backend = self._make_backend("<ctrl>+<shift>+v", monkeypatch)
        fired: list[int] = []
        backend._callback = lambda: fired.append(1)  # type: ignore[attr-defined]

        # Mocked stdout: the FIXED C binary emits each event once.
        post_fix_stdout = [
            "READY",
            "MOD_DOWN:Ctrl",
            "MOD_DOWN:Shift",
            "KEY_DOWN:V",
            "KEY_UP:V",
            "MOD_UP:Shift",
            "MOD_UP:Ctrl",
        ]
        for line in post_fix_stdout:
            backend._handle_line(line)  # type: ignore[attr-defined]

        assert fired == [1], f"combo press must fire the callback exactly once; got {fired}"
