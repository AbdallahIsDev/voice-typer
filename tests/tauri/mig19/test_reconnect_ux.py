"""
Reconnect UX validation (ADR-0020 §10).
KNOWN GAPS (report, do not fix)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
_CLIENT_SRC = _REPO_ROOT / "voice_typer" / "client" / "src"
_RENDERER_SRC = _CLIENT_SRC / "renderer" / "src"
_SRC_TAURI = _REPO_ROOT / "src-tauri"

_APP_STORE_TS = _RENDERER_SRC / "stores" / "appStore.ts"
# ``tauri-bridge.ts`` was split into a ``tauri-bridge/`` directory
_TAURI_BRIDGE_DIR = _RENDERER_SRC / "lib" / "tauri-bridge"
_TAURI_BRIDGE_TS = _TAURI_BRIDGE_DIR / "index.ts"
_USE_CONNECTION_TS = _RENDERER_SRC / "hooks" / "useConnection.ts"
# ``hooks/usePython.ts`` is now a public barrel re-export; the call
_USE_PYTHON_BARREL_TS = _RENDERER_SRC / "hooks" / "usePython.ts"
_PYTHON_BRIDGE_DIR = _RENDERER_SRC / "lib" / "python-bridge"
_USE_PYTHON_SOURCES = (
    _USE_PYTHON_BARREL_TS,
    _PYTHON_BRIDGE_DIR / "usePython.ts",
    _PYTHON_BRIDGE_DIR / "error-envelope.ts",
    _PYTHON_BRIDGE_DIR / "command-timeouts.ts",
)
_APP_TSX = _RENDERER_SRC / "App.tsx"
# RT-FIX-9 successor refactor: the per-state restart/disconnect copy
_CONNECTION_STATUS_SCREEN_TSX = _RENDERER_SRC / "components" / "layout" / "ConnectionStatusScreen.tsx"
_IPC_TS = _RENDERER_SRC / "types" / "ipc" / "push_events.ts"
_SUPERVISOR_RS = _SRC_TAURI / "src" / "sidecar" / "supervisor.rs"
_WS_RS = _SRC_TAURI / "src" / "sidecar" / "ws.rs"

# Expected connection-status literals (single source of truth).
_REQUIRED_STATUS_LITERALS = ("connected", "disconnected", "restarting", "reconnecting")
# The "reconnecting" EVENT name (bridge-synthesised) is handled by
_DESIRED_RECONNECTING_LITERAL = "reconnecting"

_TAURI_EVENT_SUPERVISOR_RELAUNCHING = "supervisor_relaunching"
_TAURI_EVENT_SUPERVISOR_RECONNECTED = "supervisor_reconnected"

# ─── Expected synthesised python-event type names (bridge translation) ─
_PY_EVENT_RECONNECTING = "reconnecting"
_PY_EVENT_RECONNECTED = "reconnected"


@pytest.fixture(scope="module")
def app_store_source() -> str:
    """Read appStore.ts as text (for static assertions)."""
    assert _APP_STORE_TS.is_file(), f"appStore.ts not found: {_APP_STORE_TS}"
    return _APP_STORE_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tauri_bridge_source() -> str:
    """Read tauri-bridge as text (for static assertions)."""
    assert _TAURI_BRIDGE_DIR.is_dir(), f"tauri-bridge/ directory not found: {_TAURI_BRIDGE_DIR}"
    sibling_files = sorted(p for p in _TAURI_BRIDGE_DIR.glob("*.ts"))
    ordered = [_TAURI_BRIDGE_TS] + [p for p in sibling_files if p.name != "index.ts"]
    parts: list[str] = []
    for path in ordered:
        assert path.is_file(), f"tauri-bridge submodule not found: {path}"
        parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


@pytest.fixture(scope="module")
def use_connection_source() -> str:
    """Read useConnection.ts as text (for static assertions)."""
    assert _USE_CONNECTION_TS.is_file(), f"useConnection.ts not found: {_USE_CONNECTION_TS}"
    return _USE_CONNECTION_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def use_python_source() -> str:
    """Read the usePython implementation as text (for static assertions)."""
    parts: list[str] = []
    for _path in _USE_PYTHON_SOURCES:
        assert _path.is_file(), f"usePython source not found: {_path}"
        parts.append(_path.read_text(encoding="utf-8"))
    return "\n".join(parts)


@pytest.fixture(scope="module")
def app_tsx_source() -> str:
    """Read App.tsx as text (for static assertions)."""
    assert _APP_TSX.is_file(), f"App.tsx not found: {_APP_TSX}"
    return _APP_TSX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def connection_status_screen_source() -> str:
    """Read ConnectionStatusScreen.tsx as text (for static assertions)."""
    assert _CONNECTION_STATUS_SCREEN_TSX.is_file(), (
        f"ConnectionStatusScreen.tsx not found: {_CONNECTION_STATUS_SCREEN_TSX}"
    )
    return _CONNECTION_STATUS_SCREEN_TSX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ipc_ts_source() -> str:
    """Read types/ipc/push_events.ts as text (for static assertions)."""
    assert _IPC_TS.is_file(), f"types/ipc/push_events.ts not found: {_IPC_TS}"
    return _IPC_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def supervisor_rs_source() -> str:
    """Read src-tauri/src/sidecar/supervisor.rs as text (for static assertions)."""
    assert _SUPERVISOR_RS.is_file(), f"supervisor.rs not found: {_SUPERVISOR_RS}"
    return _SUPERVISOR_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ws_rs_source() -> str:
    """Read the Rust WS bridge source: ws.rs plus its ws/reader.rs /"""
    assert _WS_RS.is_file(), f"ws.rs not found: {_WS_RS}"
    parts = [_WS_RS.read_text(encoding="utf-8")]
    for name in ("reader.rs", "writer.rs"):
        sibling = _WS_RS.parent / "ws" / name
        assert sibling.is_file(), f"{sibling} not found, the ws.rs reader/writer module split was rolled back"
        parts.append(sibling.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def test_app_store_exists() -> None:
    """Sanity: the Zustand store file exists at the expected path."""
    assert _APP_STORE_TS.is_file()


def test_connection_status_union_includes_required_literals(
    app_store_source: str,
) -> None:
    """ADR-0020 §10 + MIG-1.9: the ``ConnectionStatus`` union must"""
    # Extract: `export type ConnectionStatus = "a" | "b" | ... ;`
    union_re = re.compile(
        r"export\s+type\s+ConnectionStatus\s*=\s*([^;]+);",
        re.MULTILINE | re.DOTALL,
    )
    match = union_re.search(app_store_source)
    assert match is not None, (
        "Expected `export type ConnectionStatus = ...;` declaration in "
        "appStore.ts, the union is the single source of truth for the "
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
    """``ConnectionStatus`` union - the ``useConnection`` hook sets it"""
    union_re = re.compile(
        r"export\s+type\s+ConnectionStatus\s*=\s*([^;]+);",
        re.MULTILINE | re.DOTALL,
    )
    match = union_re.search(app_store_source)
    assert match is not None
    union_body = match.group(1)
    literals = re.findall(r'"([a-z_]+)"', union_body)

    # GAP-A resolved - the literal was added (see test docstring).
    assert _DESIRED_RECONNECTING_LITERAL in literals, (
        f"GAP-A regression: 'reconnecting' must stay in the ConnectionStatus union. Found literals: {literals}"
    )
    # The cold-start literal coexists (distinct states, distinct copy).
    assert "connecting" in literals, (
        "ConnectionStatus union must keep 'connecting' (cold-start) "
        f"alongside 'reconnecting' (transient recovery). Found: {literals}"
    )


def test_bridge_subscribes_to_supervisor_relaunching_event(
    tauri_bridge_source: str,
) -> None:
    """matching ``python-event`` frame so the ``useConnection`` hook's"""
    # The bridge must register a Tauri event listener for the supervisor events.
    listen_re = re.compile(
        r'\.listen\s*\(\s*["\']' + re.escape(_TAURI_EVENT_SUPERVISOR_RELAUNCHING) + r'["\']',
        re.MULTILINE | re.DOTALL,
    )
    assert listen_re.search(tauri_bridge_source), (
        'tauri-bridge must call tauri.event.listen("supervisor_relaunching", ...), '
        "without this subscription the renderer never learns the WS "
        "disconnected + the UI stays frozen on 'connected' while the "
        "sidecar is dead."
    )

    # The bridge must map supervisor_relaunching → synthesised "reconnecting" frame.
    mapping_type_re = re.compile(
        r'type:\s*["\']' + re.escape(_PY_EVENT_RECONNECTING) + r'["\']',
        re.MULTILINE,
    )
    mapping_reason_re = re.compile(
        r'reason:\s*["\']' + re.escape(_TAURI_EVENT_SUPERVISOR_RELAUNCHING) + r'["\']',
        re.MULTILINE,
    )
    mapping_reason_re = re.compile(
        r'reason:\s*["\']' + re.escape(_TAURI_EVENT_SUPERVISOR_RELAUNCHING) + r'["\']',
        re.MULTILINE,
    )
    mapping_reason_fallback_re = re.compile(
        r"reason:\s*[^\n]*\|\|\s*['\"]" + re.escape(_TAURI_EVENT_SUPERVISOR_RELAUNCHING) + r"['\"]",
        re.MULTILINE,
    )
    assert mapping_type_re.search(tauri_bridge_source), (
        f"tauri-bridge must synthesise type: {_PY_EVENT_RECONNECTING!r}, "
        f"this is the translation that lets usePythonEvent('reconnecting', ...) "
        f"see the Rust host's disconnect signal."
    )
    assert mapping_reason_re.search(tauri_bridge_source) or mapping_reason_fallback_re.search(tauri_bridge_source), (
        f"tauri-bridge must include reason: {_TAURI_EVENT_SUPERVISOR_RELAUNCHING!r} in the synthesised event data."
    )

    # The synthesised frame must be passed to the onEvent callback.
    callback_re = re.compile(
        r"handler\s*\(\s*event\s*\)",
        re.MULTILINE,
    )
    assert callback_re.search(tauri_bridge_source), (
        "tauri-bridge must invoke the handler with the synthesised event "
        "object so usePythonEvent subscribers see the supervisor events."
    )


