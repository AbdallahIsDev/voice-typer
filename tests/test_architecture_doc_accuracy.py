"""doc-parity test: assert the architecture docs match reality."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ARCH_DOC = ROOT / "docs" / "ARCHITECTURE.md"
SHUTDOWN_DOC = ROOT / "docs" / "modules" / "shutdown_controller.md"
AUDIO_Q_DOC = ROOT / "docs" / "modules" / "audio_quality_controller.md"
SIDECAR_DOC = ROOT / "docs" / "modules" / "sidecar_ws.md"
INDEX_DOC = ROOT / "docs" / "modules" / "_index.md"
TIMER_DOC = ROOT / "docs" / "modules" / "timer_coordinator.md"
VOLUME_DOC = ROOT / "docs" / "modules" / "volume_controller.md"
ERROR_ENV_DOC = ROOT / "docs" / "architecture" / "error-envelope-contract.md"
MAIN_RS = ROOT / "src-tauri" / "src" / "main.rs"
CARGO_TOML = ROOT / "src-tauri" / "Cargo.toml"
MAIN_RUNTIME_CAPS = ROOT / "src-tauri" / "capabilities" / "main-runtime.json"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_event_bus_count_matches_doc_and_code():
    """Doc must say \"52-event bus\" and ``EVENT_TYPES`` must have 52 entries."""
    doc = _read(ARCH_DOC)
    assert "52-event bus" in doc, "ARCHITECTURE.md must describe the bus as '52-event bus' ."
    assert "49-event bus" not in doc, "Stale '49-event bus' must not appear in ARCHITECTURE.md ."
    # And the IPC-contract section's frozen-surface count must also say 78 / 52.
    assert "78 commands / 52 events" in doc, "IPC contract section must say '78 commands / 52 events'."
    assert "75 commands / 49 events" not in doc, "Stale '75 commands / 49 events' must not appear in ARCHITECTURE.md ."

    from voice_typer.server.event_bus import EVENT_TYPES

    assert len(EVENT_TYPES) == 52, (
        f"EVENT_TYPES in voice_typer/server/event_bus.py must have 52 "
        f"entries (actual: {len(EVENT_TYPES)}). Update doc + test together."
    )


def test_gp92_capabilities_row_lists_accurate_perms():
    """The capabilities row in ARCHITECTURE.md must match main-runtime.json."""
    doc = _read(ARCH_DOC)
    caps_row_match = re.search(r"\| Capabilities \|.*?\|\s*(?P<body>[^|]+)\s*\|", doc)
    assert caps_row_match is not None, "Capabilities row not found."
    body = caps_row_match.group("body")

    # (it's part of the  finding's required text) but must NOT list
    assert "clipboard-manager" in body, (
        "capabilities row must mention clipboard-manager's removal (XE-4-4) so future readers know why it is absent."
    )
    assert "`clipboard-manager` was removed in XE-4-4" in body, (
        "capabilities row must say '`clipboard-manager` was removed in XE-4-4'."
    )
    # The granular tray perms must NOT be listed as active grants.
    for stale_tray_perm in [
        "allow-set-icon",
        "allow-set-menu",
        "allow-set-tooltip",
        "allow-set-title",
        "allow-get-by-id",
        "allow-remove-by-id",
        "allow-new",
    ]:
        assert stale_tray_perm not in body, (
            f"granular tray perm '{stale_tray_perm}' was dropped, must not be listed as an active grant."
        )

    required_claims = [
        "core:default",
        "shell:allow-spawn",
        "shell:allow-kill",
        "notification:allow-notify",
        "allow-is-permission-granted",
        "allow-request-permission",
        "dialog:allow-save",
        "dialog:allow-open",
        "core:tray:default",
        "navigator.clipboard.writeText()",
    ]
    for claim in required_claims:
        assert claim in body, f"capabilities row must mention '{claim}'. Row body: {body!r}"

    # Cross-check against the actual capability file.
    caps = json.loads(_read(MAIN_RUNTIME_CAPS))
    perms = caps["permissions"]
    expected_perms = {
        "core:default",
        "core:event:default",
        "core:window:allow-show",
        "core:window:allow-hide",
        "core:window:allow-set-focus",
        "core:window:allow-close",
        "core:window:allow-minimize",
        "core:window:allow-start-dragging",
        "core:window:allow-set-position",
        "core:window:allow-toggle-maximize",
        "shell:allow-spawn",
        "shell:allow-kill",
        "notification:allow-notify",
        "notification:allow-is-permission-granted",
        "notification:allow-request-permission",
        "dialog:allow-save",
        "dialog:allow-open",
        "core:tray:default",
    }
    assert set(perms) == expected_perms, (
        f"main-runtime.json permissions drift. Expected {sorted(expected_perms)}, got {sorted(perms)}."
    )
    # clipboard-manager must NOT be present in the file.
    assert not any("clipboard-manager" in p for p in perms), (
        "clipboard-manager permission must be absent from main-runtime.json (XE-4-4)."
    )


