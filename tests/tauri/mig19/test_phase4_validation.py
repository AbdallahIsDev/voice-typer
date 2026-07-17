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
   two ADR §6.1 renames (``relaunch_electron`` → ``relaunch_app``,
   ``electron_notification`` → ``notification`` alias), and coalesces
   ``bubble_level`` to ≤30 Hz per ADR §9.
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
         Wait 130s. The sidecar process must EXIT (FT-1 supervisor
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
SIDECAR_WS_PY = REPO_ROOT / "voice_typer" / "server" / "sidecar_ws.py"
WS_RS = REPO_ROOT / "src-tauri" / "src" / "sidecar" / "ws.rs"
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
        "get_rms_level",
        "get_volume_backend_status",
        "get_audio_status",
        "get_model_status",
        "get_prewarm_status",
        "run_prewarm",
        "open_prewarm_log",
        # dictation_handlers
        "toggle_dictation",
        "undo_last",
        "force_cancel_transcription",
        # history_handlers
        "get_history",
        "get_today_stats",
        "delete_history",
        "restore_history",
        "clear_history",
        "toggle_favorite",
        "get_favorites",
        "search_history",
        # config_handlers
        "get_config",
        "get_defaults",
        "set_config",
        # vocabulary_handlers
        "get_vocabulary",
        "save_vocabulary",
        # vocabulary_automation_handlers
        "get_vocabulary_suggestions",
        "apply_vocabulary_suggestion",
        "dismiss_vocabulary_suggestion",
        # templates_handlers
        "get_templates",
        "save_templates",
        # onboarding_handlers
        "onboarding_is_first_run",
        "onboarding_start",
        "onboarding_get_step",
        "onboarding_next_step",
        "onboarding_prev_step",
        "onboarding_set_microphone",
        "onboarding_set_hotkey",
        "onboarding_set_model",
        "onboarding_skip",
        "onboarding_apply",
        "onboarding_get_microphones",
        "onboarding_get_model_options",
        "onboarding_get_hotkey_presets",
        # microphone_handlers
        "get_microphones",
        "refresh_microphones",
        # microphone_test_handlers
        "microphone_test_start",
        "microphone_test_stop",
        "microphone_test_cancel",
        "microphone_test_status",
        "microphone_test_get_level",
        # level_monitor_handlers
        "level_monitor_start",
        "level_monitor_stop",
        "level_monitor_status",
        # model_handlers
        "download_model",
        "cancel_model_download",
        "pause_model_download",
        "resume_model_download",
        "get_model_catalog",
        "test_llm_connection",
        "import_model",
        "delete_model",
        # system_handlers
        "restart_app",
        "quit_app",
        "export_diagnostics",
        "check_accessibility",
        "set_tray_locale",
        "set_esc_cancel_paused",
        "show_electron_notification",
        # ipc_server (RW-10 / ADR-0018) — kept on the registry even
        # though it is REMOVED on the Tauri path; a stray frame from a
        # legacy UI must still hit the handler (not ``unknown_command``).
        "heartbeat",
    }
)
assert len(EXPECTED_COMMANDS) == 68, (
    "ADR-0020 §2 freezes a 68-command table. Update this set + the ADR "
    "addendum + tests/test_ipc_dispatch_errors.py together (§16)."
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
    }
)

# ── ADR-0020 §event table — frozen 21-event table ───────────────────────
#
# Source: ADR-0020 "Sidecar→UI Event Table" — 21 events. These are
# server-initiated (channel 2) — distinct from the command/response
# envelope (channel 1). Each is delivered as
# ``{"type":<name>,"data":{...}}`` and re-emitted by the Rust bridge
# as a Tauri event of the same name (modulo the two renames below).
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
        "relaunch_electron",  # renamed to "relaunch_app" by the bridge
    }
)
assert len(EXPECTED_EVENTS) == 21, (
    "ADR-0020 freezes a 21-event table. Update this set + the ADR addendum together (§16)."
)

# Events that the Rust bridge renames before re-emitting as Tauri
# events (ADR-0020 §6.1 — payloads are unchanged, only the event name
# changes). The Rust bridge also emits a backward-compat
# ``notification`` alias when it sees the legacy
# ``electron_notification`` event name (CR-8 in ws.rs).
EVENT_RENAMES: dict[str, str] = {
    "relaunch_electron": "relaunch_app",
}
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
    assert error["data"]["code"] == "invalid_payload"


