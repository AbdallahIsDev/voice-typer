"""IPC command registry — the canonical dispatch table.

This module is the single source of truth for three interrelated
constants that the IPC dispatcher consults at runtime:

- data:`_COMMAND_REGISTRY` — ``{command_name: handler_method_name}``
  mapping. :class:`voice_typer.server.ipc_server.IPCServer._dispatch`
  looks up the handler-method-name string here and resolves it via
  ``getattr(self, handler_name)`` at dispatch time. The
  ``__init__``-time typo-validation loop iterates over this dict to
  assert every entry resolves to a callable bound method on
  class:`IPCServer` ( / ).
- data:`_READONLY_COMMANDS` — frozenset of command names whose
  handlers do NOT mutate shared app/service state. The dispatcher
  bypasses the per-server ``_dispatch_lock`` for these so a
  long-running state-mutating handler (e.g. ``download_model``) does
  not block a quick status poll from a second authenticated connection
().
- data:`_PYTHON_ONLY_COMMANDS` — frozenset of commands that are
  intentionally absent from the TS / Rust allowlists (). These
  commands are registered in :data:`_COMMAND_REGISTRY` (so the
  dispatcher recognizes them) but are NEVER invoked by the renderer —
  they are server-internal or host-internal (e.g. ``shutdown`` is
  invoked by the Tauri host's WS transport; ``tray_click`` is invoked
  by the Rust host's tray-icon click handler).

Why this module exists
----------------------

Pre- these three constants lived in three different places:
``_COMMAND_REGISTRY`` and ``_PYTHON_ONLY_COMMANDS`` were class
attributes on :class:`IPCServer` (in the 2,100-line
``ipc_server.py`` god-module), and ``_READONLY_COMMANDS`` lived in
``voice_typer/server/ipc/_helpers.py``. The split made the
"three layers must agree" parity contract (pinned by
``tests/test_ec4_python_command_registry_parity.py`` and
``tests/test_ipc_command_registry_sync.py``) harder to reason about:
the reader had to know where each constant lived.

(this module) is a behavior-preserving extraction — same dict,
same keys, same values. :class:`IPCServer` re-exports them as class
attributes for backward compatibility (so every existing
``IPCServer._COMMAND_REGISTRY`` / ``IPCServer._PYTHON_ONLY_COMMANDS``
call site — pinned by ``tests/test_ipc_shutdown_registry.py``,
``tests/test_ec4_python_command_registry_parity.py``,
``tests/test_ipc_command_registry_sync.py``,
``tests/tauri/mig19/test_phase4_validation.py``,
``tests/tauri/test_tauri_sidecar_gate.py``,
``tests/test_dead_code_stays_removed.py`` — keeps working unchanged).

``ipc_server.py`` and ``ipc._helpers`` both re-export the
module-level ``_READONLY_COMMANDS`` name (sourcing it from this
module) so existing ``from voice_typer.server.ipc_server import
_READONLY_COMMANDS`` and ``from voice_typer.server.ipc._helpers
import _READONLY_COMMANDS`` callers keep working unchanged. Both
re-exports point at the SAME frozenset object defined below — single
source of truth. The legacy parallel ``_READONLY_COMMANDS`` definition
that used to live in ``ipc/_helpers.py`` was deleted;
``ipc._helpers`` now imports the name from this module so the two
cannot silently drift.

Registry history
----------------

The following command-name strings were at various points in the
codebase history members of :data:`_COMMAND_REGISTRY` and were
subsequently REMOVED. Each removal was coordinated across the Python
registry, the TS ``ALLOWED_COMMANDS`` set, and the Rust
``allowed_commands()`` array. The regression guard in
``tests/test_dead_code_stays_removed.py`` pins the removals so they
cannot silently re-appear. Brief context for each removal:

- ``refresh_microphones``, ``get_rms_level``, ``get_audio_status`` —
  removed to match the Tauri/Rust allowlist narrowing. The
  service-layer methods still exist (called from internal code
  paths); only the IPC dispatch route was deleted.
- ``onboarding_get_step`` — the renderer no longer invokes it (the
  wizard state is held client-side).
- ``onboarding_get_model_catalog`` — the renderer uses
  ``get_model_catalog`` (the non-onboarding command) for model
  catalog data; this onboarding-scoped alias was never wired up on
  the client.
- ``onboarding_request_keyboard_permission`` — the renderer's
  permission flow now uses ``onboarding_check_permissions`` + a
  Tauri-side invocation.
- ``microphone_test_status`` — the renderer polls
  ``microphone_test_get_level`` at 60 Hz during a test; the separate
  status query was unused.
- ``level_monitor_status`` — the renderer subscribes to the
  ``level_monitor_level`` push event instead of polling a status
  endpoint.
- ``test_llm_connection`` — the renderer's Settings page now uses
  the service-layer method directly (not over IPC).
- ``export_diagnostics``, ``check_accessibility``,
  ``show_electron_notification`` — the Tauri host now handles each
  via a dedicated Rust command (``export_diagnostics``,
  ``check_accessibility``, and the tray-notification path
  respectively) rather than bridging through Python IPC.
- ``get_vocabulary_suggestions``, ``apply_vocabulary_suggestion``,
  ``dismiss_vocabulary_suggestion`` — the vocabulary-automation
  feature was deferred pending UX redesign and the renderer's
  ``allowed-commands.ts`` dropped the three entries. The handler
  mixin still exists for the future re-wiring.
- ``delete_all_personal_data``, ``export_gdpr_bundle`` — the Tauri
  host now invokes them via dedicated Rust commands (with their own
  allowlist entries and consent prompts) rather than bridging through
  the generic dispatch path. The Python-side service methods still
  exist (called from the Rust bridge).
"""