def test_gp93_cargo_manifest_row_does_not_mention_removed_deps():
    doc = _read(ARCH_DOC)
    cargo_row_match = re.search(r"\| Cargo manifest \|.*?\|\s*(?P<body>[^|]+)\s*\|", doc)
    assert cargo_row_match is not None, "Cargo manifest row not found."
    body = cargo_row_match.group("body")

    assert "tray-icon" in body, "row must mention tray-icon feature."
    assert "image-png" in body, "row must mention image-png feature."
    assert "config-json5" in body, "row must mention config-json5 feature."
    assert "tokio-tungstenite" in body
    assert "futures-util" in body
    assert "libc" in body
    # required text. But the row must NOT list either as an active dep
    assert "clipboard-manager" in body, "Cargo manifest row must mention clipboard-manager's removal (XE-4-4)."
    assert "enigo" in body, "Cargo manifest row must mention enigo's removal (FZ-19)."
    assert "were removed (XE-4-4 / FZ-19)" in body, "row must say 'were removed (XE-4-4 / FZ-19)'."
    assert "tauri-plugin-clipboard-manager" not in body, (
        "row must not list tauri-plugin-clipboard-manager as an active plugin."
    )

    # Cross-check against the actual Cargo.toml.
    cargo = _read(CARGO_TOML)
    # but must NOT appear as an actual dependency declaration. A real dep
    dep_pattern = re.compile(r"^tauri-plugin-clipboard-manager\s*=", re.MULTILINE)
    assert not dep_pattern.search(cargo), (
        "tauri-plugin-clipboard-manager must NOT be declared as a dependency "
        "in Cargo.toml (XE-4-4). Comments mentioning it are fine."
    )
    # Same for enigo, must not be a declared dep.
    enigo_dep_pattern = re.compile(r"^enigo\s*=", re.MULTILINE)
    assert not enigo_dep_pattern.search(cargo), "enigo must NOT be declared as a dependency in Cargo.toml (FZ-19)."
    assert 'features = ["tray-icon"' in cargo
    assert "tokio-tungstenite" in cargo
    assert "libc = " in cargo


def _parse_generate_handler() -> list[str]:
    """Return the list of Tauri commands registered in main.rs."""
    src = _read(MAIN_RS)
    m = re.search(r"generate_handler!\[(.*?)\]", src, re.DOTALL)
    assert m is not None, "generate_handler! block not found in main.rs"
    inner = m.group(1)
    # Strip // comments, strip whitespace, drop empty lines and trailing commas.
    cmds: list[str] = []
    for line in inner.split("\n"):
        line = line.split("//")[0].strip().rstrip(",")
        if line:
            cmds.append(line)
    return cmds