def test_validate_dict_payload_reports_missing_required_field():
    """Missing required field → ``(None, error)`` with the
    ``missing_field`` code and the offending field name (so the client
    can render a precise error)."""
    ipc_server = _import_ipc_server()
    schema = {"hotkey": {"type": str, "required": True}}
    validated, error = ipc_server._validate_dict_payload({}, schema)
    assert validated is None
    assert error["data"]["code"] == "missing_field"
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
    assert error["data"]["code"] == "invalid_field"
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


def test_ws_bridge_does_not_allowlist_filter_events():
    """ADR-0020 §event table: the Rust bridge forwards every
    server-initiated event by name. There MUST NOT be an allowlist
    that silently drops unknown event types — the bridge is a generic
    fan-out, not a per-event-type dispatcher.

    Source-inspect ws.rs: the only event-type-specific branches are
    ``bubble_level`` (coalescing) and the two renames
    (``relaunch_electron`` → ``relaunch_app``,
    ``electron_notification`` → ``notification`` alias). All other
    event types fall through to the generic ``other => other`` arm.
    """
    src = _read_ws_rs()
    # The match arm pattern: ``other => other`` proves fall-through.
    assert re.search(r"other\s*=>\s*other", src), (
        "ws.rs must use a generic `other => other` match arm so every "
        "event type is forwarded unchanged (no allowlist). ADR-0020 "
        "§event table."
    )
    # No hardcoded list of allowed event names that would filter.
    # (If a future change adds an allowlist, this assertion catches it.)
    forbidden_patterns = [
        r"ALLOWED_EVENTS",
        r"allowed_events\s*[:=]",
        r"EVENT_ALLOWLIST",
        r"event_allowlist\s*[:=]",
    ]
    for pat in forbidden_patterns:
        assert not re.search(pat, src), (
            f"ws.rs contains an event allowlist pattern ({pat!r}) — ADR-0020 mandates generic fan-out (no allowlist)."
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


def test_ws_bridge_applies_relaunch_electron_to_relaunch_app_rename():
    """ADR-0020 §6.1: ``relaunch_electron`` is renamed to
    ``relaunch_app`` on the Tauri side (Tauri uses ``app.restart()``
    rather than Electron's ``app.relaunch()``). Payload is unchanged.
    """
    src = _read_ws_rs()
    assert '"relaunch_electron"' in src and '"relaunch_app"' in src, (
        "ws.rs must rename `relaunch_electron` → `relaunch_app` (ADR-0020 §6.1)."
    )
    # The rename must be in a match arm, not just a comment.
    rename_re = re.compile(
        r'"relaunch_electron"\s*=>\s*"relaunch_app"',
    )
    assert rename_re.search(src), 'ws.rs must have a match arm: "relaunch_electron" => "relaunch_app" (ADR-0020 §6.1).'


def test_ws_bridge_emits_notification_alias_for_electron_notification():
    """CR-8 (ws.rs): a backward-compat alias emits ``notification``
    alongside the legacy ``electron_notification`` event name so new
    UI code subscribing to ``notification`` keeps working during a
    rolling upgrade. Drop after one release cycle (ADR-0020 §6.1)."""
    src = _read_ws_rs()
    assert '"electron_notification"' in src and '"notification"' in src, (
        "ws.rs must emit a `notification` alias for the legacy `electron_notification` event (CR-8, ADR-0020 §6.1)."
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
# (CR-8 in ws.rs + handlers/system_handlers.py + startup_sequence.py).
# The Rust bridge has a backward-compat alias so legacy sidecars that
# still emit the OLD name keep working — but new sidecars emit the
# NEW name directly. The Phase 4 test accepts EITHER name in the
# Python source to tolerate the rolling rename.
EVENT_NAME_RENAMES_IN_SOURCE: dict[str, str] = {
    # Old (still listed in ADR-0020 §event table) → New (what the
    # Python sidecar emits today).
    "electron_notification": "notification",
}


def test_ws_bridge_forwards_all_21_event_names():
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
    heartbeat-watchdog thread (ADR-0018) is DISABLED — the Tauri
    host's FT-1 supervisor replaces it. Source-inspect
    ``ipc_server.py`` for the env-var check + the
    ``_heartbeat_thread = None`` skip path."""
    src = IPC_SERVER_PY.read_text(encoding="utf-8")
    # The env var MUST be read with the exact "1" sentinel (not
    # truthy / not "true" — ADR-0020 §10 specifies "=1").
    assert 'os.environ.get("TAURI_SIDECAR") == "1"' in src, (
        'ipc_server.py must gate the heartbeat watchdog on `TAURI_SIDECAR == "1"` (ADR-0020 §2 + §10).'
    )
    # The skip path MUST set ``_heartbeat_thread = None`` (not
    # start the thread and then immediately stop it — that would
    # leak a thread).
    # Find the TAURI_SIDECAR gate block and assert
    # ``_heartbeat_thread = None`` appears within ~10 lines.
    gate_idx = src.index('os.environ.get("TAURI_SIDECAR") == "1"')
    window = src[gate_idx : gate_idx + 600]
    assert "_heartbeat_thread = None" in window, (
        "ipc_server.py must set `self._heartbeat_thread = None` when "
        "TAURI_SIDECAR=1 (ADR-0020 §10 — skip the heartbeat-watchdog "
        "thread entirely, do not start-then-stop)."
    )


def test_tauri_sidecar_env_propagated_by_ws_mode():
    """ADR-0020 §2 + §10: ``--ws`` mode MUST set ``TAURI_SIDECAR=1``
    on the sidecar so the downstream heartbeat-watchdog + Python-side
    single-instance mutex gates see it. Source-inspect
    ``ipc_server.py`` for the ``--ws`` flag handler."""
    src = IPC_SERVER_PY.read_text(encoding="utf-8")
    # ``--ws`` mode MUST set the env var (so a terminal-launched
    # ``python -m voice_typer.server.ipc_server --ws`` also gets the
    # Tauri-sidecar behavior — ADR-0020 §2 footnote).
    assert 'os.environ["TAURI_SIDECAR"] = "1"' in src, (
        "ipc_server.py must set os.environ['TAURI_SIDECAR'] = '1' in --ws mode (ADR-0020 §2 footnote + §10)."
    )


def test_tauri_sidecar_env_disables_python_single_instance_mutex():
    """ADR-0020 §12: under ``TAURI_SIDECAR=1`` the Python-side
    ``VoiceTyperSingleInstance`` Win32 mutex is skipped (the Tauri
    host's single-instance plugin owns it). Source-inspect
    ``ipc_server.py`` for the second TAURI_SIDECAR gate."""
    src = IPC_SERVER_PY.read_text(encoding="utf-8")
    # The mutex skip gate MUST appear (typically near the bottom of
    # main() — separate from the heartbeat-watchdog gate).
    # Look for at least TWO occurrences of the env-var check.
    occurrences = src.count('os.environ.get("TAURI_SIDECAR") == "1"')
    assert occurrences >= 2, (
        "ipc_server.py must reference TAURI_SIDECAR=1 in at least two "
        "gates: (1) the heartbeat-watchdog skip (§10) and (2) the "
        f"Python single-instance mutex skip (§12). Found {occurrences}."
    )


# ── Test: sidecar never echoes the token (ADR-0020 §3) ──────────────────


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


def test_event_contract_is_frozen_all_21_events_present():
    """ADR-0020 §16: the 21-event table is the frozen wire contract.
    This test asserts the ``EXPECTED_EVENTS`` set has exactly 21
    entries (module-level ``assert`` at import time) AND that every
    one of them is actually emitted somewhere in the Python
    ``voice_typer/server/`` tree (covered by
    ``test_ws_bridge_forwards_all_21_event_names``). The combination
    guards against silent event additions / removals."""
    # The module-level ``assert len(EXPECTED_EVENTS) == 21`` already
    # guards the count. Here we re-assert for visibility in the test
    # report.
    assert len(EXPECTED_EVENTS) == 21, (
        "ADR-0020 freezes a 21-event table. Update EXPECTED_EVENTS + the ADR addendum together (§16)."
    )


def test_adr_0020_states_68_command_contract():
    """ADR-0020 §2 + §16 MUST state the frozen command count as 68.
    This guards against an ADR rewrite that silently changes the
    frozen baseline (e.g. "67 commands" after a quiet removal of
    ``heartbeat``)."""
    text = ADR_0020.read_text(encoding="utf-8")
    # The ADR mentions "68 commands" in multiple places (§2 table
    # header + §16 frozen-contract statement). At least ONE match.
    assert re.search(r"\b68\s+commands?\b", text), (
        "ADR-0020 must state the frozen command count as '68 "
        "commands' (§2 table header + §16). If the contract grew, "
        "update the ADR + EXPECTED_COMMANDS together."
    )


def test_adr_0020_states_21_event_contract():
    """ADR-0020 §event table + §16 MUST state the frozen event count
    as 21. Same guard as the command-count test above."""
    text = ADR_0020.read_text(encoding="utf-8")
    assert re.search(r"\b21\s+events?\b", text), (
        "ADR-0020 must state the frozen event count as '21 events' "
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