def test_ws_rs_emits_supervisor_relaunching_on_disconnect(ws_rs_source: str) -> None:
    """ADR-0020 §10 the WS reader task must emit ``supervisor_relaunching``"""
    # The emit call site must reference supervisor_relaunching with reason
    emit_re = re.compile(
        r'emit\s*\(\s*["\']' + re.escape(_TAURI_EVENT_SUPERVISOR_RELAUNCHING) + r'["\']',
        re.MULTILINE,
    )
    assert emit_re.search(ws_rs_source), (
        f"ws.rs must emit {_TAURI_EVENT_SUPERVISOR_RELAUNCHING!r} when the WS reader "
        f"task detects an unexpected close, this is the CR-5 immediate-"
        f"emit path that lets the UI show a 'reconnecting…' banner before "
        f"the backoff schedule runs."
    )
    # Verify the "disconnected" reason (the  path).
    assert '"disconnected"' in ws_rs_source, (
        "ws.rs must emit supervisor_relaunching with reason='disconnected' on "
        "the CR-5 immediate-emit path (distinct from supervisor.rs's "
        "'exhausted_retries' / 'backoff_exhausted' reasons)."
    )


def test_bridge_subscribes_to_supervisor_reconnected_event(
    tauri_bridge_source: str,
) -> None:
    """ADR-0020 §10: the Tauri bridge must subscribe to the Rust host's"""
    # Post- split: the bridge inlines a separate
    listen_re = re.compile(
        r'\.listen\s*\(\s*["\']' + re.escape(_TAURI_EVENT_SUPERVISOR_RECONNECTED) + r'["\']',
        re.MULTILINE | re.DOTALL,
    )
    assert listen_re.search(tauri_bridge_source), (
        'tauri-bridge must call tauri.event.listen("supervisor_reconnected", ...), '
        "without this the renderer would stay on 'restarting' forever after "
        "a successful respawn (the 'reconnected' python-event would "
        "never fire)."
    )

    # Post-split, the mapping is inlined as:
    mapping_type_re = re.compile(
        r'type:\s*["\']' + re.escape(_PY_EVENT_RECONNECTED) + r'["\']',
        re.MULTILINE,
    )
    mapping_reason_re = re.compile(
        r'reason:\s*["\']' + re.escape(_TAURI_EVENT_SUPERVISOR_RECONNECTED) + r'["\']',
        re.MULTILINE,
    )
    assert mapping_type_re.search(tauri_bridge_source), (
        f"tauri-bridge must synthesise type: {_PY_EVENT_RECONNECTED!r}, "
        f"this is the translation that lets usePythonEvent('reconnected', ...) "
        f"see the Rust host's supervisor success signal."
    )
    assert mapping_reason_re.search(tauri_bridge_source), (
        f"tauri-bridge must include reason: {_TAURI_EVENT_SUPERVISOR_RECONNECTED!r} in the synthesised event data."
    )


