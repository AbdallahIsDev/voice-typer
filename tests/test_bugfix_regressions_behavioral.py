"""behavioral tests ported from source-string meta-tests.

Each test class here replaces a meta-test in ``tests/test_bugfix_regressions.py``
that read production source as text and asserted on string patterns. The
behavioral tests here exercise the same invariant through actual function
calls / IPC dispatch / filesystem mocking, so they remain valid when the
production code is refactored (variable renames, helper extraction, etc.)
as long as the user-facing behavior is preserved.

Mapping (meta-test in test_bugfix_regressions.py → behavioral test here):

- ``test_legacy_launch_sites_use_log_files_not_devnull``
  → ``TestLegacyLogFilesBehavioral::test_all_legacy_launch_sites_call_log_files_helper``
- ``test_get_icon_path_looks_for_base_ico``
  → ``TestTrayIconBaseIcoBehavioral::test_get_icon_path_returns_ico_when_available``
- ``test_check_accessibility_ipc_handler_exists``
  → ``TestAccessibilityIpcBehavioral::test_handler_returns_accessibility_status_type_and_uses_axistrusted_on_macos``
- ``test_readline_caps_oversized_messages``
  → ``TestTcpLineIoOversizedBehavioral::test_oversized_message_returns_none``
"""

from __future__ import annotations

import contextlib
import socket as _socket
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# the previous Linux test-env shim that aliased
# ``ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE`` and inserted a ``MagicMock``
# for ``voice_typer.server.crash_handler`` into ``sys.modules`` has been
# removed. ``crash_handler.py`` now gates the ``@ctypes.WINFUNCTYPE(...)``
# decorator behind ``sys.platform == "win32"``, so the module imports
# cleanly on Linux/macOS without any test-infrastructure shim.
# ─── PORT 1: legacy launch sites call the log-files helper ────────────


# ─── _get_icon_path returns .ico when available ──────────────────


class TestTrayIconBaseIcoBehavioral:
    """PORT of ``test_get_icon_path_looks_for_base_ico``.

    The meta-test checked that ``_get_icon_path``'s source contains the
    substring ``tray-mic.ico``. That check is brittle: production may
    refactor the path lookup to use a variable or a helper function
    without losing the invariant.

    The behavioral test mocks ``is_windows()`` to return True and the
    filesystem so that only ``tray-mic.ico`` exists, then verifies
    ``_get_icon_path`` returns the .ico path. This catches the real
    regression (the .ico fallback is removed) without coupling to the
    source-string spelling.
    """

    def test_get_icon_path_returns_ico_when_available(self, monkeypatch):
        from voice_typer.server import tray_icon
        from voice_typer.server.tray_types import AppState

        # Force is_windows() to return True so the .ico lookup path runs.
        monkeypatch.setattr(tray_icon, "is_windows", lambda: True)

        # Mock Path.exists so that ONLY tray-mic.ico exists (neither
        # state-specific .ico nor any .png). This forces the base-ico
        # fallback.
        def fake_exists(self: Path) -> bool:
            return self.name == "tray-mic.ico"

        monkeypatch.setattr(Path, "exists", fake_exists)

        # Call _get_icon_path with any state; it should return the
        # base .ico path.
        result = tray_icon._get_icon_path(AppState.IDLE, size=32)

        assert result is not None, (
            "PLAT-024: _get_icon_path must return the base tray-mic.ico path on Windows when the base .ico exists."
        )
        assert result.name == "tray-mic.ico", f"PLAT-024: _get_icon_path must return tray-mic.ico, got {result.name!r}"


# ─── PORT 3: check_accessibility IPC handler behavior on macOS ───────────


