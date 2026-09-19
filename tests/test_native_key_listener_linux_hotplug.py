"""
Integration tests for the Linux native key-listener hotplug support (inotify).
These tests do NOT require ``/dev/input`` access or a real USB keyboard.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LISTENER_SRC = PROJECT_ROOT / "voice_typer" / "server" / "native" / "linux-key-listener.c"
C_TEST_SRC = PROJECT_ROOT / "tests" / "c" / "test_linux_key_listener_hotplug.c"

_LINUX_GCC = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("gcc") is None,
    reason="Linux native listener tests require Linux + gcc",
)


class TestLinuxKeyListenerHotplugC:
    """C-level test: compile and run the hotplug unit-test harness."""

    @_LINUX_GCC
    def test_hotplug_logic_compiles_and_passes(self, tmp_path: Path) -> None:
        """The C test harness compiles cleanly and all assertions pass."""
        assert LISTENER_SRC.is_file(), f"missing listener source: {LISTENER_SRC}"
        assert C_TEST_SRC.is_file(), f"missing C test source: {C_TEST_SRC}"

        binary = tmp_path / "test_hotplug"
        cmd = [
            "gcc",
            "-O2",
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Wno-unused-function",
            str(C_TEST_SRC),
            "-o",
            str(binary),
        ]
        compiled = subprocess.run(cmd, capture_output=True, text=True)
        assert compiled.returncode == 0, (
            f"gcc failed to compile C hotplug test harness:\ncmd: {' '.join(cmd)}\nstderr:\n{compiled.stderr}"
        )

        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
        assert ran.returncode == 0, (
            f"C hotplug test harness exited {ran.returncode}:\nstdout:\n{ran.stdout}\nstderr:\n{ran.stderr}"
        )
        assert "ALL PASSED" in ran.stdout, (
            f"C hotplug test harness did not report ALL PASSED:\nstdout:\n{ran.stdout}\nstderr:\n{ran.stderr}"
        )

    @_LINUX_GCC
    def test_production_source_compiles_clean_with_werror(self, tmp_path: Path) -> None:
        """The production listener compiles warning-free under -Wall -Wextra -Werror."""
        assert LISTENER_SRC.is_file(), f"missing listener source: {LISTENER_SRC}"
        binary = tmp_path / "linux-key-listener"
        cmd = [
            "gcc",
            "-O2",
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(LISTENER_SRC),
            "-o",
            str(binary),
        ]
        compiled = subprocess.run(cmd, capture_output=True, text=True)
        assert compiled.returncode == 0, (
            f"gcc failed to compile the production listener warning-free:\n"
            f"cmd: {' '.join(cmd)}\nstderr:\n{compiled.stderr}"
        )
        assert compiled.stderr.strip() == "", (
            f"production listener produced compiler diagnostics under -Werror:\n{compiled.stderr}"
        )
        assert binary.is_file()


def _source() -> str:
    assert LISTENER_SRC.is_file(), f"missing listener source: {LISTENER_SRC}"
    return LISTENER_SRC.read_text(encoding="utf-8")


def _function_body(src: str, signature: str) -> str:
    """next top-level ``static`` or EOF). Used to scope assertions like \"no"""
    start = src.find(signature)
    assert start != -1, f"function not found in listener source: {signature}"
    brace = src.find("{", start)
    assert brace != -1
    end = src.find("\nstatic ", brace)
    if end == -1:
        end = len(src)
    return src[brace:end]