def test_supervisor_rs_emits_reconnected_on_success(supervisor_rs_source: str) -> None:
    """ADR-0020 §10: the supervisor must emit ``supervisor_reconnected``"""
    emit_re = re.compile(
        r'emit\s*\(\s*["\']' + re.escape(_TAURI_EVENT_SUPERVISOR_RECONNECTED) + r'["\']',
        re.MULTILINE,
    )
    assert emit_re.search(supervisor_rs_source), (
        f"supervisor.rs must emit {_TAURI_EVENT_SUPERVISOR_RECONNECTED!r} on a successful "
        f"respawn, this is the success signal that lets the renderer "
        f"transition out of 'restarting' back to 'connected'."
    )


def test_use_connection_handles_reconnected_event(
    use_connection_source: str,
) -> None:
    """status to ``\"connected\"`` (or ``\"disconnected\"`` if the post-"""
    sub_re = re.compile(
        r'usePythonEvent\s*\(\s*["\']' + re.escape(_PY_EVENT_RECONNECTED) + r'["\']',
        re.MULTILINE,
    )
    assert sub_re.search(use_connection_source), (
        "useConnection.ts must subscribe to the 'reconnected' python-event "
        "via usePythonEvent('reconnected', ...). Without this the renderer "
        "would stay on 'restarting' forever after a successful respawn."
    )

    # On success: setConnectionStatus("connected")
    success_re = re.compile(
        r'setConnectionStatus\s*\(\s*["\']connected["\']',
        re.MULTILINE,
    )
    assert success_re.search(use_connection_source), (
        "useConnection.ts must call setConnectionStatus('connected') on "
        "successful post-reconnect get_config probe, the WS being up is "
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
        "if the post-reconnect get_config probe fails, surfaces the "
        "'Lost connection' + Retry Connection button to the user."
    )


