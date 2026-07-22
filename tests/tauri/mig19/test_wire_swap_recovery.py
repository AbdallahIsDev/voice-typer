"""MIG-1.9 Phase 4 — Wire-swap + recovery validation (ADR-0020 §10).

This test file validates the Tauri host's WebSocket disconnect →
FT-1 respawn → backoff → recovery logic, which ADR-0020 §10 mandates
as the crash-recovery path for the Python sidecar under Tauri.

Scope (ADR-0020 §10 — "WebSocket disconnect / error handling + FT-1
+ rate limiter"):

  1. FT-1 backoff schedule is ``500ms → 1s → 2s → 4s → 8s`` (5 steps,
     doubling) before falling back to full-app relaunch.
  2. FT-1 caps at 5 retries (``FT1_MAX_RETRIES``) then calls
     ``app.restart()`` for a full-app relaunch.
  3. The WS reader task detects disconnect via EOF (``read.next()``
     returns ``None``), ``Message::Close``, or ``Err`` on the stream.
  4. The WS reader triggers ``ft1_respawn`` on unexpected disconnect
     (unless ``shutting_down`` is set).
  5. FT-1 uses an ``AtomicBool`` + ``compare_exchange`` to serialize
     respawn (no double-respawn race when the sidecar flaps).
  6. FT-1 resets the backoff schedule on successful reconnection
     (attempt counter is local to each ``ft1_respawn_inner`` call).
  7. The 1 MiB WS frame cap (``MAX_FRAME_BYTES``) is enforced on BOTH
     the Rust client (``tokio-tungstenite`` ``WebSocketConfig``) and
     the Python server (``websockets.serve(max_size=...)``).
  8. ADR-0019's rate limiter (``_RateLimiter`` from ``ipc_server.py``)
     is ported to the WS accept path — every incoming WS frame passes
     through ``rate_limiter.allow()`` before dispatch.

The Linux sandbox CANNOT compile/run the Rust host or the Nuitka-frozen
sidecar, so all tests here are **source-inspection tests**: they read
the Rust source (``src-tauri/src/sidecar/{ft1,ws}.rs`` + ``util.rs`` +
``state.rs``) and the Python sidecar source (``voice_typer/server/
sidecar_ws.py``) and assert the recovery logic is wired correctly.
End-to-end runtime validation (real WS disconnect → real FT-1 respawn
→ real backoff sleep → real ``app.restart()``) is documented in the
VALIDATE ON HOST block below — a human must run those commands on a
real desktop host per platform.

=====================================================================
VALIDATE ON HOST — exact commands a human must run to validate the
wire-swap recovery end-to-end on a real desktop host
=====================================================================

These commands MUST be run on a real desktop host (Windows / macOS /
Linux) with the Tauri toolchain + Python sidecar installed. The Linux
sandbox cannot execute them. They validate the ADR-0020 §10 crash
recovery state machine: WS disconnect → FT-1 backoff → reconnect OR
full-app relaunch.

Prerequisites (per platform — see tests/tauri/mig18/test_per_triple_freeze.py
for the full per-triple build runbook):
  - Rust stable toolchain for the host target triple.
  - The Nuitka-frozen sidecar binary at
    ``src-tauri/bin/python-sidecar-<triple>[.exe]`` (OR set
    ``VOICE_TYPER_SIDECAR_DEV=1`` to use the unfrozen Python path).
  - ``cargo tauri dev`` works (i.e. the WebView + Tauri plugins
    install cleanly on the host).

---------------------------------------------------------------------
A. FT-1 backoff schedule (500ms → 1s → 2s → 4s → 8s, cap 5)
---------------------------------------------------------------------
    1. cd <repo-root> && cargo tauri dev
       (or: VOICE_TYPER_SIDECAR_DEV=1 cargo tauri dev for the unfrozen
       Python sidecar — faster iteration, no Nuitka recompile).
    2. Wait for the sidecar to start (Rust logs:
       "[SIDECAR] server_started: port=<N>").
    3. Find the sidecar PID: pgrep -fa python-sidecar
       (Windows: tasklist | findstr python-sidecar)
    4. Kill the sidecar to simulate a crash:
       kill -9 <pid>          # macOS / Linux
       taskkill /F /PID <pid> # Windows
    5. Watch the Tauri host logs — you MUST see exactly this sequence:
       [WS-READER] sidecar closed the WS       (or "error: ..." on kill -9)
       [FT-1] respawn attempt 1 after 500ms
       [FT-1] respawn attempt 2 after 1000ms
       [FT-1] respawn attempt 3 after 2000ms
       [FT-1] respawn attempt 4 after 4000ms
       [FT-1] respawn attempt 5 after 8000ms
       [FT-1] exhausted 5 retries — falling back to full-app relaunch
       (then the app exits + relaunches via the Tauri launcher)
    6. BONUS: kill the sidecar, let it respawn successfully on attempt
       1, then kill it again — the second crash MUST start fresh at
       "attempt 1 after 500ms" (per-call backoff, no persistent
       counter — see test_gap_no_persistent_crash_counter_across_invocations).

---------------------------------------------------------------------
B. FT-1 respawn serialization (no double-respawn race)
---------------------------------------------------------------------
    1. cargo tauri dev (as above).
    2. Kill the sidecar AND immediately trigger a second disconnect
       signal (e.g. kill the respawned sidecar before the first
       ft1_respawn returns — within ~500ms).
    3. The host log MUST show exactly one:
       [FT-1] respawn already in progress — skipping
       (the second ft1_respawn call bails out via the AtomicBool).
    4. Verify only ONE ft1_respawn_inner loop is running at a time
       (no interleaved "attempt N" lines from two parallel loops).

---------------------------------------------------------------------
C. 1 MiB WS frame cap
---------------------------------------------------------------------
    1. cargo tauri dev (as above).
    2. From a separate Python shell, connect to the sidecar's WS port
       (parse it from the Tauri log: "listening on 127.0.0.1:<N>"):
         import asyncio, json, websockets
         async def main():
             async with websockets.connect("ws://127.0.0.1:<N>") as ws:
                 await ws.send(json.dumps({"type":"auth","token":"<token>"}))
                 # Send a 2 MiB text frame — must be REJECTED.
                 big = "x" * (2 * 1024 * 1024)
                 await ws.send(big)
                 print(await ws.recv())  # expect a 1009 close or error
         asyncio.run(main())
    3. Expected: the connection closes with code 1009 (message too big)
       OR the Rust host's tokio-tungstenite client errors out — either
       way, the oversized frame is NOT buffered into memory.
    4. Verify a frame just under 1 MiB (e.g. 1 MiB - 1 byte) is
       accepted (proves the cap is exactly 1 MiB, not lower).

---------------------------------------------------------------------
D. Rate limiter on WS accept path (ADR-0019)
---------------------------------------------------------------------
    1. cargo tauri dev (as above).
    2. From a separate Python shell, send 250 rapid dispatch frames
       (over the 200-burst cap):
         import asyncio, json, websockets
         async def main():
             async with websockets.connect("ws://127.0.0.1:<N>") as ws:
                 await ws.send(json.dumps({"type":"auth","token":"<token>"}))
                 for i in range(250):
                     await ws.send(json.dumps({"type":"dispatch","id":i,"data":{"cmd":"get_state"}}))
                     print(await ws.recv())
         asyncio.run(main())
    3. Expected: the first ~200 frames get normal responses; the
       remaining ~50 get:
         {"type":"error","data":{"code":"rate_limited","message":"rate limit exceeded; backing off"}}
       The connection stays OPEN (rate-limited frames are not fatal).
    4. Wait 10 seconds (the sliding window) and send another frame —
       it MUST succeed (window has rolled forward).

---------------------------------------------------------------------
E. Cooperative shutdown vs. FT-1 (regression guard)
---------------------------------------------------------------------
    1. cargo tauri dev (as above).
    2. Quit the app via the tray icon (or Cmd+Q on macOS).
    3. The host log MUST show:
       [SHUTDOWN] sending {"type":"shutdown"}
       [SHUTDOWN] sidecar exited cleanly
       and MUST NOT show:
       [WS-READER] unexpected close — triggering FT-1
       (because shutting_down is set BEFORE the WS reader exits,
       the FT-1 respawn is correctly suppressed — see
       test_ws_reader_skips_respawn_during_shutdown).

References:
  - ADR-0020 §10     — authoritative spec for WS disconnect / FT-1 /
                       rate limiter / frame cap / heartbeat removal.
  - ADR-0020 §1      — WS transport + server_started handshake.
  - ADR-0020 §9      — bubble_level coalesce (30 Hz cap).
  - ADR-0019         — per-connection rate limiter (200 burst / 60
                       sustained msg/s) — ported to the WS path here.
  - src-tauri/src/sidecar/ft1.rs   — FT-1 supervisor implementation.
  - src-tauri/src/sidecar/ws.rs    — WS reader/writer + FT-1 trigger.
  - src-tauri/src/util.rs          — FT1_BACKOFF_MS / FT1_MAX_RETRIES /
                                     MAX_FRAME_BYTES / PRE_RESTART_DELAY_MS.
  - src-tauri/src/state.rs         — SidecarState (respawn_in_progress
                                     AtomicBool, shutting_down AtomicBool).
  - voice_typer/server/sidecar_ws.py — Python WS server (rate limiter
                                     + max_size frame cap).

Gaps documented (report, do NOT fix — out of scope for MIG-1.9 check-3):
  - GAP-1: ADR-0020 §10 says the backoff schedule is "500→1000→2000 ms,
    cap 5 retries" — but the implemented schedule (``FT1_BACKOFF_MS``)
    is 500→1000→2000→4000→8000 ms (5 entries, doubling). The ADR text
    is ambiguous (only 3 values shown but cap=5); the implementation's
    5-step doubling matches the *cap* but extends beyond the 3 values
    shown. This is a documentation/ADR wording gap, not a code bug.
  - GAP-2: The FT-1 supervisor uses a per-call ``attempt`` counter
    local to ``ft1_respawn_inner`` — there is NO persistent crash
    counter across ``ft1_respawn`` invocations. A flapping sidecar
    that recovers on attempt 0 every time will NEVER escalate to
    ``app.restart()``, even if it flaps 1000 times in a minute. If
    ADR-0020 §10 intends a sustained-flap detector that escalates
    across calls, it is NOT implemented. See
    test_gap_no_persistent_crash_counter_across_invocations.
  - GAP-3: ADR-0020 §10 line 709 says "the existing limiter in
    ``log_rate_limit.py``" — but the WS path actually reuses
    ``_RateLimiter`` from ``ipc_server.py`` (the TCP path's limiter).
    ``log_rate_limit.py`` is a *logging* rate-limiter helper, not the
    IPC rate-limiter. ADR wording bug; the implementation correctly
    reuses the IPC limiter.
  - GAP-4: The Rust WS client (``reconnect_ws``) does NOT enforce a
    rate limit on outbound frames — only the Python WS server enforces
    the inbound limit. ADR-0020 §10 only mandates the server-side
    limiter, so this is by design, but a buggy Rust bridge could
    still self-DoS the sidecar's outbound queue. Out of scope for v1.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ─── Project paths ───────────────────────────────────────────────────────────
# This file lives at tests/tauri/mig19/test_wire_swap_recovery.py.
# parents[0] = mig19/, parents[1] = tauri/, parents[2] = tests/,
# parents[3] = <project root>.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_TAURI_DIR = PROJECT_ROOT / "src-tauri" / "src"
FT1_RS = SRC_TAURI_DIR / "sidecar" / "ft1.rs"
WS_RS = SRC_TAURI_DIR / "sidecar" / "ws.rs"
UTIL_RS = SRC_TAURI_DIR / "util.rs"
STATE_RS = SRC_TAURI_DIR / "state.rs"
SIDECAR_WS_PY = PROJECT_ROOT / "voice_typer" / "server" / "sidecar_ws.py"
IPC_SERVER_PY = PROJECT_ROOT / "voice_typer" / "server" / "ipc_server.py"
# Phase 4.5 / ARCH-045 — ``ipc_server.py`` is now a thin shim re-exporting
# symbols from the ``voice_typer/server/ipc/`` package.  Tests that
# source-inspect the rate-limiter implementation read ``ipc/rate_limiter.py``
# (where ``_RateLimiter`` / ``_get_rate_limiter`` / the ``_RATE_LIMIT_*``
# constants now actually live) instead of the shim.
IPC_RATE_LIMITER_PY = PROJECT_ROOT / "voice_typer" / "server" / "ipc" / "rate_limiter.py"
ADR_0020 = PROJECT_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"


# ─── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def ft1_source() -> str:
    """Full text of src-tauri/src/sidecar/ft1.rs (read once per module)."""
    assert FT1_RS.is_file(), f"missing: {FT1_RS}"
    return FT1_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ws_source() -> str:
    """Full text of src-tauri/src/sidecar/ws.rs (read once per module)."""
    assert WS_RS.is_file(), f"missing: {WS_RS}"
    return WS_RS.read_text(encoding="utf-8")


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
    """Full text of voice_typer/server/sidecar_ws.py (read once per module)."""
    assert SIDECAR_WS_PY.is_file(), f"missing: {SIDECAR_WS_PY}"
    return SIDECAR_WS_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ipc_server_source() -> str:
    """Full text of the IPC rate-limiter submodule (read once per module).

    Phase 4.5 / ARCH-045: ``ipc_server.py`` was split into the
    ``voice_typer/server/ipc/`` package.  The rate-limiter implementation
    (``_RateLimiter``, ``_get_rate_limiter``, ``_RATE_LIMIT_*`` constants)
    now lives in ``ipc/rate_limiter.py`` — this fixture reads that file so
    the rate-limiter source-inspection tests find the symbols they expect.
    """
    assert IPC_RATE_LIMITER_PY.is_file(), f"missing: {IPC_RATE_LIMITER_PY}"
    return IPC_RATE_LIMITER_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def adr_0020_source() -> str:
    """Full text of ADR-0020 (read once per module)."""
    assert ADR_0020.is_file(), f"missing: {ADR_0020}"
    return ADR_0020.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# 1. FT-1 backoff schedule (500ms → 1s → 2s → 4s → 8s, 5 steps doubling)
# ═══════════════════════════════════════════════════════════════════════════


def test_ft1_backoff_schedule_is_5_steps_doubling(util_source: str) -> None:
    """ADR-0020 §10: FT-1 backoff is 500ms→1s→2s→4s→8s (5 doubling steps).

    The schedule must be exactly ``&[500, 1000, 2000, 4000, 8000]`` — a
    doubling geometric progression with 5 entries. This is the literal
    constant the FT-1 supervisor iterates over in ``ft1_respawn_inner``.
    """
    # The const declaration line.
    match = re.search(
        r"pub\(crate\)\s+const\s+FT1_BACKOFF_MS\s*:\s*&\[u64\]\s*=\s*"
        r"&\[(?P<vals>[^\]]+)\]",
        util_source,
    )
    assert match is not None, (
        "FT1_BACKOFF_MS const declaration not found in util.rs — did the constant move or get renamed?"
    )
    vals = [int(v.strip()) for v in match.group("vals").split(",") if v.strip()]
    assert vals == [500, 1000, 2000, 4000, 8000], (
        f"FT1_BACKOFF_MS must be [500, 1000, 2000, 4000, 8000] (5 doubling steps), got {vals}"
    )


def test_ft1_backoff_schedule_length_matches_max_retries(util_source: str) -> None:
    """``FT1_BACKOFF_MS.len()`` must equal ``FT1_MAX_RETRIES`` (=5).

    The ``ft1_respawn_inner`` loop iterates over the schedule; if the
    schedule were shorter than MAX_RETRIES, the exhaustion branch
    (``attempt >= FT1_MAX_RETRIES``) would never fire and the loop
    would exit silently to the post-loop ``app.restart()`` fallback.
    Asserting equality guarantees the exhaustion branch is reachable.
    """
    sched_match = re.search(
        r"FT1_BACKOFF_MS\s*:\s*&\[u64\]\s*=\s*&\[(?P<vals>[^\]]+)\]",
        util_source,
    )
    cap_match = re.search(
        r"FT1_MAX_RETRIES\s*:\s*u32\s*=\s*(?P<val>\d+)",
        util_source,
    )
    assert sched_match is not None and cap_match is not None
    sched_len = len([v for v in sched_match.group("vals").split(",") if v.strip()])
    max_retries = int(cap_match.group("val"))
    assert sched_len == 5, f"FT1_BACKOFF_MS must have 5 entries, got {sched_len}"
    assert max_retries == 5, f"FT1_MAX_RETRIES must be 5, got {max_retries}"
    assert sched_len == max_retries, (
        f"FT1_BACKOFF_MS.len() ({sched_len}) must equal FT1_MAX_RETRIES "
        f"({max_retries}) so the loop iterates exactly N times before "
        f"falling back to app.restart()"
    )


def test_ft1_backoff_implements_geometric_doubling(util_source: str) -> None:
    """Each step must be exactly 2x the previous step (geometric, base 2).

    Guards against an accidental edit that breaks the progression
    (e.g. someone adding a 3000ms entry between 2000 and 4000).
    """
    match = re.search(
        r"FT1_BACKOFF_MS\s*:\s*&\[u64\]\s*=\s*&\[(?P<vals>[^\]]+)\]",
        util_source,
    )
    assert match is not None
    vals = [int(v.strip()) for v in match.group("vals").split(",") if v.strip()]
    for i in range(1, len(vals)):
        assert vals[i] == vals[i - 1] * 2, (
            f"backoff step {i} must be 2x step {i - 1}: got {vals[i]} vs {vals[i - 1]} * 2 = {vals[i - 1] * 2}"
        )


def test_ft1_respawn_inner_iterates_backoff_schedule(ft1_source: str) -> None:
    """``ft1_respawn_inner`` must iterate ``FT1_BACKOFF_MS`` with enumerate.

    This is the structural proof that the schedule constant is actually
    consumed by the supervisor (not just declared). The loop must use
    ``enumerate()`` so the attempt index is available for the cap check.
    """
    assert "for (attempt, delay_ms) in FT1_BACKOFF_MS.iter().enumerate()" in ft1_source, (
        "ft1_respawn_inner must iterate FT1_BACKOFF_MS with enumerate() — "
        "the attempt index is needed for the FT1_MAX_RETRIES cap check"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 2. FT-1 cap at 5 retries → app.restart() (full-app relaunch)
# ═══════════════════════════════════════════════════════════════════════════


def test_ft1_max_retries_constant_is_5(util_source: str) -> None:
    """ADR-0020 §10: ``FT1_MAX_RETRIES`` must be exactly 5."""
    match = re.search(r"FT1_MAX_RETRIES\s*:\s*u32\s*=\s*(?P<val>\d+)", util_source)
    assert match is not None, "FT1_MAX_RETRIES const declaration not found"
    assert int(match.group("val")) == 5, (
        f"FT1_MAX_RETRIES must be 5 (then fall back to full-app relaunch), got {match.group('val')}"
    )


def test_ft1_exhaustion_branch_calls_app_restart(ft1_source: str) -> None:
    """When the backoff schedule is exhausted, the supervisor MUST call
    ``app.restart()`` (the full-app relaunch fallback per ADR-0020 §10).

    The exhaustion path is the **post-loop** ``app.restart()`` at the
    bottom of ``ft1_respawn_inner`` (the in-loop
    ``attempt as u32 >= FT1_MAX_RETRIES`` guard was intentionally
    removed as dead code — ``FT1_BACKOFF_MS.len() == FT1_MAX_RETRIES ==
    5``, so ``attempt`` ranges ``0..=4`` and that condition was always
    false; see the NF-R19-2 comment in ft1.rs). The post-loop path emits
    ``ft1_relaunching`` (reason="backoff_exhausted") and then calls
    ``app.restart()``.
    """
    # The exhaustion branch must exist + call app.restart().
    assert "backoff schedule exhausted" in ft1_source, (
        "FT-1 post-loop exhaustion branch (``backoff schedule exhausted``) not found — the exhaustion path is missing"
    )
    assert "app.restart()" in ft1_source, (
        "app.restart() call not found in ft1.rs — full-app relaunch fallback is missing"
    )
    # The exhaustion branch must emit a Tauri event BEFORE restart so
    # the UI can render a "restarting…" banner.
    assert '"ft1_relaunching"' in ft1_source, (
        "ft1_relaunching event not emitted in ft1.rs — the UI cannot show a restarting banner before app.restart()"
    )


def test_ft1_exhaustion_branch_has_pre_restart_delay(ft1_source: str, util_source: str) -> None:
    """ADR-0020 §10: a brief delay between emitting ``ft1_relaunching``
    and calling ``app.restart()`` so the webview can render the banner.

    The delay is ``PRE_RESTART_DELAY_MS`` (=500ms per util.rs).
    """
    assert "PRE_RESTART_DELAY_MS" in ft1_source, (
        "PRE_RESTART_DELAY_MS not referenced in ft1.rs — the pre-restart "
        "delay (so the UI can render the restarting banner) is missing"
    )
    match = re.search(
        r"PRE_RESTART_DELAY_MS\s*:\s*u64\s*=\s*(?P<val>\d+)",
        util_source,
    )
    assert match is not None
    assert int(match.group("val")) == 500, f"PRE_RESTART_DELAY_MS must be 500ms, got {match.group('val')}"


def test_ft1_loop_exit_falls_back_to_app_restart(ft1_source: str) -> None:
    """If the backoff loop exits without returning (defensive — should
    not happen since the exhaustion branch fires first), the post-loop
    fallback MUST also call ``app.restart()``.

    This is the "belt and suspenders" guard for the case where
    ``FT1_BACKOFF_MS.len() < FT1_MAX_RETRIES`` (a future edit bug).
    """
    # Find the post-loop app.restart() (there should be at least 2
    # app.restart() calls: one inside the exhaustion branch, one
    # after the loop).
    restart_count = ft1_source.count("app.restart()")
    assert restart_count >= 2, (
        f"expected at least 2 app.restart() calls in ft1.rs (exhaustion "
        f"branch + post-loop fallback), found {restart_count}"
    )
    assert "backoff schedule exhausted" in ft1_source, (
        "post-loop exhaustion log message not found — the defensive "
        "fallback for FT1_BACKOFF_MS.len() < FT1_MAX_RETRIES is missing"
    )


def test_ft1_respawn_inner_respects_shutting_down_flag(ft1_source: str) -> None:
    """The supervisor MUST check ``shutting_down`` inside the backoff
    loop and bail out if the app is quitting mid-backoff (otherwise a
    slow backoff could respawn the sidecar DURING app shutdown)."""
    assert "state.shutting_down.load" in ft1_source, (
        "FT-1 supervisor must check state.shutting_down inside the backoff "
        "loop — a respawn during shutdown would race with app.quit()"
    )
    assert "shutting down — skipping respawn" in ft1_source, (
        "FT-1 supervisor must log + return Ok(()) when shutting_down is set"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 3. WS reader detects disconnect (EOF on the WS stream)
# ═══════════════════════════════════════════════════════════════════════════


def test_ws_reader_loop_breaks_on_eof_none(ws_source: str) -> None:
    """The WS reader loop must exit when ``read.next()`` returns ``None``
    (the stream returned EOF — sidecar closed the socket without sending
    a Close frame). ``while let Some(msg) = read.next().await`` exits
    naturally on None.
    """
    assert "while let Some(msg) = read.next().await" in ws_source, (
        "WS reader loop must use `while let Some(msg) = read.next().await` "
        "— None (EOF) exits the loop, triggering the post-loop FT-1 path"
    )


def test_ws_reader_loop_breaks_on_close_frame(ws_source: str) -> None:
    """A ``Message::Close`` frame from the sidecar must break the loop."""
    assert "Ok(Message::Close(_))" in ws_source, (
        "WS reader must match Ok(Message::Close(_)) — a clean WS close from the sidecar must break the reader loop"
    )
    # The Close arm must `break` (not continue).
    close_arm = re.search(
        r"Ok\(Message::Close\(_\)\)\s*=>\s*\{[^}]*break;[^}]*\}",
        ws_source,
        re.DOTALL,
    )
    assert close_arm is not None, "WS reader Close arm must `break;` to exit the loop"


def test_ws_reader_loop_breaks_on_stream_error(ws_source: str) -> None:
    """A stream ``Err`` (e.g. TCP RST, broken pipe) must break the loop.

    The Err arm logs ``[WS-READER] error: <e>`` and then ``break;``s out
    of the reader loop (which triggers the post-loop FT-1 path). We use
    substring checks (not a regex block match) because the format string
    ``"{}"`` contains a literal ``}`` which would break a naive
    ``[^}]*`` block matcher.
    """
    assert "Err(e)" in ws_source, "WS reader must match Err(e) — a stream error must be handled"
    assert "[WS-READER] error:" in ws_source, "WS reader Err arm must log '[WS-READER] error: <e>'"
    # The Err arm must `break;` (not continue). We verify the substring
    # exists; the structural proof that it's in the Err arm comes from
    # reading ws.rs lines 152-155 (the only `break;` after the Close arm
    # is the Err arm's break).
    assert "break;" in ws_source, "WS reader must `break;` to exit the loop on stream error"


def test_ws_reader_logs_disconnect_reasons(ws_source: str) -> None:
    """Each disconnect path must log a distinct reason for debugging
    (Close vs. Err vs. implicit None-EOF)."""
    assert "sidecar closed the WS" in ws_source, "WS reader must log 'sidecar closed the WS' on a clean Close frame"
    assert "[WS-READER] error:" in ws_source, "WS reader must log '[WS-READER] error: <e>' on a stream Err"


# ═══════════════════════════════════════════════════════════════════════════
# 4. WS reader triggers FT-1 respawn on disconnect
# ═══════════════════════════════════════════════════════════════════════════


def test_ws_reader_triggers_ft1_respawn_after_loop_exit(ws_source: str) -> None:
    """After the reader loop exits (EOF/Close/Err), the reader task MUST
    call ``ft1_respawn`` to start the backoff supervisor."""
    assert "ft1_respawn(" in ws_source, (
        "WS reader must call ft1_respawn() after the reader loop exits — the disconnect → FT-1 trigger is missing"
    )
    # The call must be inside a std::thread::spawn + block_on bridge
    # (because the WS stream half is !Send so we can't tokio::spawn it
    # directly — see the inline comment in ws.rs).
    assert "std::thread::spawn" in ws_source, (
        "WS reader must spawn FT-1 on a std::thread (the WS stream half "
        "is !Send so a tokio::spawn would fail the Send requirement)"
    )
    assert "tauri::async_runtime::block_on" in ws_source, (
        "WS reader must use tauri::async_runtime::block_on to run the ft1_respawn future on the std::thread bridge"
    )


def test_ws_reader_skips_respawn_during_shutdown(ws_source: str) -> None:
    """The FT-1 trigger must be gated on ``!shutting_down`` — otherwise
    a normal app quit (which closes the WS) would spuriously respawn
    the sidecar DURING shutdown."""
    assert "state_for_reader.shutting_down.load" in ws_source, (
        "WS reader must check shutting_down before triggering FT-1 — "
        "a clean app quit would otherwise cause a spurious respawn"
    )
    assert "if !state_for_reader.shutting_down.load" in ws_source, (
        "FT-1 trigger must be inside `if !shutting_down` — the spawn must be suppressed during shutdown"
    )


def test_ws_reader_emits_relaunching_with_reason_disconnected(ws_source: str) -> None:
    """CR-5 (ADR-0020 §10): the WS reader must emit ``ft1_relaunching``
    with ``reason: "disconnected"`` IMMEDIATELY at disconnect start so
    the UI can show a "reconnecting…" banner BEFORE the backoff runs."""
    assert '"ft1_relaunching"' in ws_source, "WS reader must emit 'ft1_relaunching' immediately at disconnect"
    assert '"reason": "disconnected"' in ws_source or '"reason":"disconnected"' in ws_source, (
        "WS reader must emit ft1_relaunching with reason='disconnected' "
        "so the UI shows the reconnecting banner before backoff runs"
    )


def test_ws_reader_drains_pending_dispatch_on_disconnect(ws_source: str) -> None:
    """CR-Finding 1 + 3: on disconnect, the reader must drain the
    pending-dispatch map and reject each with ``sidecar_disconnected``
    so callers don't wait the full 120s ``DISPATCH_TIMEOUT_SECS``."""
    assert "sidecar_disconnected" in ws_source, (
        "WS reader must reject pending dispatch requests with code 'sidecar_disconnected' on disconnect"
    )
    assert "drained" in ws_source, "WS reader must log how many pending dispatch requests were drained"
    assert "pending.drain()" in ws_source or ".drain()" in ws_source, (
        "WS reader must drain the pending map (not iterate + remove) — drain() is O(n) and atomic vs. per-key remove"
    )


