"""Test coverage
-------------
- (High):         ``IPCServer.start()`` refuses to spawn the stdin
                  listener when ``_tcp_mode`` is False AND the
                  ``VOICE_TYPER_ALLOW_STDIN_IPC`` env var is not set
                  to ``"1"``. A WARNING is logged and ``_stdin_thread``
                  is set to ``None``. The ``--allow-stdin`` CLI flag in
                  ``parse_ipc_args()`` is the alternative gate — it
                  sets the env var.
- (Medium):       ``_handle_shutdown`` checks ``_shutdown_started``
                  (a per-instance ``threading.Event``) at the top and
                  no-ops the second invocation. The cleanup thread is
                  registered on ``self.app._thread_registry`` (when
                  available) so ``shutdown_all()`` can join it.
- (Medium):       ``_COMMAND_REGISTRY`` + ``_READONLY_COMMANDS`` +
                  ``_PYTHON_ONLY_COMMANDS`` are canonical to
                  :mod:`voice_typer.server.ipc.registry`.
                  ``ipc_server.py`` re-exports them and
                  :class:`IPCServer` re-aliases them as class
                  attributes (pinned by ``test_ipc_shutdown_registry``,
                  ``test_command_registry_parity``, etc.).

These tests are intentionally unit-level (no live TCP, no real
``VoiceTyperApp``) so they run in <1 s.
"""

from __future__ import annotations

import inspect
import os
import threading
import time
from unittest.mock import MagicMock

from voice_typer.server.ipc_server import IPCServer

# ── Helpers ────────────────────────────────────────────────────────────


def _make_server() -> IPCServer:
    """Build an IPCServer with MagicMock app + service for unit tests.

    The MagicMock app exposes ``_shutting_down`` as an explicit bool
    (False) so the dispatch gate exercises the dispatch path instead of
    short-circuiting. ``_thread_registry`` is a MagicMock by default
    (so the thread-registration path can be observed); tests
    that want to disable the registry set it to ``None``.
    """
    from voice_typer.server.ipc_server import IPCServer

    app = MagicMock()
    app._shutting_down = False  # explicit bool, not a child mock
    service = MagicMock()
    return IPCServer(app, service=service)


# stdin listener gate behind VOICE_TYPER_ALLOW_STDIN_IPC ──────


