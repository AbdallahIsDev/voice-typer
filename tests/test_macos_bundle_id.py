"""Tests for the macOS host bundle-ID runtime resolution.

The Accessibility re-grant notification in ``startup_tasks.py`` must
show ``tccutil reset Accessibility <bundle-id>`` with the REAL bundle
ID of the currently-running host app (Electron or Tauri), never a
hardcoded value. The resolver walks the parent-process chain from the
Python backend up to the nearest ``*.app`` bundle and reads
``CFBundleIdentifier`` from its ``Contents/Info.plist``.

This module tests:

1. ``app_bundle_root`` — pure path parsing.
2. ``read_bundle_identifier`` — Info.plist parsing (missing file / key /
   wrong type are all ``None``).
3. ``_resolve_host_bundle_id`` — the process-chain walk (scripted ``ps``
   output; resolves the nearest ``.app``, skips non-app ancestors,
   stops at launchd / ps failure / depth bound).
4. ``resolve_host_bundle_id`` — macOS-only guard (no ``ps`` on other
   platforms).
5. The ``startup_tasks`` integration: the message helper embeds the
   resolved bundle ID when available and falls back to the generic
   walkthrough otherwise, and the module never hardcodes a bundle ID.
6. Real-process integration (macOS-only): the ACTUAL ``ps`` walk
   against the live process tree — a process launched inside a
   synthetic ``.app`` resolves its bundle ID end-to-end, and the
   public no-arg resolver agrees with an independent read of the
   current process chain.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from voice_typer.server.server_platform import macos_bundle_id as mbid
from voice_typer.server.startup_tasks import _a11y_regrant_message


def _make_app_bundle(root: Path, bundle_id: str) -> Path:
    """Create a minimal ``Voice Typer.app/Contents/Info.plist`` at ``root``."""
    app = root / "Voice Typer.app"
    contents = app / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": bundle_id}))
    return app


# ─── app_bundle_root ───────────────────────────────────────────────────────


class TestAppBundleRoot:
    def test_finds_app_root_in_macos_host_path(self):
        root = mbid.app_bundle_root("/Applications/Voice Typer.app/Contents/MacOS/Voice Typer")
        assert root == Path("/Applications/Voice Typer.app")

    def test_finds_app_root_anywhere_in_path(self):
        root = mbid.app_bundle_root(
            "/Users/x/Projects/Voice Typer.app/Contents/Resources/python-sidecar-aarch64-apple-darwin"
        )
        assert root == Path("/Users/x/Projects/Voice Typer.app")

    def test_returns_none_without_app_segment(self):
        assert mbid.app_bundle_root("/usr/bin/python3") is None
        assert mbid.app_bundle_root("python3") is None

    def test_returns_none_for_empty_path(self):
        assert mbid.app_bundle_root("") is None


# ─── read_bundle_identifier ────────────────────────────────────────────────


class TestReadBundleIdentifier:
    def test_reads_cf_bundle_identifier(self, tmp_path):
        app = _make_app_bundle(tmp_path, "com.voicetyper.desktop")
        assert mbid.read_bundle_identifier(app) == "com.voicetyper.desktop"

    def test_returns_none_when_plist_missing(self, tmp_path):
        app = tmp_path / "Empty.app"
        app.mkdir()
        assert mbid.read_bundle_identifier(app) is None

    def test_returns_none_when_bundle_missing(self, tmp_path):
        assert mbid.read_bundle_identifier(tmp_path / "NoSuch.app") is None

    def test_returns_none_when_key_missing(self, tmp_path):
        app = tmp_path / "NoKey.app"
        contents = app / "Contents"
        contents.mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleName": "X"}))
        assert mbid.read_bundle_identifier(app) is None

    def test_returns_none_when_value_not_a_string(self, tmp_path):
        app = tmp_path / "BadVal.app"
        contents = app / "Contents"
        contents.mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": 42}))
        assert mbid.read_bundle_identifier(app) is None

    def test_returns_none_when_plist_malformed(self, tmp_path):
        app = tmp_path / "Broken.app"
        contents = app / "Contents"
        contents.mkdir(parents=True)
        (contents / "Info.plist").write_bytes(b"this is not a plist")
        assert mbid.read_bundle_identifier(app) is None


# ─── _resolve_host_bundle_id (process-chain walk) ──────────────────────────


def _fake_ps(lines: dict[int, str]):
    """Return a ``_process_chain_line``-shaped fake serving ``lines``."""

    def fake(pid: int) -> str:
        return lines.get(pid, "")

    return fake


class TestResolveHostBundleId:
    def test_resolves_nearest_app_bundle(self, tmp_path, monkeypatch):
        app = _make_app_bundle(tmp_path, "com.voicetyper.desktop")
        monkeypatch.setattr(
            mbid,
            "_process_chain_line",
            _fake_ps(
                {
                    300: f"200 {app}/Contents/MacOS/Voice Typer",
                    200: "1 /sbin/launchd",
                }
            ),
        )
        assert mbid._resolve_host_bundle_id(start_pid=300) == "com.voicetyper.desktop"

    def test_skips_non_app_ancestors_before_host(self, tmp_path, monkeypatch):
        app = _make_app_bundle(tmp_path, "com.voicetyper.desktop")
        monkeypatch.setattr(
            mbid,
            "_process_chain_line",
            _fake_ps(
                {
                    # intermediate ancestor: a launcher script (no .app)
                    300: "200 /usr/local/bin/voice-typer-launch",
                    200: f"150 {app}/Contents/MacOS/Voice Typer",
                    150: "1 /sbin/launchd",
                }
            ),
        )
        assert mbid._resolve_host_bundle_id(start_pid=300) == "com.voicetyper.desktop"

    def test_returns_none_when_no_app_in_chain(self, monkeypatch):
        monkeypatch.setattr(
            mbid,
            "_process_chain_line",
            _fake_ps(
                {
                    300: "200 /usr/bin/python3",
                    200: "1 /sbin/launchd",
                }
            ),
        )
        assert mbid._resolve_host_bundle_id(start_pid=300) is None

    def test_stops_at_launchd(self, monkeypatch):
        monkeypatch.setattr(mbid, "_process_chain_line", _fake_ps({300: "1 /sbin/launchd"}))
        assert mbid._resolve_host_bundle_id(start_pid=300) is None

    def test_returns_none_on_ps_failure(self, monkeypatch):
        monkeypatch.setattr(mbid, "_process_chain_line", _fake_ps({}))
        assert mbid._resolve_host_bundle_id(start_pid=300) is None

    def test_returns_none_on_unparsable_ps_line(self, monkeypatch):
        monkeypatch.setattr(mbid, "_process_chain_line", _fake_ps({300: "not-a-pid garbage"}))
        assert mbid._resolve_host_bundle_id(start_pid=300) is None

    def test_respects_chain_depth_bound(self, monkeypatch):
        # A pathological chain with no .app must terminate, not loop.
        lines = {300 - i: f"{299 - i} /usr/bin/python{i}" for i in range(20)}
        monkeypatch.setattr(mbid, "_process_chain_line", _fake_ps(lines))
        assert mbid._resolve_host_bundle_id(start_pid=300) is None


class TestPublicResolveHostBundleId:
    def test_non_macos_returns_none_without_running_ps(self, monkeypatch):
        monkeypatch.setattr(mbid, "is_macos", lambda: False)
        called: list[int] = []

        def boom(pid: int) -> str:
            called.append(pid)
            raise AssertionError("ps must not run on non-macOS hosts")

        monkeypatch.setattr(mbid, "_process_chain_line", boom)
        monkeypatch.setattr(mbid, "_resolve_host_bundle_id", boom)
        assert mbid.resolve_host_bundle_id() is None
        assert called == []

    def test_macos_delegates_to_walk(self, monkeypatch):
        monkeypatch.setattr(mbid, "is_macos", lambda: True)
        monkeypatch.setattr(
            mbid,
            "_resolve_host_bundle_id",
            lambda: "com.voicetyper.desktop",
        )
        assert mbid.resolve_host_bundle_id() == "com.voicetyper.desktop"


# ─── real-process integration (macOS-only) ─────────────────────────────────


def _expected_bundle_id_from_current_chain() -> str | None:
    """Oracle: walk the LIVE process tree from the runner's parent (real
    ``ps``) and return the bundle ID the resolver SHOULD produce.

    Mirrors the resolver's walk so the integration test has an
    independent expected value computed from the same real ``ps``
    output — the point is exercising the real process tree, plist
    parsing and path handling end-to-end, not re-verifying the loop.
    """
    pid = os.getppid()
    for _ in range(mbid._MAX_CHAIN_DEPTH):
        line = mbid._process_chain_line(pid)
        if not line:
            return None
        parts = line.split(None, 1)
        if len(parts) < 2:
            return None
        try:
            parent_pid = int(parts[0])
        except ValueError:
            return None
        root = mbid.app_bundle_root(parts[1])
        if root is not None:
            bundle_id = mbid.read_bundle_identifier(root)
            if bundle_id:
                return bundle_id
        pid = parent_pid
    return None


class TestRealProcessTreeIntegration:
    """macOS-only integration: the REAL ``ps`` walk against the live tree.

    ``_process_chain_line`` is NOT mocked here. ``ps -p <pid> -o ppid=
    -o comm=`` is BSD syntax and ``*.app`` bundle semantics are
    macOS-specific, so both tests skip on Linux/Windows and are
    exercised on the macos-14 CI leg (build.yml runs the full pytest
    suite there on every PR).
    """

    @pytest.mark.skipif(sys.platform != "darwin", reason="macos-only real ps walk")
    def test_resolver_returns_bundle_id_for_process_launched_inside_app(self, tmp_path):
        """A process whose executable lives inside a ``*.app`` must resolve.

        Builds a synthetic ``Voice Typer Test.app`` on disk with a real
        Mach-O executable (a copy of ``/bin/sleep`` — a shebang script
        would report the INTERPRETER path in ``ps comm``, not the bundle
        path) and a real ``Info.plist``, launches it as a live
        subprocess, then runs the resolver's real ``ps`` walk from that
        pid. The space in the app name also exercises the comm-based
        path parsing (no tokenization).
        """
        app = tmp_path / "Voice Typer Test.app"
        contents = app / "Contents"
        macos_dir = contents / "MacOS"
        macos_dir.mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.voicetyper.desktop"}))
        host = macos_dir / "sleep"
        shutil.copy2("/bin/sleep", host)  # real exe at a path inside the .app
        host.chmod(0o755)

        proc = subprocess.Popen([str(host), "30"])
        try:
            # Premise check: the live process's comm must report an .app
            # path (robust to macOS /var -> /private/var canonicalisation).
            line = mbid._process_chain_line(proc.pid)
            parts = line.split(None, 1)
            assert len(parts) == 2, f"ps must report '<ppid> <exe>'; got: {line!r}"
            assert mbid.app_bundle_root(parts[1]) is not None, f"ps comm must expose the bundle path; got: {line!r}"
            assert mbid._resolve_host_bundle_id(start_pid=proc.pid) == "com.voicetyper.desktop"
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    @pytest.mark.skipif(sys.platform != "darwin", reason="macos-only real ps walk")
    def test_public_resolver_consistent_with_current_process_tree(self):
        """``resolve_host_bundle_id()`` must agree with the real tree it walked.

        Runs the no-arg public resolver (walks from the test runner's own
        parent via real ``ps``) and compares against an independent read
        of the same chain. When the suite is launched inside an ``.app``
        (packaged run, CI inside a bundle), the resolver must return that
        bundle's identifier — non-None; in a plain dev/terminal run it
        must return None. Either way the real walk must match the tree.
        """
        result = mbid.resolve_host_bundle_id()
        expected = _expected_bundle_id_from_current_chain()
        assert result == expected, (
            f"resolver returned {result!r} but the live process tree yields "
            f"{expected!r} (launched inside an .app => bundle ID, else None)"
        )


# ─── startup_tasks integration ─────────────────────────────────────────────


class TestRegrantMessage:
    def test_includes_tccutil_command_when_bundle_id_resolved(self):
        msg = _a11y_regrant_message("com.voicetyper.desktop")
        assert "tccutil reset Accessibility com.voicetyper.desktop" in msg
        assert "Open System Settings" not in msg

    def test_falls_back_to_settings_walkthrough_when_unresolved(self):
        msg = _a11y_regrant_message(None)
        assert "Open System Settings" in msg
        assert "tccutil" not in msg

    def test_embeds_any_runtime_bundle_id(self):
        # The message must follow the resolved value, not a fixed one —
        # this is the whole point of runtime resolution (e.g. a future
        # Tauri build with a different identifier).
        msg = _a11y_regrant_message("com.voicetyper.some-other-build")
        assert "tccutil reset Accessibility com.voicetyper.some-other-build" in msg


class TestStartupTasksSource:
    """Source-inspection guard: startup_tasks must never hardcode the bundle ID."""

    def test_uses_runtime_resolution_and_no_hardcoded_bundle_id(self):
        import inspect

        from voice_typer.server import startup_tasks

        src = inspect.getsource(startup_tasks)
        assert "resolve_host_bundle_id()" in src, (
            "startup_tasks.py must resolve the host bundle ID at runtime "
            "(resolve_host_bundle_id) for the tccutil re-grant notification."
        )
        assert "tccutil reset Accessibility com.voicetyper" not in src, (
            "startup_tasks.py must NOT hardcode a bundle ID in the tccutil "
            "re-grant notification — resolve it at runtime instead."
        )


class TestOnboardingSource:
    """Source-inspection guard: onboarding.py must never hardcode the
    bundle ID in its macOS permissions guidance."""

    def test_uses_runtime_resolution_and_no_hardcoded_bundle_id(self):
        import inspect

        from voice_typer.server import onboarding

        src = inspect.getsource(onboarding)
        assert "resolve_host_bundle_id()" in src, (
            "onboarding.py must resolve the host bundle ID at runtime "
            "(resolve_host_bundle_id) for the macOS permissions guidance "
            "(the tccutil re-grant command in the onboarding walkthrough)."
        )
        assert "tccutil reset Accessibility com.voicetyper" not in src, (
            "onboarding.py must NOT hardcode a bundle ID in the macOS "
            "permissions guidance — resolve it at runtime instead."
        )