class TestLinuxKeyListenerHotplugSourceWiring:
    """Source-level pins: the inotify machinery exists and is wired into"""

    def test_source_includes_inotify_header(self) -> None:
        """The listener uses the kernel inotify API (no extra libraries)."""
        src = _source()
        assert "#include <sys/inotify.h>" in src, (
            "linux-key-listener.c must include <sys/inotify.h> for hotplug monitoring of /dev/input"
        )

    def test_source_defines_hotplug_setup_and_teardown(self) -> None:
        """setup_hotplug_watch / close_hotplug_watch exist and degrade, not die."""
        src = _source()
        assert "static void setup_hotplug_watch(void)" in src
        assert "static void close_hotplug_watch(void)" in src
        # Setup failure must never be fatal: a WARN diagnostic + disabled fd.
        assert "hotplug monitoring disabled" in src, (
            "inotify setup failure must log a warning and degrade to startup-only device detection (never exit)"
        )
        body = _function_body(src, "static void setup_hotplug_watch(void)")
        assert "return" in body  # failure paths return, they do not exit

    def test_setup_hotplug_watch_called_from_main(self) -> None:
        """main() arms the hotplug watch before entering the event loop."""
        src = _source()
        body = _function_body(src, "int main(int argc, char **argv)")
        assert "setup_hotplug_watch();" in body, (
            "main must call setup_hotplug_watch() so hotplug is active while running"
        )
        assert "close_hotplug_watch();" in body, "main must call close_hotplug_watch() on shutdown (no fd leak)"

    def test_watch_mask_covers_add_and_remove_events(self) -> None:
        """The watch subscribes to node add AND remove (plus IN_ATTRIB for"""
        src = _source()
        body = _function_body(src, "static void setup_hotplug_watch(void)")
        for bit in ("IN_CREATE", "IN_MOVED_TO", "IN_DELETE", "IN_MOVED_FROM"):
            assert bit in body, f"watch mask must include {bit} for hotplug add/remove"
        assert "IN_ATTRIB" in body, (
            "watch mask must include IN_ATTRIB so the udev permission fixup "
            "(chmod) triggers an open retry without a timer"
        )
        assert '"/dev/input"' in body, "the watch must target /dev/input"

    def test_handle_inotify_events_called_from_run_loop(self) -> None:
        """The event loop polls the inotify fd and drains it in-line."""
        src = _source()
        assert "static void handle_inotify_events(void)" in src
        body = _function_body(src, "static int run_loop(void)")
        assert "g_inotify_fd" in body, "run_loop must include the inotify fd in its poll set"
        assert "handle_inotify_events();" in body, "run_loop must call handle_inotify_events() when the watch fires"

    def test_inotify_event_buffer_sized_per_man_page(self) -> None:
        """The read buffer is sizeof(struct inotify_event) + NAME_MAX + 1"""
        src = _source()
        assert "sizeof(struct inotify_event) + NAME_MAX + 1" in src, (
            "inotify read buffer must be sized per the man page (sizeof(struct inotify_event) + NAME_MAX + 1)"
        )
        assert "NAME_MAX" in src
        body = _function_body(src, "static void handle_inotify_events(void)")
        assert "IN_Q_OVERFLOW" in body or "unnamed" in body or "len == 0" in body, (
            "unnamed inotify events (e.g. IN_Q_OVERFLOW, wd == -1) must be ignored"
        )

    def test_unplug_read_error_path_removes_stale_fd(self) -> None:
        """A drained device whose read() returns EOF/error (not EAGAIN) is"""
        src = _source()
        body = _function_body(src, "static int run_loop(void)")
        assert "device_gone" in body, "run_loop must detect unplugged devices via read() EOF/error"
        assert "EAGAIN" in body, "EAGAIN must still be treated as a normal drain"
        assert "remove_device_at" in body, "run_loop must remove the stale fd from the tracked set on unplug"
        assert "POLLHUP" in body, "POLLHUP/POLLERR must enter the drain path so the unplug is confirmed"

    def test_add_and_remove_helpers_exist(self) -> None:
        """The tracked-set helpers the inotify handler drives exist."""
        src = _source()
        for signature in (
            "static int is_event_node_name(const char *name)",
            "static int add_device_by_name(const char *name)",
            "static void remove_device_at(int idx)",
            "static void remove_device_by_name(const char *name)",
            "static int find_device_index_by_name(const char *name)",
            "static int find_device_index_by_fd(int fd)",
        ):
            assert signature in src, f"missing hotplug helper: {signature}"

    def test_hotplug_is_silent_on_stdout_protocol(self) -> None:
        """Wire protocol is byte-identical: the hotplug helpers never emit"""
        src = _source()
        for signature in (
            "static int add_device_by_name(const char *name)",
            "static void remove_device_at(int idx)",
            "static void remove_device_by_name(const char *name)",
            "static void setup_hotplug_watch(void)",
            "static void handle_inotify_events(void)",
        ):
            body = _function_body(src, signature)
            assert "emit(" not in body and "emitf(" not in body, (
                f"{signature} must not write to stdout, the wire protocol "
                "is parsed by the Python consumer and must stay unchanged"
            )
        # The protocol surface itself is untouched.
        assert 'NATIVE_BINARY_VERSION "1.0.0"' in src, (
            "wire-protocol version must stay 1.0.0 unless the native manifest is bumped in lockstep"
        )
        assert 'emit("READY")' in src
        assert 'emitf("VERSION:%s", NATIVE_BINARY_VERSION)' in src

    def test_discovery_shares_the_hotplug_add_path(self) -> None:
        """Startup discovery routes through add_device_by_name so there is"""
        src = _source()
        body = _function_body(src, "static int discover_devices(void)")
        assert "add_device_by_name(" in body, (
            "discover_devices must share the hotplug add path (single open/filter/track implementation)"
        )
        # Error contract of discovery is unchanged (consumer-facing lines).
        assert "ERROR:No keyboard devices found" in body
        assert "EACCES" in body