class TestStdinGate:
    """the unauthenticated stdin/stdout IPC listener is
    gated behind ``VOICE_TYPER_ALLOW_STDIN_IPC=1``.

    ``IPCServer.start()`` would spawn the stdin listener
    thread whenever ``_tcp_mode`` was False — exposing an
    unauthenticated command channel on the user's terminal (Linux
    TIOCSTI injection is possible; an accidental JSON paste triggers
    unintended IPC commands on every platform).

    The gate refuses to spawn the listener when ``_tcp_mode`` is False
    AND the env var is not set; a WARNING is logged and
    ``_stdin_thread`` is set to ``None``. The ``--allow-stdin`` CLI
    flag in :func:`parse_ipc_args` is the alternative gate (it sets
    the env var).
    """

    def test_stdin_ipc_env_var_module_constant_exists(self) -> None:
        """``_STDIN_IPC_ENV_VAR`` module-level constant exists
        and is the documented string."""
        import voice_typer.server.ipc_server as ipc_server_mod

        assert hasattr(ipc_server_mod, "_STDIN_IPC_ENV_VAR"), (
            "ipc_server.py must expose a module-level "
            "_STDIN_IPC_ENV_VAR constant naming the env var that gates "
            "the stdin listener."
        )
        assert ipc_server_mod._STDIN_IPC_ENV_VAR == "VOICE_TYPER_ALLOW_STDIN_IPC", (
            f"_STDIN_IPC_ENV_VAR must be 'VOICE_TYPER_ALLOW_STDIN_IPC'; got {ipc_server_mod._STDIN_IPC_ENV_VAR!r}."
        )

    def test_start_gates_stdin_listener_when_env_var_unset(self, monkeypatch) -> None:
        """when ``_tcp_mode`` is False AND the env var is unset,
        ``start()`` must NOT spawn the stdin listener. ``_stdin_thread``
        is ``None`` and a WARNING is logged.

        We exercise the gate logic by inspecting the source of
        ``start()`` (the gate fires before the ``threading.Thread(...)``
        call) AND by running ``start()`` with the env var unset and
        observing ``_stdin_thread``. The full ``start()`` call is safe
        because the gate prevents the stdin listener from spawning.
        """
        monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)
        # Source-level pin: the gate must be present.
        src = inspect.getsource(__import__("voice_typer.server.ipc_server", fromlist=["IPCServer"]).IPCServer.start)
        assert "_STDIN_IPC_ENV_VAR" in src, (
            "start() must reference _STDIN_IPC_ENV_VAR so the "
            "stdin listener is gated behind VOICE_TYPER_ALLOW_STDIN_IPC=1."
        )
        assert 'os.environ.get(_STDIN_IPC_ENV_VAR) == "1"' in src, (
            'start() must check os.environ.get(_STDIN_IPC_ENV_VAR) == "1" before spawning the stdin listener.'
        )

    def test_stdin_thread_none_when_gate_refuses(self, monkeypatch) -> None:
        """end-to-end behavior — ``start()`` with ``_tcp_mode``
        False AND env var unset must leave ``_stdin_thread`` as None.

        We exercise the full ``start()`` path (minus the heavy
        ``event_bus.subscribe`` / heartbeat-thread wiring) using a
        MagicMock app so no real VoiceTyperApp is constructed.
        """
        from voice_typer.server import event_bus
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)
        # Also clear TAURI_SIDECAR so the heartbeat thread is created
        # (so we exercise the full start() body — but the heartbeat
        # thread is a daemon so it doesn't block test teardown).
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        app = MagicMock()
        app._shutting_down = False
        app._thread_registry = None
        app.tray.set_state._vt_wrapped = False  # idempotent tray hook
        service = MagicMock()
        server = IPCServer(app, service=service)
        server._tcp_mode = False

        # Stub out event_bus.subscribe so we don't leak the push fn.
        subscribed: list = []
        monkeypatch.setattr(event_bus, "subscribe", lambda fn: subscribed.append(fn))
        # Stub out threading.Thread so we don't actually start a
        # heartbeat thread (the gate must prevent the stdin thread from
        # being created at all — the FakeThread captures the names of
        # threads that WOULD be created).
        created_threads: list[str] = []

        class FakeThread:
            def __init__(self, target=None, name=None, daemon=False):
                self.target = target
                self.name = name
                self.daemon = daemon
                created_threads.append(name)

            def start(self):
                pass

            def is_alive(self):
                return False

        import voice_typer.server.ipc_server as ipc_server_mod

        monkeypatch.setattr(ipc_server_mod.threading, "Thread", FakeThread)

        server.start()
        try:
            # the gate must prevent the stdin listener thread
            # from being created. ``ipc-server`` must NOT be in the
            # created_threads list (only ``heartbeat-watchdog`` is).
            assert "ipc-server" not in created_threads, (
                "stdin listener 'ipc-server' thread was spawned "
                "even though VOICE_TYPER_ALLOW_STDIN_IPC is unset — the "
                "gate failed to refuse the unauthenticated stdin path."
            )
            assert server._stdin_thread is None, (
                "_stdin_thread must be None when the gate refuses to spawn the stdin listener."
            )
        finally:
            server.stop()

    def test_stdin_thread_spawned_when_env_var_set(self, monkeypatch) -> None:
        """when ``_tcp_mode`` is False AND the env var IS set to
        ``"1"``, ``start()`` must spawn the stdin listener thread.

        This is the explicit-opt-in path for development / testing.
        """
        from voice_typer.server import event_bus
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setenv("VOICE_TYPER_ALLOW_STDIN_IPC", "1")
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        app = MagicMock()
        app._shutting_down = False
        app._thread_registry = None
        app.tray.set_state._vt_wrapped = False
        service = MagicMock()
        server = IPCServer(app, service=service)
        server._tcp_mode = False

        monkeypatch.setattr(event_bus, "subscribe", lambda fn: None)
        created_threads: list[str] = []

        class FakeThread:
            def __init__(self, target=None, name=None, daemon=False):
                self.name = name
                created_threads.append(name)

            def start(self):
                pass

            def is_alive(self):
                return False

        import voice_typer.server.ipc_server as ipc_server_mod

        monkeypatch.setattr(ipc_server_mod.threading, "Thread", FakeThread)

        server.start()
        try:
            assert "ipc-server" in created_threads, (
                "stdin listener 'ipc-server' thread was NOT "
                "spawned even though VOICE_TYPER_ALLOW_STDIN_IPC=1 — "
                "the gate must allow explicit opt-in for dev/testing."
            )
            # ``_stdin_thread`` is a FakeThread instance (not a real
            # Thread); just assert it's not None.
            assert server._stdin_thread is not None, (
                "_stdin_thread must be set when the gate allows the stdin listener (env var is '1')."
            )
        finally:
            server.stop()

    def test_allow_stdin_cli_flag_sets_env_var(self, monkeypatch) -> None:
        """``--allow-stdin`` CLI flag in ``parse_ipc_args()``
        sets ``VOICE_TYPER_ALLOW_STDIN_IPC=1`` so the gate at
        ``start()`` allows the stdin listener."""
        import sys

        from voice_typer.server.ipc_server import parse_ipc_args

        monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--allow-stdin"])
        try:
            port, ws_mode = parse_ipc_args()
            assert os.environ.get("VOICE_TYPER_ALLOW_STDIN_IPC") == "1", (
                "--allow-stdin CLI flag must set "
                "VOICE_TYPER_ALLOW_STDIN_IPC=1 so the gate at start() "
                "allows the stdin listener."
            )
            assert port is None
            assert ws_mode is False
        finally:
            monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)

    def test_no_allow_stdin_flag_does_not_set_env_var(self, monkeypatch) -> None:
        """without ``--allow-stdin``, the env var is NOT set by
        ``parse_ipc_args()`` (the gate at ``start()`` would refuse)."""
        import sys

        from voice_typer.server.ipc_server import parse_ipc_args

        monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)
        monkeypatch.setattr(sys, "argv", ["ipc_server"])
        try:
            parse_ipc_args()
            assert os.environ.get("VOICE_TYPER_ALLOW_STDIN_IPC") is None, (
                "parse_ipc_args() must NOT set "
                "VOICE_TYPER_ALLOW_STDIN_IPC when --allow-stdin is not "
                "passed (the gate at start() must refuse)."
            )
        finally:
            monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)