# ─── Test 4: bridge emits "restarting" event when supervisor relaunches app ─


def test_use_connection_maps_reconnecting_event_to_restarting_status(
    use_connection_source: str,
) -> None:
    """synthesised ``\"reconnecting\"`` python-event (which the bridge"""
    sub_re = re.compile(
        r'usePythonEvent\s*\(\s*["\']' + re.escape(_PY_EVENT_RECONNECTING) + r'["\']',
        re.MULTILINE,
    )
    assert sub_re.search(use_connection_source), (
        "useConnection.ts must subscribe to the 'reconnecting' python-event "
        "via usePythonEvent('reconnecting', ...). This is the bridge-"
        "synthesised event that fires when the Rust host detects a WS "
        "disconnect (CR-5) or exhausts supervisor retries (full-app relaunch)."
    )

    handler_re = re.compile(
        r'setConnectionStatus\s*\(\s*["\']reconnecting["\']',
        re.MULTILINE,
    )
    assert handler_re.search(use_connection_source), (
        "useConnection.ts must call setConnectionStatus('reconnecting') "
        "inside the usePythonEvent('reconnecting', ...) handler - this "
        "transitions the UI to the shared recovering spinner branch."
    )


def test_supervisor_rs_emits_relaunching_on_exhaustion(supervisor_rs_source: str) -> None:
    """ADR-0020 §10: the supervisor must emit ``supervisor_relaunching``"""
    emit_re = re.compile(
        r'emit\s*\(\s*["\']' + re.escape(_TAURI_EVENT_SUPERVISOR_RELAUNCHING) + r'["\']',
        re.MULTILINE,
    )
    matches = emit_re.findall(supervisor_rs_source)
    assert len(matches) >= 1, (
        f"supervisor.rs must emit {_TAURI_EVENT_SUPERVISOR_RELAUNCHING!r} on backoff-schedule "
        f"exhaustion (reason='backoff_exhausted') before app.restart(). "
        f"Found {len(matches)} emit calls."
    )
    assert "backoff_exhausted" in supervisor_rs_source, (
        "supervisor.rs must emit supervisor_relaunching with reason='backoff_exhausted' "
        "when the backoff schedule loop exits without a successful respawn."
    )