def test_gp94_tauri_command_count_in_doc_matches_code():
    doc = _read(ARCH_DOC)
    rust_row_match = re.search(r"\| Rust host \|.*?\|\s*(?P<body>[^|]+)\s*\|", doc)
    assert rust_row_match is not None, "Rust host row not found."
    body = rust_row_match.group("body")

    # Doc must say "24 Tauri commands: 1 generic `dispatch` + 23 typed shortcuts"
    assert "24 Tauri commands" in body, f"Rust host row must say '24 Tauri commands'. Got: {body!r}"
    assert "1 generic `dispatch`" in body
    assert "23 typed shortcuts" in body

    cmds = _parse_generate_handler()
    assert len(cmds) == 24, f"generate_handler! in main.rs must register 24 commands (actual: {len(cmds)}: {cmds})"
    assert cmds[0] == "dispatch", f"First command must be `dispatch` (actual: {cmds[0]!r})."
    # Every typed shortcut name from the frozen §16 contract must be present.
    expected_typed = {
        "shutdown_sidecar",
        "export_history",
        "export_vocabulary",
        "export_templates",
        "export_config",
        "bubble_show",
        "bubble_signal_ready",
        "bubble_set_position",
        "bubble_set_draggable",
        "bubble_move_by",
        "bubble_hide_complete",
        "bubble_dismiss",
        "bubble_resize",
        "bubble_toggle_dictation",
        "open_logs",
        "open_model_import_dialog",
        "renderer_log_error",
        "set_host_locale",
        # 2026-09-16 predecessor-parity additions (review.md MO-118 / MO-120 /
        "open_external_url_command",
        "reveal_path_command",
        "restart_sidecar",
        "renderer_heartbeat",
        "save_stats_image",
    }
    actual_typed = set(cmds[1:])
    assert actual_typed == expected_typed, (
        f"Typed shortcuts drift. Expected {sorted(expected_typed)}, got {sorted(actual_typed)}."
    )

    # Stale "ONE generic `dispatch`" must be gone.
    assert "ONE generic `dispatch`" not in body, "stale 'ONE generic `dispatch`' phrase must be removed."


def test_gp94_main_rs_line_count_is_246():
    """
    Doc claims 246 lines; main.rs must actually be 246 lines.
    C-ARCH-1 / C-TOKIO-1 / C-TAURI-2 anchors kept). Still wiring-only.
    """
    doc = _read(ARCH_DOC)
    assert "246 lines" in doc, "Doc must claim '246 lines' for main.rs."
    actual = sum(1 for _ in _read(MAIN_RS).splitlines())
    assert actual == 246, (
        f"src-tauri/src/main.rs must be 246 lines (actual: {actual}). Update the doc + this test together."
    )
    # Stale counts must NOT be in the doc.
    assert "240 lines" not in doc, "Stale '240 lines' must be removed from doc."
    assert "264 lines" not in doc, "Stale '264 lines' must be removed from doc."
    assert "488 lines" not in doc, "Stale '488 lines' must be removed from doc."
    assert "288 lines" not in doc, "Stale '288 lines' must be removed from doc."
    assert "305 lines" not in doc, "Stale '305 lines' must be removed from doc."
    assert "320 lines" not in doc, "Stale '320 lines' must be removed from doc."
    assert "325 lines" not in doc, "Stale '325 lines' must be removed from doc."
    assert "326 lines" not in doc, "Stale '326 lines' must be removed from doc."
    assert "333 lines" not in doc, "Stale '333 lines' must be removed from doc."
    assert "378 lines" not in doc, "Stale '378 lines' must be removed from doc."
    assert "279 lines" not in doc, "Stale '279 lines' must be removed from doc."
    assert "349 lines" not in doc, "Stale '349 lines' must be removed from doc."
    assert "337 lines" not in doc, "Stale '337 lines' must be removed from doc."
    assert "385 lines" not in doc, "Stale '385 lines' must be removed from doc."
    assert "329 lines" not in doc, "Stale '329 lines' must be removed from doc."
    assert "413 lines" not in doc, "Stale '413 lines' must be removed from doc."
    assert "434 lines" not in doc, "Stale '434 lines' must be removed from doc."
    assert "342 lines" not in doc, "Stale '342 lines' must be removed from doc."
    assert "330 lines" not in doc, "Stale '330 lines' must be removed from doc."