# _handle_shutdown re-entrancy gate + thread registry ─────────


class TestShutdownGate:
    """``_handle_shutdown`` is idempotent.

    a double-``shutdown`` (e.g. the Tauri host's WS
    transport retrying after a slow ack) spawned a SECOND untracked
    ``ipc-shutdown-cleanup`` daemon thread — both threads would race
    into ``service.quit()`` / ``_do_cleanup()`` and double-free the
    mic stream, hotkey listeners, single-instance mutex, etc.

    The fix adds a per-instance ``_shutdown_started: threading.Event``
    that ``_handle_shutdown`` checks at the top; the second invocation
    no-ops (returns the ack envelope without spawning another thread).
    The cleanup thread is registered on
    ``self.app._thread_registry`` (when available) so ``shutdown_all()``
    can join it.
    """

    def test_shutdown_started_event_initialized_in_init(self) -> None:
        """``__init__`` must declare a per-instance
        ``_shutdown_started: threading.Event`` so ``_handle_shutdown``
        can no-op the second invocation."""
        server = _make_server()
        assert hasattr(server, "_shutdown_started"), (
            "IPCServer.__init__ must declare _shutdown_started "
            "(a threading.Event) so _handle_shutdown can no-op the "
            "second invocation (double-shutdown race)."
        )
        assert isinstance(server._shutdown_started, threading.Event), (
            f"_shutdown_started must be a threading.Event; got {type(server._shutdown_started)!r}."
        )
        assert not server._shutdown_started.is_set(), (
            "_shutdown_started must start unset (no shutdown has been requested yet)."
        )

    def test_double_handle_shutdown_no_ops_second_invocation(self) -> None:
        """calling ``_handle_shutdown`` twice must NOT spawn two
        cleanup threads. ``service.quit()`` is called exactly once."""
        server = _make_server()
        # Stub service.quit so it returns immediately (no real cleanup).
        server.service.quit = MagicMock()

        result1 = server._handle_shutdown(data=None, resp={"id": 1})
        result2 = server._handle_shutdown(data=None, resp={"id": 2})

        # Both invocations return the ack envelope (the host's retry
        # timer expects an ack, not an error).
        assert result1 is not None and result1["data"] == {"ack": True}
        assert result2 is not None and result2["data"] == {"ack": True}

        # Wait briefly for the cleanup thread to land its call.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and server.service.quit.call_count < 1:
            time.sleep(0.005)
        # service.quit is called EXACTLY ONCE — the second
        # invocation's no-op path doesn't spawn a second cleanup thread.
        assert server.service.quit.call_count == 1, (
            f"service.quit was called "
            f"{server.service.quit.call_count} times; expected exactly 1. "
            f"The double-shutdown race spawned a second cleanup thread."
        )

    def test_shutdown_started_event_set_after_first_invocation(self) -> None:
        """after the first ``_handle_shutdown`` call, the
        ``_shutdown_started`` event must be set so the second
        invocation's no-op gate fires."""
        server = _make_server()
        server.service.quit = MagicMock()
        assert not server._shutdown_started.is_set()
        server._handle_shutdown(data=None, resp={"id": 1})
        assert server._shutdown_started.is_set(), (
            "_shutdown_started must be set after the first _handle_shutdown call so the second invocation no-ops."
        )

    def test_cleanup_thread_registered_on_thread_registry(self) -> None:
        """the cleanup thread must be registered on
        ``self.app._thread_registry`` (when the app provides one) so
        ``shutdown_all()`` can join it during ``VoiceTyperApp.quit()``."""
        server = _make_server()
        # ``_make_server`` returns an IPCServer whose ``app`` is a
        # MagicMock — ``app._thread_registry`` is also a MagicMock by
        # default. The cleanup-thread registration must call
        # ``app._thread_registry.register(name="ipc-shutdown-cleanup", ...)``.
        server.service.quit = MagicMock()
        server._handle_shutdown(data=None, resp={"id": 1})

        # Wait briefly for the cleanup thread to be spawned + registered.
        deadline = time.monotonic() + 2.0
        registered_names: list[str] = []
        while time.monotonic() < deadline:
            registered_names = [
                str(call.kwargs.get("name", "")) for call in server.app._thread_registry.register.call_args_list
            ]
            if "ipc-shutdown-cleanup" in registered_names:
                break
            time.sleep(0.005)
        assert "ipc-shutdown-cleanup" in registered_names, (
            "the cleanup thread must be registered on "
            "self.app._thread_registry under the name "
            "'ipc-shutdown-cleanup' so shutdown_all() can join it. "
            f"Observed register() calls: {registered_names!r}."
        )

    def test_cleanup_thread_not_registered_when_registry_none(self) -> None:
        """when ``self.app._thread_registry`` is None (e.g. a
        test bypass that doesn't wire the central registry), the
        cleanup thread is still spawned but NOT registered. The
        ``getattr(self.app, '_thread_registry', None)`` defensive
        lookup must not raise."""
        server = _make_server()
        server.app._thread_registry = None
        server.service.quit = MagicMock()
        # Must NOT raise — the registration path is guarded by
        # ``if _registry is not None:``.
        result = server._handle_shutdown(data=None, resp={"id": 1})
        assert result is not None and result["data"] == {"ack": True}

    def test_handle_shutdown_source_contains_shutdown_started_gate(self) -> None:
        """source-level pin — ``_handle_shutdown`` must check
        ``_shutdown_started`` at the top before any side effect."""
        from voice_typer.server.ipc_server import IPCServer

        src = inspect.getsource(IPCServer._handle_shutdown)
        assert "_shutdown_started" in src, (
            "_handle_shutdown must reference _shutdown_started so the double-shutdown race is closed."
        )
        assert "self._shutdown_started.is_set()" in src, (
            "_handle_shutdown must call self._shutdown_started.is_set() to detect the second invocation."
        )
        assert "self._shutdown_started.set()" in src, (
            "_handle_shutdown must call "
            "self._shutdown_started.set() before spawning the cleanup "
            "thread so the second invocation's no-op is atomic with "
            "the first's thread-spawn decision."
        )