from __future__ import annotations

# read-only IPC commands whose handlers do NOT mutate shared
# app/service state. These bypass the per-server ``_dispatch_lock`` so a
# long-running state-mutating handler (e.g. ``download_model``) does not
# block a quick status poll from a second authenticated connection. The
# set is intentionally minimal — only commands whose handler bodies are
# pure reads (no recorder / config / model / history mutation).
_READONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "get_status",
        "get_config",
        "get_model_catalog",
        "heartbeat",
    }
)

# commands intentionally absent from the TS / Rust allowlists.
# These commands are registered in the Python ``_COMMAND_REGISTRY``
# (so the dispatcher recognizes them) but are NEVER invoked by the
# renderer — they are server-internal or host-internal:
#
# - ``shutdown``: invoked by the Tauri host's WS transport to
#   request cooperative server shutdown (the host then closes the
#   socket). A compromised renderer must NOT be able to invoke
#   this — that would let it DoS the backend.
# - ``tray_click``: invoked by the Rust host's tray-icon click
#   handler. The renderer has no business sending this — it would
#   let a compromised renderer spoof tray clicks.
#
# This frozenset is the single source of truth for the
# ``test_ec4_python_command_registry_parity`` regression test
# which asserts that the Python registry, the TS allowlist, and
# the Rust allowlist agree on membership (modulo this documented
# exception set).
_PYTHON_ONLY_COMMANDS: frozenset[str] = frozenset({"shutdown", "tray_click"})