def test_supervisor_rs_calls_app_restart_after_exhaustion(supervisor_rs_source: str) -> None:
    """ADR-0020 §10: after emitting ``supervisor_relaunching`` on exhaustion,"""
    assert "app.restart()" in supervisor_rs_source, (
        "supervisor.rs must call app.restart() after emitting supervisor_relaunching "
        "on exhaustion, this is the full-app relaunch fallback when "
        "supervisor's in-process respawn has failed."
    )


def test_use_python_throws_when_bridge_missing(use_python_source: str) -> None:
    """The ``usePython`` ``call`` function must reject with a JS Error when"""
    assert "Python bridge not available" in use_python_source, (
        "usePython.ts must reject with an Error whose message is "
        "'Python bridge not available' when window.python is undefined. "
        "This is the renderer-side error surface for a missing backend."
    )
    # The guard must come BEFORE the call to api.call (so the error
    guard_re = re.compile(
        r"if\s*\(\s*!api\s*\)\s*"
        r"(?:throw\s+new\s+Error|return\s+Promise\.reject\s*\(\s*new\s+Error)"
        r"\s*\(\s*[\"']Python bridge not available[\"']\s*\)",
        re.MULTILINE | re.DOTALL,
    )
    guard_match = guard_re.search(use_python_source)
    assert guard_match is not None, (
        "usePython.ts must reject with `new Error('Python bridge not available')` "
        "when `!api` (window.python undefined)."
    )
    # Find `withCommandTimeout(\s*api.call` AFTER the guard statement.
    rest = use_python_source[guard_match.end() :]
    call_re = re.compile(
        r"withCommandTimeout\s*\(\s*api\.call",
        re.MULTILINE | re.DOTALL,
    )
    assert call_re.search(rest), (
        "usePython.ts must call withCommandTimeout(api.call(...)) AFTER "
        "the `if (!api)` guard, otherwise the renderer would "
        "wait for the 120s command timeout instead of surfacing the "
        "'Python bridge not available' error immediately."
    )