def test_gp95_module_paths_use_package_form():
    doc = _read(ARCH_DOC)
    # Stale single-file references must be gone.
    assert "`voice_typer/server/crash_handler.py`" not in doc, (
        "stale 'crash_handler.py' must be replaced with 'crash_handler/' package."
    )
    assert "`voice_typer/server/level_monitor.py`" not in doc, (
        "stale 'level_monitor.py' must be replaced with 'level_monitor/' package."
    )
    assert "`voice_typer/server/clipboard_target_safety.py`" not in doc, (
        "stale 'clipboard_target_safety.py' must be replaced with package form."
    )
    # New package-style references must be present.
    assert "`voice_typer/server/crash_handler/`" in doc
    assert "`voice_typer/server/level_monitor/`" in doc
    assert "`clipboard_target_safety/`" in doc

    # Cross-check file counts.
    crash_files = list((ROOT / "voice_typer/server/crash_handler").glob("*.py"))
    assert len(crash_files) == 8, f"crash_handler/ must be an 8-file package (actual: {len(crash_files)})."
    level_files = list((ROOT / "voice_typer/server/level_monitor").glob("*.py"))
    assert len(level_files) == 5, f"level_monitor/ must be a 5-file package (actual: {len(level_files)})."
    cts_files = list((ROOT / "voice_typer/server/clipboard_target_safety").glob("*.py"))
    assert len(cts_files) == 4, f"clipboard_target_safety/ must be a 4-file package (actual: {len(cts_files)})."


def test_gp96_shutdown_controller_entry_points_match_code():
    doc = _read(SHUTDOWN_DOC)
    # Required entry-point names per .
    for name in ["`quit()`", "`_do_cleanup()`", "`_do_fast_cleanup()`", "`_atexit_cleanup()`"]:
        assert name in doc, f"shutdown_controller.md must document {name}."
    # Stale entry-point names must be gone.
    assert "`shutdown()`" not in doc, (
        "stale `shutdown()` entry point must be removed (the primary entry point is `quit()`)."
    )
    assert "`force_shutdown()`" not in doc, (
        "stale `force_shutdown()` entry point must be removed (replaced by `_do_fast_cleanup()`)."
    )
    assert "`is_shutting_down`" not in doc or "no" in doc.lower(), (
        "doc must clarify that `is_shutting_down` is NOT a public "
        "property, the actual flag is the private `_shutting_down` attribute."
    )
    # Code-side cross-check: ShutdownController has these methods.
    from voice_typer.server.shutdown_controller import ShutdownController

    for method in ["quit", "_do_cleanup", "_do_fast_cleanup", "_atexit_cleanup"]:
        assert callable(getattr(ShutdownController, method, None)), (
            f"ShutdownController must define `{method}` (per  doc)."
        )


def test_gp97_audio_quality_controller_entry_points_match_code():
    doc = _read(AUDIO_Q_DOC)
    for name in [
        "`_on_audio_quality_chunk(rms: float, peak: float)`",
        "`_rebuild_audio_processor(force_sr: int | None = None)`",
        "`_finalize_audio_quality_report(audio: np.ndarray)`",
    ]:
        assert name in doc, f"audio_quality_controller.md must document {name}."
    # Stale entry-point names must be gone.
    for stale in [
        "`accumulate_chunk(level_data)`",
        "`reconfigure(changed_fields)`",
        "`finalize_report()`",
        "`reset()`",
    ]:
        assert stale not in doc, f"stale entry point {stale} must be removed from doc."
    # The IPC Surface section must be dropped entirely.
    assert "## IPC Surface" not in doc, (
        "audio_quality_controller.md must NOT have an IPC Surface section (controller publishes no events)."
    )
    # Code-side cross-check.
    from voice_typer.server.audio_quality_controller import AudioQualityController

    for method in ["_on_audio_quality_chunk", "_rebuild_audio_processor", "_finalize_audio_quality_report"]:
        assert callable(getattr(AudioQualityController, method, None)), (
            f"AudioQualityController must define `{method}` (per  doc)."
        )