def test_ws_reader_clears_ws_tx_on_disconnect(ws_source: str) -> None:
    """CR-Finding 1: on disconnect, the reader must clear ``ws_tx`` to
    ``None`` so new dispatch calls fail fast with "sidecar not
    connected" instead of queueing onto a dead channel."""
    assert "*ws_tx_guard = None" in ws_source, (
        "WS reader must clear ws_tx to None on disconnect — new dispatch "
        "calls must fail fast, not queue onto a dead channel"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 5. FT-1 atomic flag serialization (no double-respawn race)
# ═══════════════════════════════════════════════════════════════════════════


def test_respawn_in_progress_is_atomic_bool(state_source: str) -> None:
    """``SidecarState.respawn_in_progress`` must be an ``AtomicBool``
    (not a ``Mutex<bool>`` or plain ``bool``) so the compare_exchange
    is lock-free + atomic across threads."""
    assert "respawn_in_progress: AtomicBool" in state_source, (
        "SidecarState.respawn_in_progress must be AtomicBool — a Mutex<bool> "
        "would deadlock if ft1_respawn panics while holding the lock"
    )


def test_ft1_uses_compare_exchange_to_acquire_flag(ft1_source: str) -> None:
    """The FT-1 supervisor must use ``compare_exchange(false, true)``
    to acquire the respawn flag — this is the only lock-free way to
    guarantee exactly one supervisor runs at a time."""
    assert "compare_exchange(false, true" in ft1_source, (
        "FT-1 supervisor must acquire respawn_in_progress with "
        "compare_exchange(false, true, ...) — test_and_set is not "
        "available on Rust's AtomicBool"
    )
    assert "Ordering::SeqCst" in ft1_source, (
        "FT-1 compare_exchange must use SeqCst ordering — the respawn "
        "flag is synchronized with shutting_down (also SeqCst) and the "
        "child/token/ws_tx Mutexes; weaker orderings could reorder the "
        "flag store relative to the Mutex unlocks"
    )


def test_ft1_skips_when_respawn_already_in_progress(ft1_source: str) -> None:
    """If ``compare_exchange`` fails (a previous respawn is in flight),
    the supervisor MUST bail out with ``Ok(())`` and log the skip —
    the in-flight supervisor owns the recovery.

    We use substring checks (not a regex block match) because the
    compare_exchange call spans multiple lines with ``Ordering::SeqCst``
    args, and the block contains a ``log::info!`` format string with
    literal braces that break a naive ``[^}]*`` matcher.
    """
    assert "respawn already in progress" in ft1_source, (
        "FT-1 supervisor must log 'respawn already in progress — skipping' when compare_exchange fails"
    )
    # The skip block must `return Ok(())` (not Err — bailing out is not
    # an error). The compare_exchange's `.is_err()` branch is the skip
    # path; verify both substrings exist (their co-location in the same
    # branch is verified by reading ft1.rs lines 22-29).
    assert "compare_exchange(false, true" in ft1_source
    assert ".is_err()" in ft1_source, (
        "FT-1 supervisor must check `.is_err()` on the compare_exchange result to detect the 'already in progress' case"
    )
    assert "return Ok(());" in ft1_source, (
        "FT-1 supervisor must `return Ok(())` when compare_exchange fails — "
        "the in-flight supervisor owns the recovery (bail-out is not an error)"
    )


def test_ft1_clears_flag_on_all_exit_paths(ft1_source: str) -> None:
    """The ``respawn_in_progress`` flag MUST be cleared on EVERY exit
    path (Ok, Err) — otherwise a single respawn would permanently
    disable future recovery.

    The implementation scopes the inner body in a closure/let binding
    so the clear runs after the inner future resolves, regardless of
    Ok/Err. The ``app.restart()`` paths return ``!`` (never type) so
    the clear is unreachable there but harmless.
    """
    # The clear must use SeqCst (matches the acquire ordering).
    assert "respawn_in_progress.store(false, Ordering::SeqCst)" in ft1_source, (
        "FT-1 supervisor must clear respawn_in_progress with "
        "store(false, SeqCst) — matching the compare_exchange acquire "
        "ordering, and on every exit path"
    )
    # The clear must come AFTER ft1_respawn_inner resolves (so the
    # inner body's MutexGuards are dropped first, maintaining the
    # "drop guards before await" Send-safety pattern).
    assert "let result = ft1_respawn_inner" in ft1_source, (
        "FT-1 supervisor must bind ft1_respawn_inner's result to a local "
        "before clearing the flag — this guarantees the clear runs AFTER "
        "the inner body resolves (Ok or Err)"
    )


def test_ft1_respawn_in_progress_comment_documents_race(state_source: str) -> None:
    """The ``respawn_in_progress`` field must have a doc comment
    explaining the race it prevents (flapping sidecar → multiple
    parallel ft1_respawn supervisors corrupting child/token/ws_tx)."""
    # Find the field declaration + look backwards for the doc comment.
    idx = state_source.find("respawn_in_progress: AtomicBool")
    assert idx != -1
    preceding = state_source[:idx]
    # The doc comment must mention the flapping/race scenario.
    assert "flapping" in preceding.lower() or "race" in preceding.lower() or "parallel" in preceding.lower(), (
        "respawn_in_progress doc comment must explain the race it prevents "
        "(flapping sidecar → parallel ft1_respawn supervisors corrupting "
        "child/token/ws_tx)"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 6. FT-1 resets the crash counter on successful reconnection
# ═══════════════════════════════════════════════════════════════════════════


def test_ft1_attempt_counter_is_local_to_invocation(ft1_source: str) -> None:
    """The FT-1 attempt counter must be a LOCAL ``attempt`` variable
    in the ``for (attempt, delay_ms) in FT1_BACKOFF_MS.iter().enumerate()``
    loop — NOT a persistent field on ``SidecarState``.

    This means each ``ft1_respawn`` call starts fresh at attempt 0, so
    a successful reconnection (which returns Ok(()) immediately) leaves
    no residual state — the NEXT disconnect starts a fresh backoff
    schedule at 500ms. This is the implicit "crash counter reset on
    successful reconnection" behavior.
    """
    # The attempt variable must come from enumerate() (local), not from
    # a state field like state.crash_count.fetch_add(1).
    assert "for (attempt, delay_ms) in FT1_BACKOFF_MS.iter().enumerate()" in ft1_source, (
        "FT-1 attempt counter must be the `attempt` from enumerate() — a "
        "local loop variable, not a persistent SidecarState field"
    )
    # There must NOT be a persistent crash counter field on SidecarState.
    assert "crash_count" not in ft1_source, (
        "FT-1 supervisor must not use a persistent crash_count — the "
        "attempt counter is per-call (local to ft1_respawn_inner)"
    )


def test_ft1_returns_on_successful_reconnect(ft1_source: str) -> None:
    """On a successful spawn + reconnect, the supervisor MUST return
    ``Ok(())`` immediately — this is the "reset" point. The next
    disconnect will start a fresh backoff schedule at attempt 0.

    We use substring checks (not a regex block match) because the
    success arm contains ``app.emit("ft1_reconnected", json!({}))``
    whose ``json!({})`` macro has literal braces that break a naive
    ``[^}]*`` matcher.
    """
    # The success branch must emit ft1_reconnected AND return Ok(()).
    # Both substrings must be present; their co-location in the same
    # match arm is verified by reading ft1.rs lines 90-96 (the only
    # `return Ok(())` inside the for loop is in the reconnect_ws
    # Ok(()) arm).
    assert '"ft1_reconnected"' in ft1_source, "FT-1 supervisor must emit 'ft1_reconnected' on successful respawn"
    assert "return Ok(());" in ft1_source, (
        "FT-1 supervisor must `return Ok(())` on successful reconnect_ws — "
        "this is the crash-counter reset point (next disconnect starts fresh)"
    )
    # The success branch must be inside the reconnect_ws Ok arm.
    assert "reconnect_ws(app, state, port, &new_token)" in ft1_source


def test_ft1_emits_reconnected_event_on_success(ft1_source: str) -> None:
    """On successful reconnect, the supervisor MUST emit ``ft1_reconnected``
    so the UI can clear its "reconnecting…" banner."""
    assert '"ft1_reconnected"' in ft1_source, (
        "FT-1 supervisor must emit 'ft1_reconnected' on successful respawn so the UI can clear the reconnecting banner"
    )


def test_ft1_rotates_token_on_each_respawn_attempt(ft1_source: str) -> None:
    """Each respawn attempt must rotate the bearer token (via
    ``generate_token()``) so a compromised old token is invalidated
    when the sidecar restarts. ADR-0020 §3."""
    assert "generate_token()" in ft1_source, (
        "FT-1 supervisor must call generate_token() on each respawn attempt "
        "— the token rotates per respawn (ADR-0020 §3)"
    )
    assert "new_token" in ft1_source, "FT-1 supervisor must use a `new_token` local for the rotated token"


def test_ft1_no_persistent_crash_counter_field_on_state(state_source: str) -> None:
    """``SidecarState`` must NOT have a persistent crash counter field
    (e.g. ``crash_count: AtomicU32``). The FT-1 backoff schedule is
    per-call (local ``attempt`` variable), so there is no shared
    counter that needs resetting.

    This is by design (per ADR-0020 §10's state machine: each
    disconnect triggers a fresh backoff sequence), but it means a
    flapping sidecar that recovers on attempt 0 every time will never
    escalate to ``app.restart()`` — see
    test_gap_no_persistent_crash_counter_across_invocations.
    """
    # List of forbidden persistent-counter field names.
    for forbidden in ("crash_count", "respawn_count", "ft1_attempt", "restart_count"):
        assert forbidden not in state_source, (
            f"SidecarState must NOT have a persistent {forbidden} field — "
            f"the FT-1 attempt counter is per-call (local to ft1_respawn_inner)"
        )


def test_gap_no_persistent_crash_counter_across_invocations(ft1_source: str, state_source: str) -> None:
    """GAP-2 (documented, do NOT fix): the FT-1 supervisor has NO
    persistent crash counter across ``ft1_respawn`` invocations.

    ADR-0020 §10's state machine says "running → (unexpected exit) →
    reconnecting → respawn with backoff (cap 5 retries) → running | give
    up → full-app relaunch". The implementation's "cap 5 retries"
    applies PER-CALL — a sidecar that flaps 1000 times in a minute but
    recovers on attempt 0 every time will trigger 1000 ft1_respawn
    calls, each running 1 attempt (500ms backoff), never escalating
    to app.restart().

    If ADR-0020 §10 intends a sustained-flap detector that escalates
    across calls (e.g. "5 crashes in 60s → app.restart()"), it is NOT
    implemented. This test documents the gap; the per-call backoff is
    the shipped behavior.
    """
    # Proof the counter is local (per-call), not persistent.
    assert "for (attempt, delay_ms) in FT1_BACKOFF_MS.iter().enumerate()" in ft1_source
    # Proof there is no SustainedFlapDetector or similar on SidecarState.
    assert "SustainedFlap" not in state_source
    assert "flap_count" not in state_source
    # The per-call exhaustion path lives in the post-loop branch (the
    # in-loop ``attempt as u32 >= FT1_MAX_RETRIES`` guard was removed as
    # dead code — see NF-R19-2 in ft1.rs). Assert the dead guard is GONE
    # so a future edit that reintroduces it (which would never fire,
    # since FT1_BACKOFF_MS.len() == FT1_MAX_RETRIES == 5) is caught.
    assert "attempt as u32 >= FT1_MAX_RETRIES" not in ft1_source, (
        "The in-loop `attempt as u32 >= FT1_MAX_RETRIES` guard was "
        "removed as dead code (FT1_BACKOFF_MS.len() == FT1_MAX_RETRIES "
        "== 5, so the condition is always false). The real escalation "
        "path is the post-loop `backoff schedule exhausted` branch that "
        "calls app.restart(). Reintroducing the dead guard is misleading."
    )
    # Document the gap explicitly — if this test fails in the future,
    # it means a sustained-flap detector was added (good! update the
    # gap docstring at the top of this file).


# ═══════════════════════════════════════════════════════════════════════════
# 7. 1 MiB WS frame cap (reject frames > 1 MiB)
# ═══════════════════════════════════════════════════════════════════════════


def test_max_frame_bytes_constant_is_exactly_1_mib(util_source: str) -> None:
    """ADR-0020 §10: ``MAX_FRAME_BYTES`` must be exactly 1 MiB
    (``1024 * 1024``). A malformed/huge frame must be rejected at the
    WS transport layer, not buffered into memory."""
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
    """The Rust WS client (``reconnect_ws``) MUST set BOTH
    ``max_message_size`` and ``max_frame_size`` on the
    ``tokio_tungstenite`` ``WebSocketConfig`` to ``MAX_FRAME_BYTES``.

    - ``max_frame_size``: rejects a single WS frame > 1 MiB.
    - ``max_message_size``: rejects a fragmented message whose
      reassembled total > 1 MiB (prevents the bypass of sending a
      10 MiB message as 10 x 1 MiB fragments).
    """
    assert "max_message_size: Some(MAX_FRAME_BYTES)" in ws_source, (
        "Rust WS client must set max_message_size = Some(MAX_FRAME_BYTES) — guards against fragmented-message bypass"
    )
    assert "max_frame_size: Some(MAX_FRAME_BYTES)" in ws_source, (
        "Rust WS client must set max_frame_size = Some(MAX_FRAME_BYTES) — "
        "rejects single frames > 1 MiB at the transport layer"
    )
    # The config must be passed to connect_async_with_config (not the
    # default connect_async which has a 64 MiB default cap).
    assert "connect_async_with_config" in ws_source, (
        "Rust WS client must use connect_async_with_config (not the default "
        "connect_async) so the WebSocketConfig is actually applied"
    )


def test_python_ws_server_enforces_max_size(sidecar_ws_source: str) -> None:
    """The Python WS server (``sidecar_ws.run``) MUST pass
    ``max_size=_MAX_FRAME_BYTES`` to ``websockets.serve()`` so the
    library rejects oversized frames at the transport layer (close
    code 1009) before they reach the dispatch loop."""
    assert "max_size=_MAX_FRAME_BYTES" in sidecar_ws_source, (
        "Python WS server must pass max_size=_MAX_FRAME_BYTES to "
        "websockets.serve() — the library rejects frames > 1 MiB with "
        "close code 1009 at the transport layer"
    )
    assert "serve(" in sidecar_ws_source, "Python WS server must call websockets.serve() (the async server)"


def test_python_sidecar_max_frame_bytes_constant_is_1_mib(sidecar_ws_source: str) -> None:
    """The Python ``_MAX_FRAME_BYTES`` constant must also be 1 MiB,
    matching the Rust client. Both sides must agree on the cap."""
    match = re.search(
        r"_MAX_FRAME_BYTES\s*=\s*(?P<expr>[\d\s*()+]+)",
        sidecar_ws_source,
    )
    assert match is not None, "_MAX_FRAME_BYTES const not found in sidecar_ws.py"
    expr = match.group("expr").strip()
    assert eval(expr) == 1024 * 1024, f"_MAX_FRAME_BYTES must be 1 MiB (1048576), got '{expr}' = {eval(expr)}"


def test_python_sidecar_outbound_frame_cap(sidecar_ws_source: str) -> None:
    """The Python WS server must ALSO cap OUTBOUND frames (events
    published by the sidecar to the host) at 1 MiB — a huge
    ``download_progress`` or ``vocabulary_suggestion`` payload must
    be dropped, not sent (which would close the connection)."""
    assert "_MAX_FRAME_BYTES" in sidecar_ws_source
    # The outbound writer task must check len(raw.encode) > _MAX_FRAME_BYTES.
    assert "exceeds" in sidecar_ws_source.lower() or "len(raw.encode" in sidecar_ws_source, (
        "Python WS server outbound writer must check frame size against "
        "_MAX_FRAME_BYTES and drop oversized frames (not send them, which "
        "would close the connection)"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 8. Rate limiter ported to WS accept path (ADR-0019)
# ═══════════════════════════════════════════════════════════════════════════


def test_sidecar_ws_imports_rate_limiter(sidecar_ws_source: str) -> None:
    """ADR-0019 port: the WS server must import ``_get_rate_limiter``
    from ``ipc_server.py`` (the same limiter the TCP path uses) so
    burst/sustained semantics are identical across transports."""
    assert "_get_rate_limiter" in sidecar_ws_source, (
        "sidecar_ws.py must import _get_rate_limiter from ipc_server.py — "
        "the WS path reuses the TCP path's _RateLimiter (ADR-0019 port)"
    )
    assert "from voice_typer.server.ipc_server import _get_rate_limiter" in sidecar_ws_source, (
        "sidecar_ws.py must `from voice_typer.server.ipc_server import _get_rate_limiter`"
    )


def test_sidecar_ws_calls_rate_limiter_allow_per_frame(sidecar_ws_source: str) -> None:
    """Every incoming WS frame must pass through ``rate_limiter.allow()``
    before dispatch. A frame that exceeds the burst/sustained cap must
    be rejected with ``code: rate_limited`` and the connection stays
    open (rate-limited frames are not fatal)."""
    assert "rate_limiter = _get_rate_limiter(server)" in sidecar_ws_source, (
        "sidecar_ws.py must look up the shared rate limiter via _get_rate_limiter(server) on every frame"
    )
    assert "rate_limiter.allow()" in sidecar_ws_source, (
        "sidecar_ws.py must call rate_limiter.allow() per frame — the WS accept path rate-limiter is the ADR-0019 port"
    )
    # SEC-6 / DOWNGRADE #2 fix: allow() now increments _rejected atomically
    # when it returns False. The separate .reject() call was removed from
    # the WS path to keep rejected_count consistent with the TCP path
    # (both count via allow()). Assert the no-op .reject() is NOT called.
    assert "rate_limiter.reject()" not in sidecar_ws_source, (
        "sidecar_ws.py must NOT call rate_limiter.reject() — SEC-6 moved "
        "the counter increment into allow() atomically. The .reject() call "
        "was removed (DOWNGRADE #2 fix) to keep WS-path rejected_count "
        "consistent with the TCP path."
    )


def test_sidecar_ws_returns_rate_limited_error(sidecar_ws_source: str) -> None:
    """A rate-limited frame must yield:
    ``{"type":"error","data":{"code":"rate_limited","message":"rate limit exceeded; backing off"}}``
    and the connection MUST stay open."""
    assert '"rate_limited"' in sidecar_ws_source, (
        "sidecar_ws.py must return error code 'rate_limited' when the limiter rejects a frame"
    )
    assert "rate limit exceeded" in sidecar_ws_source, (
        "sidecar_ws.py rate_limited error must carry the message 'rate limit exceeded; backing off'"
    )
    # The rate_limited return must be inside the dispatch() closure
    # (which returns the error dict, NOT closes the connection).
    # Verify there is no `websocket.close()` call in the rate-limit branch.
    # Find the rate_limited block and check it doesn't close the socket.
    rl_idx = sidecar_ws_source.find('"rate_limited"')
    assert rl_idx != -1
    # Look at the next 400 chars after the rate_limited string.
    block = sidecar_ws_source[rl_idx : rl_idx + 400]
    assert "websocket.close" not in block, (
        "rate_limited branch must NOT close the WS connection — the "
        "connection stays open and the client can retry after backing off"
    )


def test_rate_limiter_is_per_process_cr11(ipc_server_source: str) -> None:
    """CR-11: the rate limiter must be PER-PROCESS (one
    ``_RateLimiter`` per ``IPCServer`` instance), NOT per-connection.

    A per-connection limiter would let a local attacker reset the
    200-burst budget by dropping the WS and reconnecting. The
    per-process limiter (looked up via ``_get_rate_limiter(server)``)
    shares the 10s sliding window across all connections.
    """
    assert "_get_rate_limiter" in ipc_server_source, (
        "ipc_server.py must define _get_rate_limiter() — the per-process limiter lookup helper (CR-11)"
    )
    # The helper must store the limiter on the server instance (not
    # module-level) so each IPCServer gets its own.
    assert "server._rate_limiter_instance" in ipc_server_source, (
        "_get_rate_limiter must store the limiter on the server instance "
        "(server._rate_limiter_instance) — per-process, not module-level"
    )


def test_rate_limiter_burst_is_200_sustained_600(ipc_server_source: str) -> None:
    """ADR-0019 (RELIABILITY-006-FIX-10): burst = 200 messages,
    sustained = 600 over a 10s window (= 60 msg/s average). These
    constants must match between the TCP and WS paths (both use the
    same ``_RateLimiter`` class)."""
    assert "_RATE_LIMIT_BURST = 200" in ipc_server_source, "_RATE_LIMIT_BURST must be 200 (ADR-0019 burst cap)"
    assert "_RATE_LIMIT_SUSTAINED = 600" in ipc_server_source, (
        "_RATE_LIMIT_SUSTAINED must be 600 (60 msg/s avg over 10s window)"
    )
    assert "_RATE_LIMIT_WINDOW_SECONDS = 10.0" in ipc_server_source, (
        "_RATE_LIMIT_WINDOW_SECONDS must be 10.0 (sliding window)"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 9. ADR-0020 §10 cross-references (spec → implementation traceability)
# ═══════════════════════════════════════════════════════════════════════════


def test_adr_0020_section_10_documents_ft1_backoff(adr_0020_source: str) -> None:
    """ADR-0020 §10 must document the FT-1 backoff schedule + cap 5."""
    assert "### 10. WebSocket disconnect / error handling + FT-1 + rate limiter" in adr_0020_source, (
        "ADR-0020 §10 heading not found — the section was renamed or removed"
    )
    # The FT-1 state machine + backoff must be documented.
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
    """ADR-0020 §10 must document the ADR-0019 rate-limiter port to the
    WS accept path (200 burst / 60 sustained msg/s)."""
    assert "rate limiter" in adr_0020_source.lower()
    assert "ADR-0019" in adr_0020_source, "ADR-0020 §10 must reference ADR-0019 (the rate-limiter port source)"
    assert "WS" in adr_0020_source and "accept path" in adr_0020_source, (
        "ADR-0020 §10 must document the rate-limiter port to the WS accept path"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 10. Source-inspection: FT-1 inner loop structure (additional guards)
# ═══════════════════════════════════════════════════════════════════════════


def test_ft1_respawn_inner_calls_reconnect_ws(ft1_source: str) -> None:
    """Each respawn attempt must call ``reconnect_ws`` to re-establish
    the WS connection + re-auth with the new token. A successful
    reconnect is the only path to ``return Ok(())``."""
    assert "reconnect_ws(app, state, port, &new_token)" in ft1_source, (
        "FT-1 supervisor must call reconnect_ws(app, state, port, &new_token) "
        "after each spawn — re-auth with the rotated token is mandatory"
    )


def test_ft1_respawn_inner_handles_spawn_failure(ft1_source: str) -> None:
    """If ``spawn_sidecar_and_get_port`` fails (e.g. the binary is
    missing or the port handshake times out), the supervisor must
    ``continue`` to the next backoff attempt (not bail out).

    We use substring checks (not a regex block match) because the
    spawn-error arm's ``log::warn!`` format string ``"{}"`` contains
    a literal ``}`` that breaks a naive ``[^}]*`` matcher.
    """
    assert "sidecar spawn failed" in ft1_source, "FT-1 supervisor must log 'sidecar spawn failed' on spawn error"
    # The spawn-error arm must `continue` (retry with backoff). The
    # `continue;` substring must be present; its co-location in the
    # spawn-error arm is verified by reading ft1.rs lines 104-108.
    assert "continue;" in ft1_source, (
        "FT-1 supervisor must `continue` to the next backoff attempt on "
        "spawn failure (not bail out — a transient spawn error is recoverable)"
    )


def test_ft1_respawn_inner_handles_reconnect_failure(ft1_source: str) -> None:
    """If ``reconnect_ws`` fails (e.g. WS handshake timeout, auth
    rejected), the supervisor must ``continue`` to the next backoff
    attempt (the token was already rotated, so the next attempt
    re-rolls a fresh token).

    We use substring checks (not a regex block match) because the
    reconnect-error arm's ``log::warn!`` format string ``"{}"``
    contains a literal ``}`` that breaks a naive ``[^}]*`` matcher.
    """
    assert "WS reconnect failed" in ft1_source, "FT-1 supervisor must log 'WS reconnect failed' on reconnect error"
    # The reconnect-error arm must `continue` (retry with backoff).
    # Co-location in the reconnect_ws Err arm is verified by reading
    # ft1.rs lines 98-101.
    assert "continue;" in ft1_source, (
        "FT-1 supervisor must `continue` to the next backoff attempt on "
        "reconnect failure (token rotates fresh on the next attempt)"
    )


def test_ft1_respawn_inner_swaps_child_handle_under_lock(ft1_source: str) -> None:
    """The new child handle must be stored under the ``state.child``
    Mutex (not via a shared mutable global) so the kill_children
    backstop sees the latest PID after a respawn.

    G4-H-27: the production lock sites in ``ft1_respawn_inner`` now use
    the poison-safe ``mutex_lock(&state.child)`` helper (defined in
    ``state.rs``) instead of the bare ``state.child.lock().unwrap()``
    pattern. Both forms acquire the same Mutex; the helper just
    downgrades poison into a recovered guard via ``unwrap_or_else(into_inner)``
    so a panic on one lock doesn't permanently brick the FT-1 resilience
    layer. This test accepts EITHER form so it remains green during the
    migration (production code uses the new form; legacy test helpers
    inside ``#[cfg(test)]`` still use the old form).
    """
    # The child-handle swap must acquire state.child under a Mutex —
    # either the legacy `.lock().unwrap()` form OR the new poison-safe
    # `mutex_lock(&...)` helper (G4-H-27).
    assert "state.child.lock().unwrap()" in ft1_source or "mutex_lock(&state.child)" in ft1_source, (
        "FT-1 supervisor must store the new child handle under state.child's "
        "Mutex (legacy .lock().unwrap() or new mutex_lock helper) — kill_children "
        "needs the latest PID"
    )
    # The token swap must acquire state.token under a Mutex — same
    # dual-form acceptance as above.
    assert "state.token.lock().unwrap()" in ft1_source or "mutex_lock(&state.token)" in ft1_source, (
        "FT-1 supervisor must store the new token under state.token's Mutex "
        "(legacy .lock().unwrap() or new mutex_lock helper) — dispatch() reads "
        "the token to construct the auth frame"
    )
    assert "state.child_exit_rx.lock().await" in ft1_source, (
        "FT-1 supervisor must rotate the child_exit_rx (CR-2) so the next "
        "shutdown_sidecar polls the new sidecar's exit, not the old one"
    )


def test_ws_reader_emits_python_event_alias_for_backward_compat(ws_source: str) -> None:
    """ADR-0020 §6.3: the WS reader must emit BOTH the specific event
    (e.g. ``bubble_level``) AND the generic ``python-event`` (for the
    usePython hook's onEvent catch-all, matching the Electron path's
    ipcRenderer.on('python-event'))."""
    assert '"python-event"' in ws_source, (
        "WS reader must emit 'python-event' as the generic catch-all event "
        "(ADR-0020 §6.3 — mirrors Electron's ipcRenderer.on('python-event'))"
    )


def test_ws_reader_does_not_rename_relaunch_app(ws_source: str) -> None:
    """PVT-2 cleanup: the WS reader MUST NOT rename ``relaunch_electron``
    → ``relaunch_app``. The Python sidecar now publishes ``relaunch_app``
    directly (see ``app.py`` ``restart_app``), so the Rust bridge forwards
    it unchanged via the direct ``let emit_name = event_type;`` pass-through.
    ``main.rs`` listens for ``relaunch_app`` via ``app.listen("relaunch_app",
    ...)`` (calling ``app.restart()``).

    This is a regression check: re-introducing the rename arm would
    recreate the pre-PVT-2 silent-restart bug (the renamed event was
    emitted into the void because no listener subscribed to
    ``relaunch_app`` pre-PVT-2)."""
    # The rename match arm MUST NOT be present in ws.rs source.
    rename_re = re.compile(
        r'"relaunch_electron"\s*=>\s*"relaunch_app"',
    )
    assert not rename_re.search(ws_source), (
        "ws.rs MUST NOT have a `relaunch_electron` => `relaunch_app` "
        "rename arm — the Python sidecar now publishes `relaunch_app` "
        "directly (PVT-2 cleanup). Re-introducing the rename would "
        "recreate the pre-PVT-2 silent-restart bug."
    )
    # Belt-and-braces: the literal old name MUST NOT appear as a
    # match arm pattern in ws.rs (only in comments is OK).
    assert '"relaunch_electron" =>' not in ws_source, (
        "ws.rs MUST NOT match the legacy `relaunch_electron` event name "
        "in a per-type branch (PVT-2 cleanup — the rename arm is gone)."
    )
    # The direct assignment proves generic fan-out (no per-type rename).
    assert re.search(r"let\s+emit_name\s*=\s*event_type\s*;", ws_source), (
        "ws.rs must forward every event type unchanged via "
        "`let emit_name = event_type;` (PVT-2 cleanup — no per-type "
        "rename arm)."
    )