def test_use_python_translates_error_envelopes_to_throws(
    use_python_source: str,
) -> None:
    """The ``usePython`` ``call`` function must translate both error-"""
    # Check 1: _error envelope
    assert '"_error"' in use_python_source, (
        "usePython.ts must inspect the resolved result for an `_error` "
        "field (predecessor main-process synthetic error envelope) and "
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
    """The ``usePython`` ``call`` function must race the underlying"""
    assert "withCommandTimeout" in use_python_source, (
        "usePython.ts must wrap the underlying bridge call in "
        "withCommandTimeout(), without this a hung `get_status` would "
        "wait 120s (the host-side blanket timeout) instead of 5s "
        "(the renderer-side per-command timeout)."
    )
    assert "timed out after" in use_python_source, (
        "usePython.ts must throw an Error of the form "
        '`IPC command "<cmd>" timed out after <ms>ms` when the '
        "per-command timeout fires, this is the error string the "
        "UI's catch block reads to surface a 'Lost connection' message."
    )


def test_app_tsx_renders_connection_status_screen_when_connecting(
    app_tsx_source: str,
) -> None:
    """``App.tsx`` must render the ``<ConnectionStatusScreen />``"""
    screen_branch_re = re.compile(
        r"<ConnectionStatusScreen\b",
        re.MULTILINE | re.DOTALL,
    )
    assert screen_branch_re.search(app_tsx_source), (
        "App.tsx must render <ConnectionStatusScreen> for non-connected "
        "states (connecting / restarting / disconnected)."
    )
    # ConnectionStatusScreen must receive the connectionStatus prop.
    status_prop_re = re.compile(
        r"<ConnectionStatusScreen[^>]*\bstatus=\{\s*connectionStatus\s*\}",
        re.MULTILINE | re.DOTALL,
    )
    assert status_prop_re.search(app_tsx_source), (
        "App.tsx must pass status={connectionStatus} to "
        "<ConnectionStatusScreen> so the component can render the "
        "connecting / restarting / disconnected branch internally."
    )


test_app_tsx_renders_spinner_when_connecting = test_app_tsx_renders_connection_status_screen_when_connecting


def test_app_tsx_renders_connection_status_screen_when_restarting(
    app_tsx_source: str,
    connection_status_screen_source: str,
) -> None:
    """``App.tsx`` must render the ``<ConnectionStatusScreen />``"""
    screen_branch_re = re.compile(r"<ConnectionStatusScreen\b", re.MULTILINE | re.DOTALL)
    assert screen_branch_re.search(app_tsx_source), (
        "App.tsx must render <ConnectionStatusScreen> for the restarting state."
    )
    assert "restartingBackend" in connection_status_screen_source, (
        "ConnectionStatusScreen.tsx must use the app.restartingBackend "
        "i18n key in the 'restarting' branch, distinct from "
        "app.startingBackend so users don't think the restart is hung "
        "on a 466 MB re-download."
    )


test_app_tsx_renders_spinner_when_restarting = test_app_tsx_renders_connection_status_screen_when_restarting


def test_app_tsx_renders_retry_button_when_disconnected(
    app_tsx_source: str,
    connection_status_screen_source: str,
) -> None:
    """``App.tsx`` must render a \"Retry Connection\" button + \"Lost"""
    on_retry_re = re.compile(
        r"<ConnectionStatusScreen[^>]*\bonRetry=\{\s*handleRetryConnection\s*\}",
        re.MULTILINE | re.DOTALL,
    )
    assert on_retry_re.search(app_tsx_source), (
        "App.tsx must pass onRetry={handleRetryConnection} to "
        "<ConnectionStatusScreen> so the disconnected branch can "
        "render the Retry button."
    )
    # The disconnected branch must use the lostConnection i18n key.
    assert "lostConnection" in connection_status_screen_source, (
        "ConnectionStatusScreen.tsx must use the app.lostConnection "
        "i18n key in the 'disconnected' branch, surfaces 'Lost "
        "connection to Python backend' to the user."
    )


def test_app_tsx_destructures_handle_retry_connection(
    app_tsx_source: str,
) -> None:
    """``useConnection`` hook's return value, so the disconnected-branch"""
    destructure_re = re.compile(
        r"handleRetryConnection\s*\}[\s\S]{0,200}?=\s*useConnection\s*\(",
        re.MULTILINE,
    )
    assert destructure_re.search(app_tsx_source), (
        "App.tsx must destructure handleRetryConnection from the "
        "useConnection(...) return value, without this the retry "
        "button's onClick would be undefined."
    )


def test_i18n_keys_exist_in_english_translations() -> None:
    """The 4 reconnect-UX i18n keys must exist in the English"""
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


def test_use_connection_returns_handle_retry_connection(
    use_connection_source: str,
) -> None:
    """The ``useConnection`` hook must return ``handleRetryConnection``"""
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
        "BEFORE awaiting call('get_config'), this gives the user "
        "immediate feedback (spinner appears) before the probe resolves."
    )


def test_use_connection_returns_connection_status_and_last_error(
    use_connection_source: str,
) -> None:
    """The ``useConnection`` hook must return ``connectionStatus``,"""
    return_re = re.compile(
        r"return\s*\{\s*recordingState\s*,\s*connectionStatus\s*,\s*"
        r"lastError\s*,\s*handleRetryConnection\s*,?\s*\}",
        re.MULTILINE | re.DOTALL,
    )
    assert return_re.search(use_connection_source), (
        "useConnection.ts must return an object with recordingState, "
        "connectionStatus, lastError, and handleRetryConnection, the "
        "4 fields App.tsx reads from the hook."
    )