# Command registry: maps IPC command name to handler method.
# Built once at module load; ``IPCServer._dispatch`` does a single dict lookup.
# Each handler takes (data, resp) and returns resp (to send) or None
# (for commands that send their response internally, like restart_app).
#
# reconciliation (2026-07-18, updated 2026-10): the registry
# contains exactly 65 commands. The 63 "domain" handlers live in
# voice_typer/server/handlers/ (one mixin module per domain). The
# remaining two — `heartbeat` (, ADR-0018 Electron-alive watchdog)
# and `relaunch_ack` (PERF-005, ack of `relaunch_electron` so
# `restart_app` can drop its fixed 300 ms sleep) — are resident on
# IPCServer itself because they touch IPC-server-owned state
# (`_last_heartbeat_at`, `_relaunch_ack_event`) and don't belong to
# any domain mixin. The earlier "68 commands" claim in ADR-0020 §2
# was stale; `relaunch_ack` was added by PERF-005 after the original
# count. The count was bumped from 63 to 65 to reflect the two
# `force_cancel_transcription` and `tray_click` handlers added since
# the prior reconciliation (the `tray_click` host-only command was
# already counted in `_PYTHON_ONLY_COMMANDS`).
_COMMAND_REGISTRY: dict[str, str] = {
    "get_status": "_handle_get_status",
    "toggle_dictation": "_handle_toggle_dictation",
    "undo_last": "_handle_undo_last",
    # re-paste the last transcription (repaste_handlers mixin).
    "repaste_last": "_handle_repaste_last",
    "get_config": "_handle_get_config",
    "get_defaults": "_handle_get_defaults",
    "set_config": "_handle_set_config",
    "get_history": "_handle_get_history",
    "get_today_stats": "_handle_get_today_stats",
    "delete_history": "_handle_delete_history",
    "restore_history": "_handle_restore_history",
    "clear_history": "_handle_clear_history",
    "toggle_favorite": "_handle_toggle_favorite",
    "get_favorites": "_handle_get_favorites",
    "search_history": "_handle_search_history",
    # On-demand full-text + total-count handlers. Wired by the
    # history-handlers audit (see
    # ``handlers/history_handlers.py`` for the implementation). The
    # Dashboard's "Total Dictations" stat calls ``get_history_count``
    # (capped at 200 via ``get_history`` previously); the History
    # page calls ``get_transcription_text`` when the user expands a
    # row past the 500-char preview.
    "get_history_count": "_handle_get_history_count",
    "get_transcription_text": "_handle_get_transcription_text",
    "get_microphones": "_handle_get_microphones",
    "get_volume_backend_status": "_handle_get_volume_backend_status",
    "get_model_status": "_handle_get_model_status",
    # ADR-0009 Issue 3: prewarm cache status (Hot/Partial/Cold label,
    # cache ratio, last-run timestamp, elapsed seconds) for the About
    # page's "Cache Status" card.
    "get_prewarm_status": "_handle_get_prewarm_status",
    # Task 3: manually trigger a prewarm run (force=True) from the
    # About page's "Run Prewarm Now" button. Spawns a detached
    # subprocess; the frontend polls get_prewarm_status to track it.
    "run_prewarm": "_handle_run_prewarm",
    # Task 2: open the prewarm log file in the OS default text editor
    # from the About page's "View prewarm log" button.
    "open_prewarm_log": "_handle_open_prewarm_log",
    "get_vocabulary": "_handle_get_vocabulary",
    "save_vocabulary": "_handle_save_vocabulary",
    "get_templates": "_handle_get_templates",
    "save_templates": "_handle_save_templates",
    "restart_app": "_handle_restart_app",
    "quit_app": "_handle_quit_app",
    # Tauri host's cooperative-shutdown command.
    # Registered in the shared dispatch table so the WS transport
    # (sidecar_ws.py) can drop its special-case intercept and route
    # ``shutdown`` through the same path as every other command.
    # The handler delegates to ``self.service.quit()`` (NOT
    # ``self.app.quit()``) so any side-effect added to the service
    # layer runs identically on TCP / stdin / WS.
    "shutdown": "_handle_shutdown",
    "onboarding_is_first_run": "_handle_onboarding_is_first_run",
    "onboarding_start": "_handle_onboarding_start",
    "onboarding_next_step": "_handle_onboarding_next_step",
    "onboarding_prev_step": "_handle_onboarding_prev_step",
    "onboarding_set_microphone": "_handle_onboarding_set_microphone",
    "onboarding_set_hotkey": "_handle_onboarding_set_hotkey",
    "onboarding_set_model": "_handle_onboarding_set_model",
    "onboarding_skip": "_handle_onboarding_skip",
    "onboarding_apply": "_handle_onboarding_apply",
    "onboarding_get_microphones": "_handle_onboarding_get_microphones",
    "onboarding_get_model_options": "_handle_onboarding_get_model_options",
    "onboarding_get_hotkey_presets": "_handle_onboarding_get_hotkey_presets",
    # platform-conditional permission probe
    # (macOS Accessibility / Linux input group + udev rule) used by
    # the Permissions step.
    "onboarding_check_permissions": "_handle_onboarding_check_permissions",
    # Onboarding wizard reset. The handler lives in
    # ``handlers/onboarding_handlers.py``.
    "onboarding_reset": "_handle_onboarding_reset",
    "microphone_test_start": "_handle_microphone_test_start",
    "microphone_test_stop": "_handle_microphone_test_stop",
    "microphone_test_cancel": "_handle_microphone_test_cancel",
    "microphone_test_get_level": "_handle_microphone_test_get_level",
    "level_monitor_start": "_handle_level_monitor_start",
    "level_monitor_stop": "_handle_level_monitor_stop",
    "import_model": "_handle_import_model",
    "download_model": "_handle_download_model",
    "cancel_model_download": "_handle_cancel_model_download",
    # pause/resume in-progress model downloads.
    "pause_model_download": "_handle_pause_model_download",
    "resume_model_download": "_handle_resume_model_download",
    # full model catalog (rich metadata for the
    # Models page: VRAM, languages, speed/accuracy ratings).
    "get_model_catalog": "_handle_get_model_catalog",
    "delete_model": "_handle_delete_model",
    "set_tray_locale": "_handle_set_tray_locale",
    # Cloud-provider "Test Connection" probe. The renderer's
    # ``useCloudProviders.testConnection`` action dispatches this command
    # instead of issuing a cross-origin fetch directly (which would leak
    # the API key through browser dev-tools observability and violate
    # C-DATA-1's "renderer production code path stays network-free"
    # promise). The handler lives in
    # ``handlers/cloud_test_handlers.py`` (CloudTestHandlersMixin) and
    # performs the network call, gated by an explicit user click on the
    # Cloud tab's "Test Connection" button. Listed in
    # ``KNOWN_UNDOCUMENTED_COMMANDS`` (tests/tauri/mig19/
    # test_phase4_validation.py) pending a formal ADR-0020 §16 addendum.
    "test_cloud_connection": "_handle_test_cloud_connection",
    # XZ-SEC-05: add a hostname to the runtime URL allowlist + persist
    # to config.json under `trusted_extra_hosts` (self-hosted LLM/ASR
    # endpoint remediation). Handler lives in ConfigHandlersMixin
    # (handlers/config_handlers.py). Listed in KNOWN_UNDOCUMENTED_COMMANDS
    # (tests/tauri/mig19/test_phase4_validation.py) pending a formal
    # ADR-0020 §16 addendum, mirroring test_cloud_connection.
    "add_trusted_endpoint": "_handle_add_trusted_endpoint",
    # ESC-: pause/resume the global ESC cancel hotkey so the
    # frontend (HotkeyPicker in hotkey capture mode) can temporarily
    # disable it, preventing the backend from processing Escape while
    # the UI is capturing a custom hotkey.
    "set_esc_cancel_paused": "_handle_set_esc_cancel_paused",
    # P5: vocabulary automation — confidence-score-based correction
    # suggestions. See ``vocabulary_automation_handlers.py``.
    # Finding #3: force-cancel a stuck transcription.  Invokes
    # ``_force_recover_from_stuck_transcription(force=True)`` to reset
    # the busy flag and tray state immediately, bypassing the normal
    # 3×90s watchdog timeout.
    "force_cancel_transcription": "_handle_force_cancel_transcription",
    # Electron-alive heartbeat.  Electron's main process
    # sends this every 5 seconds; the backend's heartbeat-watchdog
    # daemon thread calls ``app.quit()`` if 9 consecutive heartbeats
    # are missed (45s timeout) so a crashed/force-killed Electron
    # doesn't strand the backend with the mic open + mutex held.
    "heartbeat": "_handle_heartbeat",
    # Electron acks receipt/processing of ``relaunch_electron``
    # so restart_app can drop its fixed 300ms sleep in favour of an
    # event-driven wait (bounded by a 2s timeout).
    "relaunch_ack": "_handle_relaunch_ack",
    # ADR-0020 §6.5 / §16: Tauri sidecar tray-menu click dispatch.
    # The Tauri host forwards a clicked menu item id; the backend looks
    # it up in the tray's id→callback map and invokes the action. Unknown
    # ids return a structured ``unknown_tray_item`` error (distinct from
    # ``unknown_command``) so the host can surface "missing item" vs
    # "unknown command" differently.
    "tray_click": "_handle_tray_click",
    # Fix-A (IMPROVE-mode run, 2026-07-21): GDPR Art. 17 (right
    # to erasure) and Art. 20 (right to data portability) handlers.
    # Registered via dedicated Rust commands; service methods live on
    # VoiceTyperService (delete_all_personal_data / export_gdpr_bundle).
}


__all__ = [
    "_COMMAND_REGISTRY",
    "_PYTHON_ONLY_COMMANDS",
    "_READONLY_COMMANDS",
]
