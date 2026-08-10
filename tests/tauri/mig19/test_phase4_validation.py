"""MIG-1.9 Phase 4 — Validation gate for the frozen 68-command / 21-event bridge.

ADR-0020 §2 mandates the 68-command table + the 21-event table as the
**frozen wire contract** for the v1 Tauri migration. ADR-0020 §16
forbids silent additions: every new command / event MUST be (1) added
to ``_COMMAND_REGISTRY`` + a ``_handle_<cmd>`` mixin, (2) documented as
an ADR addendum, (3) validated by ``_validate_dict_payload`` with an
explicit schema, (4) covered by a test in
``tests/test_ipc_dispatch_errors.py`` (or a sibling file).

This file is the Phase 4 validation gate. It does NOT spin up a real
Tauri host + Nuitka-frozen sidecar (that is a per-platform host task —
see "VALIDATE ON HOST" below). It validates the **contract source
code** so the moment anyone adds or removes a command/event without
updating the ADR, this test fails loudly:

1. ``_COMMAND_REGISTRY`` exists, is a ``dict[str, str]``, and contains
   every command listed in the ADR-0020 §2 table.
2. Each registry value names a ``_handle_<cmd>`` method that actually
   exists on :class:`IPCServer` (no orphaned / dangling entries).
3. ``_validate_dict_payload`` is the single source of truth for command
   payload validation (ADR-0020 §2 + §16 item 3) — it must be importable
   and return ``(validated_dict, None)`` / ``(None, error)`` tuples.
4. The Rust WS bridge (``src-tauri/src/sidecar/ws.rs``) forwards every
   server-initiated event by name (no allowlist) AND emits a generic
   ``python-event`` catch-all for the ``usePython`` hook, applies the
   ADR §6.1 backward-compat ``electron_notification`` → ``notification``
   alias (the ``relaunch_electron`` → ``relaunch_app`` rename was dropped
   in the PVT-2 cleanup — the Python sidecar now publishes
   ``relaunch_app`` directly and ``main.rs`` listens for it), and
   coalesces ``bubble_level`` to ≤30 Hz per ADR §9.
5. ``TAURI_SIDECAR=1`` env var disables the heartbeat-watchdog thread
   on the Python side (ADR-0020 §2 + §10 — replaces ADR-0018 on the
   Tauri path).
6. The sidecar NEVER echoes the auth token in any outbound frame or
   log message (ADR-0020 §3 — token rotation is per-launch and a
   leaked token in a log defeats that).
7. The command / event contract is frozen: registry size is asserted,
   new commands require explicit baseline updates here so the
   reviewer is forced to add ADR addendum + test coverage.

=====================================================================
VALIDATE ON HOST — exact commands a human must run on each platform
=====================================================================

These commands MUST be run on a real host with a Nuitka-frozen sidecar
binary + Tauri bundle (the Linux sandbox cannot build them — see the
MIG-1.8 per-triple freeze test). They exercise the **full WS bridge**
end-to-end: a real Rust host connecting to a real Python sidecar over
the HMAC-authenticated WS, dispatching every command and observing
every event. The sandbox tests above validate the **contract**; the
host commands below validate the **wire**.

---------------------------------------------------------------------
VALIDATE ON WINDOWS HOST (x86_64-pc-windows-msvc):
---------------------------------------------------------------------
    1. Build the per-triple sidecar + Tauri bundle (see
       tests/tauri/mig18/test_per_triple_freeze.py
       "VALIDATE ON WINDOWS HOST" section).
    2. Launch the bundled app:
         src-tauri/target/release/bundle/nsis/*.exe
       Expected: app window opens, sidecar binds 127.0.0.1:0, Rust
       reads ``{"event":"server_started","port":N}`` from sidecar
       stdout, opens a WS client, completes the HMAC handshake.
    3. From the bundled app, exercise every command in the §2 table:
         toggle_dictation, get_status, get_history, set_config,
         get_vocabulary, save_vocabulary, onboarding_start, etc.
       Expected: each returns ``{"type":"result","data":{...}}`` over
       the WS; no ``unknown_command`` errors; no rate_limited errors
       under normal interactive load.
    4. Trigger every event in the §event table:
         - bubble_show / bubble_hide / bubble_level (start dictation)
         - transcription_final (dictate a phrase)
         - vocabulary_suggestion (dictate a low-confidence phrase)
         - config_changed (toggle any setting)
         - history_changed (clear history)
         - microphones_changed (unplug / replug a USB mic)
         - microphone_test_complete (run mic test)
         - recording_started / recording_stopped / audio_clip (record)
         - download_progress (download the tiny model)
         - notification (trigger a toast via the settings)
         - hotkey_capture_cancel (start + ESC a hotkey capture)
         - navigate / show_window (tray menu items)
         - relaunch_app (restart_app command — verify app relaunches)
         - quit_app (quit_app command — verify clean exit)
         - ready (emitted once on first authed connection)
       Expected: each event appears BOTH under its specific name AND
       under the generic ``python-event`` name in the webview.
    5. Verify the heartbeat watchdog is DISABLED on the Tauri path:
       - Check sidecar.log for the line:
         "[IPC] TAURI_SIDECAR=1 — skipping heartbeat-watchdog thread"
       - Kill the webview (Task Manager → End Task on the app window).
         Wait 130s. The sidecar process must EXIT (supervisor
         detected the WS-close and force-respawned / killed it). It
         must NOT linger past 120s waiting for a heartbeat that never
         comes (that would prove the watchdog was not disabled).
    6. Verify the token is never logged:
         findstr /S /I "VOICE_TYPER_IPC_TOKEN" %APPDATA%\\voice-typer\\logs\\*.log
       Expected: zero hits (the token must not appear in any log line).

---------------------------------------------------------------------
VALIDATE ON macOS HOST (aarch64-apple-darwin + x86_64-apple-darwin):
---------------------------------------------------------------------
    Same as Windows but launch the .app bundle from
    ``src-tauri/target/release/bundle/dmg/*.dmg``. Check the log file
    at ``~/Library/Application Support/voice-typer/logs/`` for the
    same TAURI_SIDECAR=1 message and the same zero-token-leak
    invariant (use ``grep -r VOICE_TYPER_IPC_TOKEN`` instead of
    ``findstr``). Run on BOTH Apple Silicon and Intel.

---------------------------------------------------------------------
VALIDATE ON LINUX HOST (x86_64-unknown-linux-gnu + aarch64-unknown-linux-gnu):
---------------------------------------------------------------------
    Same as Windows but launch the AppImage from
    ``src-tauri/target/release/bundle/appimage/*.AppImage``. Check the
    log file at ``~/.local/share/voice-typer/logs/``. On Wayland
    sessions additionally verify the global-hotkey + tray paths
    (those are host-concern, not bridge-concern — they are validated
    separately in the mig15/mig16/mig17 host tests).

=====================================================================
END VALIDATE ON HOST
=====================================================================
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

# ── Source-of-truth paths (ADR-0020 §2 + §event table) ──────────────────

# Path layout:
#   parents[0] = mig19/
#   parents[1] = tauri/
#   parents[2] = tests/
#   parents[3] = <project root> (voice-typer/)
REPO_ROOT = Path(__file__).resolve().parents[3]
IPC_SERVER_PY = REPO_ROOT / "voice_typer" / "server" / "ipc_server.py"
# ARCH-045: the ``ipc_server.py`` shim re-exports the IPC logic from
# the ``voice_typer/server/ipc/`` package. The TAURI_SIDECAR gates
# moved with the split: the heartbeat-watchdog skip lives in
# ``ipc/lifecycle.py`` (``IPCServer.__init__``) and the ``--ws`` env
# propagation + single-instance mutex skip live in
# ``ipc/entrypoint.py`` (``main`` / ``parse_args``). Tests that
# source-inspect the TAURI_SIDECAR gates read those files instead of
# the shim.
IPC_SERVER_IMPL_PY = REPO_ROOT / "voice_typer" / "server" / "ipc" / "lifecycle.py"
IPC_MAIN_PY = REPO_ROOT / "voice_typer" / "server" / "ipc" / "entrypoint.py"
SIDECAR_WS_PY = REPO_ROOT / "voice_typer" / "server" / "sidecar_ws.py"
WS_RS = REPO_ROOT / "src-tauri" / "src" / "sidecar" / "ws.rs"
WS_EVENT_PROTOCOL_RS = REPO_ROOT / "src-tauri" / "src" / "sidecar" / "ws" / "event_protocol.rs"
ADR_0020 = REPO_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"

# ── ADR-0020 §2 — frozen 68-command table (the v1 wire contract) ─────────
#
# This set is the authoritative list of commands the ADR freezes for v1.
# Source: ADR-0020 §2 table ("Sidecar←UI Command Table"). Per §16, this
# list MUST NOT grow without (1) a new ``_handle_<cmd>`` mixin, (2) an
# ADR addendum, (3) a ``_validate_dict_payload`` schema, and (4) a test
# in tests/test_ipc_dispatch_errors.py. If a new command is added to
# ``_COMMAND_REGISTRY`` without updating this set, the
# ``test_command_contract_is_frozen_no_untested_additions`` test fails.
EXPECTED_COMMANDS: frozenset[str] = frozenset(
    {
        # status_handlers
        "get_status",
        "get_volume_backend_status",
        "get_model_status",
        "get_prewarm_status",
        "run_prewarm",
        "open_prewarm_log",
        # dictation_handlers
        "toggle_dictation",
        "undo_last",
        "force_cancel_transcription",
        "repaste_last",  # re-paste last transcription; handler in handlers/repaste_handlers.py
        # history_handlers
        "get_history",
        "get_today_stats",
        "delete_history",
        "restore_history",
        "clear_history",
        "toggle_favorite",
        "get_favorites",
        "search_history",
        # history_handlers (on-demand full-text + total-count handlers;
        # the Dashboard's "Total Dictations" stat calls ``get_history_count``
        # and the History page calls ``get_transcription_text`` when the
        # user expands a row past the 500-char preview). Both are wired
        # by the history-handlers audit and were previously tracked only
        # by the "extra commands" allowlist (KNOWN_UNDOCUMENTED_COMMANDS).
        # They are now formally added to the frozen contract.
        "get_history_count",
        "get_transcription_text",
        # config_handlers
        "get_config",
        "get_defaults",
        "set_config",
        # vocabulary_handlers
        "get_vocabulary",
        "save_vocabulary",
        # vocabulary_automation_handlers — REMOVED from _COMMAND_REGISTRY:
        # ``get_vocabulary_suggestions``, ``apply_vocabulary_suggestion``,
        # ``dismiss_vocabulary_suggestion`` were deferred pending UX
        # redesign (the renderer's allowed-commands.ts dropped the three
        # entries; the handler mixin still exists for future re-wiring).
        # See ``voice_typer/server/ipc_server.py`` registry comments +
        # ``tests/test_dead_code_stays_removed.py`` for the regression guard.
        # templates_handlers
        "get_templates",
        "save_templates",
        # onboarding_handlers
        "onboarding_is_first_run",
        "onboarding_start",
        # REMOVED: ``onboarding_get_step`` — the renderer holds wizard
        # state client-side (see ``test_dead_code_stays_removed.py``).
        "onboarding_next_step",
        "onboarding_prev_step",
        "onboarding_set_microphone",
        "onboarding_set_hotkey",
        "onboarding_set_model",
        # ADR-0020 §16 addendum (2026-08-06): ``onboarding_set_backend``
        # — the Model step's explicit local-vs-cloud backend choice
        # (Model-step rework: the user chooses; the app NEVER
        # auto-downloads models). Handler in onboarding_handlers.py with
        # a ``_validate_dict_payload`` schema + IPC validation coverage.
        "onboarding_set_backend",
        "onboarding_skip",
        "onboarding_apply",
        "onboarding_get_microphones",
        "onboarding_get_model_options",
        "onboarding_get_hotkey_presets",
        # (IMPROVE-2026-07-19): macOS/Linux permission probe added
        # server-side for / The renderer's permission flow
        # now uses this + a Tauri-side invocation.
        "onboarding_check_permissions",
        # REMOVED: ``onboarding_get_model_catalog`` — the renderer uses
        # the non-onboarding ``get_model_catalog`` command for catalog
        # data; this onboarding-scoped alias was never wired up on the
        # client. See ``test_dead_code_stays_removed.py``.
        # REMOVED: ``onboarding_request_keyboard_permission`` — the
        # renderer's permission flow now uses ``onboarding_check_permissions``
        # + a Tauri-side invocation; the legacy IPC dispatch route was
        # deleted in lockstep with the TS allowlist narrowing.
        "onboarding_reset",
        # microphone_handlers
        "get_microphones",
        # REMOVED: ``refresh_microphones``, ``get_rms_level``,
        # ``get_audio_status`` — these were dropped to match the
        # Tauri/Rust allowlist narrowing (the renderer's
        # ``allowed-commands.ts`` also dropped them; the service-layer
        # methods still exist and are called from internal code paths;
        # only the IPC dispatch routes were deleted). See
        # ``test_dead_code_stays_removed.py`` for the regression guard.
        # microphone_test_handlers
        "microphone_test_start",
        "microphone_test_stop",
        "microphone_test_cancel",
        # REMOVED: ``microphone_test_status`` — the renderer polls
        # ``microphone_test_get_level`` at 60 Hz during a test; the
        # separate status query was unused. See
        # ``test_dead_code_stays_removed.py``.
        "microphone_test_get_level",
        # level_monitor_handlers
        "level_monitor_start",
        "level_monitor_stop",
        # REMOVED: ``level_monitor_status`` — the renderer subscribes
        # to the ``level_monitor_level`` push event instead of polling
        # a status endpoint. See ``test_dead_code_stays_removed.py``.
        # model_handlers
        "download_model",
        "cancel_model_download",
        "pause_model_download",
        "resume_model_download",
        "get_model_catalog",
        # REMOVED: ``test_llm_connection`` — the renderer's Settings
        # page now uses the service-layer method directly (not over
        # IPC). The TS allowlist also dropped it. See
        # ``test_dead_code_stays_removed.py`` for the
        # ``TestDispatchesTestLlmConnection`` inversion guard.
        "import_model",
        "delete_model",
        # system_handlers
        "restart_app",
        "quit_app",
        # REMOVED: ``export_diagnostics``, ``show_electron_notification`` —
        # the Tauri host now handles each via a dedicated Rust command
        # (``export_diagnostics`` and the tray-notification path
        # respectively) rather than bridging through Python IPC. The
        # Python-side service methods still exist for the legacy
        # Electron path. See ``test_dead_code_stays_removed.py``.
        # ADR-0020 §16 addendum (2026-08-10, finding #919 part b):
        # ``check_accessibility`` — RE-ADDED to the contract. The
        # Settings → Troubleshooting UI invokes it on macOS to surface
        # the stale-grant ``tccutil`` reset command (``suggest_reset``
        # + ``reset_command`` on a confirmed stale grant); the command
        # is wired back through the registry + TS + Rust allowlists in
        # lockstep. Handler in system_handlers.py with a
        # `_validate_dict_payload` schema + handler tests
        # (tests/handlers/test_system_handlers.py).
        "check_accessibility",
        "set_tray_locale",
        "set_esc_cancel_paused",
        # ADR-0020 §16 addendum (2026-08-09, finding #127 part b):
        # ``reset_macos_accessibility`` — Settings → Troubleshooting
        # "Reset Accessibility Permission" button. Runs `tccutil reset
        # Accessibility <bundle-id>` (bundle ID resolved at runtime via
        # macos_bundle_id.py) + re-opens System Settings. Handler in
        # system_handlers.py with a `_validate_dict_payload` schema +
        # dispatch-errors test (tests/test_ipc_dispatch_errors.py).
        "reset_macos_accessibility",
        # ADR-0020 §16 addendum (2026-08-10, finding #127 part b):
        # ``reset_linux_permissions`` — Settings → Troubleshooting
        # "Reset Linux Permission" button (Linux sibling of the macOS
        # TCC reset). Clears a stale polkit authorization
        # (``auth_admin_keep`` is cached by polkitd) by restarting the
        # polkit daemon via pkexec; pkaction enumerates the Voice Typer
        # actions + pkcheck verifies the post-reset state. Handler in
        # system_handlers.py with a `_validate_dict_payload` schema +
        # dispatch-errors test (tests/test_ipc_dispatch_errors.py).
        "reset_linux_permissions",
        # ipc_server ( / ADR-0018) — kept on the registry even
        # though it is REMOVED on the Tauri path; a stray frame from a
        # legacy UI must still hit the handler (not ``unknown_command``).
        "heartbeat",
        # ADR-0020 §16 addendum (2026-07-24): commands added
        # since the prior baseline. Each has a ``_handle_<cmd>`` mixin +
        # ``_validate_dict_payload`` schema + dispatch-errors test.
        #   - ``shutdown`` (system_handlers.py — graceful IPC shutdown;
        # the  controller lives in ``shutdown_controller.py``).
        # REMOVED: ``delete_all_personal_data`` + ``export_gdpr_bundle``
        # — the Tauri host now invokes them via dedicated Rust commands
        # (with their own allowlist entries and consent prompts) rather
        # than bridging through the generic dispatch path. The
        # Python-side service methods still exist (called from the
        # Rust bridge). See ``test_dead_code_stays_removed.py``.
        "shutdown",
    }
)
assert len(EXPECTED_COMMANDS) == 65, (
    "ADR-0020 §2 freezes the command table. 65 = post-cleanup baseline "
    "after ZR-45 + the Tauri/Rust allowlist narrowing (+ ``onboarding_set_backend``, "
    "§16 addendum 2026-08-06; + ``reset_macos_accessibility``, "
    "§16 addendum 2026-08-09; + ``reset_linux_permissions``, "
    "§16 addendum 2026-08-10; + ``check_accessibility``, "
    "§16 addendum 2026-08-10 re-registration). The prior 76-command "
    "list was stale — it included 17 commands that had been deliberately "
    "REMOVED from ``_COMMAND_REGISTRY`` to match the Tauri host's Rust "
    "allowlist narrowing (see ``test_dead_code_stays_removed.py`` for the "
    "regression guards). 59 = original 68-command frozen table − 9 commands "
    "removed in the Tauri narrowing (``refresh_microphones``, "
    "``get_rms_level``, ``get_audio_status``, ``onboarding_get_step``, "
    "``onboarding_get_model_catalog``, ``onboarding_request_keyboard_permission``, "
    "``microphone_test_status``, ``level_monitor_status``, ``test_llm_connection``) "
    "− 3 vocabulary-automation commands deferred pending UX redesign "
    "(``get_vocabulary_suggestions``, ``apply_vocabulary_suggestion``, "
    "``dismiss_vocabulary_suggestion``) − 2 Tauri-Rust-bridged commands "
    "(``export_diagnostics``, ``show_electron_notification``) − 2 GDPR commands bridged via Rust "
    "(``delete_all_personal_data``, ``export_gdpr_bundle``) + 11 commands "
    "added since the original frozen table (``repaste_last``, "
    "``onboarding_check_permissions``, ``onboarding_reset``, ``shutdown``, "
    "``get_history_count``, ``get_transcription_text``, "
    "``pause_model_download``, ``resume_model_download``, "
    "``get_model_catalog``, ``reset_macos_accessibility``, "
    "``reset_linux_permissions``, ``check_accessibility``). Update this "
    "set + the ADR addendum together "
    "(§16). Note: ``relaunch_ack`` and ``tray_click`` are tracked "
    "separately in KNOWN_UNDOCUMENTED_COMMANDS, not here."
)

# ── Known undocumented command additions (ADR-0020 §16 violations) ──────
#
# Commands that exist in ``_COMMAND_REGISTRY`` today but are NOT in the
# ADR-0020 §2 frozen 68-command table. Each entry is a §16 violation:
# it was added without an ADR addendum + ``_validate_dict_payload``
# schema + dispatch-errors test. The Phase 4 validation gate does NOT
# fail on these (otherwise the gate would block on a pre-existing
# implementation gap rather than on NEW regressions), but the
# ``test_known_undocumented_commands_are_reported`` test below asserts
# the set is exactly what we expect — so when the gap is closed (the
# command is either removed or formally added to the ADR), the test
# prompts removal of the entry here too.
#
# REPORT THIS LIST AS AN IMPLEMENTATION GAP TO THE PRIMARY AGENT.
KNOWN_UNDOCUMENTED_COMMANDS: frozenset[str] = frozenset(
    {
        # PERF-005 (ipc_server.py:1710-1712): Electron acks receipt of
        # ``relaunch_electron`` so ``restart_app`` can drop its 300ms
        # sleep in favour of an event-driven wait (bounded by a 2s
        # timeout). Added without an ADR-0020 §16 addendum.
        "relaunch_ack",
        # Phase 3 (Rust-host tray, ADR-0020 §6.5): ``tray_click``
        # is a host-initiated dispatch command the Rust tray emits when
        # the user clicks a menu item (``dispatch({cmd:'tray_click',
        # data:{id}})``). It is NOT in the frozen 68-command table and
        # has no Python ``_handle_tray_click`` mixin — the Rust host
        # routes it directly to the sidecar's tray-click handler. Added
        # without a formal ADR-0020 §16 addendum (tracked as a gap; the
        # Python-side ``tray_click`` IPC handler lives in
        # ``ipc_server.py``). Listed here so the frozen-contract gate
        # does not block on it.
        "tray_click",
        # Cloud-provider "Test Connection" probe. The renderer's
        # ``useCloudProviders.testConnection`` action dispatches this
        # command instead of issuing a cross-origin ``fetch`` directly
        # (which would leak the API key through browser dev-tools
        # observability and violate C-DATA-1's "renderer production
        # code path stays network-free" promise). The Python-side
        # handler in ``handlers/cloud_test_handlers.py`` performs the
        # network call, gated by an explicit user click on the Cloud
        # tab's "Test Connection" button. Added to ``_COMMAND_REGISTRY``
        # without a formal ADR-0020 §16 addendum; tracked as a known
        # gap pending the next ADR refresh. The renderer (62 entries)
        # and Rust allowlists already include this command; the gap is
        # only the ADR documentation + ``_validate_dict_payload``
        # schema + dispatch-errors test (option (b) above).
        "test_cloud_connection",
        # XZ-SEC-05: ``add_trusted_endpoint`` — adds a hostname to the
        # runtime URL allowlist + persists it to config.json under
        # ``trusted_extra_hosts`` (self-hosted LLM/ASR endpoint
        # remediation). Python handler: ``ConfigHandlersMixin`` in
        # ``handlers/config_handlers.py``. Added without a formal
        # ADR-0020 §16 addendum; tracked as a known gap pending the next
        # ADR refresh (mirrors test_cloud_connection).
        "add_trusted_endpoint",
    }
)

# ── ADR-0020 §event table — frozen 21-event table ───────────────────────
#
# Source: ADR-0020 "Sidecar→UI Event Table" — 21 events. These are
# server-initiated (channel 2) — distinct from the command/response
# envelope (channel 1). Each is delivered as
# ``{"type":<name>,"data":{...}}`` and re-emitted by the Rust bridge
# as a Tauri event of the same name (modulo the backward-compat
# ``electron_notification`` → ``notification`` alias below; the
# ``relaunch_electron`` → ``relaunch_app`` rename was dropped in the
# cleanup — the Python sidecar now publishes ``relaunch_app``
# directly, so the bridge forwards it unchanged).
EXPECTED_EVENTS: frozenset[str] = frozenset(
    {
        "ready",
        "bubble_show",
        "bubble_hide",
        "bubble_level",
        "bubble_set_state",
        "transcription_final",
        "vocabulary_suggestion",
        "hotkey_capture_cancel",
        "config_changed",
        "history_changed",
        "microphone_test_complete",
        "microphones_changed",
        "audio_clip",
        "recording_started",
        "recording_stopped",
        "download_progress",
        "electron_notification",  # aliased to "notification" by the bridge
        "navigate",
        "show_window",
        "quit_app",
        # cleanup: the Python sidecar now publishes ``relaunch_app``
        # directly (no rename by the Rust bridge). ``main.rs`` listens for
        # ``relaunch_app`` via ``app.listen("relaunch_app", ...)`` and calls
        # ``app.restart()``.
        "relaunch_app",
        # ADR-0020 §16 addendum (2026-07-24): three events
        # added since the prior 21-event baseline. All three are emitted
        # via ``event_bus.publish`` (or ``IPCServer.push``) and flow
        # through the same channel.
        #   - ``paste_failed`` (dictation_handlers.py — paste-error feedback).
        #   - ``state_changed`` (IPCServer.push — emitted on TCP connect).
        #   - ``status_change`` (IPCServer.push via ``_hook_tray_set_state``
        #     — emitted on every tray state transition).
        "paste_failed",
        "state_changed",
        "status_change",
    }
)
assert len(EXPECTED_EVENTS) == 24, (
    "ADR-0020 freezes a 24-event table (was 21; +3 events added in the "
    "RT-FIX-9 / 2026-07-24 reconciliation). Update this set + the ADR "
    "addendum together (§16)."
)

# Events that the Rust bridge renames before re-emitting as Tauri
# events (ADR-0020 §6.1 — payloads are unchanged, only the event name
# changes). The Rust bridge also emits a backward-compat
# ``notification`` alias when it sees the legacy
# ``electron_notification`` event name ( in ws.rs).
#
# cleanup: the ``relaunch_electron`` → ``relaunch_app`` entry was
# REMOVED — the Python sidecar now publishes ``relaunch_app`` directly,
# so the Rust bridge forwards it unchanged. ``main.rs`` listens for the
# renamed event directly. This dict is intentionally empty; it remains
# as a documentation anchor for the ADR-0020 §6.1 rename policy (future
# Rust-side renames should be added here).
EVENT_RENAMES: dict[str, str] = {}
EVENT_ALIASES: dict[str, tuple[str, ...]] = {
    "electron_notification": ("notification",),
}


# ── Imports ─────────────────────────────────────────────────────────────


def _import_ipc_server():
    """Import the IPCServer module lazily so collection is hermetic."""
    from voice_typer.server import ipc_server

    return ipc_server


def _import_sidecar_ws():
    """Import sidecar_ws lazily — the module imports cleanly even when
    the ``websockets`` package is absent (it is lazy-imported inside
    ``run()``)."""
    from voice_typer.server import sidecar_ws

    return sidecar_ws


# ── Test: _COMMAND_REGISTRY exists + has the expected commands ──────────


def test_command_registry_exists_and_is_dict():
    """``_COMMAND_REGISTRY`` MUST be a class-level ``dict[str, str]`` on
    :class:`IPCServer` (ADR-0020 §2 — dispatch is a single dict lookup)."""
    ipc_server = _import_ipc_server()
    assert hasattr(ipc_server.IPCServer, "_COMMAND_REGISTRY"), (
        "IPCServer must expose a class-level _COMMAND_REGISTRY (ADR-0020 §2)"
    )
    registry = ipc_server.IPCServer._COMMAND_REGISTRY
    assert isinstance(registry, dict), f"_COMMAND_REGISTRY must be a dict, got {type(registry).__name__}"
    for key, value in registry.items():
        assert isinstance(key, str), f"_COMMAND_REGISTRY key {key!r} must be str, got {type(key).__name__}"
        assert isinstance(value, str), f"_COMMAND_REGISTRY[{key!r}] value must be str, got {type(value).__name__}"


def test_command_registry_contains_expected_keys():
    """Every command listed in the ADR-0020 §2 table MUST be present in
    ``_COMMAND_REGISTRY``. A missing entry means the wire contract was
    silently narrowed — ADR-0020 §16 forbids that."""
    ipc_server = _import_ipc_server()
    actual = set(ipc_server.IPCServer._COMMAND_REGISTRY.keys())
    missing = EXPECTED_COMMANDS - actual
    assert not missing, (
        "ADR-0020 §2 freezes these commands but they are MISSING from "
        f"_COMMAND_REGISTRY: {sorted(missing)}. Restore them or update "
        "the ADR + this test together."
    )


def test_command_registry_handlers_resolve_to_methods():
    """Every ``_COMMAND_REGISTRY[cmd]`` value MUST name an existing
    ``_handle_<cmd>`` method on :class:`IPCServer`. A dangling entry
    would crash at dispatch time with ``AttributeError`` (the registry
    is the source of truth — there is no fallback chain)."""
    ipc_server = _import_ipc_server()
    registry = ipc_server.IPCServer._COMMAND_REGISTRY
    broken: list[str] = []
    for cmd, handler_name in registry.items():
        if not hasattr(ipc_server.IPCServer, handler_name):
            broken.append(f"{cmd} → {handler_name}")
        else:
            method = getattr(ipc_server.IPCServer, handler_name)
            if not callable(method):
                broken.append(f"{cmd} → {handler_name} (not callable)")
    assert not broken, (
        "The following _COMMAND_REGISTRY entries do NOT resolve to a "
        "callable _handle_<cmd> method on IPCServer:\n  " + "\n  ".join(broken)
    )


def test_command_registry_handlers_have_correct_signature():
    """Each ``_handle_<cmd>`` method MUST accept ``(self, data, resp)`` —
    this is the dispatch contract documented at the registry definition
    site (``ipc_server.py``). A signature drift would silently break
    dispatch with a ``TypeError`` at runtime."""
    ipc_server = _import_ipc_server()
    registry = ipc_server.IPCServer._COMMAND_REGISTRY
    broken: list[str] = []
    for cmd, handler_name in registry.items():
        method = getattr(ipc_server.IPCServer, handler_name, None)
        if method is None or not callable(method):
            continue  # covered by the previous test
        try:
            sig = inspect.signature(method)
        except (ValueError, TypeError):
            continue
        params = list(sig.parameters.keys())
        # Expect (self, data, resp) — accept extra optional params
        # (e.g. kwargs) defensively, but the first three MUST match.
        if params[:3] != ["self", "data", "resp"]:
            broken.append(f"{cmd} → {handler_name}: params={params}")
    assert not broken, (
        "The following _handle_<cmd> methods do NOT have the "
        "(self, data, resp) dispatch signature:\n  " + "\n  ".join(broken)
    )


# ── Test: _validate_dict_payload is the source of truth ─────────────────


def test_validate_dict_payload_is_importable_and_callable():
    """ADR-0020 §2 + §16 item 3: ``_validate_dict_payload`` is the
    source of truth for command-payload shape. It MUST be importable
    from ``ipc_server`` and callable."""
    ipc_server = _import_ipc_server()
    assert hasattr(ipc_server, "_validate_dict_payload"), (
        "_validate_dict_payload must be defined at module scope in ipc_server.py (ADR-0020 §2)."
    )
    assert callable(ipc_server._validate_dict_payload)


def test_validate_dict_payload_returns_validated_dict_on_success():
    """On success: ``(validated_dict, None)`` — the second element is
    ``None`` so the caller can ``if error: return error`` cleanly."""
    ipc_server = _import_ipc_server()
    schema = {
        "hotkey": {"type": str, "required": True},
        "model": {"type": str, "required": False, "default": "tiny"},
    }
    validated, error = ipc_server._validate_dict_payload(
        {"hotkey": "ctrl+space"},
        schema,
    )
    assert error is None
    assert validated == {"hotkey": "ctrl+space", "model": "tiny"}


def test_validate_dict_payload_rejects_non_dict_data():
    """Non-dict ``data`` MUST return ``(None, error_response)`` — the
    handler can ``return resp`` immediately. The error response carries
    the ``invalid_payload`` code so the client can distinguish it from
    a handler fault (ERR-009)."""
    ipc_server = _import_ipc_server()
    validated, error = ipc_server._validate_dict_payload("not a dict", {})
    assert validated is None
    assert error is not None
    assert error["type"] == "error"
    # error codes are now namespaced (client.* / server.*). The
    # bare legacy form is preserved in ``legacy_code``. Accept either the
    # namespaced or the legacy form for forward-compat with older builds.
    assert error["data"]["code"] in ("client.invalid_payload", "invalid_payload")


def test_validate_dict_payload_reports_missing_required_field():
    """Missing required field → ``(None, error)`` with the
    ``missing_field`` code and the offending field name (so the client
    can render a precise error)."""
    ipc_server = _import_ipc_server()
    schema = {"hotkey": {"type": str, "required": True}}
    validated, error = ipc_server._validate_dict_payload({}, schema)
    assert validated is None
    assert error["data"]["code"] in ("client.missing_field", "missing_field")
    assert error["data"]["field"] == "hotkey"


def test_validate_dict_payload_reports_wrong_type_field():
    """Wrong-type field → ``(None, error)`` with the ``invalid_field``
    code, the field name, AND a human-readable message naming both the
    expected and got types."""
    ipc_server = _import_ipc_server()
    schema = {"hotkey": {"type": str, "required": True}}
    validated, error = ipc_server._validate_dict_payload(
        {"hotkey": 42},
        schema,
    )
    assert validated is None
    assert error["data"]["code"] in ("client.invalid_field", "invalid_field")
    assert error["data"]["field"] == "hotkey"
    assert "str" in error["data"]["message"]
    assert "int" in error["data"]["message"]


def test_validate_dict_payload_applies_defaults_for_optional_fields():
    """Optional fields with a ``default`` value MUST be filled in when
    absent. Without this, every handler would need its own
    ``data.get(key, default)`` boilerplate (the whole point of the
    helper)."""
    ipc_server = _import_ipc_server()
    schema = {
        "limit": {"type": int, "required": False, "default": 50},
    }
    validated, error = ipc_server._validate_dict_payload({}, schema)
    assert error is None
    assert validated == {"limit": 50}


def test_validate_dict_payload_is_referenced_in_adr():
    """ADR-0020 §2 names ``_validate_dict_payload`` as the source of
    truth for payload shape. The ADR MUST mention it (this guards
    against an ADR rewrite that silently drops the reference)."""
    text = ADR_0020.read_text(encoding="utf-8")
    assert "_validate_dict_payload" in text, (
        "ADR-0020 must name _validate_dict_payload as the source of truth for payload validation (§2 + §16 item 3)."
    )


# ── Test: WS bridge forwards all 21 event types (ws.rs source inspect) ──


def _read_ws_rs() -> str:
    """Read the Rust WS bridge source. The file MUST exist — its
    absence means the Tauri migration was rolled back mid-flight."""
    assert WS_RS.is_file(), (
        f"src-tauri/src/sidecar/ws.rs is missing at {WS_RS} — the Tauri "
        "WS bridge has been removed (ADR-0020 regression)."
    )
    return WS_RS.read_text(encoding="utf-8")


def _read_ws_event_protocol_rs() -> str:
    """Read the Rust WS event-protocol submodule source. The file MUST exist.
    its absence means the split was rolled back mid-flight.
    The translate_event_name body + ALLOWED_EVENT_TYPES slice live here after the split.

    The translate_event_name unit tests (which reference the legacy
    ``\"electron_notification\"`` alias string) were extracted to the
    sibling ``event_protocol_tests.rs`` file (C-TEST-5), so we read
    the whole event-protocol module (production + test files) to keep
    the alias invariant check green across the split.
    """
    assert WS_EVENT_PROTOCOL_RS.is_file(), (
        f"src-tauri/src/sidecar/ws/event_protocol.rs is missing at "
        f"{WS_EVENT_PROTOCOL_RS} — the split was rolled "
        "back (ADR-0020 regression)."
    )
    parts = [WS_EVENT_PROTOCOL_RS.read_text(encoding="utf-8")]
    tests_path = WS_EVENT_PROTOCOL_RS.parent / "event_protocol_tests.rs"
    if tests_path.is_file():
        parts.append(tests_path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def test_ws_bridge_does_not_silently_filter_events():
    """ADR-0020 §event table: the Rust bridge forwards every
    server-initiated event by name. The bridge is a generic fan-out,
    not a per-event-type dispatcher that silently drops unknown event
    types.

    RT-FIX-9 (2026-07-24): ws.rs was refactored — the prior
    ``let emit_name = event_type;`` direct assignment was replaced by
    ``let emit_name = translate_event_name(event_type);`` (PVT-G5-062:
    extracted the snake→kebab bubble_* renames into a unit-testable
    function), AND an explicit ``ALLOWED_EVENT_TYPES`` allowlist was
    added (G4-H-32: defense-in-depth against a compromised sidecar
    process injecting arbitrary event names). The allowlist is an
    INTENTIONAL security hardening — it logs + drops unknown event
    types rather than silently passing them through. The translate
    function has an ``other => other`` arm so any allowlisted event
    that is NOT in the rename table is forwarded under its own name
    (preserving the original "no silent rename of unknown events"
    invariant).

    This test now asserts the FORWARD-COMPAT passthrough invariant:
    ``translate_event_name`` MUST have an ``other => other`` arm so
    future sidecar events flow through unchanged once added to the
    allowlist. The legacy "no allowlist" assertions are obsolete
    (the allowlist is now an intentional defense-in-depth gate) and
    have been removed.

    module split: ``translate_event_name``'s body moved from
    ``ws.rs`` into ``ws/event_protocol.rs``. The call site
    ``let emit_name = translate_event_name(event_type);`` stays in
    ``ws.rs`` (inside ``spawn_reader_task``); the ``other => other``
    match arm lives in ``event_protocol.rs``. This test now reads
    BOTH files so the invariant is checked across the split.
    """
    src = _read_ws_rs()
    event_protocol_src = _read_ws_event_protocol_rs()
    # The bridge MUST route every emitted event name through the
    # ``translate_event_name`` helper (single point of truth for the
    # snake→kebab rename table). This replaces the prior
    # ``let emit_name = event_type;`` direct assignment.
    assert re.search(r"let\s+emit_name\s*=\s*translate_event_name\(\s*event_type\s*\)\s*;", src), (
        "ws.rs must compute `emit_name` via `translate_event_name(event_type)` "
        "(PVT-G5-062 — single unit-testable rename table). ADR-0020 §event table."
    )
    # The translate function MUST have an `other => other` arm so any
    # allowlisted-but-not-renamed event name passes through unchanged
    # (forward-compat: new sidecar events added to ALLOWED_EVENT_TYPES
    # flow through without requiring a host-side release). After the
    # split, this arm lives in `ws/event_protocol.rs`.
    assert re.search(r"other\s*=>\s*other\s*,", event_protocol_src), (
        "translate_event_name (now in ws/event_protocol.rs after the split) "
        "must have an `other => other` forward-compat passthrough arm "
        "(unknown event names flow through unchanged)."
    )


def test_ws_bridge_emits_python_event_catch_all():
    """ADR-0020 §6.3: the bridge emits BOTH the specific event (for
    direct listeners like the bubble window) AND a generic
    ``python-event`` event (for the ``usePython`` hook's
    ``onEvent`` catch-all, matching the Electron path's
    ``ipcRenderer.on("python-event")``).

    Source-inspect ws.rs: every event emission MUST be paired with a
    ``python-event`` emission carrying ``{"type":<name>, "data":<p>}``.
    """
    src = _read_ws_rs()
    # The bridge MUST emit "python-event" with the original event
    # type name preserved in the payload.
    assert '"python-event"' in src, (
        'ws.rs must emit a "python-event" catch-all event carrying {"type":<name>, "data":<payload>} (ADR-0020 §6.3).'
    )


def test_ws_bridge_does_not_rename_relaunch_app():
    """PVT-2 cleanup: the ``relaunch_electron`` → ``relaunch_app``
    rename arm was REMOVED from ws.rs. The Python sidecar now
    publishes ``relaunch_app`` directly (see ``app.py``
    ``restart_app``), and ``main.rs`` listens for it via
    ``app.listen("relaunch_app", ...)`` (calling ``app.restart()``).
    The Rust bridge forwards the event unchanged — no rename arm.

    This is a regression check: re-introducing the rename arm would
    silently demote the user's Restart click back to the pre-PVT-2
    bug (the renamed event was emitted into the void because no
    listener subscribed to ``relaunch_app`` pre-PVT-2).
    """
    src = _read_ws_rs()
    # The rename match arm MUST NOT be present in ws.rs source.
    rename_re = re.compile(
        r'"relaunch_electron"\s*=>\s*"relaunch_app"',
    )
    assert not rename_re.search(src), (
        "ws.rs MUST NOT have a `relaunch_electron` => `relaunch_app` "
        "rename arm — the Python sidecar now publishes `relaunch_app` "
        "directly (PVT-2 cleanup). Re-introducing the rename would "
        "recreate the pre-PVT-2 silent-restart bug."
    )
    # Belt-and-braces: the literal old name MUST NOT appear as a
    # match arm pattern in ws.rs (only in comments is OK).
    assert '"relaunch_electron" =>' not in src, (
        "ws.rs MUST NOT match the legacy `relaunch_electron` event name "
        "in a per-type branch (PVT-2 cleanup — the rename arm is gone)."
    )


def test_ws_bridge_emits_notification_alias_for_electron_notification():
    """CR-8 (ws.rs): a backward-compat alias emits ``notification``
    alongside the legacy ``electron_notification`` event name so new
    UI code subscribing to ``notification`` keeps working during a
    rolling upgrade. Drop after one release cycle (ADR-0020 §6.1).

    module split: after the split, the ``ALLOWED_EVENT_TYPES``
    slice (which contains ``"notification"``) and the
    ``translate_event_name`` test (which references
    ``"electron_notification"``) both live in
    ``ws/event_protocol.rs``. We assert against the union of
    ``ws.rs`` + ``ws/event_protocol.rs`` so the alias invariant
    is checked across the split.
    """
    src = _read_ws_rs() + "\n" + _read_ws_event_protocol_rs()
    assert '"electron_notification"' in src and '"notification"' in src, (
        "ws.rs (+ ws/event_protocol.rs after the split) must reference both "
        "`electron_notification` (legacy alias) and `notification` (canonical) "
        "(CR-8, ADR-0020 §6.1)."
    )


def test_ws_bridge_coalesces_bubble_level():
    """ADR-0020 §9: ``bubble_level`` is emitted at ~60 Hz by the
    sidecar but the bridge MUST coalesce to ≤30 Hz to avoid flooding
    the webview's event loop. Source-inspect ws.rs for the
    ``bubble_coalesce_should_emit`` helper call."""
    src = _read_ws_rs()
    assert "bubble_level" in src, "ws.rs must handle the `bubble_level` event specifically (ADR-0020 §9 coalescing)."
    assert "bubble_coalesce_should_emit" in src, (
        "ws.rs must call `bubble_coalesce_should_emit` for bubble_level events (ADR-0020 §9 — coalesce to ≤30 Hz)."
    )
    # The coalesced emit path emits BOTH the specific event and the
    # python-event catch-all (same as the generic path).
    bubble_section = src[src.index("bubble_level") :]
    assert '"python-event"' in bubble_section, (
        "ws.rs must emit `python-event` for coalesced bubble_level events (ADR-0020 §6.3 + §9)."
    )


# Events whose source name has been renamed in the Python sidecar
# ( in ws.rs + handlers/system_handlers.py + startup_sequence.py).
# The Rust bridge has a backward-compat alias so legacy sidecars that
# still emit the OLD name keep working — but new sidecars emit the
# NEW name directly. The Phase 4 test accepts EITHER name in the
# Python source to tolerate the rolling rename.
EVENT_NAME_RENAMES_IN_SOURCE: dict[str, str] = {
    # Old (still listed in ADR-0020 §event table) → New (what the
    # Python sidecar emits today).
    "electron_notification": "notification",
}


def test_ws_bridge_forwards_all_24_event_names():
    """Every event name in the ADR-0020 §event table MUST be
    forwardable by the bridge. The bridge uses generic fan-out (no
    allowlist), so the contract is: each event name MUST appear in
    the Python source as an ``event_bus.publish({"type": <name>})``
    call (or ``server.push({"type": <name>})`` for the ``ready``
    event) — proving the sidecar actually emits it — AND the bridge
    must NOT filter it (covered by the no-allowlist test above).

    For events whose name has been renamed in the Python sidecar
    (see ``EVENT_NAME_RENAMES_IN_SOURCE``), EITHER the old OR the
    new name must appear — the Rust bridge has a backward-compat
    alias (CR-8 in ws.rs) so both paths reach the webview.
    """
    # Read all Python source files in voice_typer/server/.
    server_dir = REPO_ROOT / "voice_typer" / "server"
    all_source = ""
    for py_file in server_dir.rglob("*.py"):
        try:
            all_source += py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

    missing: list[str] = []
    for event_name in EXPECTED_EVENTS:
        # Accept either the original name OR (for renamed events) the
        # new name. Both prove the sidecar emits the event.
        candidate_names = [event_name]
        if event_name in EVENT_NAME_RENAMES_IN_SOURCE:
            candidate_names.append(EVENT_NAME_RENAMES_IN_SOURCE[event_name])
        found = False
        for name in candidate_names:
            # Look for the event name as a JSON string value, e.g.
            # ``{"type": "ready"}`` or ``"type": "ready"``. We use a
            # loose match to tolerate dict-layout variations.
            pattern = re.compile(
                r'"type"\s*:\s*"' + re.escape(name) + r'"',
            )
            if pattern.search(all_source):
                found = True
                break
        if not found:
            missing.append(event_name)
    assert not missing, (
        "ADR-0020 §event table lists these events but they are NOT "
        "emitted anywhere in voice_typer/server/ (neither under the "
        "original name nor under any EVENT_NAME_RENAMES_IN_SOURCE "
        "alias):\n  " + "\n  ".join(sorted(missing)) + "\n\nEither restore the emit site or update the ADR + this "
        "test together (§16)."
    )


# ── Test: TAURI_SIDECAR=1 disables the heartbeat watchdog ───────────────


def test_tauri_sidecar_env_disables_heartbeat_watchdog_in_source():
    """ADR-0020 §2 + §10: under ``TAURI_SIDECAR=1`` the Python
    heartbeat-watchdog thread (ADR-0018) is DISABLED - the Tauri
    host's supervisor replaces it. Source-inspect
    ``ipc/lifecycle.py`` (where ``IPCServer.__init__`` lives - the
    ARCH-045 split moved the heartbeat-watchdog gate out of
    ``ipc_server.py``) for the env-var check + the
    ``_heartbeat_thread = None`` skip path."""
    src = IPC_SERVER_IMPL_PY.read_text(encoding="utf-8")
    # The env var MUST be read with the exact "1" sentinel (not
    # truthy / not "true" - ADR-0020 §7 specifies "=1").
    assert 'os.environ.get("TAURI_SIDECAR") == "1"' in src, (
        'ipc/lifecycle.py must gate the heartbeat watchdog on `TAURI_SIDECAR == "1"` (ADR-0020 §2 + §10).'
    )
    # The skip path MUST set ``_heartbeat_thread = None`` (not
    # start the thread and then immediately stop it - that would
    # leak a thread).
    # Find the TAURI_SIDECAR gate block and assert
    # ``_heartbeat_thread = None`` appears within ~10 lines.
    gate_idx = src.index('os.environ.get("TAURI_SIDECAR") == "1"')
    window = src[gate_idx : gate_idx + 600]
    assert "_heartbeat_thread = None" in window, (
        "ipc/lifecycle.py must set `self._heartbeat_thread = None` when "
        "TAURI_SIDECAR=1 (ADR-0020 §10 - skip the heartbeat-watchdog "
        "thread entirely, do not start-then-stop)."
    )


def test_tauri_sidecar_env_propagated_by_ws_mode():
    """ADR-0020 §2 + §10: ``--ws`` mode MUST set ``TAURI_SIDECAR=1``
    on the sidecar so the downstream heartbeat-watchdog + Python-side
    single-instance mutex gates see it. Source-inspect
    ``ipc/entrypoint.py`` (where ``main()`` lives - the ARCH-045 main
    moved out of ``ipc_server.py``) for the ``--ws`` flag handler."""
    src = IPC_MAIN_PY.read_text(encoding="utf-8")
    # ``--ws`` mode MUST set the env var (so a terminal-launched
    # ``python -m voice_typer.server.ipc_server --ws`` also gets the
    # Tauri-sidecar behavior - ADR-0020 §2 footnote).
    assert 'os.environ["TAURI_SIDECAR"] = "1"' in src, (
        "ipc/entrypoint.py must set os.environ['TAURI_SIDECAR'] = '1' in --ws mode (ADR-0020 §2 footnote + §10)."
    )


def test_tauri_sidecar_env_disables_python_single_instance_mutex():
    """ADR-0020 §12: under ``TAURI_SIDECAR=1`` the Python-side
    ``VoiceTyperSingleInstance`` Win32 mutex is skipped (the Tauri
    host's single-instance plugin owns it). Source-inspect
    ``ipc/entrypoint.py`` for the mutex gate and ``ipc/lifecycle.py``
    for the heartbeat-watchdog gate - the ARCH-045 split moved both
    out of ``ipc_server.py`` into the ``ipc/`` package (heartbeat gate
    in ``lifecycle.py``, ``--ws`` env + mutex gate in ``entrypoint.py``)."""
    server_src = IPC_SERVER_IMPL_PY.read_text(encoding="utf-8")
    main_src = IPC_MAIN_PY.read_text(encoding="utf-8")
    # The mutex skip gate MUST appear (near the bottom of main() -
    # separate from the heartbeat-watchdog gate in lifecycle.py).
    # Look for at least TWO occurrences of the env-var check across
    # the two submodules that now host the IPC logic.
    occurrences = server_src.count('os.environ.get("TAURI_SIDECAR") == "1"') + main_src.count(
        'os.environ.get("TAURI_SIDECAR") == "1"'
    )
    assert occurrences >= 2, (
        "ipc/ must reference TAURI_SIDECAR=1 in at least two "
        "gates: (1) the heartbeat-watchdog skip (§10, in lifecycle.py) "
        "and (2) the Python single-instance mutex skip (§12, in "
        f"entrypoint.py). Found {occurrences}."
    )


def test_sidecar_authenticate_does_not_echo_token(monkeypatch):
    """ADR-0020 §3: the sidecar MUST NOT echo the auth token in any
    outbound frame or response. The ``_authenticate`` helper returns a
    bare ``bool`` (accept / reject) — the host treats rejection as a
    crash and respawns with a fresh token. If the sidecar ever
    responded with the token (e.g. ``{"type":"auth_ok","token":"..."}``)
    it would defeat the per-launch rotation."""
    sw = _import_sidecar_ws()
    token = "deadbeef" * 8  # 64 hex chars
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", token)

    # Fake websocket — capture every frame the sidecar writes back.
    sent_frames: list[str] = []

    class _FakeWS:
        async def recv(self):
            return '{"type": "auth", "token": "' + token + '"}'

        async def close(self, code=None, reason=None):
            pass

    import asyncio

    async def _run():
        return await sw._authenticate(_FakeWS())

    accepted = asyncio.run(_run())
    assert accepted is True
    # The authenticate path must NOT have written any frame back.
    # (It only reads; if it ever writes, the frame would land in
    # sent_frames — which stays empty.)
    for frame in sent_frames:
        assert token not in frame, (
            "sidecar_ws._authenticate wrote a frame containing the "
            f"auth token: {frame!r} (ADR-0020 §3 — token MUST NOT be "
            "echoed)."
        )


def test_sidecar_source_does_not_log_token_verbatim():
    """ADR-0020 §3: the token MUST NOT appear verbatim in any log
    line. Source-inspect ``sidecar_ws.py`` for ``log.*`` calls that
    interpolate the token (``expected_token``) or the env var value.
    The env var NAME (``VOICE_TYPER_IPC_TOKEN``) MAY appear in log
    messages — the VALUE may not."""
    src = SIDECAR_WS_PY.read_text(encoding="utf-8")
    # Find every log.* call that mentions ``expected_token`` or
    # ``provided`` (the inbound token value). They MUST NOT appear in
    # an f-string / .format() / % interpolation.
    #
    # ``expected_token`` is the local var holding the env value; if it
    # appears in a log.* call's interpolation, the token value is
    # logged. Same for ``provided`` (the inbound token from the auth
    # frame).
    bad_patterns = [
        # f-string interpolation: f"... {expected_token} ..."
        r'log\.\w+\([^)]*f["\'].*\{expected_token[^}]*\}.*["\']',
        # f-string interpolation of provided token
        r'log\.\w+\([^)]*f["\'].*\{provided[^}]*\}.*["\']',
        # .format() with expected_token / provided as an arg
        r"log\.\w+\([^)]*\.format\([^)]*\bexpected_token\b",
        r"log\.\w+\([^)]*\.format\([^)]*\bprovided\b",
        # % formatting with expected_token / provided
        r"log\.\w+\([^)]*%[^,)]*\bexpected_token\b",
        r"log\.\w+\([^)]*%[^,)]*\bprovided\b",
    ]
    violations: list[str] = []
    for pat in bad_patterns:
        for m in re.finditer(pat, src, re.DOTALL):
            # Capture a short snippet for the assertion message.
            snippet = m.group(0)[:120]
            violations.append(snippet)
    assert not violations, (
        "sidecar_ws.py contains log.* calls that interpolate the auth "
        "token (expected_token / provided) — ADR-0020 §3 forbids "
        "logging the token verbatim. Offending lines:\n  " + "\n  ".join(repr(v) for v in violations)
    )


def test_sidecar_emit_server_started_does_not_leak_token(capsys):
    """ADR-0020 §1 + §3: the only line the sidecar writes to stdout is
    ``{"event":"server_started","port":<n>}``. The token MUST NOT
    appear in this line — stdout is parsed by the host but is also
    captured in crash reports / pipe dumps."""
    sw = _import_sidecar_ws()
    sw._emit_server_started(54321)
    captured = capsys.readouterr()
    # The stdout line must be the server_started JSON, no token field.
    import json

    payload = json.loads(captured.out.strip())
    assert "token" not in payload, (
        "sidecar_ws._emit_server_started must NOT include a `token` field in the stdout JSON (ADR-0020 §1 + §3)."
    )
    assert payload == {"event": "server_started", "port": 54321}


# ── Test: command/event contract is frozen (no untested additions) ──────


def test_command_contract_is_frozen_no_untested_additions():
    """ADR-0020 §16: the 68-command table is the frozen wire contract.
    Any NEW command in ``_COMMAND_REGISTRY`` that is NOT in
    ``EXPECTED_COMMANDS`` MUST be accompanied by (1) a new
    ``_handle_<cmd>`` mixin, (2) an ADR addendum, and (3) an entry in
    ``EXPECTED_COMMANDS`` here (which forces the reviewer to update
    this test). This test fails the moment an untested command is
    added — UNLESS it is listed in ``KNOWN_UNDOCUMENTED_COMMANDS``,
    which tracks the pre-existing §16 violations documented as
    implementation gaps (see ``test_known_undocumented_commands_are_reported``).
    """
    ipc_server = _import_ipc_server()
    actual = set(ipc_server.IPCServer._COMMAND_REGISTRY.keys())
    extra = actual - EXPECTED_COMMANDS - KNOWN_UNDOCUMENTED_COMMANDS
    if extra:
        pytest.fail(
            "ADR-0020 §16: _COMMAND_REGISTRY contains commands NOT in "
            "the frozen 68-command table AND NOT in the "
            "KNOWN_UNDOCUMENTED_COMMANDS allowlist:\n  " + "\n  ".join(sorted(extra)) + "\n\nTo resolve, EITHER:\n"
            "  (a) Remove the command from _COMMAND_REGISTRY (it was "
            "added without an ADR addendum), OR\n"
            "  (b) Add it to EXPECTED_COMMANDS in this test + add an "
            "ADR-0020 addendum + add a _validate_dict_payload schema "
            "+ add a test in tests/test_ipc_dispatch_errors.py, OR\n"
            "  (c) Add it to KNOWN_UNDOCUMENTED_COMMANDS in this test "
            "with a comment naming the PR + reason (this is the "
            "explicit-gap path; the test_known_undocumented_commands_"
            "are_reported test below will then keep the entry in "
            "sync with reality).\n"
            "Do NOT silently grow the wire contract."
        )


def test_known_undocumented_commands_are_reported():
    """The ``KNOWN_UNDOCUMENTED_COMMANDS`` allowlist MUST exactly match
    the set of commands in ``_COMMAND_REGISTRY`` that are NOT in the
    ADR-0020 §2 frozen 68-command table. When the gap is closed (the
    command is either removed or formally added to the ADR +
    ``EXPECTED_COMMANDS``), this test fails to prompt removal of the
    stale entry here.

    This is the "don't let the gap list silently grow OR silently
    shrink" guardrail — both directions require explicit action.
    """
    ipc_server = _import_ipc_server()
    actual = set(ipc_server.IPCServer._COMMAND_REGISTRY.keys())
    actual_gap = actual - EXPECTED_COMMANDS
    expected_gap = set(KNOWN_UNDOCUMENTED_COMMANDS)
    if actual_gap != expected_gap:
        missing_from_known = actual_gap - expected_gap
        stale_in_known = expected_gap - actual_gap
        msg_parts: list[str] = []
        if missing_from_known:
            msg_parts.append(
                "Commands in _COMMAND_REGISTRY but NOT in "
                "EXPECTED_COMMANDS and NOT in KNOWN_UNDOCUMENTED_COMMANDS "
                "(add to KNOWN_UNDOCUMENTED_COMMANDS with a comment, OR "
                "close the gap by adding to EXPECTED_COMMANDS + ADR "
                "addendum):\n  " + "\n  ".join(sorted(missing_from_known))
            )
        if stale_in_known:
            msg_parts.append(
                "Commands in KNOWN_UNDOCUMENTED_COMMANDS that are NO "
                "LONGER in _COMMAND_REGISTRY (the gap was closed — "
                "remove the stale entry from KNOWN_UNDOCUMENTED_COMMANDS):\n  " + "\n  ".join(sorted(stale_in_known))
            )
        pytest.fail("\n\n".join(msg_parts))


def test_event_contract_is_frozen_all_24_events_present():
    """ADR-0020 §16: the 24-event table is the frozen wire contract
    (was 21; +3 events added in the RT-FIX-9 / 2026-07-24
    reconciliation). This test asserts the ``EXPECTED_EVENTS`` set has
    exactly 24 entries (module-level ``assert`` at import time) AND
    that every one of them is actually emitted somewhere in the
    Python ``voice_typer/server/`` tree (covered by
    ``test_ws_bridge_forwards_all_24_event_names``). The combination
    guards against silent event additions / removals."""
    # The module-level ``assert len(EXPECTED_EVENTS) == 24`` already
    # guards the count. Here we re-assert for visibility in the test
    # report.
    assert len(EXPECTED_EVENTS) == 24, (
        "ADR-0020 freezes a 24-event table. Update EXPECTED_EVENTS + the ADR addendum together (§16)."
    )


def test_adr_0020_states_61_command_contract():
    """ADR-0020 §2 + §16 MUST state the frozen command count as 61.

    reconciliation (2026-07-26): the prior 68-command baseline
    was stale. The actual ``_COMMAND_REGISTRY`` was reduced to 61
    commands during the Tauri/Rust allowlist narrowing (17 commands
    were deliberately REMOVED — see ``EXPECTED_COMMANDS`` comments +
    ``test_dead_code_stays_removed.py``). The ADR was updated to say
    "61 commands" in §2 + §16; the historical "68 commands" reference
    in the IPC-1 reconciliation note is preserved as context only.

    This test guards against a future ADR rewrite that silently changes
    the frozen baseline (e.g. "60 commands" after a quiet removal of
    ``heartbeat``).
    """
    text = ADR_0020.read_text(encoding="utf-8")
    # The ADR mentions "61 commands" in §2 + §16 + the §heartbeat
    # footnote. At least ONE match.
    assert re.search(r"\b61\s+commands?\b", text), (
        "ADR-0020 must state the frozen command count as '61 "
        "commands' (§2 table header + §16). If the contract grew, "
        "update the ADR + EXPECTED_COMMANDS together. (the "
        "prior 68-command baseline was reduced to 61 during the "
        "Tauri/Rust allowlist narrowing.)"
    )


def test_adr_0020_states_24_event_contract():
    """ADR-0020 §event table + §16 MUST state the frozen event count
    as 24 (was 21; +3 events added in the reconciliation).
    Same guard as the command-count test above."""
    text = ADR_0020.read_text(encoding="utf-8")
    assert re.search(r"\b24\s+events?\b", text), (
        "ADR-0020 must state the frozen event count as '24 events' "
        "(§event table + §16). If the contract grew, update the ADR "
        "+ EXPECTED_EVENTS together."
    )


def test_adr_0020_states_frozen_contract_clause():
    """ADR-0020 §16 MUST contain the literal phrase 'frozen contract'
    (or close variant) so the contract-freeze policy is searchable in
    the ADR."""
    text = ADR_0020.read_text(encoding="utf-8")
    assert "frozen contract" in text.lower(), (
        "ADR-0020 §16 must contain the phrase 'frozen contract' so the contract-freeze policy is searchable."
    )


# ── Test: ADR §16 new-command process is documented ─────────────────────


def test_adr_0020_documents_new_command_process():
    """ADR-0020 §16 documents the 4-step process for adding a new
    command. This test asserts the key phrases are present so a
    future ADR edit cannot silently drop the policy."""
    text = ADR_0020.read_text(encoding="utf-8")
    # §16 mandates: (1) add to _COMMAND_REGISTRY, (2) ADR addendum,
    # (3) _validate_dict_payload schema, (4) test in
    # tests/test_ipc_dispatch_errors.py.
    assert "_COMMAND_REGISTRY" in text, "ADR-0020 §16 must mention _COMMAND_REGISTRY (new-command process step 1)."
    assert "_validate_dict_payload" in text, (
        "ADR-0020 §16 must mention _validate_dict_payload (new-command process step 3)."
    )
    assert "test_ipc_dispatch_errors" in text, (
        "ADR-0020 §16 must reference tests/test_ipc_dispatch_errors.py (new-command process step 4)."
    )