def test_gp98_sidecar_ws_doc_is_accurate():
    doc = _read(SIDECAR_DOC)
    # Required auth phrasing per .
    assert "one-shot bearer-token auth" in doc, "sidecar_ws.md must say 'one-shot bearer-token auth'."
    assert "hmac.compare_digest" in doc, "sidecar_ws.md must mention hmac.compare_digest (constant-time)."
    assert "NOT an HMAC scheme" in doc, "sidecar_ws.md must explicitly say 'NOT an HMAC scheme'."
    # Stale phrasing must be gone.
    assert "HMAC auth handshake" not in doc, "stale 'HMAC auth handshake' must be removed."
    # Entry-point signatures per .
    assert "`run(server: IPCServer) -> int`" in doc
    assert "`_emit_server_started(port: int, protocol: int | None = None)`" in doc
    assert "`_authenticate(websocket) -> bool`" in doc

    # Code-side cross-check: the functions exist with the right signatures.
    import inspect

    from voice_typer.server import sidecar_ws

    # `run` is module-level.
    sig_run = inspect.signature(sidecar_ws.run)
    assert list(sig_run.parameters) == ["server"], f"sidecar_ws.run signature must be (server) (actual: {sig_run})."
    sig_emit = inspect.signature(sidecar_ws._emit_server_started)
    assert list(sig_emit.parameters) == ["port", "protocol"], (
        f"_emit_server_started signature must be (port, protocol=None) (actual: {sig_emit})."
    )
    sig_auth = inspect.signature(sidecar_ws._authenticate)
    assert list(sig_auth.parameters) == ["websocket"], (
        f"_authenticate signature must be (websocket) (actual: {sig_auth})."
    )


def test_timer_coordinator_doc_matches_code():
    doc = _read(TIMER_DOC)
    assert "`_schedule_timer(delay: float, func) -> threading.Thread`" in doc
    assert "`_cancel_pending_timers()`" in doc

    from voice_typer.server.timer_coordinator import TimerCoordinator

    for method in ["_schedule_timer", "_cancel_pending_timers"]:
        assert callable(getattr(TimerCoordinator, method, None)), f"TimerCoordinator must define `{method}`."


def test_volume_controller_doc_matches_code():
    doc = _read(VOLUME_DOC)
    assert "`_on_volume_crash_restore(state)`" in doc
    assert "`_duck_volume()`" in doc
    assert "`_restore_volume(fade_ms: int | None = None)`" in doc

    from voice_typer.server.volume_controller import VolumeController

    for method in ["_on_volume_crash_restore", "_duck_volume", "_restore_volume"]:
        assert callable(getattr(VolumeController, method, None)), f"VolumeController must define `{method}`."


def test_index_lists_all_five_module_docs():
    doc = _read(INDEX_DOC)
    for name in [
        "shutdown_controller",
        "audio_quality_controller",
        "sidecar_ws",
        "timer_coordinator",
        "volume_controller",
    ]:
        assert f"`{name}`" in doc, f"_index.md must list the `{name}` module."
        # The actual file must exist.
        assert (ROOT / "docs" / "modules" / f"{name}.md").exists(), f"docs/modules/{name}.md must exist."


# TCP transport removed: the error-envelope contract test that pinned


def test_prewarm_resolver_module_deleted_per_plan_p1():
    """Per ``plan-runtime-pack-split.md`` §6.2 Option P-1 (Decision §6.3),"""
    prewarm_resolver_py = ROOT / "voice_typer" / "server" / "prewarm_resolver.py"
    assert not prewarm_resolver_py.is_file(), (
        f"{prewarm_resolver_py} must NOT exist, it was deleted per "
        "plan-runtime-pack-split.md §6.2 P-1 (prewarm is now a startup "
        "phase of voice_typer/worker/__main__.py, and the Tauri build no "
        "longer bundles a prewarm-<triple>[.exe]). See ADR-0011 "
        "(Status: Superseded)."
    )
    # The worker module, which absorbed the prewarm startup phase —
    worker_main = ROOT / "voice_typer" / "worker" / "__main__.py"
    assert worker_main.is_file(), (
        f"{worker_main} must exist, it is the worker exe entry point "
        "that absorbed prewarm as a startup phase per plan-runtime-pack-"
        "split.md §6.2 P-1."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
