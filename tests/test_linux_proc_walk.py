"""Tests for the Linux ``/proc`` parent-chain walker.

The Linux sibling of the macOS host-bundle resolver
(``test_macos_bundle_id.py``): ``linux_proc_walk.py`` climbs the real
parent-process chain by reading ``/proc/<pid>/stat`` (parent PID) and
``/proc/<pid>/cmdline`` (argv[0]). Linux has no ``*.app`` bundles, so
host-bundle detection is a documented no-op (the resolver always
returns ``None`` on Linux) — the WALK is what matters: it must
terminate cleanly (never raise, never loop) against both real and
fixture ``/proc`` trees. CI exercises the real ``/proc`` walk on the
Linux runner after ``cargo tauri build``.

This module tests:

1. ``_stat_ppid`` — ``/proc/<pid>/stat`` field-4 parsing (including the
   comm-with-spaces case that makes naive splitting wrong).
2. ``_cmdline_exe`` — argv[0] extraction from the NUL-separated
   ``cmdline`` blob.
3. ``_read_proc_entry`` — best-effort ``(ppid, exe)`` read (missing
   files are ``(None, ...)``-per-field, never exceptions).
4. ``_resolve_linux_host_bundle_id`` — the chain walk over REAL
   fixture files in ``tmp_path`` (a scripted ``/proc`` tree): chain
   traversal, termination at pid<=1 / unreadable stat / depth bound.
5. ``resolve_linux_host_bundle_id`` — Linux-only guard (no ``/proc``
   reads on other platforms).
"""

from __future__ import annotations

import sys

import pytest
from voice_typer.server.server_platform import linux_proc_walk as lwalk

# /proc/<pid>/stat shape: "pid (comm) state ppid ..." — comm may contain
# spaces (kernel comm is up to 15 chars and may include spaces). Only
# the leading fields are parsed; the tail is filler.
_STAT_TAIL = "0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"


def _stat_line(pid: int, comm: str, state: str, ppid: int) -> str:
    return f"{pid} ({comm}) {state} {ppid} {_STAT_TAIL}"


def _make_proc_tree(root, entries: dict[int, tuple[str, bytes]]) -> None:
    """Build a fixture ``/proc`` tree: ``{pid: (stat_text, cmdline_bytes)}``."""
    for pid, (stat, cmdline) in entries.items():
        pdir = root / str(pid)
        pdir.mkdir(parents=True)
        (pdir / "stat").write_text(stat, encoding="utf-8")
        (pdir / "cmdline").write_bytes(cmdline)


# ─── _stat_ppid ────────────────────────────────────────────────────────────


class TestStatPpid:
    def test_parses_ppid_from_simple_stat(self):
        assert lwalk._stat_ppid(_stat_line(300, "sleep 30", "S", 200)) == 200

    def test_parses_ppid_with_spaces_in_comm(self):
        assert lwalk._stat_ppid(_stat_line(200, "multi word comm", "S", 150)) == 150

    def test_returns_none_without_closing_paren(self):
        assert lwalk._stat_ppid("300 (no close") is None

    def test_returns_none_when_fields_missing_after_comm(self):
        assert lwalk._stat_ppid("300 (comm) S") is None

    def test_returns_none_when_ppid_not_an_int(self):
        assert lwalk._stat_ppid("300 (comm) S not-a-pid 300 0 0") is None

    def test_returns_none_for_empty_input(self):
        assert lwalk._stat_ppid("") is None


# ─── _cmdline_exe ──────────────────────────────────────────────────────────


class TestCmdlineExe:
    def test_returns_argv0_from_nul_separated_cmdline(self):
        blob = b"/opt/voice-typer/bin/voice-typer\x00--flag\x00"
        assert lwalk._cmdline_exe(blob) == "/opt/voice-typer/bin/voice-typer"

    def test_returns_none_for_empty_blob(self):
        assert lwalk._cmdline_exe(b"") is None

    def test_returns_none_for_whitespace_only_argv0(self):
        assert lwalk._cmdline_exe(b"   \x00--flag") is None

    def test_undecodable_bytes_fall_back_to_replacement(self):
        # errors="replace" in the walker means we always get *something*;
        # the test pins that a byte that cannot be utf-8-decoded does not
        # crash the read (replacement chars survive into the log line).
        blob = b"\xff\xfe\x00arg"
        assert lwalk._cmdline_exe(blob) is not None


# ─── _read_proc_entry ──────────────────────────────────────────────────────


class TestReadProcEntry:
    def test_reads_ppid_and_exe(self, tmp_path):
        _make_proc_tree(
            tmp_path,
            {300: (_stat_line(300, "sleep 30", "S", 200), b"/usr/bin/sleep\x0030")},
        )
        assert lwalk._read_proc_entry(300, tmp_path) == (200, "/usr/bin/sleep")

    def test_missing_pid_dir_yields_none_none(self, tmp_path):
        assert lwalk._read_proc_entry(9999, tmp_path) == (None, None)

    def test_missing_stat_only(self, tmp_path):
        pdir = tmp_path / "300"
        pdir.mkdir()
        (pdir / "cmdline").write_bytes(b"/usr/bin/sleep\x0030")
        assert lwalk._read_proc_entry(300, tmp_path) == (None, "/usr/bin/sleep")

    def test_missing_cmdline_only(self, tmp_path):
        _make_proc_tree(tmp_path, {300: (_stat_line(300, "sleep 30", "S", 200), b"")})
        (tmp_path / "300" / "cmdline").unlink()
        assert lwalk._read_proc_entry(300, tmp_path) == (200, None)

    def test_host_without_proc_root_yields_none_none(self, tmp_path):
        assert lwalk._read_proc_entry(300, tmp_path / "no-such-proc") == (None, None)