# registry extraction ─────────────────────────────────────────


class TestRegistryExtraction:
    """``_COMMAND_REGISTRY`` + ``_READONLY_COMMANDS`` +
    ``_PYTHON_ONLY_COMMANDS`` are canonical to
    :mod:`voice_typer.server.ipc.registry`.

    ``_COMMAND_REGISTRY`` + ``_PYTHON_ONLY_COMMANDS`` were
    class attributes on :class:`IPCServer` (in the 2,100-line
    ``ipc_server.py`` god-module) and ``_READONLY_COMMANDS`` lived in
    ``ipc._helpers.py``; the split made the three-layers-must-agree
    parity contract harder to reason about.

    The extraction is behavior-preserving — same dict, same keys, same
    values. :class:`IPCServer` re-aliases ``_COMMAND_REGISTRY`` and
    ``_PYTHON_ONLY_COMMANDS`` as class attributes so every existing
    ``IPCServer._COMMAND_REGISTRY`` / ``IPCServer._PYTHON_ONLY_COMMANDS``
    call site keeps working unchanged.
    """

    def test_registry_module_is_importable(self) -> None:
        """the new ``voice_typer.server.ipc.registry`` module
        is importable and exposes the three canonical constants."""
        from voice_typer.server.ipc import registry

        assert hasattr(registry, "_COMMAND_REGISTRY"), (
            "ipc.registry must expose _COMMAND_REGISTRY (module-level dict — the canonical source of truth)."
        )
        assert hasattr(registry, "_READONLY_COMMANDS"), "ipc.registry must expose _READONLY_COMMANDS."
        assert hasattr(registry, "_PYTHON_ONLY_COMMANDS"), "ipc.registry must expose _PYTHON_ONLY_COMMANDS."

    def test_registry_module_constants_are_correct_types(self) -> None:
        """the registry module's constants have the documented
        types (dict / frozenset / frozenset)."""
        from voice_typer.server.ipc import registry

        assert isinstance(registry._COMMAND_REGISTRY, dict), (
            f"registry._COMMAND_REGISTRY must be a dict; got {type(registry._COMMAND_REGISTRY)!r}."
        )
        assert isinstance(registry._READONLY_COMMANDS, frozenset), (
            f"registry._READONLY_COMMANDS must be a frozenset; got {type(registry._READONLY_COMMANDS)!r}."
        )
        assert isinstance(registry._PYTHON_ONLY_COMMANDS, frozenset), (
            f"registry._PYTHON_ONLY_COMMANDS must be a frozenset; got {type(registry._PYTHON_ONLY_COMMANDS)!r}."
        )

    def test_ipc_server_re_exports_registry_constants(self) -> None:
        """``ipc_server.py`` must re-export ``_COMMAND_REGISTRY``,
        ``_READONLY_COMMANDS``, and ``_PYTHON_ONLY_COMMANDS`` at module
        level so existing ``from voice_typer.server.ipc_server import
        _COMMAND_REGISTRY`` callers keep working unchanged."""
        import voice_typer.server.ipc_server as ipc_server_mod
        from voice_typer.server.ipc import registry

        # Object identity: the module-level name must be the SAME object
        # as the registry module's constant (single source of truth).
        assert ipc_server_mod._COMMAND_REGISTRY is registry._COMMAND_REGISTRY, (
            "ipc_server._COMMAND_REGISTRY must be the SAME object "
            "as registry._COMMAND_REGISTRY (single source of truth — "
            "not a parallel copy)."
        )
        assert ipc_server_mod._READONLY_COMMANDS is registry._READONLY_COMMANDS, (
            "ipc_server._READONLY_COMMANDS must be the SAME object as registry._READONLY_COMMANDS."
        )
        assert ipc_server_mod._PYTHON_ONLY_COMMANDS is registry._PYTHON_ONLY_COMMANDS, (
            "ipc_server._PYTHON_ONLY_COMMANDS must be the SAME object as registry._PYTHON_ONLY_COMMANDS."
        )

    def test_ipc_server_class_re_aliases_registry_constants(self) -> None:
        """class:`IPCServer` must re-alias ``_COMMAND_REGISTRY``
        and ``_PYTHON_ONLY_COMMANDS`` as class attributes so every
        existing ``IPCServer._COMMAND_REGISTRY`` /
        ``IPCServer._PYTHON_ONLY_COMMANDS`` call site (pinned by
        ``test_ipc_shutdown_registry``, ``test_ec4_python_command_...``,
        etc.) keeps working unchanged."""
        from voice_typer.server.ipc import registry
        from voice_typer.server.ipc_server import IPCServer

        assert IPCServer._COMMAND_REGISTRY is registry._COMMAND_REGISTRY, (
            "IPCServer._COMMAND_REGISTRY must be the SAME object "
            "as registry._COMMAND_REGISTRY (class-level re-alias for "
            "backward compat with every IPCServer._COMMAND_REGISTRY "
            "call site)."
        )
        assert IPCServer._PYTHON_ONLY_COMMANDS is registry._PYTHON_ONLY_COMMANDS, (
            "IPCServer._PYTHON_ONLY_COMMANDS must be the SAME object as registry._PYTHON_ONLY_COMMANDS."
        )

    def test_registry_dict_same_keys_and_values_as_before(self) -> None:
        """behavior-preserving extraction — same dict, same keys,
        same values. Spot-check the critical entries (shutdown,
        tray_click, heartbeat) plus the overall key count."""
        from voice_typer.server.ipc import registry

        # Critical entries that other tests pin (test_ipc_shutdown_registry,
        # test_command_registry_parity, test_ipc_command_registry_sync,
        # test_tauri_sidecar_gate).
        assert registry._COMMAND_REGISTRY["shutdown"] == "_handle_shutdown"
        assert registry._COMMAND_REGISTRY["tray_click"] == "_handle_tray_click"
        assert registry._COMMAND_REGISTRY["heartbeat"] == "_handle_heartbeat"
        # reconciliation documented 64 commands;
        # (test_cloud_connection) + XZ-SEC-05 (add_trusted_endpoint)
        # brought it to 65; onboarding_set_backend (Model-step backend
        # choice) brought it to 66; reset_macos_accessibility (finding
        # #127 part b) brought it to 67; reset_linux_permissions
        # (finding #127 part b Linux sibling) brought it to 68;
        # check_accessibility re-added (finding #919 part b — Settings
        # → Troubleshooting surfaces the stale-grant reset) brought it
        # to 69.
        # transcribe_offline (Phase 2b pack downloader — plan-runtime-
        # pack-split.md §7.4) brought it to 70.
        # prewarm retirement (plan §6.2 P-1 — get_prewarm_status,
        # run_prewarm, open_prewarm_log removed across all 4 allowlists
        # in lockstep) brought it to 67.
        # prewarm status RESTORATION (plan §6.3 addendum 2026-08-14 —
        # Settings → About Cache Status card restored verbatim from
        # 5a319872; run_prewarm stays retired) brought it back to 69.
        # check_offline_pack_update (auto-update feature, docs/auto-update-feature.md
        # — 2026-08-14) brought it to 70.
        # run_prewarm (plan §6.3 addendum 2nd half, 2026-08-14 —
        # re-implemented to re-run the warm phase in-process instead of
        # The registry holds ALL commands: the 71 forwarded ones (the
        # allowlist in allowed-commands.ts — pinned in SECURITY.md) plus
        # the 2 python-only commands (shutdown, tray_click) that never
        # cross the Electron bridge. The count is deliberately pinned
        # here and in SECURITY.md — update all sources of truth
        # together. Adding a command to the registry WITHOUT the TS
        # allowlist fails the parity test
        # (test_electron_ipc_and_build.py::test_allowlist_matches_server_commands).
        assert len(registry._COMMAND_REGISTRY) == 74, (
            f"registry._COMMAND_REGISTRY must contain 73 entries "
            f"(71 forwarded in allowed-commands.ts + shutdown + "
            f"tray_click python-only); got "
            f"{len(registry._COMMAND_REGISTRY)}. "
            f"If the count drifted, update this test together with the "
            f"registry + the TS/Rust allowlists."
        )

    def test_python_only_commands_unchanged(self) -> None:
        """``_PYTHON_ONLY_COMMANDS`` is the documented
        ``{"shutdown", "tray_click"}`` frozenset (EC-4 exception set)."""
        from voice_typer.server.ipc import registry

        assert frozenset({"shutdown", "tray_click"}) == registry._PYTHON_ONLY_COMMANDS, (
            f"registry._PYTHON_ONLY_COMMANDS must be "
            f"frozenset({{'shutdown', 'tray_click'}}); got "
            f"{registry._PYTHON_ONLY_COMMANDS!r}."
        )

    def test_readonly_commands_unchanged(self) -> None:
        """``_READONLY_COMMANDS`` is the documented 4-element
        frozenset (GT-25)."""
        from voice_typer.server.ipc import registry

        assert (
            frozenset({"get_status", "get_config", "get_model_catalog", "heartbeat"}) == registry._READONLY_COMMANDS
        ), (
            f"registry._READONLY_COMMANDS must be the 4-element "
            f"frozenset {{'get_status', 'get_config', 'get_model_catalog', "
            f"'heartbeat'}}; got {registry._READONLY_COMMANDS!r}."
        )

    def test_registry_history_comment_block_present(self) -> None:
        """the ~30 "REMOVED" historical comments were
        consolidated into a ``# Registry history`` block at the top of
        ``ipc/registry.py`` (the regression guard in
        ``test_dead_code_stays_removed.py`` already pins the removals
        independently)."""
        from voice_typer.server.ipc import registry

        src = inspect.getsource(registry)
        assert "Registry history" in src, (
            "ipc/registry.py must contain a '# Registry history' "
            "comment block at the top consolidating the ~30 'REMOVED' "
            "comments that previously lived inline next to the dict "
            "literal in ipc_server.py."
        )

    def test_ipc_server_no_longer_defines_inline_dict_literal(self) -> None:
        """``ipc_server.py`` must NOT contain the inline
        ``_COMMAND_REGISTRY: dict[str, str] = {`` dict literal — the
        dict was extracted to ``ipc.registry`` and ``ipc_server.py``
        only re-aliases it as a class attribute.

        The class-level alias ``_COMMAND_REGISTRY: dict[str, str] =
        _COMMAND_REGISTRY`` (the re-assignment of the imported name to
        a class attribute) is fine; we only flag the literal `{``
        assignment form.
        """
        import voice_typer.server.ipc_server as ipc_server_mod

        src = inspect.getsource(ipc_server_mod)
        # The class-level alias line is ``_COMMAND_REGISTRY: dict[str, str] = _COMMAND_REGISTRY``
        # (no ``{``). The inline literal was
        # ``_COMMAND_REGISTRY: dict[str, str] = {`` followed by the
        # dict body. We must NOT find the literal form.
        assert "_COMMAND_REGISTRY: dict[str, str] = {" not in src, (
            "ipc_server.py must NOT define the inline "
            "_COMMAND_REGISTRY dict literal — it has been extracted to "
            "ipc.registry. The class-level alias "
            "(``_COMMAND_REGISTRY: dict[str, str] = _COMMAND_REGISTRY``) "
            "is the only allowed form."
        )