def test_use_connection_probe_has_retry_cap(use_connection_source: str) -> None:
    """The ``useConnection`` connection-probe effect must cap its"""
    max_retries_re = re.compile(
        r"const\s+maxRetries\s*=\s*(?:5\b|CONNECTION_PROBE_MAX_RETRIES\b)",
        re.MULTILINE,
    )
    assert max_retries_re.search(use_connection_source), (
        "useConnection.ts must define `const maxRetries = 5` (or "
        "`CONNECTION_PROBE_MAX_RETRIES`) in the connection-probe effect, "
        "caps the cold-start retry loop so a permanently-down backend "
        "transitions to 'disconnected'."
    )


def test_use_connection_periodic_health_check_flips_to_disconnected(
    use_connection_source: str,
) -> None:
    """The ``useConnection`` periodic health check (15 s interval)"""
    interval_start_re = re.compile(
        r"setInterval\s*\(\s*\(\)\s*=>\s*probe\s*\(\s*false\s*\)\s*,\s*(?:15[_\s]*000|HEALTH_CHECK_INTERVAL_MS)\s*\)",
        re.MULTILINE,
    )
    interval_match = interval_start_re.search(use_connection_source)
    assert interval_match is not None, (
        "useConnection.ts must call setInterval(() => probe(false), 15_000) "
        "for the periodic health check (BG-92, 15s interval; the body "
        "is now a thin call to the hoisted probe arrow function)."
    )
    # The probe arrow function must define a catch block that calls
    probe_def_re = re.compile(
        r"const\s+probe\s*=\s*async\s*\(\s*isRetry\s*:\s*boolean\s*\)\s*(?::\s*Promise<[^>]*>\s*)?=>\s*\{",
        re.MULTILINE,
    )
    probe_match = probe_def_re.search(use_connection_source)
    assert probe_match is not None, (
        "useConnection.ts must define `const probe = async (isRetry: boolean) => {`, "
        "the hoisted health-check probe (BG-92)."
    )
    window = use_connection_source[probe_match.start() : probe_match.start() + 1500]
    catch_disconnected_re = re.compile(
        r"catch\s*\{[^}]*setConnectionStatus\s*\(\s*[\"']disconnected[\"']",
        re.MULTILINE | re.DOTALL,
    )
    assert catch_disconnected_re.search(window), (
        "useConnection.ts must call setConnectionStatus('disconnected') "
        "in the probe's catch block, without it a mid-session backend "
        "crash would leave the renderer stuck on 'connected' until the "
        "user manually refreshes."
    )


def test_use_connection_subscribes_to_error_event(
    use_connection_source: str,
) -> None:
    """``\"error\"`` python-event and surface the message via"""
    sub_re = re.compile(
        r'usePythonEvent\s*\(\s*["\']error["\']',
        re.MULTILINE,
    )
    assert sub_re.search(use_connection_source), (
        "useConnection.ts must subscribe to the 'error' python-event "
        "via usePythonEvent('error', ...), this is how backend "
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
        "the usePythonEvent('error', ...) handler, forwards the "
        "backend's error message to the store."
    )


def test_use_connection_subscribes_to_status_change_event(
    use_connection_source: str,
) -> None:
    """``\"status_change\"`` python-event so recording-state changes push"""
    sub_re = re.compile(
        r'usePythonEvent\s*\(\s*["\']status_change["\']',
        re.MULTILINE,
    )
    assert sub_re.search(use_connection_source), (
        "useConnection.ts must subscribe to the 'status_change' "
        "python-event, this is how recording-state changes flow "
        "from the backend to the renderer."
    )