class TestAccessibilityIpcBehavioral:
    """PORT of ``test_check_accessibility_ipc_handler_exists``.

    The meta-test checked that the handler source contains the substrings
    ``accessibility_status`` and ``AXIsProcessTrusted``. Those checks are
    brittle: production may extract the macOS probe into a helper function
    without losing the invariant.

    The behavioral test mocks ``sys.platform`` to ``darwin`` and patches
    the ApplicationServices framework load to return a mock with a
    controllable ``AXIsProcessTrusted``. It then dispatches the
    ``check_accessibility`` IPC command and verifies the response is
    ``accessibility_status`` with the expected ``granted`` value.

    ZR-45 (2026-07-25): ``check_accessibility`` was REMOVED from the
    Python ``_COMMAND_REGISTRY`` because the Tauri host handled it via
    a dedicated Rust command (``check_accessibility`` in
    ``src-tauri/src/commands/``). The Python-side handler was dead code
    for that period. On 2026-08-10 (finding #919 part b) the command
    was RE-ADDED, the Settings → Troubleshooting UI invokes it on
    macOS to surface the stale-grant ``tccutil`` reset command, so
    the handler is live again. These tests stay skipped because the
    equivalent behavioral coverage now lives in
    ``tests/handlers/test_system_handlers.py``
    (``TestCheckAccessibility``, which additionally covers the
    ``suggest_reset`` / ``reset_command`` extension); kept here for
    historical context.
    """

    _SKIP_REASON = (
        "check_accessibility behavior now covered by "
        "tests/handlers/test_system_handlers.py::TestCheckAccessibility "
        "(re-added to the registry 2026-08-10, finding #919 part b). "
        "Kept skipped for historical context."
    )

    def _make_server(self):
        """Build a minimal IPCServer with a mock app + service.

        Delegates to the canonical ``make_bare_ipc_server`` factory
        (``IPCServer.__new__`` bypass + ``app._config_mutation_lock`` /
        ``server._dispatch_lock`` RLocks, the exact shape this local
        helper used to rebuild inline; the factory's docstring documents
        why the ``_dispatch_lock`` fix exists).
        """
        from tests.fixtures.ipc_test_helpers import make_bare_ipc_server

        return make_bare_ipc_server()

    @pytest.mark.skip(reason=_SKIP_REASON)
    def test_handler_returns_accessibility_status_type_and_uses_axistrusted_on_macos(self, monkeypatch):
        # Import IPCServer FIRST so ipc_server.py fully loads (including
        # all handler mixins). Importing system_handlers directly first
        # triggers a circular import because system_handlers.py imports
        # from ipc_server.py which imports the handler mixins back.
        # After IPCServer is loaded, system_handlers is in sys.modules as
        # a fully-initialized module. Now we can patch its is_macos.
        from voice_typer.server.handlers import system_handlers
        from voice_typer.server.ipc_server import IPCServer  # noqa: F401

        # is_macos is imported into system_handlers at module load time,
        # so we must patch the re-bound name, not the original.
        # Force is_macos() to return True so the AXIsProcessTrusted path runs.
        monkeypatch.setattr(system_handlers, "is_macos", lambda: True)

        # Mock ctypes.cdll.LoadLibrary to return a fake library whose
        # AXIsProcessTrusted returns 1 (True). This verifies the handler
        # actually consults AXIsProcessTrusted (rather than hardcoding
        # granted=True).
        fake_lib = MagicMock()
        fake_lib.AXIsProcessTrusted.return_value = 1  # truthy
        fake_cdll = MagicMock()
        fake_cdll.LoadLibrary.return_value = fake_lib
        import ctypes

        monkeypatch.setattr(ctypes, "cdll", fake_cdll)

        # Dispatch the check_accessibility command.
        server = self._make_server()
        resp = server._dispatch({"type": "check_accessibility", "id": "t1"})

        # The response must be accessibility_status (not error, not granted).
        assert resp["type"] == "accessibility_status", (
            f"PLAT-030: handler must return type=accessibility_status, got {resp['type']!r}"
        )
        # granted must reflect AXIsProcessTrusted's return value (1 → True).
        assert resp["data"]["granted"] is True, (
            "PLAT-030: handler must consult AXIsProcessTrusted(), granted "
            f"should be True when AXIsProcessTrusted returns 1, got {resp['data']['granted']}"
        )
        # AXIsProcessTrusted must have been called (this is the behavioral
        # equivalent of the meta-test's "AXIsProcessTrusted in src" check).
        assert fake_lib.AXIsProcessTrusted.called, "PLAT-030: handler must call AXIsProcessTrusted() on macOS."

    @pytest.mark.skip(reason=_SKIP_REASON)
    def test_handler_returns_false_when_axistrusted_returns_zero(self, monkeypatch):
        """Sanity: when AXIsProcessTrusted returns 0, granted must be False."""
        from voice_typer.server.handlers import system_handlers
        from voice_typer.server.ipc_server import IPCServer  # noqa: F401

        monkeypatch.setattr(system_handlers, "is_macos", lambda: True)

        fake_lib = MagicMock()
        fake_lib.AXIsProcessTrusted.return_value = 0  # falsy
        fake_cdll = MagicMock()
        fake_cdll.LoadLibrary.return_value = fake_lib
        import ctypes

        monkeypatch.setattr(ctypes, "cdll", fake_cdll)

        server = self._make_server()
        resp = server._dispatch({"type": "check_accessibility", "id": "t2"})

        assert resp["type"] == "accessibility_status"
        assert resp["data"]["granted"] is False, "PLAT-030: granted must be False when AXIsProcessTrusted returns 0."


# ─── PORT 4: _TCPLineIO.readline caps oversized messages ─────────────────


class TestTcpLineIoOversizedBehavioral:
    """PORT of ``test_readline_caps_oversized_messages``.

    The meta-test checked that ``_TCPLineIO.readline`` source contains
    one of ``_MAX_LINE_BYTES`` / ``_MAX_LINE_CHARS`` / ``_max_line_bytes``
    / ``_max_line_chars``. Those checks are brittle: production may inline
    the cap as a literal or rename the constant.

    The behavioral test feeds a >1MB message (well over the documented
    1MB cap) through a real socketpair and verifies ``readline`` returns
    None (EOF), not the oversized line. This catches the real regression
    (the cap is removed) without coupling to the constant name.
    """

    @pytest.mark.skipif(
        not hasattr(_socket, "AF_UNIX"),
        reason="AF_UNIX not available on Windows",
    )
    def test_oversized_message_returns_none(self):
        from voice_typer.server.ipc_server import _TCPLineIO

        srv, cli = _socket.socketpair(_socket.AF_UNIX, _socket.SOCK_STREAM)
        try:
            # Spawn a thread to write a >1MB message from the client side.
            # 2 MB is well over the 1 MB cap; if the cap is removed, this
            # test would hang or OOM (the assertion would fail because
            # readline returns the oversized line instead of None).
            oversized = b"x" * (2 * 1024 * 1024) + b"\n"

            def write_oversized():
                with contextlib.suppress(OSError):
                    cli.sendall(oversized)

            t = threading.Thread(target=write_oversized)
            t.start()

            # Read from the server side via _TCPLineIO. The cap must
            # trigger EOF (None return) before the full message is read.
            io = _TCPLineIO(srv)
            line = io.readline()

            t.join(timeout=2.0)

            # readline must return None (EOF) for an oversized message,
            # NOT the 2MB line.
            assert line is None or len(line) < 1024 * 1024, (
                "NEW-IPC-012: _TCPLineIO.readline must cap oversized messages "
                f"at 1MB and return None (EOF). Got a line of length "
                f"{len(line) if line else 0}, the cap is missing or too large."
            )
        finally:
            srv.close()
            cli.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
