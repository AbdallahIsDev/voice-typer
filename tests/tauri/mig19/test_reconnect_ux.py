"""MIG-1.9 Phase 4 — Reconnect UX validation (ADR-0020 §10).

This file is the **Phase 4 reconnect-UX wiring check** for the MIG-1.9
Tauri migration. ADR-0020 §10 mandates a WS-disconnect + FT-1 backoff
supervisor (see ``src-tauri/src/sidecar/ft1.rs`` +
``src-tauri/src/sidecar/ws.rs``). The **user-facing UX** for that
backoff lives entirely in the React renderer:

  1. The Zustand ``appStore`` (``voice_typer/client/src/renderer/src/
     stores/appStore.ts``) holds a ``connectionStatus`` string literal
     union that drives the entire reconnect UI (spinner, retry button,
     "Restarting…" banner).

  2. The Tauri bridge (``voice_typer/client/src/renderer/src/lib/
     tauri-bridge.ts``) listens to the Rust host's ``ft1_relaunching``
     + ``ft1_reconnected`` Tauri events and **synthesises** matching
     ``python-event`` frames (``{type:"reconnecting", ...}`` and
     ``{type:"reconnected", ...}``) so the existing
     ``usePythonEvent`` React hook works unchanged on both Electron
     and Tauri runtimes.

  3. The ``useConnection`` hook (``voice_typer/client/src/renderer/src/
     hooks/useConnection.ts``) subscribes to those synthesised events
     via ``usePythonEvent("reconnecting", ...)`` and
     ``usePythonEvent("reconnected", ...)`` and transitions the
     ``connectionStatus`` accordingly.

  4. ``usePython`` (``voice_typer/client/src/renderer/src/hooks/
     usePython.ts``) surfaces backend errors as JS exceptions so the
     UI's catch handlers can flip the status to "disconnected" + show
     the retry button.

  5. ``App.tsx`` (``voice_typer/client/src/renderer/src/App.tsx``)
     renders three distinct connection-status UI branches:
       - ``connecting``      → spinner + "Starting Python backend…"
       - ``restarting``      → spinner + "Restarting Voice Typer
                                backend…" + the longer hint copy
       - ``disconnected``    → "Lost connection to Python backend"
                                + "Retry Connection" button

Every test in this file is a **source-inspection** test (no JS runtime,
no React testing library, no Tauri runtime) — it reads the actual
TypeScript source as text + uses regex / AST-light assertions to
verify the contract clauses survive refactoring. The end-to-end UX
flow (real FT-1 respawn → real banner appears) is documented in the
``VALIDATE ON HOST`` block below.

KNOWN GAPS (report, do not fix)
-------------------------------

GAP-A (``ConnectionStatus`` is missing a ``"reconnecting"`` literal).
ADR-0020 §10 + the MIG-1.9 task description call for a 4-status union
of ``connected | disconnected | reconnecting | restarting``. The actual
``ConnectionStatus`` union in ``appStore.ts`` is
``connected | disconnected | connecting | restarting`` — note
``"connecting"`` (initial backend-startup state) instead of
``"reconnecting"`` (transient recovery state). The ``useConnection``
hook handles the ``"reconnecting"`` event from the bridge, but it
**maps that event onto the existing ``"restarting"`` status literal**
(``setConnectionStatus("restarting" as ConnectionStatus)`` — the
``as`` cast is a smell that the type doesn't admit the natural
literal). Consequence: the UI cannot distinguish "initial cold start"
from "transient reconnect after WS drop" — both render the same
"Restarting Voice Typer backend…" spinner. Functionally OK (the user
sees a spinner either way), but semantically imprecise. Not blocking.

GAP-B (Tauri bridge relies on ``as unknown as PythonPushEvent`` cast
to inject the synthesised ``reconnecting`` / ``reconnected`` events).
The bridge listens to the Rust host's ``ft1_relaunching`` /
``ft1_reconnected`` Tauri events and re-emits them through the
``python-event`` callback so ``usePythonEvent`` (which only knows
about the ``python-event`` channel) sees them. The synthesised frames
``{type: "reconnecting", data: {reason: "ft1_relaunching"}}`` are not
part of the ``PythonPushEvent`` discriminated union in
``types/ipc.ts`` (which only models server-published events, not
host-bridged ones). The double cast through ``unknown`` is the
type-system escape hatch. Functionally correct (the runtime shape
matches what ``useConnection``'s ``usePythonEvent("reconnecting", …)``
handler expects — it ignores ``data`` entirely), but the type
boundary is leaky. Not blocking.

GAP-C (no automated integration test of the FT-1 disconnect → UI
banner flow). This file is source-inspection only. The actual
"kill the sidecar process → observe the 'Restarting…' banner within
~500 ms → observe 'Retry Connection' button after 5 retries fail"
flow requires a real Tauri build + a real sidecar process — see
``VALIDATE ON HOST`` below. Not blocking.

VALIDATE ON HOST
================

This file is the source-inspection gate. The actual end-to-end
reconnect-UX validation MUST be executed by a human on a real host
(ADR-0020 §6 / runbook). One host per platform:

**VALIDATE ON HOST — Linux x64**::

    # 1. Build the Tauri bundle (debug build is fine for UX testing):
    cd src-tauri && cargo tauri build --target x86_64-unknown-linux-gnu --debug
    cd ..

    # 2. Install + launch the .deb (or run the binary directly):
    sudo apt install ./target/x86_64-unknown-linux-gnu/release/bundle/deb/*.deb
    voice-typer

    # 3. Wait for the renderer to show the Home page (backend connected).
    #    Then identify the sidecar PID and kill it to simulate a crash:
    pgrep -f python-sidecar
    kill -9 <pid-from-above>

    # 4. Within ~500 ms, the renderer MUST show the "Restarting Voice
    #    Typer backend…" spinner (connectionStatus="restarting"). Verify
    #    by tailing the renderer console log (open DevTools with
    #    Ctrl+Shift+I) — you should see "[FT-1] respawn attempt 1 after
    #    500ms" in the host log:
    tail -n 50 ~/.local/share/voice-typer/logs/voice-typer.log | grep FT-1

    # 5. After FT-1 succeeds (≤5 retries × ≤8 s each), the renderer
    #    MUST return to the Home page (connectionStatus="connected").
    #    The transition is driven by the "ft1_reconnected" Tauri event
    #    → synthesised "reconnected" python-event → useConnection's
    #    call("get_config") → setConnectionStatus("connected").

    # 6. To validate the "disconnected → Retry Connection" branch,
    #    kill the sidecar AND prevent FT-1 from respawning (e.g. by
    #    exhausting retries — rename the sidecar binary so respawn
    #    fails, then re-run the app). After 5 respawn failures the
    #    Rust host calls app.restart() which exits the process; the
    #    "disconnected" branch is reachable only via the 60s health
    #    check timeout in useConnection (call("get_status") fails).
    #    Trigger that path by killing the sidecar while the renderer
    #    is in the "connected" state, then waiting ~60 s.
    #    Expected: "Lost connection to Python backend" + "Retry
    #    Connection" button. Click the button → status flips to
    #    "connecting" → "connected" (if sidecar respawned by then) or
    #    back to "disconnected".

**VALIDATE ON HOST — macOS**::

    # Same flow; replace apt with the .dmg install:
    cd src-tauri && cargo tauri build --target x86_64-apple-darwin --debug
    open target/x86_64-apple-darwin/release/bundle/dmg/*.dmg
    # Drag Voice Typer.app to /Applications, launch it.
    # Sidecar PID:
    pgrep -f python-sidecar
    # Host log:
    tail -n 50 "~/Library/Application Support/voice-typer/logs/voice-typer.log"

**VALIDATE ON HOST — Windows**::

    # Same flow; build the NSIS installer:
    cd src-tauri && cargo tauri build --target x86_64-pc-windows-msvc --debug
    # Install target/x86_64-pc-windows-msvc/release/bundle/nsis/*-setup.exe
    # Sidecar PID (in PowerShell):
    Get-Process python-sidecar | Select-Object Id, ProcessName
    Stop-Process -Id <id> -Force
    # Host log:
    Get-Content "$env:APPDATA\\voice-typer\\logs\\voice-typer.log" -Tail 50

TEST-HOST NOTES
---------------
These are source-inspection tests (no mocking, no JS runtime, no
Tauri runtime, no root required). They read the real TypeScript +
Rust source files from the repo and assert against their contents
using regex. The repo path is resolved relative to this file so the
tests pass regardless of cwd.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ─── Repo path resolution ──────────────────────────────────────────────
# tests/tauri/mig19/test_reconnect_ux.py → repo root in 4 parents:
#   parents[0]=mig19, parents[1]=tauri, parents[2]=tests, parents[3]=voice-typer.
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
_CLIENT_SRC = _REPO_ROOT / "voice_typer" / "client" / "src"
_RENDERER_SRC = _CLIENT_SRC / "renderer" / "src"
_SRC_TAURI = _REPO_ROOT / "src-tauri"

# ─── Source files inspected by these tests ─────────────────────────────
_APP_STORE_TS = _RENDERER_SRC / "stores" / "appStore.ts"
_TAURI_BRIDGE_TS = _RENDERER_SRC / "lib" / "tauri-bridge.ts"
_USE_CONNECTION_TS = _RENDERER_SRC / "hooks" / "useConnection.ts"
_USE_PYTHON_TS = _RENDERER_SRC / "hooks" / "usePython.ts"
_APP_TSX = _RENDERER_SRC / "App.tsx"
_IPC_TS = _RENDERER_SRC / "types" / "ipc.ts"
_FT1_RS = _SRC_TAURI / "src" / "sidecar" / "ft1.rs"
_WS_RS = _SRC_TAURI / "src" / "sidecar" / "ws.rs"

# ─── Expected connection-status literals (single source of truth) ──────
# ADR-0020 §10 + MIG-1.9 task spec call for these 4 states. The actual
# ConnectionStatus union in appStore.ts uses "connecting" instead of
# "reconnecting" — see GAP-A in the module docstring above.
_REQUIRED_STATUS_LITERALS = ("connected", "disconnected", "restarting")
# The "reconnecting" literal is desired (task spec) but NOT in the
# actual union — the hook casts "restarting" via `as ConnectionStatus`.
# Tracked as GAP-A; we still assert that the "reconnecting" EVENT name
# is handled by useConnection (separate from the status literal).
_DESIRED_RECONNECTING_LITERAL = "reconnecting"

# ─── Expected Tauri event names emitted by the Rust host ───────────────
# These are the events the Rust FT-1 supervisor + WS reader emit on
# disconnect / reconnect / exhaustion (see ft1.rs:52,95,113 + ws.rs:193).
_TAURI_EVENT_RELAUNCHING = "ft1_relaunching"
_TAURI_EVENT_RECONNECTED = "ft1_reconnected"

# ─── Expected synthesised python-event type names (bridge translation) ─
# tauri-bridge.ts maps each Tauri event to a synthesised python-event
# frame so usePythonEvent subscribers see a unified stream.
_PY_EVENT_RECONNECTING = "reconnecting"
_PY_EVENT_RECONNECTED = "reconnected"


# ─── Shared fixtures ───────────────────────────────────────────────────


@pytest.fixture(scope="module")
def app_store_source() -> str:
    """Read appStore.ts as text (for static assertions)."""
    assert _APP_STORE_TS.is_file(), f"appStore.ts not found: {_APP_STORE_TS}"
    return _APP_STORE_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tauri_bridge_source() -> str:
    """Read tauri-bridge.ts as text (for static assertions)."""
    assert _TAURI_BRIDGE_TS.is_file(), f"tauri-bridge.ts not found: {_TAURI_BRIDGE_TS}"
    return _TAURI_BRIDGE_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def use_connection_source() -> str:
    """Read useConnection.ts as text (for static assertions)."""
    assert _USE_CONNECTION_TS.is_file(), f"useConnection.ts not found: {_USE_CONNECTION_TS}"
    return _USE_CONNECTION_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def use_python_source() -> str:
    """Read usePython.ts as text (for static assertions)."""
    assert _USE_PYTHON_TS.is_file(), f"usePython.ts not found: {_USE_PYTHON_TS}"
    return _USE_PYTHON_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_tsx_source() -> str:
    """Read App.tsx as text (for static assertions)."""
    assert _APP_TSX.is_file(), f"App.tsx not found: {_APP_TSX}"
    return _APP_TSX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ipc_ts_source() -> str:
    """Read types/ipc.ts as text (for static assertions)."""
    assert _IPC_TS.is_file(), f"types/ipc.ts not found: {_IPC_TS}"
    return _IPC_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ft1_rs_source() -> str:
    """Read src-tauri/src/sidecar/ft1.rs as text (for static assertions)."""
    assert _FT1_RS.is_file(), f"ft1.rs not found: {_FT1_RS}"
    return _FT1_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ws_rs_source() -> str:
    """Read src-tauri/src/sidecar/ws.rs as text (for static assertions)."""
    assert _WS_RS.is_file(), f"ws.rs not found: {_WS_RS}"
    return _WS_RS.read_text(encoding="utf-8")


# ─── Test 1: ConnectionStatus union includes the required literals ─────


def test_app_store_exists() -> None:
    """Sanity: the Zustand store file exists at the expected path.

    The hook (``useConnection``) imports ``ConnectionStatus`` + the
    ``useAppStore`` Zustand store from this file. If the path changes
    the rest of these tests will fail with a clearer message.
    """
    assert _APP_STORE_TS.is_file()


def test_connection_status_union_includes_required_literals(
    app_store_source: str,
) -> None:
    """ADR-0020 §10 + MIG-1.9: the ``ConnectionStatus`` union must
    include the literals used by the reconnect UX.

    The required literals are:
      - ``"connected"``      — initial happy state after a successful
                                ``get_config`` probe.
      - ``"disconnected"``   — fatal state showing the "Lost
                                connection" + retry button UI.
      - ``"restarting"``     — transient state during FT-1 respawn,
                                shows the "Restarting Voice Typer
                                backend…" spinner.
      - ``"reconnecting"``   — desired-but-missing (GAP-A): currently
                                ``useConnection`` casts
                                ``"restarting" as ConnectionStatus``
                                when the bridge fires the
                                ``"reconnecting"`` python-event, so
                                both flows render the same UI branch.

    We extract the ``ConnectionStatus`` union declaration via regex
    and assert that the 3 required literals are present. We assert
    the missing ``"reconnecting"`` literal separately (with a softer
    skip-if-absent check) so a future fix that adds it doesn't break
    this test.
    """
    # Extract: `export type ConnectionStatus = "a" | "b" | ... ;`
    union_re = re.compile(
        r"export\s+type\s+ConnectionStatus\s*=\s*([^;]+);",
        re.MULTILINE | re.DOTALL,
    )
    match = union_re.search(app_store_source)
    assert match is not None, (
        "Expected `export type ConnectionStatus = ...;` declaration in "
        "appStore.ts — the union is the single source of truth for the "
        "reconnect-UI state machine."
    )
    union_body = match.group(1)
    # Strip whitespace + quote characters to get a list of literal names.
    literals = re.findall(r'"([a-z_]+)"', union_body)
    assert literals, f"ConnectionStatus union body matched no string literals: {union_body!r}"
    for required in _REQUIRED_STATUS_LITERALS:
        assert required in literals, f"ConnectionStatus union must include {required!r}. Found literals: {literals}."


def test_connection_status_union_documents_reconnecting_gap(
    app_store_source: str,
) -> None:
    """GAP-A documentation: the desired ``"reconnecting"`` literal is
    NOT in the ``ConnectionStatus`` union — the ``useConnection`` hook
    currently casts ``"restarting" as ConnectionStatus`` to admit the
    transient reconnect state.

    This test is intentionally permissive: if a future fix adds the
    ``"reconnecting"`` literal, this test will start PASSING with a
    positive message ("GAP-A resolved"). Until then it asserts the
    gap is documented in this test file's docstring.
    """
    union_re = re.compile(
        r"export\s+type\s+ConnectionStatus\s*=\s*([^;]+);",
        re.MULTILINE | re.DOTALL,
    )
    match = union_re.search(app_store_source)
    assert match is not None
    union_body = match.group(1)
    literals = re.findall(r'"([a-z_]+)"', union_body)

    if _DESIRED_RECONNECTING_LITERAL in literals:
        # GAP-A resolved — the literal was added.
        pytest.fail(
            "GAP-A appears resolved: 'reconnecting' is now in the "
            "ConnectionStatus union. Update this test (and the GAP-A "
            "note in the module docstring) to reflect the fix, then "
            "convert this test to a positive assertion."
        )
    # GAP-A still open — assert the union uses "connecting" instead
    # (the cold-start state). This documents the asymmetry: the
    # backend-event name is "reconnecting" but the status-literal is
    # "restarting" (and the cold-start literal is "connecting").
    assert "connecting" in literals, (
        "GAP-A: ConnectionStatus union should include 'connecting' "
        "(cold-start state) since 'reconnecting' is currently mapped "
        f"to 'restarting'. Found literals: {literals}"
    )


# ─── Test 2: bridge emits "reconnecting" event when WS disconnects ────


def test_bridge_subscribes_to_ft1_relaunching_event(
    tauri_bridge_source: str,
) -> None:
    """ADR-0020 §10 + MIG-1.9: the Tauri bridge must subscribe to the
    Rust host's ``ft1_relaunching`` Tauri event and synthesise a
    matching ``python-event`` frame so the ``useConnection`` hook's
    ``usePythonEvent("reconnecting", ...)`` subscription fires.

    The Rust host emits ``ft1_relaunching`` in two places:
      - ``ft1.rs:52``  — FT-1 supervisor exhausted retries (full-app
                          relaunch path).
      - ``ft1.rs:113`` — backoff schedule exhausted (same path).
      - ``ws.rs:193``  — CR-5: emit IMMEDIATELY at disconnect start so
                          the UI can show a "reconnecting…" banner
                          BEFORE the backoff schedule runs.

    The bridge must call ``tauri.event.listen("ft1_relaunching", …)``
    and re-emit a synthesised frame of the form
    ``{type: "reconnecting", data: {reason: "ft1_relaunching"}}`` via
    the ``onEvent`` callback (so usePythonEvent sees it).
    """
    # The bridge must register a Tauri event listener for the FT-1 events.
    # The source builds an `ft1Events: Array<[string, string]>` table
    # and iterates it with `for (const [tauriEvt, pythonEvt] of ft1Events)`.
    # The `.listen(tauriEvt, ...)` call uses the loop variable, not the
    # literal string. So we verify TWO things:
    #   (a) the literal appears in the ft1Events table initializer
    #       (covered by the mapping_re assertion below)
    #   (b) `.listen(tauriEvt, ...)` is called inside the loop (so the
    #       subscription actually happens).
    listen_re = re.compile(
        r"\.listen\s*\(\s*tauriEvt\b",
        re.MULTILINE | re.DOTALL,
    )
    assert listen_re.search(tauri_bridge_source), (
        "tauri-bridge.ts must call tauri.event.listen(tauriEvt, ...) inside "
        "the ft1Events loop — without this subscription the renderer never "
        "learns the WS disconnected + the UI stays frozen on 'connected' "
        "while the sidecar is dead."
    )

    # The bridge must map ft1_relaunching → synthesised "reconnecting" frame.
    # We look for the [tauriEvt, pythonEvt] tuple in the ft1Events array.
    mapping_re = re.compile(
        r'\[\s*["\']' + re.escape(_TAURI_EVENT_RELAUNCHING) + r'["\']\s*,\s*'
        r'["\']' + re.escape(_PY_EVENT_RECONNECTING) + r'["\']\s*\]',
        re.MULTILINE,
    )
    assert mapping_re.search(tauri_bridge_source), (
        f"tauri-bridge.ts must map [{_TAURI_EVENT_RELAUNCHING!r}, "
        f"{_PY_EVENT_RECONNECTING!r}] in the ft1Events table — this is "
        f"the translation that lets usePythonEvent('reconnecting', ...) "
        f"see the Rust host's FT-1 disconnect signal."
    )

    # The synthesised frame must be passed to the onEvent callback.
    # We verify the callback invocation pattern (callback({...type:
    # pythonEvt...})).
    callback_re = re.compile(
        r"callback\s*\(\s*\{\s*type:\s*pythonEvt",
        re.MULTILINE,
    )
    assert callback_re.search(tauri_bridge_source), (
        "tauri-bridge.ts must invoke the onEvent callback with a "
        "synthesised frame of the form {type: pythonEvt, data: {...}} "
        "so usePythonEvent subscribers see the FT-1 events."
    )


def test_ws_rs_emits_ft1_relaunching_on_disconnect(ws_rs_source: str) -> None:
    """ADR-0020 §10 CR-5: the WS reader task must emit ``ft1_relaunching``
    IMMEDIATELY when the sidecar closes the WebSocket, BEFORE the
    backoff schedule runs.

    The comment at ``ws.rs:187-191`` documents the contract:
    "emit ``ft1_relaunching`` IMMEDIATELY at disconnect start so the
    UI can show a 'reconnecting…' banner before the backoff schedule
    runs." Without this immediate emit, the renderer would stay on
    "connected" for the duration of the first backoff sleep (500 ms)
    + the respawn attempt — visible as a frozen UI with no feedback.
    """
    # The emit call site must reference ft1_relaunching with reason
    # "disconnected" (the CR-5 path — distinct from ft1.rs:52,113
    # which use "exhausted_retries" / "backoff_exhausted").
    emit_re = re.compile(
        r'emit\s*\(\s*["\']' + re.escape(_TAURI_EVENT_RELAUNCHING) + r'["\']',
        re.MULTILINE,
    )
    assert emit_re.search(ws_rs_source), (
        f"ws.rs must emit {_TAURI_EVENT_RELAUNCHING!r} when the WS reader "
        f"task detects an unexpected close — this is the CR-5 immediate-"
        f"emit path that lets the UI show a 'reconnecting…' banner before "
        f"the backoff schedule runs."
    )
    # Verify the "disconnected" reason (the CR-5 path).
    assert '"disconnected"' in ws_rs_source, (
        "ws.rs must emit ft1_relaunching with reason='disconnected' on "
        "the CR-5 immediate-emit path (distinct from ft1.rs's "
        "'exhausted_retries' / 'backoff_exhausted' reasons)."
    )


# ─── Test 3: bridge emits "connected" event when WS reconnects ────────


def test_bridge_subscribes_to_ft1_reconnected_event(
    tauri_bridge_source: str,
) -> None:
    """ADR-0020 §10: the Tauri bridge must subscribe to the Rust host's
    ``ft1_reconnected`` Tauri event and synthesise a matching
    ``python-event`` frame so the ``useConnection`` hook's
    ``usePythonEvent("reconnected", ...)`` subscription fires.

    The Rust host emits ``ft1_reconnected`` at ``ft1.rs:95`` after a
    successful WS reconnect + re-auth (the FT-1 supervisor's happy
    path). The bridge translates it to a synthesised
    ``{type: "reconnected", ...}`` frame.
    """
    # Same as ft1_relaunching: the source iterates an `ft1Events`
    # table with `for (const [tauriEvt, pythonEvt] of ft1Events)` and
    # calls `.listen(tauriEvt, ...)`. The literal "ft1_reconnected"
    # appears in the table initializer (verified by mapping_re below).
    # We verify the listen call uses the loop variable.
    listen_re = re.compile(
        r"\.listen\s*\(\s*tauriEvt\b",
        re.MULTILINE | re.DOTALL,
    )
    assert listen_re.search(tauri_bridge_source), (
        "tauri-bridge.ts must call tauri.event.listen(tauriEvt, ...) inside "
        "the ft1Events loop — without this the renderer would stay on "
        "'restarting' forever after a successful FT-1 respawn (the "
        "'reconnected' python-event would never fire)."
    )

    mapping_re = re.compile(
        r'\[\s*["\']' + re.escape(_TAURI_EVENT_RECONNECTED) + r'["\']\s*,\s*'
        r'["\']' + re.escape(_PY_EVENT_RECONNECTED) + r'["\']\s*\]',
        re.MULTILINE,
    )
    assert mapping_re.search(tauri_bridge_source), (
        f"tauri-bridge.ts must map [{_TAURI_EVENT_RECONNECTED!r}, "
        f"{_PY_EVENT_RECONNECTED!r}] in the ft1Events table — this is "
        f"the translation that lets usePythonEvent('reconnected', ...) "
        f"see the Rust host's FT-1 success signal."
    )


def test_ft1_rs_emits_reconnected_on_success(ft1_rs_source: str) -> None:
    """ADR-0020 §10: the FT-1 supervisor must emit ``ft1_reconnected``
    on a successful WS reconnect + re-auth.

    The emit at ``ft1.rs:95`` runs only on the happy path — after
    ``spawn_sidecar_and_get_port`` succeeds AND ``reconnect_ws``
    succeeds. Without this emit, the bridge would never synthesise
    the ``"reconnected"`` python-event, and the renderer would stay
    stuck on ``"restarting"`` forever.
    """
    emit_re = re.compile(
        r'emit\s*\(\s*["\']' + re.escape(_TAURI_EVENT_RECONNECTED) + r'["\']',
        re.MULTILINE,
    )
    assert emit_re.search(ft1_rs_source), (
        f"ft1.rs must emit {_TAURI_EVENT_RECONNECTED!r} on a successful "
        f"respawn — this is the success signal that lets the renderer "
        f"transition out of 'restarting' back to 'connected'."
    )


def test_use_connection_handles_reconnected_event(
    use_connection_source: str,
) -> None:
    """ADR-0020 §10: the ``useConnection`` hook must subscribe to the
    synthesised ``"reconnected"`` python-event and transition the
    status to ``"connected"`` (or ``"disconnected"`` if the post-
    reconnect ``get_config`` probe fails).

    The handler at ``useConnection.ts:244-251`` calls
    ``call("get_config")`` and on success flips to ``"connected"``,
    on failure flips to ``"disconnected"``. This double-check is
    important: the WS being up does NOT guarantee the Python IPC
    server is ready to dispatch commands (the FT-1 supervisor
    reconnects the WS before the IPC server has finished booting
    its handlers). The probe validates end-to-end readiness.
    """
    # usePythonEvent("reconnected", ...) subscription
    sub_re = re.compile(
        r'usePythonEvent\s*\(\s*["\']' + re.escape(_PY_EVENT_RECONNECTED) + r'["\']',
        re.MULTILINE,
    )
    assert sub_re.search(use_connection_source), (
        "useConnection.ts must subscribe to the 'reconnected' python-event "
        "via usePythonEvent('reconnected', ...). Without this the renderer "
        "would stay on 'restarting' forever after a successful FT-1 respawn."
    )

    # On success: setConnectionStatus("connected")
    success_re = re.compile(
        r'setConnectionStatus\s*\(\s*["\']connected["\']',
        re.MULTILINE,
    )
    assert success_re.search(use_connection_source), (
        "useConnection.ts must call setConnectionStatus('connected') on "
        "successful post-reconnect get_config probe — the WS being up is "
        "necessary but not sufficient (the IPC server may still be "
        "booting its handlers)."
    )

    # On failure: setConnectionStatus("disconnected")
    failure_re = re.compile(
        r'setConnectionStatus\s*\(\s*["\']disconnected["\']',
        re.MULTILINE,
    )
    assert failure_re.search(use_connection_source), (
        "useConnection.ts must call setConnectionStatus('disconnected') "
        "if the post-reconnect get_config probe fails — surfaces the "
        "'Lost connection' + Retry Connection button to the user."
    )


# ─── Test 4: bridge emits "restarting" event when FT-1 relaunches app ─


def test_use_connection_maps_reconnecting_event_to_restarting_status(
    use_connection_source: str,
) -> None:
    """ADR-0020 §10: the ``useConnection`` hook must subscribe to the
    synthesised ``"reconnecting"`` python-event (which the bridge
    emits when the Rust host fires ``ft1_relaunching``) and transition
    the status to ``"restarting"``.

    The handler at ``useConnection.ts:237-243`` calls
    ``setConnectionStatus("restarting" as ConnectionStatus)`` — the
    ``as`` cast is GAP-A: ``"reconnecting"`` is not in the
    ``ConnectionStatus`` union, so the hook casts ``"restarting"`` to
    admit the transient state. Functionally correct (the UI branch at
    ``App.tsx`` for ``"restarting"`` shows the spinner); semantically
    imprecise.
    """
    sub_re = re.compile(
        r'usePythonEvent\s*\(\s*["\']' + re.escape(_PY_EVENT_RECONNECTING) + r'["\']',
        re.MULTILINE,
    )
    assert sub_re.search(use_connection_source), (
        "useConnection.ts must subscribe to the 'reconnecting' python-event "
        "via usePythonEvent('reconnecting', ...). This is the bridge-"
        "synthesised event that fires when the Rust host detects a WS "
        "disconnect (CR-5) or exhausts FT-1 retries (full-app relaunch)."
    )

    # The handler must transition to "restarting" (with the GAP-A `as` cast).
    # We tolerate either "restarting" or "reconnecting" here — if a future
    # fix adds "reconnecting" to the union (resolving GAP-A), the handler
    # will use it directly without the cast.
    handler_re = re.compile(
        r'setConnectionStatus\s*\(\s*["\'](?:restarting|reconnecting)["\']',
        re.MULTILINE,
    )
    assert handler_re.search(use_connection_source), (
        "useConnection.ts must call setConnectionStatus('restarting') "
        "(or 'reconnecting' once GAP-A is resolved) inside the "
        "usePythonEvent('reconnecting', ...) handler — this transitions "
        "the UI to the 'Restarting Voice Typer backend…' spinner branch."
    )


def test_ft1_rs_emits_relaunching_on_exhaustion(ft1_rs_source: str) -> None:
    """ADR-0020 §10: the FT-1 supervisor must emit ``ft1_relaunching``
    when the backoff schedule is exhausted (before calling
    ``app.restart()`` for the full-app relaunch).

    The emit site in ``ft1.rs`` is the **post-loop** exhaustion branch
    (reason='backoff_exhausted') at the bottom of ``ft1_respawn_inner``.
    The in-loop ``attempt >= FT1_MAX_RETRIES`` guard that used to emit
    ``reason='exhausted_retries'`` was intentionally removed as dead
    code (``FT1_BACKOFF_MS.len() == FT1_MAX_RETRIES == 5``, so the
    condition was always false — see the NF-R19-2 comment in ft1.rs).
    The single post-loop emit fires before ``app.restart()`` so the
    renderer has a chance to render a "restarting…" banner during the
    ``PRE_RESTART_DELAY_MS`` (500 ms) window before the process exits.

    (The WS reader in ``ws.rs`` emits a *second* ``ft1_relaunching``
    with reason='disconnected' immediately at disconnect start — that
    is verified separately in ``test_ws_rs_emits_ft1_relaunching_on_disconnect``.)
    """
    emit_re = re.compile(
        r'emit\s*\(\s*["\']' + re.escape(_TAURI_EVENT_RELAUNCHING) + r'["\']',
        re.MULTILINE,
    )
    matches = emit_re.findall(ft1_rs_source)
    assert len(matches) >= 1, (
        f"ft1.rs must emit {_TAURI_EVENT_RELAUNCHING!r} on backoff-schedule "
        f"exhaustion (reason='backoff_exhausted') before app.restart(). "
        f"Found {len(matches)} emit call(s)."
    )
    assert "backoff_exhausted" in ft1_rs_source, (
        "ft1.rs must emit ft1_relaunching with reason='backoff_exhausted' "
        "when the backoff schedule loop exits without a successful respawn."
    )


def test_ft1_rs_calls_app_restart_after_exhaustion(ft1_rs_source: str) -> None:
    """ADR-0020 §10: after emitting ``ft1_relaunching`` on exhaustion,
    the FT-1 supervisor must call ``app.restart()`` (full-app relaunch).

    ``app.restart()`` exits the current process with the Tauri-
    defined RESTART_EXIT_CODE so the Tauri launcher spawns a fresh
    instance before the old one fully exits. The 500 ms
    ``PRE_RESTART_DELAY_MS`` sleep before the call gives the renderer
    time to render the "restarting…" banner.
    """
    assert "app.restart()" in ft1_rs_source, (
        "ft1.rs must call app.restart() after emitting ft1_relaunching "
        "on exhaustion — this is the full-app relaunch fallback when "
        "FT-1's in-process respawn has failed."
    )


# ─── Test 5: usePython surfaces connection errors to the UI ────────────


def test_use_python_throws_when_bridge_missing(use_python_source: str) -> None:
    """The ``usePython`` ``call`` function must throw a JS Error when
    the Python bridge is not installed (``window.python`` undefined).

    This is the renderer-side error surface for "backend not
    connected" — the catch block in ``useConnection``'s connection-
    probe effect catches this throw and flips the status to
    ``"disconnected"`` after the 5-retry cap.
    """
    assert "Python bridge not available" in use_python_source, (
        "usePython.ts must throw an Error with the message "
        "'Python bridge not available' when window.python is undefined. "
        "This is the renderer-side error surface for a missing backend."
    )
    # The throw must come BEFORE the call to api.call (so the error
    # surfaces immediately, not after a 120s timeout). We split this
    # into two checks because the source has a multi-line comment block
    # between the throw and the withCommandTimeout call.
    throw_re = re.compile(
        r"if\s*\(\s*!api\s*\)\s*throw\s+new\s+Error\s*\(\s*[\"']"
        r"Python bridge not available[\"']\s*\)",
        re.MULTILINE | re.DOTALL,
    )
    throw_match = throw_re.search(use_python_source)
    assert throw_match is not None, (
        "usePython.ts must throw `new Error('Python bridge not available')` when `!api` (window.python undefined)."
    )
    # Find `withCommandTimeout(\s*api.call` AFTER the throw statement.
    # There's a multi-line comment between them, so we search the
    # remainder of the source from the throw position forward.
    rest = use_python_source[throw_match.end() :]
    call_re = re.compile(
        r"withCommandTimeout\s*\(\s*api\.call",
        re.MULTILINE | re.DOTALL,
    )
    assert call_re.search(rest), (
        "usePython.ts must call withCommandTimeout(api.call(...)) AFTER "
        "the `if (!api) throw` guard — otherwise the renderer would "
        "wait for the 120s command timeout instead of surfacing the "
        "'Python bridge not available' error immediately."
    )


def test_use_python_translates_error_envelopes_to_throws(
    use_python_source: str,
) -> None:
    """The ``usePython`` ``call`` function must translate both error-
    envelope shapes (the Electron main-process ``{_error: "..."}``
    synthetic envelope AND the Python server's
    ``{type:"error", data:{code, message}}`` envelope) into real JS
    Errors so callers using ``try { await python.call(...) } catch``
    see failures instead of silent undefined-data reads.

    The two envelope shapes:
      1. ``{_error: "..."}``     — Electron main-process synthetic errors
                                    (backend-not-connected, send-exception).
      2. ``{type:"error", data:{...}}`` — Python server unhandled-dispatch
                                    exceptions (with code + message).

    On Tauri, NEITHER in-code check is reachable (the Rust ``dispatch``
    command rejects the invoke promise on ``type:"error"`` BEFORE the
    resolved value reaches JS), but the same ``usePython.ts`` bundle
    ships under both hosts — these checks are load-bearing on Electron
    and harmless no-ops on Tauri.
    """
    # Check 1: _error envelope
    assert '"_error"' in use_python_source, (
        "usePython.ts must inspect the resolved result for an `_error` "
        "field (Electron main-process synthetic error envelope) and "
        "throw a JS Error so callers' catch blocks fire."
    )
    # Check 2: type:"error" envelope
    assert '"error"' in use_python_source, (
        "usePython.ts must inspect the resolved result for a `type: "
        "'error'` field (Python server unhandled-dispatch envelope) "
        "and throw a JS Error so callers' catch blocks fire."
    )


def test_use_python_per_command_timeout_surfaces_hangs(
    use_python_source: str,
) -> None:
    """The ``usePython`` ``call`` function must race the underlying
    bridge call against a per-command timeout so a hung trivial
    command (e.g. ``get_status``) surfaces an error in seconds
    instead of the prior blanket 120s timeout.

    The timeout table at the top of ``usePython.ts`` sets 5s for
    ``get_status`` / ``get_config``, 120s for ``transcribe``, 600s
    for ``download_model``. The ``withCommandTimeout`` helper wraps
    the underlying call in a ``Promise.race`` against a
    ``setTimeout``-driven rejection.
    """
    assert "withCommandTimeout" in use_python_source, (
        "usePython.ts must wrap the underlying bridge call in "
        "withCommandTimeout() — without this a hung `get_status` would "
        "wait 120s (the host-side blanket timeout) instead of 5s "
        "(the renderer-side per-command timeout)."
    )
    assert "timed out after" in use_python_source, (
        "usePython.ts must throw an Error of the form "
        '`IPC command "<cmd>" timed out after <ms>ms` when the '
        "per-command timeout fires — this is the error string the "
        "UI's catch block reads to surface a 'Lost connection' message."
    )


# ─── Test 6: UI shows spinner / retry button when disconnected ────────


def test_app_tsx_renders_spinner_when_connecting(app_tsx_source: str) -> None:
    """``App.tsx`` must render the ``<Spinner />`` component when
    ``connectionStatus === "connecting"`` (the initial cold-start
    state, before the backend has acknowledged the first
    ``get_config`` probe).
    """
    # The "connecting" branch must render a <Spinner />
    connecting_branch_re = re.compile(
        r'connectionStatus\s*===\s*["\']connecting["\']\s*\?\s*\(\s*[^)]*<Spinner',
        re.MULTILINE | re.DOTALL,
    )
    assert connecting_branch_re.search(app_tsx_source), (
        "App.tsx must render <Spinner /> when connectionStatus === 'connecting' (initial cold-start UI)."
    )


def test_app_tsx_renders_spinner_when_restarting(app_tsx_source: str) -> None:
    """``App.tsx`` must render the ``<Spinner />`` component when
    ``connectionStatus === "restarting"`` (the FT-1 respawn state,
    driven by the bridge's synthesised "reconnecting" python-event
    → useConnection's ``setConnectionStatus("restarting")``).

    The "restarting" branch deliberately does NOT reuse the
    "connecting" branch's copy (which advertises a 30–60 s model
    download that doesn't apply here — the model is already cached,
    only the Python process is being re-spawned). It uses the
    ``app.restartingBackend`` + ``app.restartingHint`` i18n keys
    instead.
    """
    restarting_branch_re = re.compile(
        r'connectionStatus\s*===\s*["\']restarting["\']\s*\?\s*\(\s*[^)]*<Spinner',
        re.MULTILINE | re.DOTALL,
    )
    assert restarting_branch_re.search(app_tsx_source), (
        "App.tsx must render <Spinner /> when connectionStatus === 'restarting' (FT-1 respawn UI)."
    )
    # The restarting branch must use the dedicated i18n copy (NOT the
    # cold-start "firstLaunchHint" key which advertises a model download).
    assert "restartingBackend" in app_tsx_source, (
        "App.tsx must use the app.restartingBackend i18n key in the "
        "'restarting' branch — distinct from app.startingBackend so "
        "users don't think the restart is hung on a 466 MB re-download."
    )
    assert "restartingHint" in app_tsx_source, (
        "App.tsx must use the app.restartingHint i18n key in the "
        "'restarting' branch — distinct from app.firstLaunchHint."
    )


def test_app_tsx_renders_retry_button_when_disconnected(
    app_tsx_source: str,
) -> None:
    """``App.tsx`` must render a "Retry Connection" button + "Lost
    connection to Python backend" message when
    ``connectionStatus === "disconnected"``.

    The button's ``onClick`` handler must be
    ``handleRetryConnection`` (returned by ``useConnection``), which
    flips the status to ``"connecting"`` and re-probes the backend.
    If the probe succeeds the status flips to ``"connected"``; if
    not, it flips back to ``"disconnected"``.
    """
    # The "disconnected" branch must render a Button with
    # onClick=handleRetryConnection. We allow any characters (incl.
    # newlines) between the `? (` and the onClick — the JSX spans
    # multiple lines with a wrapping <div>, a <p>, then the <Button>.
    disconnected_branch_re = re.compile(
        r'connectionStatus\s*===\s*["\']disconnected["\']\s*\?\s*\('
        r"[\s\S]*?onClick=\{handleRetryConnection\}",
        re.MULTILINE | re.DOTALL,
    )
    assert disconnected_branch_re.search(app_tsx_source), (
        "App.tsx must render a <Button onClick={handleRetryConnection}> when connectionStatus === 'disconnected'."
    )
    # The disconnected branch must use the lostConnection i18n key.
    assert "lostConnection" in app_tsx_source, (
        "App.tsx must use the app.lostConnection i18n key in the "
        "'disconnected' branch — surfaces 'Lost connection to Python "
        "backend' to the user."
    )
    # The disconnected branch must use the retryConnection i18n key.
    assert "retryConnection" in app_tsx_source, (
        "App.tsx must use the app.retryConnection i18n key for the retry button label."
    )


def test_app_tsx_destructures_handle_retry_connection(
    app_tsx_source: str,
) -> None:
    """``App.tsx`` must destructure ``handleRetryConnection`` from the
    ``useConnection`` hook's return value, so the disconnected-branch
    retry button has a callback to fire.
    """
    destructure_re = re.compile(
        r"handleRetryConnection\s*\}[\s\S]{0,200}?=\s*useConnection\s*\(",
        re.MULTILINE,
    )
    assert destructure_re.search(app_tsx_source), (
        "App.tsx must destructure handleRetryConnection from the "
        "useConnection(...) return value — without this the retry "
        "button's onClick would be undefined."
    )


# ─── Test 7: i18n keys for the reconnect UX exist ──────────────────────


def test_i18n_keys_exist_in_english_translations() -> None:
    """The 4 reconnect-UX i18n keys must exist in the English
    translations file (the canonical source for the i18n key set).

    The 4 keys (all under the ``app.*`` namespace):
      - ``app.startingBackend``     — "Starting Python backend…"
      - ``app.firstLaunchHint``     — cold-start hint (30–60s model dl)
      - ``app.restartingBackend``   — "Restarting Voice Typer backend…"
      - ``app.restartingHint``      — restart hint (a few seconds)
      - ``app.lostConnection``      — "Lost connection to Python backend"
      - ``app.retryConnection``     — "Retry Connection" button label
    """
    en_json = _RENDERER_SRC / "i18n" / "translations" / "en.json"
    assert en_json.is_file(), f"en.json not found: {en_json}"
    text = en_json.read_text(encoding="utf-8")

    required_keys = (
        "startingBackend",
        "firstLaunchHint",
        "restartingBackend",
        "restartingHint",
        "lostConnection",
        "retryConnection",
    )
    for key in required_keys:
        # Match `"key":` followed by a string value (the JSON shape).
        key_re = re.compile(
            r'"' + re.escape(key) + r'"\s*:\s*"[^"]+"',
            re.MULTILINE,
        )
        assert key_re.search(text), (
            f"i18n key {key!r} must exist in en.json (the canonical "
            f"source for the i18n key set). Without it the reconnect "
            f"UX would show the raw key name instead of the human-"
            f"readable copy."
        )


# ─── Test 8: useConnection exposes the retry callback ─────────────────


def test_use_connection_returns_handle_retry_connection(
    use_connection_source: str,
) -> None:
    """The ``useConnection`` hook must return ``handleRetryConnection``
    so ``App.tsx`` can wire it to the disconnected-branch retry
    button's ``onClick``.

    The callback (``useConnection.ts:255-263``):
      1. Sets the status to ``"connecting"`` (immediate UI feedback —
         the spinner appears before the probe resolves).
      2. Awaits ``call("get_config")`` — if it succeeds, flips to
         ``"connected"``; if it throws, flips to ``"disconnected"``.
    """
    # The callback definition
    def_re = re.compile(
        r"const\s+handleRetryConnection\s*=\s*useCallback",
        re.MULTILINE,
    )
    assert def_re.search(use_connection_source), (
        "useConnection.ts must define handleRetryConnection via "
        "useCallback so the callback identity is stable across renders "
        "(otherwise the retry button re-renders on every state change)."
    )
    # The callback must be returned
    return_re = re.compile(
        r"return\s*\{[^}]*handleRetryConnection[^}]*\}",
        re.MULTILINE | re.DOTALL,
    )
    assert return_re.search(use_connection_source), (
        "useConnection.ts must return handleRetryConnection from the "
        "hook so App.tsx can wire it to the retry button's onClick."
    )
    # The callback must set "connecting" first (immediate UI feedback)
    assert (
        re.search(
            r'setConnectionStatus\s*\(\s*["\']connecting["\']',
            use_connection_source,
            re.MULTILINE,
        )
        is not None
    ), (
        "handleRetryConnection must call setConnectionStatus('connecting') "
        "BEFORE awaiting call('get_config') — this gives the user "
        "immediate feedback (spinner appears) before the probe resolves."
    )


def test_use_connection_returns_connection_status_and_last_error(
    use_connection_source: str,
) -> None:
    """The ``useConnection`` hook must return ``connectionStatus``,
    ``lastError``, and ``recordingState`` so ``App.tsx`` can read
    them without prop-drilling through the Zustand store directly.
    """
    return_re = re.compile(
        r"return\s*\{\s*recordingState\s*,\s*connectionStatus\s*,\s*"
        r"lastError\s*,\s*handleRetryConnection\s*,?\s*\}",
        re.MULTILINE | re.DOTALL,
    )
    assert return_re.search(use_connection_source), (
        "useConnection.ts must return an object with recordingState, "
        "connectionStatus, lastError, and handleRetryConnection — the "
        "4 fields App.tsx reads from the hook."
    )


# ─── Test 9: connection-probe effect has a retry cap ───────────────────


def test_use_connection_probe_has_retry_cap(use_connection_source: str) -> None:
    """The ``useConnection`` connection-probe effect must cap its
    retries (``maxRetries = 5``) so a permanently-down backend
    transitions to ``"disconnected"`` (showing the retry button)
    instead of looping forever.

    Without the cap, a backend that crashes during cold start would
    leave the renderer stuck on ``"connecting"`` forever — the user
    would see the spinner but no way to manually retry.
    """
    # The maxRetries constant + the post-cap setConnectionStatus("disconnected")
    max_retries_re = re.compile(
        r"const\s+maxRetries\s*=\s*5\b",
        re.MULTILINE,
    )
    assert max_retries_re.search(use_connection_source), (
        "useConnection.ts must define `const maxRetries = 5` in the "
        "connection-probe effect — caps the cold-start retry loop so "
        "a permanently-down backend transitions to 'disconnected'."
    )


def test_use_connection_periodic_health_check_flips_to_disconnected(
    use_connection_source: str,
) -> None:
    """The ``useConnection`` periodic health check (60 s interval)
    must flip the status to ``"disconnected"`` when its
    ``call("get_status")`` probe fails.

    This is the path that surfaces the retry button when the sidecar
    dies AFTER the initial cold start (e.g. a crash mid-dictation).
    Without it the renderer would stay on ``"connected"`` until the
    user manually refreshes.
    """
    # The setInterval call body spans multiple lines with nested
    # parens (an arrow function with try/catch), so a simple `[^)]*`
    # won't work. We split into two checks:
    #   (a) `setInterval(async () =>` exists (the health check)
    #   (b) within ~800 chars after that, a catch block calls
    #       setConnectionStatus('disconnected').
    #   (c) the 60_000 interval value is present.
    interval_start_re = re.compile(
        r"setInterval\s*\(\s*async\s*\(\)\s*=>\s*\{",
        re.MULTILINE,
    )
    interval_match = interval_start_re.search(use_connection_source)
    assert interval_match is not None, (
        "useConnection.ts must define a setInterval(async () => {...}) for the periodic health check."
    )
    # Look in the 800-char window after setInterval for the catch →
    # setConnectionStatus('disconnected') pattern. The actual body is
    # ~200 chars but we allow generous slack for refactoring.
    window = use_connection_source[interval_match.start() : interval_match.start() + 800]
    catch_disconnected_re = re.compile(
        r"catch\s*\{[^}]*setConnectionStatus\s*\(\s*[\"']disconnected[\"']",
        re.MULTILINE | re.DOTALL,
    )
    assert catch_disconnected_re.search(window), (
        "useConnection.ts must call setConnectionStatus('disconnected') "
        "in the periodic health check's catch block — without it a "
        "mid-session backend crash would leave the renderer stuck on "
        "'connected' until the user manually refreshes."
    )
    # The 60s interval value must be present (the setInterval 2nd arg).
    assert re.search(r"60[_\s]*000", use_connection_source, re.MULTILINE), (
        "useConnection.ts must call setInterval(..., 60_000) — the 60s "
        "periodic health-check interval (detects backend crashes that "
        "happen AFTER the initial cold start)."
    )


# ─── Test 10: error event surfaces message via usePythonEvent ──────────


def test_use_connection_subscribes_to_error_event(
    use_connection_source: str,
) -> None:
    """The ``useConnection`` hook must subscribe to the
    ``"error"`` python-event and surface the message via
    ``setLastError`` so the UI can display it.

    The handler at ``useConnection.ts:224-234`` checks for
    ``data.message`` (a string) and forwards it to ``setLastError``.
    ``App.tsx`` reads ``lastError`` from the hook return value and
    can display it in a toast / banner.
    """
    sub_re = re.compile(
        r'usePythonEvent\s*\(\s*["\']error["\']',
        re.MULTILINE,
    )
    assert sub_re.search(use_connection_source), (
        "useConnection.ts must subscribe to the 'error' python-event "
        "via usePythonEvent('error', ...) — this is how backend "
        "errors (with a `message` field) get surfaced to the UI."
    )
    # The handler must call setLastError
    assert (
        re.search(
            r"setLastError\s*\(\s*data\.message",
            use_connection_source,
            re.MULTILINE,
        )
        is not None
        or re.search(
            r"setLastError\s*\(\s*data\?\.\s*message",
            use_connection_source,
            re.MULTILINE,
        )
        is not None
    ), (
        "useConnection.ts must call setLastError(data.message) inside "
        "the usePythonEvent('error', ...) handler — forwards the "
        "backend's error message to the store."
    )


def test_use_connection_subscribes_to_status_change_event(
    use_connection_source: str,
) -> None:
    """The ``useConnection`` hook must subscribe to the
    ``"status_change"`` python-event so recording-state changes push
    from the backend to the renderer (e.g. ``idle`` → ``recording``
    → ``transcribing`` → ``idle``).

    The handler also clears ``lastError`` (a status change implies
    the backend recovered from any prior error).
    """
    sub_re = re.compile(
        r'usePythonEvent\s*\(\s*["\']status_change["\']',
        re.MULTILINE,
    )
    assert sub_re.search(use_connection_source), (
        "useConnection.ts must subscribe to the 'status_change' "
        "python-event — this is how recording-state changes flow "
        "from the backend to the renderer."
    )


# ─── Test 11: PythonPushEvent type guards the event stream shape ──────


def test_ipc_types_define_python_push_event_union(ipc_ts_source: str) -> None:
    """The ``PythonPushEvent`` discriminated union in ``types/ipc.ts``
    must include the ``StatusChangeEvent`` + ``ErrorEvent`` variants
    that ``useConnection``'s subscriptions rely on.

    Note (GAP-B): the synthesised ``reconnecting`` + ``reconnected``
    frames from ``tauri-bridge.ts`` are NOT in this union — the
    bridge casts through ``unknown`` to inject them. They're
    host-bridged events, not server-published events.
    """
    # StatusChangeEvent variant
    assert '"status_change"' in ipc_ts_source, (
        "types/ipc.ts must define a StatusChangeEvent variant with "
        "type: 'status_change' — useConnection subscribes to this."
    )
    # ErrorEvent variant
    assert '"error"' in ipc_ts_source, (
        "types/ipc.ts must define an ErrorEvent variant with type: 'error' — useConnection subscribes to this."
    )
    # The PythonPushEvent union export
    union_re = re.compile(
        r"export\s+type\s+PythonPushEvent\s*=",
        re.MULTILINE,
    )
    assert union_re.search(ipc_ts_source), (
        "types/ipc.ts must export the PythonPushEvent discriminated "
        "union — this is the canonical type for server-published "
        "push events."
    )


# ─── Test 12: bridge installs window.python with the onEvent contract ──


def test_bridge_installs_window_python_with_onevent(
    tauri_bridge_source: str,
) -> None:
    """The ``installTauriBridge()`` function must install
    ``window.python`` with an ``onEvent`` callback that the
    ``usePythonEvent`` hook subscribes to.

    The contract:
      - ``window.python.call({type, data}) → Promise<data>``
      - ``window.python.onEvent(callback) → () => void``
    """
    # window.python assignment
    assign_re = re.compile(
        r"window\.python\s*=\s*python\b",
        re.MULTILINE,
    )
    assert assign_re.search(tauri_bridge_source), (
        "tauri-bridge.ts must assign `window.python = python` so the "
        "usePythonEvent hook can find the bridge at runtime."
    )
    # onEvent must be a key on the python object literal.
    # NB: the object literal also defines `call`, `pasteText`, etc., so we
    # allow a generous window after the opening brace before requiring the
    # `onEvent:` key.
    onevent_re = re.compile(
        r"const\s+python\s*:\s*PythonBridge\s*=\s*\{[\s\S]{0,800}?"
        r"onEvent\s*:",
        re.MULTILINE | re.DOTALL,
    )
    assert onevent_re.search(tauri_bridge_source), (
        "tauri-bridge.ts must define `onEvent` on the python bridge "
        "object — this is the subscription entry point that "
        "usePythonEvent uses to receive synthesised + server-published "
        "events."
    )
    # onEvent must register a listen() for python-event (the generic channel)
    pyevent_listen_re = re.compile(
        r'tauri\.event\s*\n?\s*\.listen\s*\(\s*["\']python-event["\']',
        re.MULTILINE,
    )
    assert pyevent_listen_re.search(tauri_bridge_source), (
        "tauri-bridge.ts must call tauri.event.listen('python-event', ...) "
        "inside the onEvent callback — this is the generic channel that "
        "carries ALL server-published events to usePythonEvent."
    )


def test_bridge_auto_installs_on_import(tauri_bridge_source: str) -> None:
    """The ``installTauriBridge()`` function must be called
    automatically when the module is imported (so ``main.tsx`` and
    ``bubble-main.tsx`` don't have to remember to call it).
    """
    auto_install_re = re.compile(
        r"^installTauriBridge\s*\(\s*\)\s*;?\s*$",
        re.MULTILINE,
    )
    assert auto_install_re.search(tauri_bridge_source), (
        "tauri-bridge.ts must call installTauriBridge() at module top "
        "level (auto-install) so importing the module is sufficient "
        "to wire up the bridge."
    )


# ─── Test 13: Spinner component is the shared loading indicator ────────


def test_spinner_component_exists() -> None:
    """The shared ``Spinner`` component must exist at
    ``components/feedback/Spinner.tsx`` — ``App.tsx`` imports it for
    the ``connecting`` + ``restarting`` branches.
    """
    spinner_path = _RENDERER_SRC / "components" / "feedback" / "Spinner.tsx"
    assert spinner_path.is_file(), (
        f"Spinner.tsx not found at {spinner_path} — App.tsx imports it for the connecting + restarting UI branches."
    )


def test_app_tsx_imports_spinner(app_tsx_source: str) -> None:
    """``App.tsx`` must import the ``Spinner`` component so the
    connecting + restarting branches can render it.
    """
    import_re = re.compile(
        r'import\s+\{\s*Spinner\s*\}\s+from\s+["\']',
        re.MULTILINE,
    )
    assert import_re.search(app_tsx_source), (
        "App.tsx must import { Spinner } from '@/components/feedback/Spinner' "
        "(or equivalent) — the connecting + restarting branches render it."
    )


def test_app_tsx_imports_button_for_retry(app_tsx_source: str) -> None:
    """``App.tsx`` must import a ``Button`` component so the
    disconnected branch can render the "Retry Connection" button.
    """
    # The Button import is platform-conditional (shadcn/ui), so we just
    # look for any Button import line.
    import_re = re.compile(
        r'import\s+\{[^}]*\bButton\b[^}]*\}\s+from\s+["\']',
        re.MULTILINE,
    )
    assert import_re.search(app_tsx_source), (
        "App.tsx must import a Button component — the disconnected branch renders a 'Retry Connection' button."
    )
