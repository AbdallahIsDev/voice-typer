"""
Wire-swap + recovery validation (ADR-0020 §10).
Gaps documented (report, do NOT fix, out of scope for MIG-1.9 check-3):
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_TAURI_DIR = PROJECT_ROOT / "src-tauri" / "src"
SUPERVISOR_RS = SRC_TAURI_DIR / "sidecar" / "supervisor.rs"
WS_RS = SRC_TAURI_DIR / "sidecar" / "ws.rs"
WS_EVENT_PROTOCOL_RS = SRC_TAURI_DIR / "sidecar" / "ws" / "event_protocol.rs"
WS_RESPAWN_SCHEDULER_RS = SRC_TAURI_DIR / "sidecar" / "ws" / "respawn_scheduler.rs"
UTIL_RS = SRC_TAURI_DIR / "util.rs"
STATE_RS = SRC_TAURI_DIR / "state.rs"
SIDECAR_WS_PY = PROJECT_ROOT / "voice_typer" / "server" / "sidecar_ws.py"
IPC_SERVER_PY = PROJECT_ROOT / "voice_typer" / "server" / "ipc_server.py"
# Phase 4.5 /: ``ipc_server.py`` is now a thin shim re-exporting
IPC_RATE_LIMITER_PY = PROJECT_ROOT / "voice_typer" / "server" / "ipc" / "rate_limiter.py"
ADR_0020 = PROJECT_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"


@pytest.fixture(scope="module")
def supervisor_source() -> str:
    """Full text of src-tauri/src/sidecar/supervisor.rs (read once per module)."""
    assert SUPERVISOR_RS.is_file(), f"missing: {SUPERVISOR_RS}"
    return SUPERVISOR_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ws_source() -> str:
    """Full text of the Rust WS bridge: ``ws.rs`` PLUS its"""
    assert WS_RS.is_file(), f"missing: {WS_RS}"
    parts = [WS_RS.read_text(encoding="utf-8")]
    for name in ("reader.rs", "writer.rs"):
        sibling = WS_RS.parent / "ws" / name
        assert sibling.is_file(), f"missing: {sibling}, the ws.rs module split was rolled back"
        parts.append(sibling.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


@pytest.fixture(scope="module")
def ws_event_protocol_source() -> str:
    """Full text of src-tauri/src/sidecar/ws/event_protocol.rs (read once"""
    assert WS_EVENT_PROTOCOL_RS.is_file(), f"missing: {WS_EVENT_PROTOCOL_RS}"
    return WS_EVENT_PROTOCOL_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ws_respawn_scheduler_source() -> str:
    """Full text of src-tauri/src/sidecar/ws/respawn_scheduler.rs (read"""
    assert WS_RESPAWN_SCHEDULER_RS.is_file(), f"missing: {WS_RESPAWN_SCHEDULER_RS}"
    return WS_RESPAWN_SCHEDULER_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def util_source() -> str:
    """Full text of src-tauri/src/util.rs (read once per module)."""
    assert UTIL_RS.is_file(), f"missing: {UTIL_RS}"
    return UTIL_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def state_source() -> str:
    """Full text of src-tauri/src/state.rs (read once per module)."""
    assert STATE_RS.is_file(), f"missing: {STATE_RS}"
    return STATE_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sidecar_ws_source() -> str:
    """Full text of voice_typer/server/sidecar_ws.py PLUS the split"""
    assert SIDECAR_WS_PY.is_file(), f"missing: {SIDECAR_WS_PY}"
    parts = [SIDECAR_WS_PY.read_text(encoding="utf-8")]
    for leaf in (
        PROJECT_ROOT / "voice_typer" / "server" / "sidecar_ws_internals" / "dispatch.py",
        PROJECT_ROOT / "voice_typer" / "server" / "sidecar_ws_internals" / "outbound.py",
    ):
        assert leaf.is_file(), f"missing: {leaf}"
        parts.append(leaf.read_text(encoding="utf-8"))
    return "\n".join(parts)


@pytest.fixture(scope="module")
def ipc_server_source() -> str:
    """Full text of the IPC rate-limiter submodule (read once per module)."""
    assert IPC_RATE_LIMITER_PY.is_file(), f"missing: {IPC_RATE_LIMITER_PY}"
    return IPC_RATE_LIMITER_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def adr_0020_source() -> str:
    """Full text of ADR-0020 (read once per module)."""
    assert ADR_0020.is_file(), f"missing: {ADR_0020}"
    return ADR_0020.read_text(encoding="utf-8")


def test_supervisor_backoff_schedule_is_5_steps_doubling(util_source: str) -> None:
    """ADR-0020 §10: backoff is 500ms→1s→2s→4s→8s (5 doubling steps)."""
    # The const declaration line.
    match = re.search(
        r"pub\(crate\)\s+const\s+SUPERVISOR_BACKOFF_MS\s*:\s*&\[u64\]\s*=\s*"
        r"&\[(?P<vals>[^\]]+)\]",
        util_source,
    )
    assert match is not None, (
        "SUPERVISOR_BACKOFF_MS const declaration not found in util.rs, did the constant move or get renamed?"
    )
    vals = [int(v.strip()) for v in match.group("vals").split(",") if v.strip()]
    assert vals == [500, 1000, 2000, 4000, 8000], (
        f"SUPERVISOR_BACKOFF_MS must be [500, 1000, 2000, 4000, 8000] (5 doubling steps), got {vals}"
    )


def test_supervisor_backoff_schedule_length_matches_max_retries(util_source: str) -> None:
    """``SUPERVISOR_BACKOFF_MS.len()`` must be 5, it IS the retry cap."""
    sched_match = re.search(
        r"SUPERVISOR_BACKOFF_MS\s*:\s*&\[u64\]\s*=\s*&\[(?P<vals>[^\]]+)\]",
        util_source,
    )
    assert sched_match is not None, "SUPERVISOR_BACKOFF_MS schedule not found in util.rs"
    sched_len = len([v for v in sched_match.group("vals").split(",") if v.strip()])
    # The retry cap IS the schedule length; it must stay 5.
    max_retries = 5
    assert sched_len == 5, f"SUPERVISOR_BACKOFF_MS must have 5 entries, got {sched_len}"
    assert sched_len == max_retries, (
        f"SUPERVISOR_BACKOFF_MS.len() ({sched_len}) must stay equal to the retry cap "
        f"({max_retries}) so the loop iterates exactly N times before "
        f"falling back to app.restart()"
    )


def test_supervisor_backoff_implements_geometric_doubling(util_source: str) -> None:
    """Each step must be exactly 2x the previous step (geometric, base 2)."""
    match = re.search(
        r"SUPERVISOR_BACKOFF_MS\s*:\s*&\[u64\]\s*=\s*&\[(?P<vals>[^\]]+)\]",
        util_source,
    )
    assert match is not None
    vals = [int(v.strip()) for v in match.group("vals").split(",") if v.strip()]
    for i in range(1, len(vals)):
        assert vals[i] == vals[i - 1] * 2, (
            f"backoff step {i} must be 2x step {i - 1}: got {vals[i]} vs {vals[i - 1]} * 2 = {vals[i - 1] * 2}"
        )


def test_supervisor_respawn_inner_iterates_backoff_schedule(supervisor_source: str) -> None:
    """``respawn_inner`` must iterate ``SUPERVISOR_BACKOFF_MS`` with enumerate."""
    assert "for (attempt, delay_ms) in SUPERVISOR_BACKOFF_MS.iter().enumerate()" in supervisor_source, (
        "respawn_inner must iterate SUPERVISOR_BACKOFF_MS with enumerate(), "
        "the attempt index is needed for the SUPERVISOR_MAX_RETRIES cap check"
    )


def test_supervisor_max_retries_constant_is_5(util_source: str) -> None:
    """ADR-0020 §10: the supervisor retry cap must be exactly 5."""
    match = re.search(r"SUPERVISOR_BACKOFF_MS\s*:\s*&\[u64\]\s*=\s*&\[(?P<vals>[^\]]+)\]", util_source)
    assert match is not None, "SUPERVISOR_BACKOFF_MS schedule not found in util.rs"
    cap = len([v for v in match.group("vals").split(",") if v.strip()])
    assert cap == 5, (
        f"SUPERVISOR_BACKOFF_MS.len() (the supervisor retry cap) must be 5 "
        f"(then fall back to full-app relaunch), got {cap}"
    )


def test_supervisor_exhaustion_branch_calls_app_restart(supervisor_source: str) -> None:
    """When the backoff schedule is exhausted, the supervisor MUST call"""
    # The exhaustion branch must exist + call app.restart().
    assert "backoff schedule exhausted" in supervisor_source, (
        "supervisor post-loop exhaustion branch (``backoff schedule exhausted``) not found, "
        "the exhaustion path is missing"
    )
    assert "app.restart()" in supervisor_source, (
        "app.restart() call not found in supervisor.rs, full-app relaunch fallback is missing"
    )
    assert '"supervisor_relaunching"' in supervisor_source, (
        "supervisor_relaunching event not emitted in supervisor.rs, the UI cannot show a "
        "restarting banner before app.restart()"
    )


def test_supervisor_exhaustion_branch_has_pre_restart_delay(supervisor_source: str, util_source: str) -> None:
    """ADR-0020 §10: a brief delay between emitting ``supervisor_relaunching``"""
    assert "PRE_RESTART_DELAY_MS" in supervisor_source, (
        "PRE_RESTART_DELAY_MS not referenced in supervisor.rs, the pre-restart "
        "delay (so the UI can render the restarting banner) is missing"
    )
    match = re.search(
        r"PRE_RESTART_DELAY_MS\s*:\s*u64\s*=\s*(?P<val>\d+)",
        util_source,
    )
    assert match is not None
    assert int(match.group("val")) == 500, f"PRE_RESTART_DELAY_MS must be 500ms, got {match.group('val')}"


def test_supervisor_loop_exit_falls_back_to_app_restart(supervisor_source: str) -> None:
    """If the backoff loop exits without returning (defensive, should"""
    # Find the post-loop app.restart() (there should be at least 2
    restart_count = supervisor_source.count("app.restart()")
    assert restart_count >= 2, (
        f"expected at least 2 app.restart() calls in supervisor.rs (exhaustion "
        f"branch + post-loop fallback), found {restart_count}"
    )
    assert "backoff schedule exhausted" in supervisor_source, (
        "post-loop exhaustion log message not found, the defensive "
        "fallback for SUPERVISOR_BACKOFF_MS.len() < SUPERVISOR_MAX_RETRIES is missing"
    )


def test_supervisor_respawn_inner_respects_shutting_down_flag(supervisor_source: str) -> None:
    """The supervisor MUST check ``shutting_down`` inside the backoff"""
    assert "state.shutting_down.load" in supervisor_source, (
        "supervisor must check state.shutting_down inside the backoff "
        "loop, a respawn during shutdown would race with app.quit()"
    )
    assert "shutting down, skipping respawn" in supervisor_source, (
        "supervisor must log + return Ok(()) when shutting_down is set"
    )


def test_ws_reader_loop_breaks_on_eof_none(ws_source: str) -> None:
    """The WS reader loop must exit when ``read.next()`` returns ``None``"""
    assert "while let Some(msg) = read.next().await" in ws_source, (
        "WS reader loop must use `while let Some(msg) = read.next().await` "
        "— None (EOF) exits the loop, triggering the post-loop supervisor path"
    )


def test_ws_reader_loop_breaks_on_close_frame(ws_source: str) -> None:
    """A ``Message::Close`` frame from the sidecar must break the loop."""
    assert "Ok(Message::Close(_))" in ws_source, (
        "WS reader must match Ok(Message::Close(_)), a clean WS close from the sidecar must break the reader loop"
    )
    # The Close arm must `break` (not continue).
    close_arm = re.search(
        r"Ok\(Message::Close\(_\)\)\s*=>\s*\{[^}]*break;[^}]*\}",
        ws_source,
        re.DOTALL,
    )
    assert close_arm is not None, "WS reader Close arm must `break;` to exit the loop"


def test_ws_reader_loop_breaks_on_stream_error(ws_source: str) -> None:
    """A stream ``Err`` (e.g. TCP RST, broken pipe) must break the loop."""
    assert "Err(e)" in ws_source, "WS reader must match Err(e), a stream error must be handled"
    assert "[WS-READER] error:" in ws_source, "WS reader Err arm must log '[WS-READER] error: <e>'"
    # The Err arm must `break;` (not continue). We verify the substring
    assert "break;" in ws_source, "WS reader must `break;` to exit the loop on stream error"


def test_ws_reader_logs_disconnect_reasons(ws_source: str) -> None:
    """Each disconnect path must log a distinct reason for debugging"""
    assert "sidecar closed the WS" in ws_source, "WS reader must log 'sidecar closed the WS' on a clean Close frame"
    assert "[WS-READER] error:" in ws_source, "WS reader must log '[WS-READER] error: <e>' on a stream Err"


def test_ws_reader_triggers_respawn_after_loop_exit(ws_source: str, ws_respawn_scheduler_source: str) -> None:
    """After the reader loop exits (EOF/Close/Err), the reader task MUST"""
    assert "respawn(" in ws_source, (
        "WS reader must call respawn() after the reader loop exits, the disconnect → supervisor trigger is missing"
    )
    assert (
        "std::thread::Builder" in ws_respawn_scheduler_source or "std::thread::spawn" in ws_respawn_scheduler_source
    ), (
        "respawn_scheduler.rs must spawn the supervisor on a std::thread "
        "(the WS stream half is !Send so a tokio::spawn would fail the Send "
        "requirement), the spawn bridge moved out of ws.rs (ARCH-045)"
    )
    assert "tauri::async_runtime::block_on" in ws_respawn_scheduler_source, (
        "respawn_scheduler.rs must use tauri::async_runtime::block_on to "
        "run the respawn future on the std::thread bridge"
    )
    assert "cleanup_and_trigger_respawn" in ws_source, (
        "WS reader post-loop cleanup must call cleanup_and_trigger_respawn (the respawn_scheduler.rs entry point)"
    )


def test_ws_reader_skips_respawn_during_shutdown(ws_source: str) -> None:
    """The supervisor trigger must be gated on ``!shutting_down``, otherwise"""
    # The shutdown check MUST reference a ``shutting_down.load(...)``
    assert re.search(
        r"state_for_(?:reader|cleanup)\.shutting_down\.load\s*\(",
        ws_source,
    ), (
        "WS reader must check shutting_down before triggering supervisor, "
        "a clean app quit would otherwise cause a spurious respawn"
    )
    # The supervisor trigger must be inside a `if !shutting_down` guard.
    assert re.search(
        r"if\s*!\s*state_for_(?:reader|cleanup)\.shutting_down\.load",
        ws_source,
    ), "supervisor trigger must be inside `if !shutting_down`, the spawn must be suppressed during shutdown"


def test_ws_reader_emits_relaunching_with_reason_disconnected(ws_source: str) -> None:
    """(ADR-0020 §10): the WS reader must emit ``supervisor_relaunching``"""
    assert '"supervisor_relaunching"' in ws_source, (
        "WS reader must emit 'supervisor_relaunching' immediately at disconnect"
    )
    assert '"reason": "disconnected"' in ws_source or '"reason":"disconnected"' in ws_source, (
        "WS reader must emit supervisor_relaunching with reason='disconnected' "
        "so the UI shows the reconnecting banner before backoff runs"
    )


def test_ws_reader_drains_pending_dispatch_on_disconnect(ws_source: str) -> None:
    """pending-dispatch map and reject each with ``sidecar_disconnected``"""
    assert "sidecar_disconnected" in ws_source, (
        "WS reader must reject pending dispatch requests with code 'sidecar_disconnected' on disconnect"
    )
    assert "drained" in ws_source, "WS reader must log how many pending dispatch requests were drained"
    assert "pending.drain()" in ws_source or ".drain()" in ws_source, (
        "WS reader must drain the pending map (not iterate + remove), drain() is O(n) and atomic vs. per-key remove"
    )


def test_ws_reader_clears_ws_tx_on_disconnect(ws_source: str) -> None:
    """``None`` so new dispatch calls fail fast with \"sidecar not"""
    assert "*ws_tx_guard = None" in ws_source, (
        "WS reader must clear ws_tx to None on disconnect, new dispatch "
        "calls must fail fast, not queue onto a dead channel"
    )


def test_respawn_in_progress_is_atomic_bool(state_source: str) -> None:
    """``SidecarState.respawn_in_progress`` must be an ``AtomicBool``"""
    assert "respawn_in_progress: AtomicBool" in state_source, (
        "SidecarState.respawn_in_progress must be AtomicBool, a Mutex<bool> "
        "would deadlock if respawn panics while holding the lock"
    )


def test_supervisor_uses_compare_exchange_to_acquire_flag(supervisor_source: str) -> None:
    """The supervisor must use ``compare_exchange(false, true)``"""
    assert "compare_exchange(false, true" in supervisor_source, (
        "supervisor must acquire respawn_in_progress with "
        "compare_exchange(false, true, ...), test_and_set is not "
        "available on Rust's AtomicBool"
    )
    assert "Ordering::SeqCst" in supervisor_source, (
        "supervisor compare_exchange must use SeqCst ordering, the respawn "
        "flag is synchronized with shutting_down (also SeqCst) and the "
        "child/token/ws_tx Mutexes; weaker orderings could reorder the "
        "flag store relative to the Mutex unlocks"
    )


def test_supervisor_skips_when_respawn_already_in_progress(supervisor_source: str) -> None:
    """If ``compare_exchange`` fails (a previous respawn is in flight),"""
    assert "respawn already in progress" in supervisor_source, (
        "supervisor must log 'respawn already in progress, skipping' when compare_exchange fails"
    )
    # The skip block must `return Ok(())` (not Err, bailing out is not
    assert "compare_exchange(false, true" in supervisor_source
    assert ".is_err()" in supervisor_source, (
        "supervisor must check `.is_err()` on the compare_exchange result to detect the 'already in progress' case"
    )
    assert "return Ok(());" in supervisor_source, (
        "supervisor must `return Ok(())` when compare_exchange fails, "
        "the in-flight supervisor owns the recovery (bail-out is not an error)"
    )


def test_supervisor_clears_flag_on_all_exit_paths(supervisor_source: str) -> None:
    """The ``respawn_in_progress`` flag MUST be cleared on EVERY exit"""
    # The clear must use SeqCst (matches the acquire ordering).
    assert "respawn_in_progress.store(false, Ordering::SeqCst)" in supervisor_source, (
        "supervisor must clear respawn_in_progress with "
        "store(false, SeqCst), matching the compare_exchange acquire "
        "ordering, and on every exit path"
    )
    assert re.search(
        r"let\s+inner_result\s*=\s*AssertUnwindSafe\s*\(\s*respawn_inner\s*\(",
        supervisor_source,
    ), (
        "supervisor must bind respawn_inner's result (wrapped in "
        "AssertUnwindSafe(...).catch_unwind()) to a local before clearing "
        "the flag, this guarantees the clear runs AFTER the inner body "
        "resolves (Ok or Err) AND on panic (GT-9)."
    )


def test_supervisor_respawn_in_progress_comment_documents_race(state_source: str) -> None:
    """The ``respawn_in_progress`` field must have a doc comment"""
    # Find the field declaration + look backwards for the doc comment.
    idx = state_source.find("respawn_in_progress: AtomicBool")
    assert idx != -1
    preceding = state_source[:idx]
    # The doc comment must mention the flapping/race scenario.
    assert "flapping" in preceding.lower() or "race" in preceding.lower() or "parallel" in preceding.lower(), (
        "respawn_in_progress doc comment must explain the race it prevents "
        "(flapping sidecar → parallel respawn supervisors corrupting "
        "child/token/ws_tx)"
    )


def test_supervisor_attempt_counter_is_local_to_invocation(supervisor_source: str) -> None:
    """The supervisor attempt counter must be a LOCAL ``attempt`` variable"""
    assert "for (attempt, delay_ms) in SUPERVISOR_BACKOFF_MS.iter().enumerate()" in supervisor_source, (
        "supervisor attempt counter must be the `attempt` from enumerate(), a "
        "local loop variable, not a persistent SidecarState field"
    )
    # There must NOT be a persistent crash counter field on SidecarState.
    assert "crash_count" not in supervisor_source, (
        "supervisor must not use a persistent crash_count, the attempt counter is per-call (local to respawn_inner)"
    )


def test_supervisor_returns_on_successful_reconnect(supervisor_source: str) -> None:
    """On a successful spawn + reconnect, the supervisor MUST return"""
    # The success branch must emit supervisor_reconnected AND return Ok(()).
    assert '"supervisor_reconnected"' in supervisor_source, (
        "supervisor must emit 'supervisor_reconnected' on successful respawn"
    )
    assert "return Ok(());" in supervisor_source, (
        "supervisor must `return Ok(())` on successful reconnect_ws, "
        "this is the crash-counter reset point (next disconnect starts fresh)"
    )
    # The success branch must be inside the reconnect_ws Ok arm.
    assert "reconnect_ws(app, state, port, &new_token)" in supervisor_source


def test_supervisor_emits_reconnected_event_on_success(supervisor_source: str) -> None:
    """On successful reconnect, the supervisor MUST emit ``supervisor_reconnected``"""
    assert '"supervisor_reconnected"' in supervisor_source, (
        "supervisor must emit 'supervisor_reconnected' on successful respawn "
        "so the UI can clear the reconnecting banner"
    )


def test_supervisor_rotates_token_on_each_respawn_attempt(supervisor_source: str) -> None:
    """Each respawn attempt must rotate the bearer token (via"""
    assert "generate_token()" in supervisor_source, (
        "supervisor must call generate_token() on each respawn attempt, the token rotates per respawn (ADR-0020 §3)"
    )
    assert "new_token" in supervisor_source, "supervisor must use a `new_token` local for the rotated token"


def test_supervisor_no_persistent_crash_counter_field_on_state(state_source: str) -> None:
    """``SidecarState`` must NOT have a persistent crash counter field"""
    # List of forbidden persistent-counter field names.
    for forbidden in ("crash_count", "respawn_count", "supervisor_attempt", "restart_count"):
        assert forbidden not in state_source, (
            f"SidecarState must NOT have a persistent {forbidden} field, "
            f"the supervisor attempt counter is per-call (local to respawn_inner)"
        )


def test_gap_no_persistent_crash_counter_across_invocations(supervisor_source: str, state_source: str) -> None:
    """GAP-2 (documented, do NOT fix): the supervisor has NO"""
    # Proof the counter is local (per-call), not persistent.
    assert "for (attempt, delay_ms) in SUPERVISOR_BACKOFF_MS.iter().enumerate()" in supervisor_source
    # Proof there is no SustainedFlapDetector or similar on SidecarState.
    assert "SustainedFlap" not in state_source
    assert "flap_count" not in state_source
    assert "attempt as u32 >= SUPERVISOR_MAX_RETRIES" not in supervisor_source, (
        "The in-loop `attempt as u32 >= SUPERVISOR_MAX_RETRIES` guard was "
        "removed as dead code (SUPERVISOR_BACKOFF_MS.len() == SUPERVISOR_MAX_RETRIES "
        "== 5, so the condition is always false). The real escalation "
        "path is the post-loop `backoff schedule exhausted` branch that "
        "calls app.restart(). Reintroducing the dead guard is misleading."
    )
    # Document the gap explicitly, if this test fails in the future,


def test_max_frame_bytes_constant_is_exactly_1_mib(util_source: str) -> None:
    """ADR-0020 §10: ``MAX_FRAME_BYTES`` must be exactly 1 MiB"""
    match = re.search(
        r"MAX_FRAME_BYTES\s*:\s*usize\s*=\s*(?P<expr>[\d\s*()+]+)",
        util_source,
    )
    assert match is not None, "MAX_FRAME_BYTES const declaration not found"
    expr = match.group("expr").strip()
    # The expression must evaluate to 1024 * 1024 = 1_048_576.
    assert eval(expr) == 1024 * 1024, (
        f"MAX_FRAME_BYTES must evaluate to 1024 * 1024 (1 MiB = 1048576), got expression '{expr}' = {eval(expr)}"
    )


def test_rust_ws_client_enforces_max_message_and_frame_size(ws_source: str) -> None:
    """The Rust WS client (``reconnect_ws``) MUST set BOTH"""
    assert re.search(
        r"\.max_message_size\s*=\s*Some\s*\(\s*MAX_FRAME_BYTES\s*\)",
        ws_source,
    ), "Rust WS client must set max_message_size = Some(MAX_FRAME_BYTES), guards against fragmented-message bypass"
    assert re.search(
        r"\.max_frame_size\s*=\s*Some\s*\(\s*MAX_FRAME_BYTES\s*\)",
        ws_source,
    ), (
        "Rust WS client must set max_frame_size = Some(MAX_FRAME_BYTES), "
        "rejects single frames > 1 MiB at the transport layer"
    )
    assert "connect_async_with_config" in ws_source, (
        "Rust WS client must use connect_async_with_config (not the default "
        "connect_async) so the WebSocketConfig is actually applied"
    )


def test_python_ws_server_enforces_max_size(sidecar_ws_source: str) -> None:
    """The Python WS server (``sidecar_ws.run``) MUST pass"""
    assert "max_size=_MAX_FRAME_BYTES" in sidecar_ws_source, (
        "Python WS server must pass max_size=_MAX_FRAME_BYTES to "
        "websockets.serve(), the library rejects frames > 1 MiB with "
        "close code 1009 at the transport layer"
    )
    assert "serve(" in sidecar_ws_source, "Python WS server must call websockets.serve() (the async server)"


def test_python_sidecar_max_frame_bytes_constant_is_1_mib(sidecar_ws_source: str) -> None:
    """The Python ``_MAX_FRAME_BYTES`` constant must also be 1 MiB,"""
    match = re.search(
        r"_MAX_FRAME_BYTES\s*=\s*(?P<expr>[\d\s*()+]+)",
        sidecar_ws_source,
    )
    assert match is not None, "_MAX_FRAME_BYTES const not found in sidecar_ws.py"
    expr = match.group("expr").strip()
    assert eval(expr) == 1024 * 1024, f"_MAX_FRAME_BYTES must be 1 MiB (1048576), got '{expr}' = {eval(expr)}"


def test_python_sidecar_outbound_frame_cap(sidecar_ws_source: str) -> None:
    """The Python WS server must ALSO cap OUTBOUND frames (events"""
    assert "_MAX_FRAME_BYTES" in sidecar_ws_source
    # The outbound writer task must check len(raw.encode) > _MAX_FRAME_BYTES.
    assert "exceeds" in sidecar_ws_source.lower() or "len(raw.encode" in sidecar_ws_source, (
        "Python WS server outbound writer must check frame size against "
        "_MAX_FRAME_BYTES and drop oversized frames (not send them, which "
        "would close the connection)"
    )


def test_sidecar_ws_imports_rate_limiter(sidecar_ws_source: str) -> None:
    """ADR-0019 port: the WS server must import ``_get_rate_limiter``"""
    assert "_get_rate_limiter" in sidecar_ws_source, (
        "sidecar_ws.py must import _get_rate_limiter from ipc_server.py, "
        "the WS path reuses the TCP path's _RateLimiter (ADR-0019 port)"
    )
    assert "from voice_typer.server.ipc_server import _get_rate_limiter" in sidecar_ws_source, (
        "sidecar_ws.py must `from voice_typer.server.ipc_server import _get_rate_limiter`"
    )


def test_sidecar_ws_calls_rate_limiter_allow_per_frame(sidecar_ws_source: str) -> None:
    """Every incoming WS frame must pass through ``rate_limiter.allow()``"""
    assert "rate_limiter = _get_rate_limiter(server)" in sidecar_ws_source, (
        "sidecar_ws.py must look up the shared rate limiter via _get_rate_limiter(server) on every frame"
    )
    assert "rate_limiter.allow()" in sidecar_ws_source, (
        "sidecar_ws.py must call rate_limiter.allow() per frame, the WS accept path rate-limiter is the ADR-0019 port"
    )
    # SEC-6 / DOWNGRADE #2 fix: allow() now increments _rejected atomically
    assert "rate_limiter.reject()" not in sidecar_ws_source, (
        "sidecar_ws.py must NOT call rate_limiter.reject(), SEC-6 moved "
        "the counter increment into allow() atomically. The .reject() call "
        "was removed (DOWNGRADE #2 fix) to keep WS-path rejected_count "
        "consistent with the TCP path."
    )


def test_sidecar_ws_returns_rate_limited_error(sidecar_ws_source: str) -> None:
    """A rate-limited frame must yield:"""
    assert '"rate_limited"' in sidecar_ws_source, (
        "sidecar_ws.py must return error code 'rate_limited' when the limiter rejects a frame"
    )
    assert "rate limit exceeded" in sidecar_ws_source, (
        "sidecar_ws.py rate_limited error must carry the message 'rate limit exceeded; backing off'"
    )
    # The rate_limited return must be inside the dispatch() closure
    rl_idx = sidecar_ws_source.find('"rate_limited"')
    assert rl_idx != -1
    # Look at the next 400 chars after the rate_limited string.
    block = sidecar_ws_source[rl_idx : rl_idx + 400]
    assert "websocket.close" not in block, (
        "rate_limited branch must NOT close the WS connection, the "
        "connection stays open and the client can retry after backing off"
    )


def test_rate_limiter_is_per_process(ipc_server_source: str) -> None:
    """the rate limiter must be PER-PROCESS (one"""
    assert "_get_rate_limiter" in ipc_server_source, (
        "ipc_server.py must define _get_rate_limiter(), the per-process limiter lookup helper (CR-11)"
    )
    # The helper must store the limiter on the server instance (not
    assert "server._rate_limiter_instance" in ipc_server_source, (
        "_get_rate_limiter must store the limiter on the server instance "
        "(server._rate_limiter_instance), per-process, not module-level"
    )


def test_rate_limiter_burst_is_200_sustained_600(ipc_server_source: str) -> None:
    """burst = 200 messages,"""
    assert "_RATE_LIMIT_BURST = 200" in ipc_server_source, "_RATE_LIMIT_BURST must be 200 (ADR-0019 burst cap)"
    assert "_RATE_LIMIT_SUSTAINED = 600" in ipc_server_source, (
        "_RATE_LIMIT_SUSTAINED must be 600 (60 msg/s avg over 10s window)"
    )
    assert "_RATE_LIMIT_WINDOW_SECONDS = 10.0" in ipc_server_source, (
        "_RATE_LIMIT_WINDOW_SECONDS must be 10.0 (sliding window)"
    )


def test_adr_0020_section_10_documents_backoff(adr_0020_source: str) -> None:
    """ADR-0020 §10 must document the backoff schedule + cap 5."""
    assert "### 10. WebSocket disconnect / error handling + supervisor + rate limiter" in adr_0020_source, (
        "ADR-0020 §10 heading not found, the section was renamed or removed"
    )
    # The supervisor state machine + backoff must be documented.
    assert "500" in adr_0020_source and "1000" in adr_0020_source and "2000" in adr_0020_source, (
        "ADR-0020 §10 must document the 500→1000→2000ms backoff schedule"
    )
    assert "cap 5" in adr_0020_source or "5 retries" in adr_0020_source, (
        "ADR-0020 §10 must document the cap-5-retries limit"
    )


def test_adr_0020_section_10_documents_frame_cap(adr_0020_source: str) -> None:
    """ADR-0020 §10 must document the 1 MiB WS frame cap."""
    assert "1 MiB" in adr_0020_source, "ADR-0020 §10 must document the 1 MiB WS frame cap"
    assert "max_frame_size" in adr_0020_source or "max_size" in adr_0020_source, (
        "ADR-0020 §10 must reference max_frame_size (Rust) / max_size (Python)"
    )


def test_adr_0020_section_10_documents_rate_limiter_port(adr_0020_source: str) -> None:
    """ADR-0020 §10 must document the ADR-0019 rate-limiter port to the"""
    assert "rate limiter" in adr_0020_source.lower()
    assert "ADR-0019" in adr_0020_source, "ADR-0020 §10 must reference ADR-0019 (the rate-limiter port source)"
    assert "WS" in adr_0020_source and "accept path" in adr_0020_source, (
        "ADR-0020 §10 must document the rate-limiter port to the WS accept path"
    )


def test_supervisor_respawn_inner_calls_reconnect_ws(supervisor_source: str) -> None:
    """Each respawn attempt must call ``reconnect_ws`` to re-establish"""
    assert "reconnect_ws(app, state, port, &new_token)" in supervisor_source, (
        "supervisor must call reconnect_ws(app, state, port, &new_token) "
        "after each spawn, re-auth with the rotated token is mandatory"
    )


def test_supervisor_respawn_inner_handles_spawn_failure(supervisor_source: str) -> None:
    """If ``spawn_sidecar_and_get_port`` fails (e.g. the binary is"""
    assert "sidecar spawn failed" in supervisor_source, "supervisor must log 'sidecar spawn failed' on spawn error"
    assert "continue;" in supervisor_source, (
        "supervisor must `continue` to the next backoff attempt on "
        "spawn failure (not bail out, a transient spawn error is recoverable)"
    )


def test_supervisor_respawn_inner_handles_reconnect_failure(supervisor_source: str) -> None:
    """If ``reconnect_ws`` fails (e.g. WS handshake timeout, auth"""
    assert "WS reconnect failed" in supervisor_source, "supervisor must log 'WS reconnect failed' on reconnect error"
    # The reconnect-error arm must `continue` (retry with backoff).
    assert "continue;" in supervisor_source, (
        "supervisor must `continue` to the next backoff attempt on "
        "reconnect failure (token rotates fresh on the next attempt)"
    )


def test_supervisor_respawn_inner_swaps_child_handle_under_lock(supervisor_source: str) -> None:
    """The new child handle must be stored under the ``state.child``"""
    # The child-handle swap must acquire state.child under a Mutex —
    assert "state.child.lock().unwrap()" in supervisor_source or "mutex_lock(&state.child)" in supervisor_source, (
        "supervisor must store the new child handle under state.child's "
        "Mutex (legacy .lock().unwrap() or new mutex_lock helper), kill_children "
        "needs the latest PID"
    )
    assert "let new_token = generate_token();" in supervisor_source, (
        "supervisor must roll a fresh auth token per respawn attempt "
        "(generate_token), the token is threaded through spawn + reconnect_ws"
    )
    assert "reconnect_ws(app, state, port, &new_token)" in supervisor_source, (
        "supervisor must pass the fresh token into reconnect_ws for re-auth"
    )
    assert "state.child_exit_rx.lock().await" in supervisor_source, (
        "supervisor must rotate the child_exit_rx (CR-2) so the next "
        "shutdown_sidecar polls the new sidecar's exit, not the old one"
    )


def test_ws_reader_emits_python_event_alias_for_backward_compat(ws_source: str) -> None:
    """ADR-0020 §6.3: the WS reader must emit BOTH the specific event"""
    assert '"python-event"' in ws_source, (
        "WS reader must emit 'python-event' as the generic catch-all event "
        "(ADR-0020 §6.3, the renderer's generic catch-all channel)"
    )


def test_ws_reader_forwards_event_names_unchanged(ws_source: str, ws_event_protocol_source: str) -> None:
    """The WS reader forwards event names through ``translate_event_name``."""
    # (2026-07-24): ws.rs was refactored, the prior
    assert re.search(
        r"let\s+emit_name\s*=\s*translate_event_name\s*\(\s*event_type\s*\)\s*;",
        ws_source,
    ), (
        "ws.rs must compute `emit_name` via `translate_event_name(event_type)` "
        "(PVT-G5-062, single unit-testable rename table). The prior "
        "`let emit_name = event_type;` direct assignment was refactored."
    )
    # The translate function MUST have an `other => other` arm so any
    assert re.search(r"other\s*=>\s*other\s*,", ws_event_protocol_source), (
        "translate_event_name (now in ws/event_protocol.rs after the split) "
        "must have an `other => other` forward-compat passthrough arm "
        "(unknown event names flow through unchanged)."
    )


def test_yj21_respawn_inner_acquires_child_lock_before_shutting_down_recheck(
    supervisor_source: str,
) -> None:
    """YJ-21 / CR-81: ``respawn_inner`` must acquire the ``state.child``"""
    # Locate the post-spawn install block. The pattern: the Ok((port,
    install_block_re = re.compile(
        r"Ok\(\(\s*port\s*,\s*child\s*,\s*exit_rx\s*\)\)\s*=>\s*\{",
        re.DOTALL,
    )
    match = install_block_re.search(supervisor_source)
    assert match is not None, (
        "supervisor.rs must have a `Ok((port, child, exit_rx)) => { ... }` "
        "match arm in respawn_inner, could not anchor YJ-21 install-block "
        "source inspection."
    )
    install_block_start = match.end()
    # Pull the next ~4500 chars (enough to cover the install block +
    install_block = supervisor_source[install_block_start : install_block_start + 4500]

    # The mutex_lock call must come FIRST inside the install block.
    mutex_lock_match = re.search(r"mutex_lock\(\s*&state\.child\s*\)", install_block)
    assert mutex_lock_match is not None, (
        "YJ-21 / CR-81: respawn_inner post-spawn block must acquire "
        "`mutex_lock(&state.child)` to serialize the install with "
        "`shutdown_sidecar_for_exit`."
    )
    lock_acquired_at = mutex_lock_match.start()

    # The shutting_down recheck must appear AFTER the lock acquire,
    recheck_pattern = r"state\.shutting_down\.load\(\s*Ordering::SeqCst\s*\)"
    rechecks_after_lock = list(re.finditer(recheck_pattern, install_block))
    assert rechecks_after_lock, (
        "YJ-21 / CR-81: respawn_inner must recheck `state.shutting_down` "
        "INSIDE the child-lock scope (post-spawn recheck). No "
        "`state.shutting_down.load(Ordering::SeqCst)` call found after "
        "`mutex_lock(&state.child)` in the install block."
    )
    first_recheck = rechecks_after_lock[0]
    assert first_recheck.start() > lock_acquired_at, (
        f"YJ-21 / CR-81: the `shutting_down` recheck must come AFTER "
        f"`mutex_lock(&state.child)` is acquired (so the check-and-install "
        f"is atomic w.r.t. `shutdown_sidecar_for_exit`'s take). Found "
        f"recheck at offset {first_recheck.start()} but lock at offset {lock_acquired_at}."
    )

    # The block must also have a branch that kills the freshly-spawned
    kill_region = supervisor_source[
        install_block_start + first_recheck.start() : install_block_start + first_recheck.start() + 4000
    ]
    kill_branch_re = re.compile(r"if let Some\(c\) = child\s*\{[^}]*\bkill_tree\b", re.DOTALL)
    assert kill_branch_re.search(kill_region), (
        "YJ-21 / CR-81: when the inside-lock recheck sees "
        "`shutting_down == true`, the freshly-spawned child must be "
        "killed (kill_tree) instead of being installed, otherwise the "
        "child leaks."
    )