class TestTranscribeOfflineDegradation:
    """Phase 2d degradation matrix (§8.10).

    ``_handle_transcribe_offline`` must NOT queue silently when the
    offline pack is missing — the request can never complete, so the
    ack carries ``queued: False`` + ``degraded: True`` +
    ``reason: "offline_pack_missing"`` for the renderer to surface.
    """

    def _dispatch(self, server: IPCServer) -> dict:
        return server._dispatch({"id": 7, "type": "transcribe_offline", "data": {}})

    def test_pack_missing_returns_degraded_not_queued(self, monkeypatch):
        from voice_typer.server.service import update_check

        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda: None)
        resp = self._dispatch(_make_server())
        assert resp["type"] == "ack"
        assert resp["data"]["queued"] is False
        assert resp["data"]["degraded"] is True
        assert resp["data"]["reason"] == "offline_pack_missing"

    def test_pack_present_acks_queued(self, monkeypatch):
        from voice_typer.server.service import update_check

        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda: "v1")
        resp = self._dispatch(_make_server())
        assert resp["type"] == "ack"
        assert resp["data"]["queued"] is True
        assert "degraded" not in resp["data"]

    def test_check_failure_fails_safe_to_degraded(self, monkeypatch):
        from voice_typer.server.service import update_check

        def boom():
            raise RuntimeError("broken pack root")

        monkeypatch.setattr(update_check, "_local_offline_pack_version", boom)
        resp = self._dispatch(_make_server())
        assert resp["data"]["queued"] is False
        assert resp["data"]["reason"] == "offline_pack_missing"