# ─── _resolve_linux_host_bundle_id (chain walk) ────────────────────────────


class TestResolveLinuxChainWalk:
    def test_walks_full_chain_to_init(self, tmp_path):
        _make_proc_tree(
            tmp_path,
            {
                300: (_stat_line(300, "sleep 30", "S", 200), b"/usr/bin/sleep\x0030"),
                200: (_stat_line(200, "voice-typer-launch", "S", 150), b"/opt/voice-typer/launch"),
                150: (_stat_line(150, "systemd", "S", 1), b"/lib/systemd/systemd"),
            },
        )
        assert lwalk._resolve_linux_host_bundle_id(start_pid=300, proc_root=tmp_path) is None

    def test_stops_at_pid_one_without_reading_it(self, tmp_path):
        # pid 1 must terminate the walk BEFORE any read attempt — a
        # /proc tree without an entry for pid 1 must not matter.
        _make_proc_tree(tmp_path, {300: (_stat_line(300, "sleep", "S", 1), b"/usr/bin/sleep")})
        assert lwalk._resolve_linux_host_bundle_id(start_pid=300, proc_root=tmp_path) is None

    def test_stops_on_unreadable_stat_hop(self, tmp_path):
        # 200 exists but its stat is missing -> chain ends, no crash.
        pdir = tmp_path / "200"
        pdir.mkdir()
        (pdir / "cmdline").write_bytes(b"/usr/bin/python3")
        _make_proc_tree(tmp_path, {300: (_stat_line(300, "sleep", "S", 200), b"/usr/bin/sleep")})
        assert lwalk._resolve_linux_host_bundle_id(start_pid=300, proc_root=tmp_path) is None

    def test_respects_chain_depth_bound(self, tmp_path):
        # A pathological chain with no pid-1 must terminate, not loop.
        entries = {pid: (_stat_line(pid, "python3", "S", pid - 1), b"/usr/bin/python3") for pid in range(310, 300, -1)}
        _make_proc_tree(tmp_path, entries)
        assert lwalk._resolve_linux_host_bundle_id(start_pid=310, proc_root=tmp_path) is None

    def test_host_without_proc_root_terminates_immediately(self, tmp_path):
        assert lwalk._resolve_linux_host_bundle_id(start_pid=300, proc_root=tmp_path / "no-proc") is None

    def test_never_raises_on_garbage_entries(self, tmp_path):
        _make_proc_tree(
            tmp_path,
            {
                300: ("garbage not a stat line", b""),
                200: (_stat_line(200, "python3", "S", 1), b"\xff\xfe\x00"),
            },
        )
        assert lwalk._resolve_linux_host_bundle_id(start_pid=300, proc_root=tmp_path) is None


# ─── public resolver ───────────────────────────────────────────────────────


class TestPublicResolveLinuxHostBundleId:
    def test_non_linux_returns_none_without_touching_proc(self, monkeypatch):
        monkeypatch.setattr(lwalk, "is_linux", lambda: False)
        called: list[int] = []

        def boom(pid: int) -> None:
            called.append(pid)
            raise AssertionError("the /proc walk must not run on non-Linux hosts")

        monkeypatch.setattr(lwalk, "_resolve_linux_host_bundle_id", boom)
        assert lwalk.resolve_linux_host_bundle_id() is None
        assert called == []

    def test_linux_delegates_to_walk(self, monkeypatch):
        monkeypatch.setattr(lwalk, "is_linux", lambda: True)
        monkeypatch.setattr(lwalk, "_resolve_linux_host_bundle_id", lambda: None)
        assert lwalk.resolve_linux_host_bundle_id() is None


# ─── real-process integration (Linux-only) ────────────────────────────────


class TestRealProcTreeIntegration:
    """Linux-only integration: the REAL ``/proc`` walk against the live tree.

    ``_read_proc_entry`` / ``_resolve_linux_host_bundle_id`` are NOT
    mocked here — the walker must terminate cleanly (never raise, never
    loop) against the runner's actual ``/proc`` tree, and the fixture-
    free public resolver must agree with an independent read of the
    current chain. Skipped on Windows/macOS; exercised on the Linux CI
    runner (tauri-linux-build.yml post-build step, where ``/proc`` is
    real).
    """

    @pytest.mark.skipif(sys.platform != "linux", reason="linux-only real /proc walk")
    def test_public_resolver_walks_real_proc_chain(self):
        """``resolve_linux_host_bundle_id()`` must run against the live
        ``/proc`` tree without raising and terminate cleanly returning
        ``None`` (Linux has no bundle detection — see the module
        docstring; the walk itself is the contract)."""
        assert lwalk.resolve_linux_host_bundle_id() is None

    @pytest.mark.skipif(sys.platform != "linux", reason="linux-only real /proc walk")
    def test_chain_readable_from_real_proc(self):
        """The parent chain must be readable from the live ``/proc``:
        the runner's own parent pid resolves to a real record (ppid +
        exe), so the fixture-free walk's first hop is always resolvable
        on a real Linux host."""
        import os

        ppid, exe = lwalk._read_proc_entry(os.getppid())
        assert ppid is not None, "real /proc must expose a parent pid for the runner's process"
        assert exe, "real /proc must expose a cmdline exe for the runner's process"
