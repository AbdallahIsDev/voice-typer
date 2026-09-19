"""Validation gate for the frozen 68-command / 21-event bridge."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
IPC_SERVER_PY = REPO_ROOT / "voice_typer" / "server" / "ipc_server.py"
IPC_SERVER_IMPL_PY = REPO_ROOT / "voice_typer" / "server" / "ipc" / "lifecycle.py"
IPC_MAIN_PY = REPO_ROOT / "voice_typer" / "server" / "ipc" / "entrypoint.py"
TRAY_TYPES_PY = REPO_ROOT / "voice_typer" / "server" / "tray_types.py"
SIDECAR_WS_PY = REPO_ROOT / "voice_typer" / "server" / "sidecar_ws.py"
SIDECAR_WS_HANDSHAKE_PY = REPO_ROOT / "voice_typer" / "server" / "sidecar_ws_internals" / "handshake.py"
WS_RS = REPO_ROOT / "src-tauri" / "src" / "sidecar" / "ws.rs"
WS_EVENT_PROTOCOL_RS = REPO_ROOT / "src-tauri" / "src" / "sidecar" / "ws" / "event_protocol.rs"
WS_READER_RS = REPO_ROOT / "src-tauri" / "src" / "sidecar" / "ws" / "reader.rs"
WS_WRITER_RS = REPO_ROOT / "src-tauri" / "src" / "sidecar" / "ws" / "writer.rs"
ADR_0020 = REPO_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"

# ── ADR-0020 §2, frozen 68-command table (the v1 wire contract) ─────────
# ``test_command_contract_is_frozen_no_untested_additions`` test fails.
EXPECTED_COMMANDS: frozenset[str] = frozenset(
    {
        # status_handlers
        "get_status",
        "get_volume_backend_status",
        "get_model_status",
        # RESTORED 2026-08-14 (plan §6.3 addendum, Cache Status card,
        "get_prewarm_status",
        "open_prewarm_log",
        "run_prewarm",
        "transcribe_offline",  # master plan §7.4, slim core → worker offline ASR
        "check_offline_pack_update",
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
        # §16 addendum 2026-08-16 (vocabulary usage tracking + live test
        "get_correction_usage",
        "test_vocabulary_correction",
        "get_templates",
        "save_templates",
        # onboarding_handlers
        "onboarding_is_first_run",
        "onboarding_start",
        # REMOVED: ``onboarding_get_step``, the renderer holds wizard
        "onboarding_next_step",
        "onboarding_prev_step",
        "onboarding_set_microphone",
        "onboarding_set_hotkey",
        "onboarding_set_model",
        # ADR-0020 §16 addendum (2026-08-06): ``onboarding_set_backend``
        "onboarding_set_backend",
        "onboarding_skip",
        "onboarding_apply",
        "onboarding_get_microphones",
        "onboarding_get_model_options",
        "onboarding_get_hotkey_presets",
        # (IMPROVE-2026-07-19): macOS/Linux permission probe added
        "onboarding_check_permissions",
        # REMOVED: ``onboarding_get_model_catalog``, the renderer uses
        "onboarding_reset",
        # microphone_handlers
        "get_microphones",
        # REMOVED: ``refresh_microphones``, ``get_rms_level``,
        "microphone_test_start",
        "microphone_test_stop",
        "microphone_test_read_audio",
        "microphone_test_cancel",
        # REMOVED: ``microphone_test_status``, the renderer polls
        "microphone_test_get_level",
        "level_monitor_start",
        "level_monitor_stop",
        # REMOVED: ``level_monitor_status``, the renderer subscribes
        "download_model",
        "cancel_model_download",
        "pause_model_download",
        "resume_model_download",
        # Pending-download FIFO queue snapshot (read-only, no payload)
        "get_download_queue",
        "get_model_catalog",
        # REMOVED: ``test_llm_connection``, the renderer's Settings
        "import_model",
        "delete_model",
        # system_handlers
        "restart_app",
        "quit_app",
        "check_accessibility",
        "set_tray_locale",
        "set_esc_cancel_paused",
        # ADR-0020 §16 addendum (2026-08-09, finding #127 part b):
        "reset_macos_accessibility",
        # ADR-0020 §16 addendum (2026-08-10, finding #127 part b):
        "reset_linux_permissions",
        "heartbeat",
        # ADR-0020 §16 addendum (2026-07-24): commands added
        "shutdown",
    }
)
assert len(EXPECTED_COMMANDS) == 71, (
    "ADR-0020 §2 freezes the command table. 69 = post-cleanup baseline "
    "after ZR-45 + the Tauri/Rust allowlist narrowing (+ ``onboarding_set_backend``, "
    "§16 addendum 2026-08-06; + ``reset_macos_accessibility``, "
    "§16 addendum 2026-08-09; + ``reset_linux_permissions``, "
    "§16 addendum 2026-08-10; + ``check_accessibility``, "
    "§16 addendum 2026-08-10 re-registration; + ``transcribe_offline`` "
    "§16 addendum 2026-08-13 master plan §7.4; + ``check_offline_pack_update`` "
    "§16 addendum 2026-08-14 auto-update feature docs/auto-update-feature.md; + 3 "
    "``get_prewarm_status`` / ``open_prewarm_log`` / ``run_prewarm`` §16 addendum 2026-08-14 "
    "plan §6.3 addendum, Cache Status card restored verbatim from 5a319872; ``run_prewarm`` "
    "re-implemented (in-process warm pass, no deleted-subprocess spawn); + 2 "
    "``get_correction_usage`` / ``test_vocabulary_correction`` §16 addendum 2026-08-16 "
    "— vocabulary usage tracking + live correction test panel; + "
    "``get_download_queue`` §16 addendum 2026-09-08, pending-download "
    "FIFO queue snapshot, read-only mount hydration for the Models "
    "page queue chips). The prior 76-command "
    "list was stale, it included 17 commands that had been deliberately "
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
    "(``export_diagnostics``, ``the legacy notification command``) − 2 GDPR commands bridged via Rust "
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

KNOWN_UNDOCUMENTED_COMMANDS: frozenset[str] = frozenset(
    {
        # PERF-005 (ipc_server.py:1710-1712): predecessor acks receipt of
        "relaunch_ack",
        # ``ipc_server.py``). Listed here so the frozen-contract gate
        "tray_click",
        # observability and violate C-DATA-1's "renderer production
        "test_cloud_connection",
        # XZ-SEC-05: ``add_trusted_endpoint``, adds a hostname to the
        "add_trusted_endpoint",
    }
)

# Source: ADR-0020 "Sidecar→UI Event Table", 21 events. These are
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
        "notification",
        "navigate",
        "show_window",
        "quit_app",
        "relaunch_app",
        # ADR-0020 §16 addendum (2026-07-24): three events
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
EVENT_RENAMES: dict[str, str] = {}
EVENT_ALIASES: dict[str, tuple[str, ...]] = {
    "the legacy notification event name": ("notification",),
}


def _import_ipc_server():
    """Import the IPCServer module lazily so collection is hermetic."""
    from voice_typer.server import ipc_server

    return ipc_server


def _import_sidecar_ws():
    """Import sidecar_ws lazily, the module imports cleanly even when"""
    from voice_typer.server import sidecar_ws

    return sidecar_ws


def test_command_registry_exists_and_is_dict():
    """``_COMMAND_REGISTRY`` MUST be a class-level ``dict[str, str]`` on"""
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
    """``_COMMAND_REGISTRY``. A missing entry means the wire contract was"""
    ipc_server = _import_ipc_server()
    actual = set(ipc_server.IPCServer._COMMAND_REGISTRY.keys())
    missing = EXPECTED_COMMANDS - actual
    assert not missing, (
        "ADR-0020 §2 freezes these commands but they are MISSING from "
        f"_COMMAND_REGISTRY: {sorted(missing)}. Restore them or update "
        "the ADR + this test together."
    )


def test_command_registry_handlers_resolve_to_methods():
    """Every ``_COMMAND_REGISTRY[cmd]`` value MUST name an existing"""
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
    """Each ``_handle_<cmd>`` method MUST accept ``(self, data, resp)`` —"""
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
        # Expect (self, data, resp), accept extra optional params
        if params[:3] != ["self", "data", "resp"]:
            broken.append(f"{cmd} → {handler_name}: params={params}")
    assert not broken, (
        "The following _handle_<cmd> methods do NOT have the "
        "(self, data, resp) dispatch signature:\n  " + "\n  ".join(broken)
    )


def test_validate_dict_payload_is_importable_and_callable():
    """source of truth for command-payload shape. It MUST be importable"""
    ipc_server = _import_ipc_server()
    assert hasattr(ipc_server, "_validate_dict_payload"), (
        "_validate_dict_payload must be defined at module scope in ipc_server.py (ADR-0020 §2)."
    )
    assert callable(ipc_server._validate_dict_payload)


def test_validate_dict_payload_returns_validated_dict_on_success():
    """On success: ``(validated_dict, None)``, the second element is"""
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
    """handler can ``return resp`` immediately. The error response carries"""
    ipc_server = _import_ipc_server()
    validated, error = ipc_server._validate_dict_payload("not a dict", {})
    assert validated is None
    assert error is not None
    assert error["type"] == "error"
    assert error["data"]["code"] in ("client.invalid_payload", "invalid_payload")


def test_validate_dict_payload_reports_missing_required_field():
    """``missing_field`` code and the offending field name (so the client"""
    ipc_server = _import_ipc_server()
    schema = {"hotkey": {"type": str, "required": True}}
    validated, error = ipc_server._validate_dict_payload({}, schema)
    assert validated is None
    assert error["data"]["code"] in ("client.missing_field", "missing_field")
    assert error["data"]["field"] == "hotkey"


def test_validate_dict_payload_reports_wrong_type_field():
    """Wrong-type field → ``(None, error)`` with the ``invalid_field``"""
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
    """Optional fields with a ``default`` value MUST be filled in when"""
    ipc_server = _import_ipc_server()
    schema = {
        "limit": {"type": int, "required": False, "default": 50},
    }
    validated, error = ipc_server._validate_dict_payload({}, schema)
    assert error is None
    assert validated == {"limit": 50}


def test_validate_dict_payload_is_referenced_in_adr():
    """truth for payload shape. The ADR MUST mention it (this guards"""
    text = ADR_0020.read_text(encoding="utf-8")
    assert "_validate_dict_payload" in text, (
        "ADR-0020 must name _validate_dict_payload as the source of truth for payload validation (§2 + §16 item 3)."
    )


def _read_ws_rs() -> str:
    """Read the Rust WS bridge source: the ``ws.rs`` parent module PLUS"""
    assert WS_RS.is_file(), (
        f"src-tauri/src/sidecar/ws.rs is missing at {WS_RS}, the Tauri "
        "WS bridge has been removed (ADR-0020 regression)."
    )
    parts = [WS_RS.read_text(encoding="utf-8")]
    for sibling in (WS_READER_RS, WS_WRITER_RS):
        assert sibling.is_file(), (
            f"{sibling} is missing, the ws.rs reader/writer module split was rolled back mid-flight."
        )
        parts.append(sibling.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def _read_ws_event_protocol_rs() -> str:
    """Read the Rust WS event-protocol submodule source. The file MUST exist."""
    assert WS_EVENT_PROTOCOL_RS.is_file(), (
        f"src-tauri/src/sidecar/ws/event_protocol.rs is missing at "
        f"{WS_EVENT_PROTOCOL_RS}, the split was rolled "
        "back (ADR-0020 regression)."
    )
    parts = [WS_EVENT_PROTOCOL_RS.read_text(encoding="utf-8")]
    tests_path = WS_EVENT_PROTOCOL_RS.parent / "event_protocol_tests.rs"
    if tests_path.is_file():
        parts.append(tests_path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def test_ws_bridge_does_not_silently_filter_events():
    """ADR-0020 §event table: the Rust bridge forwards every"""
    src = _read_ws_rs()
    event_protocol_src = _read_ws_event_protocol_rs()
    assert re.search(r"let\s+emit_name\s*=\s*translate_event_name\(\s*event_type\s*\)\s*;", src), (
        "ws.rs must compute `emit_name` via `translate_event_name(event_type)` "
        "(PVT-G5-062, single unit-testable rename table). ADR-0020 §event table."
    )
    # The translate function MUST have an `other => other` arm so any
    assert re.search(r"other\s*=>\s*other\s*,", event_protocol_src), (
        "translate_event_name (now in ws/event_protocol.rs after the split) "
        "must have an `other => other` forward-compat passthrough arm "
        "(unknown event names flow through unchanged)."
    )


def test_ws_bridge_emits_python_event_catch_all():
    """direct listeners like the bubble window) AND a generic"""
    src = _read_ws_rs()
    # The bridge MUST emit "python-event" with the original event
    assert '"python-event"' in src, (
        'ws.rs must emit a "python-event" catch-all event carrying {"type":<name>, "data":<payload>} (ADR-0020 §6.3).'
    )


def test_ws_bridge_emits_notification_alias_for_legacy_name():
    """(ws.rs): a backward-compat alias emits ``notification``"""
    src = _read_ws_rs() + "\n" + _read_ws_event_protocol_rs()
    assert '"notification"' in src, (
        "ws.rs (+ ws/event_protocol.rs after the split) must reference the "
        "canonical `notification` event name (CR-8, ADR-0020 §6.1). No "
        "predecessor-era alias survives in the bridge."
    )


def test_ws_bridge_coalesces_bubble_level():
    """sidecar but the bridge MUST coalesce to ≤30 Hz to avoid flooding"""
    src = _read_ws_rs()
    assert "bubble_level" in src, "ws.rs must handle the `bubble_level` event specifically (ADR-0020 §9 coalescing)."
    assert "bubble_coalesce_should_emit" in src, (
        "ws.rs must call `bubble_coalesce_should_emit` for bubble_level events (ADR-0020 §9, coalesce to ≤30 Hz)."
    )
    bubble_section = src[src.index("bubble_level") :]
    assert "is_high_rate_event_type" in src or "ER-35" in bubble_section, (
        "ws.rs must gate high-rate catch-all duplicates via `is_high_rate_event_type` (ER-35)."
    )
    assert 'emit("python-event", json!({"type": "bubble_level"' not in bubble_section[:2000], (
        "ws.rs must NOT re-emit a per-frame `python-event` json! duplicate for bubble_level (ER-35)."
    )


# Events whose source name has been renamed in the Python sidecar
EVENT_NAME_RENAMES_IN_SOURCE: dict[str, str] = {}


def test_ws_bridge_forwards_all_24_event_names():
    """Every event name in the ADR-0020 §event table MUST be"""
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
        candidate_names = [event_name]
        if event_name in EVENT_NAME_RENAMES_IN_SOURCE:
            candidate_names.append(EVENT_NAME_RENAMES_IN_SOURCE[event_name])
        found = False
        for name in candidate_names:
            # Look for the event name as a JSON string value, e.g.
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


def test_tauri_sidecar_env_disables_heartbeat_watchdog_in_source():
    """ADR-0020 §2 + §10: under ``TAURI_SIDECAR=1`` the Python"""
    src = IPC_SERVER_IMPL_PY.read_text(encoding="utf-8")
    helper_src = TRAY_TYPES_PY.read_text(encoding="utf-8")
    assert 'os.environ.get("TAURI_SIDECAR") == "1"' in helper_src, (
        'tray_types.is_tauri_sidecar must read the exact `TAURI_SIDECAR == "1"` sentinel (ADR-0020 §7).'
    )
    assert "from voice_typer.server.tray_types import is_tauri_sidecar" in src, (
        "ipc/lifecycle.py must gate the heartbeat watchdog on the canonical "
        "`is_tauri_sidecar()` helper (ADR-0020 §2 + §10)."
    )
    # The skip path MUST set ``_heartbeat_thread = None`` (not
    gate_idx = src.index("_tauri_sidecar = is_tauri_sidecar()")
    window = src[gate_idx : gate_idx + 600]
    assert "_heartbeat_thread = None" in window, (
        "ipc/lifecycle.py must set `self._heartbeat_thread = None` when "
        "TAURI_SIDECAR=1 (ADR-0020 §10 - skip the heartbeat-watchdog "
        "thread entirely, do not start-then-stop)."
    )


def test_tauri_sidecar_env_propagated_by_ws_mode():
    """ADR-0020 §2 + §10: ``--ws`` mode MUST set ``TAURI_SIDECAR=1``"""
    src = IPC_MAIN_PY.read_text(encoding="utf-8")
    # ``--ws`` mode MUST set the env var (so a terminal-launched
    assert 'os.environ["TAURI_SIDECAR"] = "1"' in src, (
        "ipc/entrypoint.py must set os.environ['TAURI_SIDECAR'] = '1' in --ws mode (ADR-0020 §2 footnote + §10)."
    )


def test_tauri_sidecar_env_disables_python_single_instance_mutex():
    """ADR-0020 §12: under ``TAURI_SIDECAR=1`` the Python-side"""
    server_src = IPC_SERVER_IMPL_PY.read_text(encoding="utf-8")
    main_src = IPC_MAIN_PY.read_text(encoding="utf-8")
    occurrences = server_src.count("is_tauri_sidecar()") + main_src.count("is_tauri_sidecar()")
    assert occurrences >= 2, (
        "ipc/ must gate on the canonical is_tauri_sidecar() helper in at "
        "least two gates: (1) the heartbeat-watchdog skip (§10, in "
        "lifecycle.py) and (2) the Python single-instance mutex skip "
        f"(§12, in entrypoint.py). Found {occurrences}."
    )


def test_sidecar_authenticate_does_not_echo_token(monkeypatch):
    """ADR-0020 §3: the sidecar MUST NOT echo the auth token in any"""
    sw = _import_sidecar_ws()
    token = "deadbeef" * 8  # 64 hex chars
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", token)

    # Fake websocket, capture every frame the sidecar writes back.
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
    for frame in sent_frames:
        assert token not in frame, (
            "sidecar_ws._authenticate wrote a frame containing the "
            f"auth token: {frame!r} (ADR-0020 §3, token MUST NOT be "
            "echoed)."
        )


def test_sidecar_source_does_not_log_token_verbatim():
    """ADR-0020 §3: the token MUST NOT appear verbatim in any log"""
    src = SIDECAR_WS_PY.read_text(encoding="utf-8") + "\n" + SIDECAR_WS_HANDSHAKE_PY.read_text(encoding="utf-8")
    bad_patterns = [
        r'log\.\w+\([^)]*f["\'].*\{expected_token[^}]*\}.*["\']',
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
        "token (expected_token / provided), ADR-0020 §3 forbids "
        "logging the token verbatim. Offending lines:\n  " + "\n  ".join(repr(v) for v in violations)
    )


def test_sidecar_emit_server_started_does_not_leak_token(capsys):
    """
    ADR-0020 §1 + §3: the only line the sidecar writes to stdout is
    ``{"event":"server_started","port":<n>}``. The token MUST NOT
    """
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


def test_command_contract_is_frozen_no_untested_additions():
    """ADR-0020 §16: the 68-command table is the frozen wire contract."""
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
    """The ``KNOWN_UNDOCUMENTED_COMMANDS`` allowlist MUST exactly match"""
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
                "LONGER in _COMMAND_REGISTRY (the gap was closed, "
                "remove the stale entry from KNOWN_UNDOCUMENTED_COMMANDS):\n  " + "\n  ".join(sorted(stale_in_known))
            )
        pytest.fail("\n\n".join(msg_parts))


def test_event_contract_is_frozen_all_24_events_present():
    """ADR-0020 §16: the 24-event table is the frozen wire contract"""
    # The module-level ``assert len(EXPECTED_EVENTS) == 24`` already
    assert len(EXPECTED_EVENTS) == 24, (
        "ADR-0020 freezes a 24-event table. Update EXPECTED_EVENTS + the ADR addendum together (§16)."
    )


def test_adr_0020_states_61_command_contract():
    """ADR-0020 §2 + §16 MUST state the frozen command count as 61."""
    text = ADR_0020.read_text(encoding="utf-8")
    # The ADR mentions "61 commands" in §2 + §16 + the §heartbeat
    assert re.search(r"\b61\s+commands?\b", text), (
        "ADR-0020 must state the frozen command count as '61 "
        "commands' (§2 table header + §16). If the contract grew, "
        "update the ADR + EXPECTED_COMMANDS together. (the "
        "prior 68-command baseline was reduced to 61 during the "
        "Tauri/Rust allowlist narrowing.)"
    )


def test_adr_0020_states_24_event_contract():
    """ADR-0020 §event table + §16 MUST state the frozen event count"""
    text = ADR_0020.read_text(encoding="utf-8")
    assert re.search(r"\b24\s+events?\b", text), (
        "ADR-0020 must state the frozen event count as '24 events' "
        "(§event table + §16). If the contract grew, update the ADR "
        "+ EXPECTED_EVENTS together."
    )


def test_adr_0020_states_frozen_contract_clause():
    """ADR-0020 §16 MUST contain the literal phrase 'frozen contract'"""
    text = ADR_0020.read_text(encoding="utf-8")
    assert "frozen contract" in text.lower(), (
        "ADR-0020 §16 must contain the phrase 'frozen contract' so the contract-freeze policy is searchable."
    )


def test_adr_0020_documents_new_command_process():
    """ADR-0020 §16 documents the 4-step process for adding a new"""
    text = ADR_0020.read_text(encoding="utf-8")
    # §16 mandates: (1) add to _COMMAND_REGISTRY, (2) ADR addendum,
    assert "_COMMAND_REGISTRY" in text, "ADR-0020 §16 must mention _COMMAND_REGISTRY (new-command process step 1)."
    assert "_validate_dict_payload" in text, (
        "ADR-0020 §16 must mention _validate_dict_payload (new-command process step 3)."
    )
    assert "test_ipc_dispatch_errors" in text, (
        "ADR-0020 §16 must reference tests/test_ipc_dispatch_errors.py (new-command process step 4)."
    )