def test_ipc_types_define_python_push_event_union(ipc_ts_source: str) -> None:
    """``types/ipc/push_events.ts`` must include the ``StatusChangeEvent``"""
    # StatusChangeEvent variant
    assert '"status_change"' in ipc_ts_source, (
        "types/ipc/push_events.ts must define a StatusChangeEvent variant "
        "with type: 'status_change', useConnection subscribes to this."
    )
    # ErrorEvent variant
    assert '"error"' in ipc_ts_source, (
        "types/ipc/push_events.ts must define an ErrorEvent variant with "
        "type: 'error', useConnection subscribes to this."
    )
    # The PythonPushEvent union export
    union_re = re.compile(
        r"export\s+type\s+PythonPushEvent\s*=",
        re.MULTILINE,
    )
    assert union_re.search(ipc_ts_source), (
        "types/ipc/push_events.ts must export the PythonPushEvent "
        "discriminated union, this is the canonical type for "
        "server-published push events."
    )


def test_bridge_installs_window_python_with_onevent(
    tauri_bridge_source: str,
) -> None:
    """The ``installTauriBridge()`` function must install"""
    assign_re = re.compile(
        r"window\.python\s*=\s*createPythonNamespace",
        re.MULTILINE,
    )
    assert assign_re.search(tauri_bridge_source), (
        "tauri-bridge must assign `window.python = createPythonNamespace(tauri)` "
        "so the usePythonEvent hook can find the bridge at runtime."
    )
    onevent_re = re.compile(
        r"onEvent\s*:",
        re.MULTILINE | re.DOTALL,
    )
    assert onevent_re.search(tauri_bridge_source), (
        "tauri-bridge.ts must define `onEvent` on the python bridge "
        "object, this is the subscription entry point that "
        "usePythonEvent uses to receive synthesised + server-published "
        "events."
    )
    pyevent_listen_re = re.compile(
        r'tauri\.event\s*\n?\s*\.listen\s*\(\s*["\']python-event["\']',
        re.MULTILINE,
    )
    assert pyevent_listen_re.search(tauri_bridge_source), (
        "tauri-bridge.ts must call tauri.event.listen('python-event', ...) "
        "inside the onEvent callback, this is the generic channel that "
        "carries ALL server-published events to usePythonEvent."
    )


def test_bridge_auto_installs_on_import(tauri_bridge_source: str) -> None:
    """The ``installTauriBridge()`` function must be called"""
    auto_install_re = re.compile(
        r"^installTauriBridge\s*\(\s*\)\s*;?\s*$",
        re.MULTILINE,
    )
    assert auto_install_re.search(tauri_bridge_source), (
        "tauri-bridge.ts must call installTauriBridge() at module top "
        "level (auto-install) so importing the module is sufficient "
        "to wire up the bridge."
    )


def test_spinner_component_exists() -> None:
    """The shared ``Spinner`` component must exist at"""
    spinner_path = _RENDERER_SRC / "components" / "feedback" / "Spinner.tsx"
    assert spinner_path.is_file(), (
        f"Spinner.tsx not found at {spinner_path}, App.tsx imports it for the connecting + restarting UI branches."
    )


def test_app_tsx_imports_connection_status_screen(app_tsx_source: str) -> None:
    """``App.tsx`` must import the ``ConnectionStatusScreen`` component"""
    import_re = re.compile(
        r'import\s+\{\s*ConnectionStatusScreen\s*\}\s+from\s+["\']',
        re.MULTILINE,
    )
    assert import_re.search(app_tsx_source), (
        "App.tsx must import { ConnectionStatusScreen } from "
        "'@/components/layout/ConnectionStatusScreen' (or equivalent), "
        "the connecting / restarting / disconnected branches render it."
    )


test_app_tsx_imports_spinner = test_app_tsx_imports_connection_status_screen


def test_app_tsx_imports_button_for_retry(
    connection_status_screen_source: str,
) -> None:
    """The disconnected-branch UI must import a ``Button`` component so"""
    # The Button import is platform-conditional (shadcn/ui), so we just
    import_re = re.compile(
        r'import\s+\{[^}]*\bButton\b[^}]*\}\s+from\s+["\']',
        re.MULTILINE,
    )
    assert import_re.search(connection_status_screen_source), (
        "ConnectionStatusScreen.tsx must import a Button component, "
        "the disconnected branch renders a 'Retry Connection' button."
    )
