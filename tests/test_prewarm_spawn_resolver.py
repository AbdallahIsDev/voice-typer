"""AB-18: ``spawn_background_prewarm`` delegates to ``resolve_prewarm_exe``.

Pre-AB-18, ``spawn_background_prewarm`` ignored the
``TAURI_SIDECAR`` / ``VOICE_TYPER_PREWARM_EXE`` env vars and
unconditionally used ``python -m voice_typer.server.prewarm`` —
paying a ~1-2 s Python interpreter bootstrap + module-imports tax on
every runtime re-spawn (called from ``model_manager.py`` after
``wait_for_prewarm`` times out).

AB-18: the function now delegates to ``resolve_prewarm_exe()``. When
the resolver returns a frozen exe path (single token), the spawn cmd
is ``[frozen_exe]``. When the resolver returns the dev-fallback
command line (``"<python>" -m voice_typer.server.prewarm``), the cmd
is ``shlex.split(resolved)`` (mirroring ``_prewarm_python`` /
``_prewarm_args`` in ``prewarm_scheduler_posix``). The Windows
``pythonw.exe`` preference is preserved on the dev-fallback branch.

These tests pin the AB-18 behaviour so a future revert (bypassing
``resolve_prewarm_exe``) fails loudly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from voice_typer.server import prewarm
from voice_typer.server.prewarm import process_tracker

# spawn delegates to resolve_prewarm_exe ──────────────────────


class TestSpawnDelegatesToResolver:
    """``spawn_background_prewarm`` must call ``resolve_prewarm_exe``."""

    def test_calls_resolve_prewarm_exe(self, monkeypatch):
        """A successful spawn calls ``resolve_prewarm_exe`` at least once."""
        resolver_calls: list = []

        def _tracking_resolver():
            resolver_calls.append(True)
            # Dev-fallback command line (no frozen exe in test env).
            import sys

            return f'"{sys.executable}" -m voice_typer.server.prewarm'

        monkeypatch.setattr(
            "voice_typer.server.prewarm_resolver.resolve_prewarm_exe",
            _tracking_resolver,
        )
        monkeypatch.setattr(prewarm, "is_windows", lambda: False)
        monkeypatch.setattr(prewarm, "is_prewarm_running", lambda: False)

        fake_proc = MagicMock()
        fake_proc.pid = 99999
        fake_popen = MagicMock(return_value=fake_proc)
        monkeypatch.setattr(
            "voice_typer.server.prewarm.process_tracker.subprocess.Popen",
            fake_popen,
        )

        pid = process_tracker.spawn_background_prewarm(force=True, trigger="manual")

        assert pid == 99999
        assert resolver_calls, "AB-18: spawn_background_prewarm must call resolve_prewarm_exe()"

    def test_frozen_exe_path_used_verbatim(self, monkeypatch, tmp_path):
        """When the resolver returns a frozen exe path, it becomes ``cmd[0]``.

        The frozen exe is a single token (no ``-m voice_typer...``
        args) — the frozen exe IS the module.
        """
        fake_exe = tmp_path / "prewarm-x86_64-unknown-linux-gnu"
        fake_exe.write_text("dummy")

        monkeypatch.setattr(
            "voice_typer.server.prewarm_resolver.resolve_prewarm_exe",
            lambda: str(fake_exe),
        )
        monkeypatch.setattr(prewarm, "is_windows", lambda: False)
        monkeypatch.setattr(prewarm, "is_prewarm_running", lambda: False)

        captured: dict = {}

        def _capturing_popen(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            fake_proc = MagicMock()
            fake_proc.pid = 4242
            return fake_proc

        monkeypatch.setattr(
            "voice_typer.server.prewarm.process_tracker.subprocess.Popen",
            _capturing_popen,
        )

        pid = process_tracker.spawn_background_prewarm(force=True, trigger="app-start")

        assert pid == 4242
        assert captured["cmd"][0] == str(fake_exe), f"AB-18: frozen exe path must be cmd[0]; got {captured['cmd']!r}"
        # The frozen exe IS the module — no ``-m`` flag must be appended.
        assert "-m" not in captured["cmd"], f"AB-18: frozen exe must not get '-m' args; got {captured['cmd']!r}"
        assert "voice_typer.server.prewarm" not in captured["cmd"], (
            f"AB-18: frozen exe must not get the module name as an arg; got {captured['cmd']!r}"
        )
        # But --force and --trigger are still appended.
        assert "--force" in captured["cmd"]
        assert "--trigger" in captured["cmd"]
        assert "app-start" in captured["cmd"]

    def test_dev_fallback_is_shlex_split(self, monkeypatch):
        """The dev-fallback command line is split via ``shlex.split``.

        Verifies the path with spaces case: a Python interpreter at
        ``/Users/My Name/python`` must end up as a single ``cmd[0]``
        token, not split on the space.
        """
        dev_fallback = '"/Users/My Name/python" -m voice_typer.server.prewarm'
        monkeypatch.setattr(
            "voice_typer.server.prewarm_resolver.resolve_prewarm_exe",
            lambda: dev_fallback,
        )
        monkeypatch.setattr(prewarm, "is_windows", lambda: False)
        monkeypatch.setattr(prewarm, "is_prewarm_running", lambda: False)

        captured: dict = {}

        def _capturing_popen(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            fake_proc = MagicMock()
            fake_proc.pid = 7777
            return fake_proc

        monkeypatch.setattr(
            "voice_typer.server.prewarm.process_tracker.subprocess.Popen",
            _capturing_popen,
        )

        pid = process_tracker.spawn_background_prewarm(force=False, trigger="manual")

        assert pid == 7777
        # shlex.split must keep the path-with-spaces as a single token.
        assert captured["cmd"][0] == "/Users/My Name/python", (
            f"AB-18: shlex.split must keep the path with spaces as one token; got cmd[0]={captured['cmd'][0]!r}"
        )
        assert captured["cmd"][1] == "-m"
        assert captured["cmd"][2] == "voice_typer.server.prewarm"
        # force=False → no --force.
        assert "--force" not in captured["cmd"]
        assert "--trigger" in captured["cmd"]

    def test_resolver_none_falls_back_to_sys_executable(self, monkeypatch):
        """If the resolver returns None, fall back to ``sys.executable -m ...``.

        Defensive: ``resolve_prewarm_exe`` returns None only when
        ``sys.executable`` is empty (an exotic failure mode). The
        spawn must still attempt to run rather than crashing the
        caller.
        """
        monkeypatch.setattr(
            "voice_typer.server.prewarm_resolver.resolve_prewarm_exe",
            lambda: None,
        )
        monkeypatch.setattr(prewarm, "is_windows", lambda: False)
        monkeypatch.setattr(prewarm, "is_prewarm_running", lambda: False)

        captured: dict = {}

        def _capturing_popen(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            fake_proc = MagicMock()
            fake_proc.pid = 31337
            return fake_proc

        monkeypatch.setattr(
            "voice_typer.server.prewarm.process_tracker.subprocess.Popen",
            _capturing_popen,
        )

        import sys

        pid = process_tracker.spawn_background_prewarm(force=True, trigger="manual")

        assert pid == 31337
        # Fallback cmd is [sys.executable, "-m", "voice_typer.server.prewarm", "--force", "--trigger", "manual"].
        assert captured["cmd"][0] == sys.executable
        assert "-m" in captured["cmd"]
        assert "voice_typer.server.prewarm" in captured["cmd"]
        assert "--force" in captured["cmd"]


# force / --trigger flags still appended ────────────────────


class TestSpawnStillAppendsFlags:
    """The ``--force`` and ``--trigger`` flags are appended on every path."""

    @pytest.mark.parametrize("force_flag", [True, False])
    def test_force_flag(self, monkeypatch, force_flag):
        monkeypatch.setattr(
            "voice_typer.server.prewarm_resolver.resolve_prewarm_exe",
            lambda: "/fake/python -m voice_typer.server.prewarm",
        )
        monkeypatch.setattr(prewarm, "is_windows", lambda: False)
        monkeypatch.setattr(prewarm, "is_prewarm_running", lambda: False)

        captured: dict = {}

        def _capturing_popen(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            fake_proc = MagicMock()
            fake_proc.pid = 1234
            return fake_proc

        monkeypatch.setattr(
            "voice_typer.server.prewarm.process_tracker.subprocess.Popen",
            _capturing_popen,
        )

        process_tracker.spawn_background_prewarm(force=force_flag, trigger="manual")

        if force_flag:
            assert "--force" in captured["cmd"]
        else:
            assert "--force" not in captured["cmd"]
        assert "--trigger" in captured["cmd"]
        assert "manual" in captured["cmd"]
