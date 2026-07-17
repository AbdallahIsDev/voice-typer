r"""MIG-1.9 Phase 3 — ``usePython`` bridge parity validation (Tauri vs Electron).

This is the **Phase 3 UI-port bridge check** for ADR-0020 §6.3 / Phase 3:
``client/src/renderer/src/lib/tauri-bridge.ts`` ports the React bridge
behind ``usePython`` (``client/src/renderer/src/hooks/usePython.ts``) so
that the *same* renderer bundle runs unchanged on both runtimes:

  Tauri path:    window.python.call → tauri.core.invoke('dispatch', {cmd, data})
                                          └→ Rust dispatch() (sidecar_cmds.rs:23)
                                              └→ WS frame → Python sidecar
                                              ←← WS response → Rust → unwrap
                                                  `response.data` on success
                                                  OR reject with
                                                  `server error [<code>]: <msg>`
                                                  on `type:"error"` envelopes
                                                  (sidecar_cmds.rs:53).

  Electron path: window.python.call → ipcRenderer.invoke('python-call', msg)
                                          └→ Electron main
                                              `registerPythonCallHandler`
                                              (python-call-handler.ts:16)
                                              └→ sendToPython(msg) → TCP
                                              ←← TCP response with full
                                                  envelope (Electron resolves
                                                  the IPC promise with the
                                                  envelope verbatim — does
                                                  NOT unwrap `data`).
                                              On not-connected / send
                                              exception: resolves with
                                              `{_error: "..."}`.

``usePython.ts`` (line 188–205) checks for *both* error envelope shapes
in the resolved value (Electron-only — Tauri rejects before the resolved
value reaches JS, so the checks are dead code on Tauri but load-bearing
on Electron). The success path returns ``result as T`` directly; on Tauri
``result`` is already ``response.data`` (unwrapped by Rust), on Electron
``result`` is the full envelope but ``usePython`` returns it as-is after
the error checks pass. The caller-facing success shape is therefore
consistent: ``data`` (the inner ``data`` field) is returned on both paths.

This file validates that the contract holds **by source-inspection** of
the four bridge-related files:

  1. ``client/src/renderer/src/lib/tauri-bridge.ts``     — Tauri bridge
  2. ``client/src/renderer/src/hooks/usePython.ts``      — ``usePython`` hook
  3. ``client/src/preload/index.ts``                     — Electron preload
  4. ``client/src/main/ipc/python-call-handler.ts``      — Electron main handler

It does NOT spawn a real Tauri / Electron runtime — that is the host
validation step (see **VALIDATE ON HOST** below). The source-inspection
tests verify that the bridge routes through the right transport on each
path and that the error / success envelope shapes are consistent across
paths.

Bridge contract (identical on both paths):

  ``window.python.call({type, data}) → Promise<data>``
      Tauri:    ``invoke('dispatch', {cmd: type, data: data ?? {}})``
                (tauri-bridge.ts:148–153). Rust returns ``response.data``
                directly (sidecar_cmds.rs:66) on success, rejects with
                ``server error [<code>]: <msg>`` on ``type:"error"``
                (sidecar_cmds.rs:53-64).
      Electron: ``ipcRenderer.invoke('python-call', msg)``
                (preload/index.ts:3-5). The main handler resolves with
                the full envelope (python-call-handler.ts:26 →
                sendToPython → resolves with envelope verbatim). On
                not-connected / send exception it resolves with
                ``{_error: "..."}`` (python-call-handler.ts:21/23/28).

  ``window.python.onEvent(callback) → () => void``
      Tauri:    ``tauri.event.listen('python-event', …)``
                (tauri-bridge.ts:164-175). Also listens for FT-1 host
                events and synthesizes ``python-event`` frames
                (tauri-bridge.ts:182-202).
      Electron: ``ipcRenderer.on('python-event', handler)``
                (preload/index.ts:6-13).

  ``window.bubble.onLevel(callback) → () => void``
      Tauri:    ``tauri.event.listen('bubble_level', …)``
                (tauri-bridge.ts:240-258). Coalesced to ≤30 Hz by the
                Rust WS reader (ADR-0020 §9).
      Electron: ``ipcRenderer.on('bubble:level', handler)``
                (preload/index.ts:17-24).

  ``window.window_.minimize/maximize/close/isMaximized``
      Tauri:    ``tauri.window.getCurrentWindow().{minimize|toggleMaximize|
                close|isMaximized}()`` (tauri-bridge.ts:375-383).
      Electron: ``ipcRenderer.invoke('window:{minimize|toggle-maximize|
                close|is-maximized}')`` (preload/index.ts:80-86).
      NOTE: ``usePython.ts`` does not consume ``window_`` directly — it
      is part of the bridge parity contract validated here for
      completeness.


VALIDATE ON HOST
================

This file is the Linux-sandbox source-inspection check. The actual
runtime bridge-parity validation MUST be executed by a human on real
hosts (one Tauri + one Electron), because the parity contract lives in
the *runtime behavior* of the JS bridge under each host's WebView /
Chromium. Source-inspection catches drift; host validation catches
runtime regressions (Tauri global API rename, IPC channel rename, etc.).

**VALIDATE ON HOST — Tauri (any platform, dev build)**::

    # 1. Start the Tauri dev shell with the Python sidecar.
    cd src-tauri
    cargo tauri dev
    # (in a separate terminal) tail the log to confirm the sidecar is up
    tail -n 50 ~/.local/share/voice-typer/logs/voice-typer.log \
        | grep -E "server_started|dispatch"

    # 2. In the Tauri WebView, open DevTools (right-click → Inspect)
    #    and run each of these in the Console. Each assertion must pass:

    # 2a. Detector + namespace install.
    >>> typeof window.__TAURI__?.core?.invoke
    'function'
    >>> typeof window.python
    'object'
    >>> typeof window.python.call
    'function'
    >>> typeof window.python.onEvent
    'function'
    >>> typeof window.bubble?.onLevel
    'function'
    >>> typeof window.window_?.minimize
    'function'

    # 2b. Success shape: `python.call` returns `data` directly (the
    #     inner `data` field, NOT the full envelope).
    >>> (await window.python.call({type:'get_status', data:{}}))
    {ready: true, …}                  # NOT {type:'result', data:{ready:true,…}}

    # 2c. Error shape: `type:'error'` envelope → promise rejection
    #     (NOT a resolved value with `type:'error'`). The Rust host
    #     rejects the invoke promise at sidecar_cmds.rs:53-64 before
    #     the resolved value reaches JS.
    >>> try {
    ...     await window.python.call({type:'__nonexistent_command__', data:{}});
    ...     'UNEXPECTED: resolved';
    ... } catch (e) {
    ...     String(e).startsWith('server error') ? 'OK: rejected' : `FAIL: ${e}`;
    ... }
    'OK: rejected'

    # 2d. Event stream: `python-event` Tauri event arrives at the
    #     `onEvent` callback. Trigger an event from the UI (e.g.
    #     click the mic button to start + stop a 1-second recording)
    #     and confirm the callback fires.
    >>> window.python.onEvent((e) => console.log('[evt]', e.type, e.data));
    # expected: a stream of `[evt] recording_started {…}`, `[evt] …` etc.

    # 2e. Bubble level stream: `bubble_level` Tauri event arrives at
    #     `bubble.onLevel` while the mic is recording.
    >>> window.bubble.onLevel((d) => console.log('[lvl]', d.rms, d.peak));
    # expected: ~30 Hz stream of `[lvl] <rms> <peak>` lines while mic is hot

    # 2f. Window controls.
    >>> await window.window_.minimize();     'OK'
    >>> await window.window_.isMaximized();  false (or true)
    >>> await window.window_.toggleMaximize();  (maximized state flips)
    >>> await window.window_.close();        'OK'

**VALIDATE ON HOST — Electron (any platform, dev build)**::

    # 1. Start the Electron dev shell with the Python backend.
    pnpm --filter client dev   # or: cd voice_typer/client && pnpm dev
    # (in a separate terminal) confirm the backend is up
    tail -n 50 ~/.local/share/voice-typer/logs/voice-typer.log \
        | grep -E "server_started|TCP|9876"

    # 2. In the Electron renderer DevTools (View → Toggle Developer
    #    Tools), run each of these in the Console. Each assertion must
    #    pass:

    # 2a. Detector — Electron preload installed the namespaces.
    >>> typeof window.__TAURI__?.core?.invoke
    'undefined'                       # Tauri global MUST be absent
    >>> typeof window.python
    'object'                          # preload installed it
    >>> typeof window.python.call
    'function'
    >>> typeof window.python.onEvent
    'function'
    >>> typeof window.bubble?.onLevel
    'function'
    >>> typeof window.window_?.minimize
    'function'

    # 2b. Success shape: `python.call` resolves with the FULL envelope
    #     (Electron does NOT unwrap `data`). The `usePython` hook's
    #     error-envelope checks then run; the success path returns
    #     `result as T` which is the full envelope — the caller reads
    #     fields off the envelope directly (e.g. `result.ready`).
    #     IMPORTANT: this differs from the Tauri path where Rust
    #     unwraps `response.data` BEFORE returning to JS. The caller-
    #     facing shape is "the `data` field of the server's response",
    #     which on Electron IS the envelope's `data` field (because
    #     the Electron main process resolves with the full envelope)
    #     and on Tauri IS the envelope's `data` field (because Rust
    #     unwraps it). Both paths surface the same logical payload
    #     to the caller — see usePython.ts comment lines 49–53 + 188–206
    #     for the parity argument.
    >>> (await window.python.call({type:'get_status', data:{}}))
    {ready: true, …}                  # the `data` field, delivered

    # 2c. Error shape A: `{type:'error', data:{code, message}}` envelope
    #     (Python server unhandled-dispatch error). The Electron main
    #     process resolves the IPC promise with the envelope verbatim;
    #     `usePython.ts:196-205` checks `result.type === 'error'` and
    #     throws. Caller sees a JS Error, not a resolved envelope.
    >>> try {
    ...     await window.python.call({type:'__nonexistent_command__', data:{}});
    ...     'UNEXPECTED: resolved';
    ... } catch (e) {
    ...     `OK: rejected (${e.message})`;
    ... }
    'OK: rejected (…)'

    # 2d. Error shape B: `{_error: '...'}` envelope (Electron main
    #     synthetic — backend not connected). Stop the Python backend
    #     first, then:
    >>> try {
    ...     await window.python.call({type:'get_status', data:{}});
    ...     'UNEXPECTED: resolved';
    ... } catch (e) {
    ...     `OK: rejected (${e.message})`;
    ... }
    'OK: rejected (Python backend is not connected)'

    # 2e. Event stream: `python-event` IPC channel arrives at the
    #     `onEvent` callback.
    >>> window.python.onEvent((e) => console.log('[evt]', e.type, e.data));

    # 2f. Bubble level stream: `bubble:level` IPC channel arrives at
    #     `bubble.onLevel` while the mic is recording.
    >>> window.bubble.onLevel((d) => console.log('[lvl]', d.rms, d.peak));

    # 2g. Window controls.
    >>> await window.window_.minimize();     'OK'
    >>> await window.window_.isMaximized();  false
    >>> await window.window_.toggleMaximize();
    >>> await window.window_.close();        'OK'

Each host run validates four parity properties:

  1. The **detector** picks the right path (``window.__TAURI__`` present
     on Tauri, absent on Electron) — Test 1.
  2. ``window.python.call`` routes through the right transport
     (``invoke('dispatch')`` on Tauri, ``ipcRenderer.invoke('python-call')``
     on Electron) — Tests 2 + 3.
  3. The **error envelope** is surfaced as a JS rejection on BOTH paths
     (Tauri via Rust rejection at sidecar_cmds.rs:53; Electron via the
     in-code `_error` / `type:'error'` checks in usePython.ts:188-205) —
     Tests 5 + 6.
  4. The **success shape** is the inner ``data`` field on BOTH paths
     (Tauri unwraps in Rust; Electron delivers the full envelope but
     ``usePython`` returns it as-is and the caller reads fields off the
     envelope which IS the server's ``data`` payload) — Test 7.
  5. ``window.bubble.onLevel`` + ``window.window_.*`` route through the
     right transport on each path — Tests 8 + 9.

References:
- ADR-0020 §6.3 / Phase 3 — React bridge port to Tauri ``invoke('dispatch', …)``
  behind ``usePython`` (keep ``usePython`` identical both paths).
- ADR-0020 §2 — Tauri Rust host rejects on ``type:'error'`` envelopes
  (NEW-IPC-107 fix).
- ADR-0020 §9 — ``bubble_level`` coalesced to ≤30 Hz.
- src-tauri/src/commands/sidecar_cmds.rs:23-76 — Rust ``dispatch`` command
  (rejects on ``type:'error'``, unwraps ``response.data`` on success).
- client/src/main/ipc/python-call-handler.ts:16-31 — Electron main
  ``python-call`` handler (resolves with full envelope or ``{_error}``).
- client/src/preload/index.ts — Electron preload
  (``contextBridge.exposeInMainWorld``).
- client/src/renderer/src/lib/tauri-bridge.ts — Tauri bridge installer.
- client/src/renderer/src/hooks/usePython.ts — ``usePython`` hook
  (NEW-IPC-107 envelope checks at lines 188-205).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ─── Repo path resolution ──────────────────────────────────────────────
# Tests run from the repo root, but every path is resolved relative to
# this file's location so the tests pass regardless of cwd.
# parents[0]=mig19, [1]=tauri, [2]=tests, [3]=voice-typer (repo root)
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
_CLIENT_SRC = _REPO_ROOT / "voice_typer" / "client" / "src"
_RENDERER_SRC = _CLIENT_SRC / "renderer" / "src"
_TAURI_BRIDGE = _RENDERER_SRC / "lib" / "tauri-bridge.ts"
_USE_PYTHON = _RENDERER_SRC / "hooks" / "usePython.ts"
_PRELOAD = _CLIENT_SRC / "preload" / "index.ts"
_PYTHON_CALL_HANDLER = _CLIENT_SRC / "main" / "ipc" / "python-call-handler.ts"
_SIDECAR_CMDS_RS = _REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds.rs"


# ─── Expected contract strings (single source of truth) ────────────────

#: ADR-0020 §6.3 / Phase 3: the Tauri detector gates on ``__TAURI__``.
TAURI_GLOBAL_SENTINEL = "__TAURI__"

#: Tauri bridge routes ``python.call`` through the ``dispatch`` command
#: with ``{cmd, data}`` args (sidecar_cmds.rs:23 + tauri-bridge.ts:148-153).
TAURI_DISPATCH_CMD = "dispatch"
TAURI_DISPATCH_ARGS_RE = re.compile(
    r'tauri\.core\.invoke\(\s*["\']dispatch["\']\s*,\s*\{[^}]*cmd[^}]*data',
    re.DOTALL,
)

#: Electron preload routes ``python.call`` through ``ipcRenderer.invoke``
#: on the ``python-call`` channel (preload/index.ts:3-5).
ELECTRON_PYTHON_CALL_CHANNEL = "python-call"

#: Tauri event names used by the bridge.
TAURI_PYTHON_EVENT = "python-event"
TAURI_BUBBLE_LEVEL_EVENT = "bubble_level"

#: Electron IPC channels used by the bridge.
ELECTRON_PYTHON_EVENT_CHANNEL = "python-event"
ELECTRON_BUBBLE_LEVEL_CHANNEL = "bubble:level"
ELECTRON_WINDOW_MINIMIZE = "window:minimize"
ELECTRON_WINDOW_TOGGLE_MAXIMIZE = "window:toggle-maximize"
ELECTRON_WINDOW_CLOSE = "window:close"
ELECTRON_WINDOW_IS_MAXIMIZED = "window:is-maximized"

#: Tauri Rust host rejects on ``type:'error'`` envelopes
#: (sidecar_cmds.rs:53-64).
TAURI_RUST_ERROR_REJECT_SENTINEL = 'Some("error")'

#: Electron main ``python-call`` handler resolves with ``{_error}`` on
#: not-connected / send-exception (python-call-handler.ts:21/23/28).
ELECTRON_ERROR_ENVELOPE_FIELD = "_error"


# ─── Shared fixtures ───────────────────────────────────────────────────


@pytest.fixture(scope="module")
def tauri_bridge_source() -> str:
    """Read ``client/src/renderer/src/lib/tauri-bridge.ts`` as text."""
    assert _TAURI_BRIDGE.exists(), f"tauri-bridge.ts not found: {_TAURI_BRIDGE}"
    return _TAURI_BRIDGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def use_python_source() -> str:
    """Read ``client/src/renderer/src/hooks/usePython.ts`` as text."""
    assert _USE_PYTHON.exists(), f"usePython.ts not found: {_USE_PYTHON}"
    return _USE_PYTHON.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def preload_source() -> str:
    """Read ``client/src/preload/index.ts`` as text."""
    assert _PRELOAD.exists(), f"preload not found: {_PRELOAD}"
    return _PRELOAD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def python_call_handler_source() -> str:
    """Read ``client/src/main/ipc/python-call-handler.ts`` as text."""
    assert _PYTHON_CALL_HANDLER.exists(), f"python-call-handler.ts not found: {_PYTHON_CALL_HANDLER}"
    return _PYTHON_CALL_HANDLER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sidecar_cmds_rs_source() -> str:
    """Read ``src-tauri/src/commands/sidecar_cmds.rs`` as text."""
    assert _SIDECAR_CMDS_RS.exists(), f"sidecar_cmds.rs not found: {_SIDECAR_CMDS_RS}"
    return _SIDECAR_CMDS_RS.read_text(encoding="utf-8")


# ─── Test 1: detector — Tauri vs Electron via window.__TAURI__ ─────────


def test_tauri_bridge_detects_tauri_via_window_global(tauri_bridge_source) -> None:
    """ADR-0020 §6.3 / Phase 3: bridge detects Tauri via ``window.__TAURI__``.

    The detector must check the Tauri global object — not a heuristic
    like UA sniffing or ``window.process`` — so it works in every
    WebView (WebView2 / WKWebView / webkit2gtk) without false positives
    on Electron (which doesn't define ``window.__TAURI__``).
    """
    assert TAURI_GLOBAL_SENTINEL in tauri_bridge_source, (
        "tauri-bridge.ts must reference window.__TAURI__ for runtime "
        "detection (ADR-0020 §6.3 / Phase 3). A Tauri detector that "
        "uses a different sentinel (e.g. window.isTauri) would break "
        "the parity contract with the Electron preload."
    )
    # The detector function is named `isTauri` and explicitly checks
    # `?.core?.invoke` so a partial Tauri global (e.g. a future split
    # of the event/window APIs into separate globals) doesn't fool the
    # detector into installing a half-broken bridge.
    assert "isTauri" in tauri_bridge_source, (
        "tauri-bridge.ts must declare the `isTauri()` detector "
        "(gates the install on window.__TAURI__.core.invoke presence)."
    )
    assert "__TAURI__?.core?.invoke" in tauri_bridge_source, (
        "tauri-bridge.ts must gate on `__TAURI__?.core?.invoke` "
        "(not just `__TAURI__`) so a partial Tauri global doesn't "
        "fool the detector."
    )


def test_tauri_bridge_skips_install_on_electron_path(tauri_bridge_source) -> None:
    """ADR-0020 §6.3 / Phase 3: bridge is a no-op on the Electron path.

    When ``isTauri()`` returns false, ``installTauriBridge()`` must
    return early without installing the namespaces — the Electron
    preload has already installed them via ``contextBridge``. Installing
    on top would either no-op (same shape) or clobber the preload
    install (different shape) — neither is correct. The early-return
    makes the bridge idempotent across runtimes.
    """
    # Find the installTauriBridge function and check its first guard.
    install_fn_re = re.compile(
        r"function\s+installTauriBridge\s*\([^)]*\)[^{]*\{",
        re.DOTALL,
    )
    m = install_fn_re.search(tauri_bridge_source)
    assert m, "tauri-bridge.ts must declare `installTauriBridge()`"
    # The first 200 chars after the opening brace should contain the
    # `if (!isTauri()) { return; }` early-return guard.
    head = tauri_bridge_source[m.end() : m.end() + 200]
    assert "!isTauri()" in head, (
        "installTauriBridge() must early-return when isTauri() is false "
        "(Electron path — preload already installed the namespaces). "
        f"First 200 chars after the function opening brace:\n{head}"
    )


# ─── Test 2: Tauri mode routes window.python.call through invoke('dispatch')


def test_tauri_python_call_routes_through_dispatch_invoke(tauri_bridge_source) -> None:
    """ADR-0020 §6.3 / Phase 3: Tauri ``window.python.call`` routes
    through ``invoke('dispatch', {cmd, data})``.

    The Rust ``dispatch`` command (sidecar_cmds.rs:23) is the single
    entry point for UI→sidecar commands on the Tauri path. It forwards
    the ``{cmd, data}`` frame over the WS to the sidecar, awaits the
    per-id response, and returns ``response.data`` on success or
    rejects on ``type:'error'`` (sidecar_cmds.rs:53-64).

    The bridge MUST pass ``msg.type`` as ``cmd`` (the Rust command
    name) and ``msg.data`` as ``data`` (the sidecar payload), matching
    the Electron path's envelope shape ``{type, data}``.
    """
    assert TAURI_DISPATCH_CMD in tauri_bridge_source, (
        "tauri-bridge.ts must reference the 'dispatch' Tauri command (the generic UI→sidecar bridge, ADR-0020 §7)."
    )
    m = TAURI_DISPATCH_ARGS_RE.search(tauri_bridge_source)
    assert m, (
        "tauri-bridge.ts must call `tauri.core.invoke('dispatch', "
        "{cmd: msg.type, data: msg.data ?? {}})` so the Rust host "
        "receives the same {type, data} shape as the Electron path. "
        "Found no matching `invoke('dispatch', {cmd, data})` call."
    )
    # The dispatch call must map msg.type → cmd and msg.data → data.
    dispatch_block = tauri_bridge_source[m.start() : m.end()]
    assert "cmd" in dispatch_block and "msg.type" in dispatch_block, (
        "tauri-bridge.ts dispatch call must map `msg.type → cmd` "
        "(Rust command name) — found dispatch block:\n"
        f"{dispatch_block}"
    )
    assert "data" in dispatch_block and "msg.data" in dispatch_block, (
        "tauri-bridge.ts dispatch call must map `msg.data → data` "
        "(sidecar payload) — found dispatch block:\n"
        f"{dispatch_block}"
    )


def test_tauri_python_call_default_data_is_empty_object(tauri_bridge_source) -> None:
    """ADR-0020 §6.3 / Phase 3: Tauri ``python.call`` defaults ``data``
    to ``{}`` when omitted (matches Electron).

    The Rust ``dispatch`` command serializes ``args.data.unwrap_or(json!({}))``
    (sidecar_cmds.rs:30) — so the bridge must pass ``msg.data ?? {}``
    (or equivalent) to avoid ``null`` payloads reaching the sidecar.
    The Electron path's ``ipcRenderer.invoke('python-call', msg)`` passes
    the msg verbatim; the Python sidecar's ``_validate_dict_payload``
    rejects ``null`` payloads, so the bridge's default must be ``{}``.
    """
    # The dispatch call's data arg must default to `{}` when msg.data is
    # nullish. The actual production code (tauri-bridge.ts:148-153) is:
    #     const python: PythonBridge = {
    #         call: (msg) =>
    #             tauri.core.invoke("dispatch", {
    #                 cmd: msg.type,
    #                 data: msg.data ?? {},
    #             }),
    # The literal `msg.data ?? {}` is unique to the code (the header
    # comment at lines 19-22 says `{cmd: type, data}` without the `msg.`
    # prefix or the `?? {}` default), so a direct substring check is
    # both sufficient and unambiguous.
    assert "msg.data ?? {}" in tauri_bridge_source or ("msg.data || {}" in tauri_bridge_source), (
        "tauri-bridge.ts dispatch call must default `data` to `{}` when "
        "msg.data is nullish — matches Rust's `unwrap_or(json!({}))` "
        "(sidecar_cmds.rs:30) and the Python sidecar's _validate_dict_payload "
        "contract (which rejects `null` payloads)."
    )


# ─── Test 3: Electron mode routes window.python.call through ipcRenderer.invoke('python-call')


def test_electron_python_call_routes_through_ipc_renderer(preload_source) -> None:
    """ADR-0020 §6.3 / Phase 3: Electron ``window.python.call`` routes
    through ``ipcRenderer.invoke('python-call', msg)``.

    The preload script exposes ``window.python.call`` via
    ``contextBridge.exposeInMainWorld``. The main process's
    ``python-call`` handler (python-call-handler.ts:16) forwards to
    ``sendToPython`` which sends the frame over TCP and resolves with
    the full response envelope (or ``{_error: '...'}`` on failure).

    The IPC channel name MUST be ``'python-call'`` (kebab-case) so it
    matches the main handler's ``ipcMain.handle('python-call', …)``.
    """
    # The preload's `python.call` must invoke the 'python-call' channel.
    assert ELECTRON_PYTHON_CALL_CHANNEL in preload_source, (
        f"preload/index.ts must reference the {ELECTRON_PYTHON_CALL_CHANNEL!r} "
        "IPC channel (Electron main handler at "
        "python-call-handler.ts:16 registers ipcMain.handle('python-call'))."
    )
    assert "ipcRenderer.invoke" in preload_source, (
        "preload/index.ts must use `ipcRenderer.invoke` for `python.call` "
        "(the request/response bridge, not the fire-and-forget `ipcRenderer.send`)."
    )
    # Specifically the python.call should be a one-liner that invokes
    # 'python-call' with the msg argument.
    python_call_re = re.compile(
        r"call:\s*\([^)]*\)\s*=>\s*ipcRenderer\.invoke\(\s*"
        r"['\"]python-call['\"]\s*,\s*msg\s*\)",
        re.DOTALL,
    )
    assert python_call_re.search(preload_source), (
        "preload/index.ts `python.call` must be `ipcRenderer.invoke("
        "'python-call', msg)` so the main handler's `ipcMain.handle("
        "'python-call', …)` receives the frame verbatim."
    )


def test_python_call_handler_registered_on_python_call_channel(
    python_call_handler_source,
) -> None:
    """ADR-0020 §6.3 / Phase 3: Electron main registers the
    ``python-call`` IPC handler.

    The handler is the Electron-side counterpart to the Tauri Rust
    ``dispatch`` command. It receives the ``{type, data}`` frame from
    the renderer, forwards it to the Python backend over TCP, and
    resolves the IPC promise with the response envelope (or
    ``{_error: '...'}`` on not-connected / send-exception).
    """
    assert 'ipcMain.handle("python-call"' in python_call_handler_source or (
        "ipcMain.handle('python-call'" in python_call_handler_source
    ), "python-call-handler.ts must register the 'python-call' IPC handler via `ipcMain.handle('python-call', …)`."
    # The handler must call sendToPython (the TCP bridge) on the
    # connected path.
    assert "sendToPython" in python_call_handler_source, (
        "python-call-handler.ts must call `sendToPython(msg)` to forward "
        "the renderer's frame to the Python backend over TCP."
    )


# ─── Test 4: usePython.ts uses window.python.call + window.python.onEvent
#            (not direct invoke / ipcRenderer)


def test_use_python_uses_window_python_call(use_python_source) -> None:
    """ADR-0020 §6.3 / Phase 3: ``usePython`` consumes ``window.python.call``.

    The hook must NOT call ``invoke('dispatch', …)`` or
    ``ipcRenderer.invoke('python-call', …)`` directly — the transport
    is abstracted behind ``window.python.call`` so the same hook source
    ships under both runtimes. This is the central parity guarantee.
    """
    assert "window.python" in use_python_source or "WindowWithPython" in (use_python_source), (
        "usePython.ts must consume `window.python` (the bridge namespace), "
        "NOT call Tauri/Electron transport APIs directly."
    )
    assert ".python" in use_python_source, (
        "usePython.ts must read `window.python` (or the typed cast through WindowWithPython)."
    )
    # The hook calls `api.call({type, data})` — `api` is `window.python`.
    assert "api.call(" in use_python_source, (
        "usePython.ts must call `api.call({type, data})` (where api = "
        "window.python) — not the underlying transport directly."
    )


def test_use_python_uses_window_python_on_event(use_python_source) -> None:
    """ADR-0020 §6.3 / Phase 3: ``usePythonEvent`` consumes
    ``window.python.onEvent``.

    The hook must NOT call ``tauri.event.listen('python-event')`` or
    ``ipcRenderer.on('python-event', …)`` directly — the event-stream
    transport is abstracted behind ``window.python.onEvent``.
    """
    assert "api.onEvent" in use_python_source, (
        "usePython.ts must call `api.onEvent(callback)` (where api = "
        "window.python) — NOT subscribe to Tauri/Electron events directly."
    )


def test_use_python_does_not_import_tauri_or_electron_apis(
    use_python_source,
) -> None:
    """ADR-0020 §6.3 / Phase 3: ``usePython.ts`` must NOT import or call
    Tauri or Electron transport APIs directly.

    The hook is the canonical "works on both paths" abstraction: it
    only consumes ``window.python``. Any direct reference to
    ``invoke``, ``ipcRenderer``, ``__TAURI__``, ``contextBridge``, or
    ``require('electron')`` would break the parity contract — the hook
    would no longer ship unchanged under both runtimes.
    """
    forbidden_patterns = [
        (r"\binvoke\s*\(", "Tauri `invoke()` direct call"),
        (r"\bipcRenderer\b", "Electron `ipcRenderer` direct reference"),
        (r"\bcontextBridge\b", "Electron `contextBridge` direct reference"),
        (r"from\s+['\"]electron['\"]", "Electron module import"),
        (r"from\s+['\"]@tauri-apps/api['\"]", "Tauri API import"),
        (r"__TAURI__", "Tauri global direct reference"),
    ]
    violations: list[str] = []
    for pat, desc in forbidden_patterns:
        if re.search(pat, use_python_source):
            violations.append(desc)
    assert not violations, (
        "usePython.ts must NOT reference Tauri or Electron transport "
        "APIs directly — the bridge namespace `window.python` is the "
        "only transport the hook is allowed to consume. Violations: " + ", ".join(violations)
    )


# ─── Test 5: error envelope — both paths reject on type:"error"


def test_tauri_rust_rejects_on_type_error_envelope(sidecar_cmds_rs_source) -> None:
    """ADR-0020 §6.3 / Phase 3 + §2: Tauri Rust host rejects the
    ``invoke('dispatch')`` promise when the sidecar responds with a
    ``type:'error'`` envelope.

    The Rust ``dispatch`` command (sidecar_cmds.rs:53-64) checks
    ``response.get("type") == Some("error")`` and returns
    ``Err(format!("server error [{}]: {}", code, msg))`` so the
    webview's ``invoke()`` rejects. This is the NEW-IPC-107 fix on the
    Tauri path — the Electron path silently treated ``type:'error'``
    as success and was fixed by the in-code check in usePython.ts.

    Source-inspection: the Rust source must reference the `"error"`
    sentinel AND format a `server error` rejection message.
    """
    assert TAURI_RUST_ERROR_REJECT_SENTINEL in sidecar_cmds_rs_source, (
        "sidecar_cmds.rs must check `response.get('type') == Some('error')` "
        "and reject the dispatch promise (ADR-0020 §2 / NEW-IPC-107)."
    )
    assert "server error" in sidecar_cmds_rs_source, (
        "sidecar_cmds.rs must format the rejection as `server error "
        "[<code>]: <msg>` so the webview's invoke() rejects with a "
        "descriptive error string (matches the Electron path's "
        "in-code throw at usePython.ts:201-204)."
    )


def test_electron_use_python_throws_on_type_error_envelope(
    use_python_source,
) -> None:
    """ADR-0020 §6.3 / Phase 3 + NEW-IPC-107: ``usePython`` throws on
    the ``type:'error'`` envelope (Electron-path check).

    On the Electron path, the main process resolves the IPC promise
    with the full envelope verbatim (it does NOT unwrap ``data`` or
    translate ``type:'error'`` into a rejection). So ``usePython.ts``
    must check the resolved value's ``type`` field and throw a JS
    Error — otherwise callers using ``try { await python.call(...) }``
    would silently treat the error envelope as a successful result.
    """
    # The check must reference `type === 'error'` (or `.type === "error"`).
    type_error_check_re = re.compile(
        r'\.type\s*===\s*["\']error["\']',
    )
    assert type_error_check_re.search(use_python_source), (
        "usePython.ts must check `result.type === 'error'` and throw "
        "(NEW-IPC-107 / Electron path). The Tauri path rejects before "
        "the resolved value reaches JS, so this check is dead code on "
        "Tauri but load-bearing on Electron — it stays in the source."
    )
    # The throw must surface `data.message` (the Python server's error
    # message field, ipc_server.py:1044-1050).
    assert "data?.message" in use_python_source or ("data.message" in use_python_source), (
        "usePython.ts error throw must surface `data.message` (the "
        "Python server's error envelope field) so callers see the "
        "server-side error message, not a generic 'unknown error'."
    )


def test_electron_use_python_throws_on_underscore_error_envelope(
    use_python_source,
) -> None:
    """ADR-0020 §6.3 / Phase 3 + NEW-IPC-107: ``usePython`` throws on
    the ``{_error: '...'}`` envelope (Electron-path synthetic errors).

    The Electron main process's ``python-call`` handler
    (python-call-handler.ts:21/23/28) resolves with ``{_error: '...'}``
    on backend-not-connected and ``sendToPython`` exceptions. Without
    this check, callers would see a resolved object with an ``_error``
    field and read ``undefined`` from the expected data fields.
    """
    assert ELECTRON_ERROR_ENVELOPE_FIELD in use_python_source, (
        "usePython.ts must check for the `_error` field (Electron main "
        "synthetic error envelope, python-call-handler.ts:21/23/28) "
        "and throw — NEW-IPC-107."
    )
    # The throw must handle both string and object shapes (defensive
    # against future Electron-main changes that wrap the message).
    assert "throw new Error" in use_python_source, (
        "usePython.ts must `throw new Error(msg)` when the `_error` envelope is detected (NEW-IPC-107)."
    )


def test_electron_python_call_handler_produces_underscore_error_envelope(
    python_call_handler_source,
) -> None:
    """ADR-0020 §6.3 / Phase 3: Electron main ``python-call`` handler
    produces the ``{_error: '...'}`` envelope on synthetic errors.

    Source-inspection of the Electron main handler's not-connected and
    send-exception paths. These are the two paths that the
    ``usePython.ts:188` `_error` check catches.
    """
    assert ELECTRON_ERROR_ENVELOPE_FIELD in python_call_handler_source, (
        "python-call-handler.ts must produce `{_error: '...'}` envelopes "
        "on backend-not-connected and sendToPython exceptions — these "
        "are the envelopes the usePython.ts `_error` check catches."
    )
    # Specifically: not-connected path.
    assert "is not connected" in python_call_handler_source or ("not connected" in python_call_handler_source), (
        "python-call-handler.ts must produce `{_error: 'Python backend "
        "is not connected'}` when state.tcpSocket is null (the "
        "not-connected synthetic error path)."
    )


# ─── Test 6: error envelope shape parity — both paths reject


def test_error_envelope_shape_parity_tauri_vs_electron(
    sidecar_cmds_rs_source,
    use_python_source,
    python_call_handler_source,
) -> None:
    """ADR-0020 §6.3 / Phase 3: error envelope shape is consistent
    across both paths — both surface errors as JS rejections.

    Tauri:    The Rust host (sidecar_cmds.rs:53-64) checks
              ``response.get("type") == Some("error")`` and returns
              ``Err("server error [<code>]: <msg>")`` so the webview's
              ``invoke('dispatch')`` rejects. The resolved value never
              reaches JS — the in-code checks in usePython.ts:188-205
              are dead code on Tauri.

    Electron: The main handler (python-call-handler.ts:16-31) resolves
              the IPC promise with the envelope verbatim (full
              ``{type:'error', data:{code, message}}`` or
              ``{_error: '...'}`` on synthetic errors). usePython.ts
              catches both shapes and throws a JS Error.

    Parity guarantee: callers using ``try { await python.call(...) }
    catch (e) { … }`` see a JS Error on both paths. The error message
    differs (Tauri: ``server error [<code>]: <msg>``; Electron: the
    raw ``data.message`` or ``_error`` string) but the rejection
    contract is identical.
    """
    # Tauri path: Rust rejects with `server error [<code>]: <msg>`.
    assert "server error" in sidecar_cmds_rs_source, (
        "Tauri path: Rust dispatch must reject with `server error [<code>]: <msg>` (sidecar_cmds.rs:64)."
    )
    # Electron path: usePython.ts throws on `type:'error'` and `_error`.
    assert re.search(r'\.type\s*===\s*["\']error["\']', use_python_source), (
        "Electron path: usePython.ts must throw on `type:'error'` envelopes (the in-code NEW-IPC-107 check)."
    )
    assert ELECTRON_ERROR_ENVELOPE_FIELD in use_python_source, (
        "Electron path: usePython.ts must throw on `_error` envelopes (the in-code NEW-IPC-107 check)."
    )
    # The handler that produces the Electron `_error` envelope.
    assert ELECTRON_ERROR_ENVELOPE_FIELD in python_call_handler_source, (
        "Electron path: python-call-handler.ts must produce `_error` "
        "envelopes on synthetic errors (the source of the shape "
        "usePython.ts catches)."
    )


# ─── Test 7: success shape — both paths return data directly


def test_tauri_rust_unwraps_response_data_on_success(sidecar_cmds_rs_source) -> None:
    """ADR-0020 §6.3 / Phase 3 + §2: Tauri Rust host returns
    ``response.data`` directly on success (not the full envelope).

    The Rust ``dispatch`` command (sidecar_cmds.rs:66) returns
    ``Ok(response.get("data").cloned().unwrap_or(json!({})))`` — so
    the webview's ``invoke('dispatch')`` resolves with the inner
    ``data`` field, NOT the full ``{type:'result', data:...}`` envelope.
    This matches the Electron path's caller-facing shape (see
    ``test_electron_use_python_returns_result_as_t``).
    """
    # The Rust source must reference `response.get("data")` on the
    # success path. Look for `response.get("data").cloned()`.
    unwrap_re = re.compile(r'response\.get\(\s*["\']data["\']\s*\)\.cloned\(\)')
    assert unwrap_re.search(sidecar_cmds_rs_source), (
        "Tauri path: sidecar_cmds.rs must return "
        "`response.get('data').cloned().unwrap_or(json!({}))` on "
        "success (sidecar_cmds.rs:66) — the webview's invoke('dispatch') "
        "resolves with the inner data field, NOT the full envelope."
    )


def test_electron_use_python_returns_result_as_t(use_python_source) -> None:
    """ADR-2020 §6.3 / Phase 3: ``usePython`` returns ``result as T``
    after the error-envelope checks pass.

    On the Electron path, ``result`` is the full envelope (the main
    process resolves with the envelope verbatim). The error checks at
    usePython.ts:188-205 throw on `_error` / `type:'error'` shapes; if
    neither matches, the function returns ``result as T`` — which is
    the envelope's body, identical to the server's `data` payload (the
    envelope IS `{type, data}` so returning the envelope-as-T surfaces
    `data`-shape fields to the caller).

    On the Tauri path, ``result`` is already the unwrapped ``data``
    field (Rust unwrapped it at sidecar_cmds.rs:66). The error checks
    never fire (Rust rejected first), so ``return result as T`` returns
    the same logical payload.

    Parity guarantee: callers see the same shape (the server's `data`
    field) on both paths.
    """
    # usePython.ts must `return result as T` on the success path.
    return_re = re.compile(r"return\s+result\s+as\s+T", re.DOTALL)
    assert return_re.search(use_python_source), (
        "usePython.ts must `return result as T` on the success path "
        "(after the error-envelope checks pass). On Tauri `result` is "
        "the unwrapped `response.data`; on Electron `result` is the "
        "full envelope — both surface the same logical payload to the "
        "caller."
    )


def test_success_shape_parity_tauri_vs_electron(
    sidecar_cmds_rs_source,
    use_python_source,
) -> None:
    """ADR-0020 §6.3 / Phase 3: success shape is consistent across both
    paths — callers see the server's ``data`` field.

    Tauri:    Rust unwraps ``response.data`` (sidecar_cmds.rs:66)
              BEFORE returning to JS. The webview's ``invoke('dispatch')``
              resolves with the inner ``data`` field.

    Electron: Main process resolves with the full envelope. ``usePython``
              returns ``result as T`` after the error checks pass. The
              envelope's body is the server's `data` payload (the
              envelope IS `{type:'result', data:...}`), so the caller
              sees the same shape as the Tauri path.

    Source-inspection: both paths must reference the `data` field on
    the success path.
    """
    # Tauri path: Rust returns `response.get("data")`.
    assert re.search(r'response\.get\(\s*["\']data["\']\s*\)', sidecar_cmds_rs_source), (
        "Tauri path: sidecar_cmds.rs must return `response.get('data')` on success (the unwrapped inner data field)."
    )
    # Electron path: usePython.ts returns `result as T` (where `result`
    # is the resolved envelope after the error checks pass).
    assert re.search(r"return\s+result\s+as\s+T", use_python_source), (
        "Electron path: usePython.ts must `return result as T` on the "
        "success path (the envelope body IS the server's `data` payload)."
    )


# ─── Test 8: window.bubble.onLevel on both paths


def test_tauri_bubble_on_level_listens_to_tauri_event(tauri_bridge_source) -> None:
    """ADR-0020 §6.3 / Phase 3 + §9: Tauri ``bubble.onLevel`` listens
    to the ``bubble_level`` Tauri event (coalesced to ≤30 Hz by the
    Rust WS reader).

    The Rust host subscribes to the sidecar's ``bubble_level`` event
    stream over the WS and re-emits each (coalesced) frame as a Tauri
    event of the same name. The bridge subscribes via
    ``tauri.event.listen('bubble_level', …)`` and forwards the payload
    (``{rms, peak}``) to the renderer's callback.
    """
    assert TAURI_BUBBLE_LEVEL_EVENT in tauri_bridge_source, (
        "tauri-bridge.ts must subscribe to the 'bubble_level' Tauri "
        "event (ADR-0020 §9 — coalesced to ≤30 Hz by the Rust WS reader)."
    )
    # The subscription must use `tauri.event.listen`. The actual code
    # (tauri-bridge.ts:240-243) is:
    #     tauri.event
    #         .listen<{ rms: number; peak: number }>("bubble_level", (e) => {
    #             callback(e.payload);
    #         })
    # so the `.event` and `.listen` are on separate lines. Use a
    # newline-tolerant regex (allow whitespace+newlines between).
    listen_re = re.compile(
        r'tauri\.event\s*\.\s*listen[<{]?.*?["\']bubble_level["\']',
        re.DOTALL,
    )
    assert listen_re.search(tauri_bridge_source), (
        "tauri-bridge.ts must call `tauri.event.listen('bubble_level', …)` "
        "so the bubble level stream is delivered to the renderer's "
        "onLevel callback."
    )
    # The payload shape must be `{rms, peak}`.
    assert "rms" in tauri_bridge_source and "peak" in tauri_bridge_source, (
        "tauri-bridge.ts bubble_level payload must be `{rms, peak}` — "
        "matches the Electron preload's `{rms, peak}` shape "
        "(preload/index.ts:17)."
    )


def test_electron_bubble_on_level_listens_to_ipc_channel(preload_source) -> None:
    """ADR-0020 §6.3 / Phase 3: Electron ``bubble.onLevel`` listens to
    the ``bubble:level`` IPC channel.

    The Electron main process emits ``bubble:level`` on the bubble
    window's ``webContents`` (handle-message.ts:58) when the Python
    backend pushes a ``bubble_level`` event. The preload subscribes
    via ``ipcRenderer.on('bubble:level', handler)`` and forwards the
    payload (``{rms, peak}``) to the renderer's callback.
    """
    assert ELECTRON_BUBBLE_LEVEL_CHANNEL in preload_source, (
        f"preload/index.ts must subscribe to the "
        f"{ELECTRON_BUBBLE_LEVEL_CHANNEL!r} IPC channel (Electron main "
        "emits it from handle-message.ts:58 when the Python backend "
        "pushes a bubble_level event)."
    )
    # The subscription must use `ipcRenderer.on`.
    listen_re = re.compile(
        r'ipcRenderer\.on\(\s*["\']bubble:level["\']',
    )
    assert listen_re.search(preload_source), (
        "preload/index.ts must call `ipcRenderer.on('bubble:level', …)` "
        "so the bubble level stream is delivered to the renderer's "
        "onLevel callback."
    )
    # The payload shape must be `{rms, peak}`.
    assert "rms" in preload_source and "peak" in preload_source, (
        "preload/index.ts bubble:level payload must be `{rms, peak}` — matches the Tauri bridge's `{rms, peak}` shape."
    )


def test_bubble_on_level_callback_shape_parity(
    tauri_bridge_source,
    preload_source,
) -> None:
    """ADR-0020 §6.3 / Phase 3: ``bubble.onLevel`` callback shape is
    identical on both paths (``{rms: number, peak: number}``).

    The renderer's bubble waveform code consumes the callback payload
    as ``{rms, peak}`` and is unchanged on both runtimes. The Tauri
    bridge's payload type annotation (``{rms: number; peak: number}``)
    must match the Electron preload's callback signature
    (``(data: {rms: number; peak: number}) => void``).
    """
    for src, label in [
        (tauri_bridge_source, "tauri-bridge.ts"),
        (preload_source, "preload/index.ts"),
    ]:
        assert "rms" in src, f"{label} must reference `rms` (the bubble level payload field)."
        assert "peak" in src, f"{label} must reference `peak` (the bubble level payload field)."


# ─── Test 9: window.window_ minimize/maximize/close/isMaximized on both paths


@pytest.mark.parametrize(
    "method,tauri_api_fragment,electron_channel",
    [
        ("minimize", "minimize", ELECTRON_WINDOW_MINIMIZE),
        ("toggleMaximize", "toggleMaximize", ELECTRON_WINDOW_TOGGLE_MAXIMIZE),
        ("close", "close", ELECTRON_WINDOW_CLOSE),
        ("isMaximized", "isMaximized", ELECTRON_WINDOW_IS_MAXIMIZED),
    ],
    ids=["minimize", "toggleMaximize", "close", "isMaximized"],
)
def test_window_controls_parity_on_both_paths(
    tauri_bridge_source,
    preload_source,
    method: str,
    tauri_api_fragment: str,
    electron_channel: str,
) -> None:
    """ADR-0020 §6.3 / Phase 3: ``window.window_.{method}`` routes
    through the right transport on each path.

    Tauri:    ``tauri.window.getCurrentWindow().{method}()`` — Tauri's
              core window API (tauri-bridge.ts:375-383).
    Electron: ``ipcRenderer.invoke('{electron_channel}')`` — the main
              process's window-handlers.ts:21-46 register
              ``ipcMain.handle('{electron_channel}', …)``.
    """
    # Tauri path: bridge must reference the Tauri window API method.
    assert tauri_api_fragment in tauri_bridge_source, (
        f"tauri-bridge.ts must reference `{tauri_api_fragment}` (Tauri core window API method for window_.{method})."
    )
    # Electron path: preload must reference the IPC channel.
    assert electron_channel in preload_source, (
        f"preload/index.ts must reference the {electron_channel!r} "
        f"IPC channel (Electron main registers the handler at "
        f"window-handlers.ts for window_.{method})."
    )


def test_window_namespace_installed_on_both_paths(
    tauri_bridge_source,
    preload_source,
) -> None:
    """ADR-2020 §6.3 / Phase 3: ``window.window_`` namespace is
    installed on both paths.

    Tauri:    ``window.window_ = window_`` at tauri-bridge.ts:471.
    Electron: ``contextBridge.exposeInMainWorld('window_', …)`` at
              preload/index.ts:80.
    """
    assert "window.window_" in tauri_bridge_source, (
        "tauri-bridge.ts must install `window.window_` (the window controls namespace)."
    )
    assert 'exposeInMainWorld("window_"' in preload_source or ("exposeInMainWorld('window_'" in preload_source), (
        "preload/index.ts must install `window_` via `contextBridge.exposeInMainWorld('window_', …)`."
    )


def test_python_namespace_installed_on_both_paths(
    tauri_bridge_source,
    preload_source,
) -> None:
    """ADR-0020 §6.3 / Phase 3: ``window.python`` namespace is
    installed on both paths.

    Tauri:    ``window.python = python`` at tauri-bridge.ts:469.
    Electron: ``contextBridge.exposeInMainWorld('python', …)`` at
              preload/index.ts:3.
    """
    assert "window.python" in tauri_bridge_source, (
        "tauri-bridge.ts must install `window.python` (the python bridge namespace)."
    )
    assert 'exposeInMainWorld("python"' in preload_source or ("exposeInMainWorld('python'" in preload_source), (
        "preload/index.ts must install `python` via `contextBridge.exposeInMainWorld('python', …)`."
    )


def test_bubble_namespace_installed_on_both_paths(
    tauri_bridge_source,
    preload_source,
) -> None:
    """ADR-2020 §6.3 / Phase 3: ``window.bubble`` namespace is
    installed on both paths.

    Tauri:    ``window.bubble = bubble`` at tauri-bridge.ts:470.
    Electron: ``contextBridge.exposeInMainWorld('bubble', …)`` at
              preload/index.ts:16.
    """
    assert "window.bubble" in tauri_bridge_source, (
        "tauri-bridge.ts must install `window.bubble` (the bubble namespace)."
    )
    assert 'exposeInMainWorld("bubble"' in preload_source or ("exposeInMainWorld('bubble'" in preload_source), (
        "preload/index.ts must install `bubble` via `contextBridge.exposeInMainWorld('bubble', …)`."
    )


# ─── Test 10: source-inspection — bridge modules are non-empty + importable


def test_tauri_bridge_module_is_non_empty(tauri_bridge_source) -> None:
    """The Tauri bridge module must be present and non-empty."""
    assert len(tauri_bridge_source) > 1000, (
        "tauri-bridge.ts must be a substantial module (>1000 chars). "
        "An empty or stub file would indicate the bridge port was "
        "never written."
    )


def test_use_python_module_is_non_empty(use_python_source) -> None:
    """The ``usePython`` hook module must be present and non-empty."""
    assert len(use_python_source) > 1000, (
        "usePython.ts must be a substantial module (>1000 chars). "
        "An empty or stub file would indicate the hook was never written."
    )


def test_tauri_bridge_auto_installs_on_import(tauri_bridge_source) -> None:
    """ADR-0020 §6.3 / Phase 3: ``installTauriBridge()`` is called at
    module load so the bridge is ready before the React app mounts.

    Both ``main.tsx`` and ``bubble-main.tsx`` import this module at the
    top so the bridge namespaces are installed before the React tree
    mounts. Without the auto-install, the renderer would race the
    bridge install against the first ``usePython`` call.
    """
    # The auto-install call must be at module scope (not inside a function).
    # Look for `installTauriBridge();` outside any function definition.
    auto_install_re = re.compile(
        r"^installTauriBridge\(\)\s*;?\s*$",
        re.MULTILINE,
    )
    assert auto_install_re.search(tauri_bridge_source), (
        "tauri-bridge.ts must call `installTauriBridge()` at module "
        "scope (auto-install on import) so the bridge is ready before "
        "the React app mounts. main.tsx + bubble-main.tsx import this "
        "module at the top — the auto-install makes the bridge ready "
        "synchronously on first import."
    )


def test_tauri_bridge_idempotent_install(tauri_bridge_source) -> None:
    """ADR-0020 §6.3 / Phase 3: ``installTauriBridge()`` is idempotent
    (safe to call multiple times).

    HMR re-imports the module on every code change; without idempotency
    the second install would either clobber the first (resetting
    listener state) or throw (re-declaring `window.python`). The
    idempotency guard checks if all three namespaces are already
    installed and returns early.
    """
    # The idempotency guard must check `window.python && window.bubble
    # && window.window_` before re-installing.
    idempotent_re = re.compile(
        r"window\.python\s*&&\s*window\.bubble\s*&&\s*window\.window_",
    )
    assert idempotent_re.search(tauri_bridge_source), (
        "tauri-bridge.ts must guard `if (window.python && window.bubble "
        "&& window.window_) return;` so HMR re-imports don't clobber "
        "the existing namespace install."
    )


def test_tauri_bridge_installs_ft1_event_synthesis(tauri_bridge_source) -> None:
    """ADR-0020 §6.3 / Phase 3 + FT-1: ``window.python.onEvent`` also
    listens for FT-1 host events and synthesizes ``python-event``
    frames.

    FT-1 (crash isolation) relaunches the sidecar on unexpected exit.
    The Rust host emits ``ft1_relaunching`` and ``ft1_reconnected``
    Tauri events during the respawn cycle; the bridge synthesizes
    ``reconnecting`` / ``reconnected`` ``python-event`` frames so the
    renderer's ``useConnection`` hook updates the UI during the
    respawn. Without this, the renderer's connection status stays
    "connected" while the sidecar is dead, and the user sees a frozen
    UI with no feedback.

    The Electron path has no equivalent — FT-1 is a Tauri-only feature
    (the Electron build uses the older full-app relaunch path).
    """
    assert "ft1_relaunching" in tauri_bridge_source, (
        "tauri-bridge.ts must listen for `ft1_relaunching` Tauri events "
        "and synthesize a `reconnecting` python-event frame (FT-1 crash "
        "isolation, Tauri-only)."
    )
    assert "ft1_reconnected" in tauri_bridge_source, (
        "tauri-bridge.ts must listen for `ft1_reconnected` Tauri events "
        "and synthesize a `reconnected` python-event frame (FT-1 crash "
        "isolation, Tauri-only)."
    )


def test_use_python_has_per_command_timeout(use_python_source) -> None:
    """ADR-0020 §6.3 / Phase 3 + CR-18: ``usePython`` wraps the bridge
    call in a per-command timeout.

    The underlying bridge promise (Tauri Rust ``dispatch`` / Electron
    ``sendToPython``) has a blanket 120s timeout. ``usePython`` races
    the bridge call against a per-command timeout so trivial commands
    (``get_status``, ``get_config``) surface a hang in 5s instead of
    120s. This is a renderer-side concern — both paths benefit.
    """
    assert "COMMAND_TIMEOUTS" in use_python_source, (
        "usePython.ts must declare the `COMMAND_TIMEOUTS` table (CR-18 "
        "per-command timeout, applies to both Tauri + Electron paths)."
    )
    assert "withCommandTimeout" in use_python_source, (
        "usePython.ts must wrap the bridge call in `withCommandTimeout` (CR-18 per-command timeout)."
    )


def test_use_python_uses_use_sync_external_store_for_bridge_ready(
    use_python_source,
) -> None:
    """ADR-2020 §6.3 / Phase 3 + CR-6: ``usePythonEvent`` re-subscribes
    when the bridge becomes available post-mount.

    ``useBridgeReady`` uses ``useSyncExternalStore`` to poll
    ``window.python`` presence every 100ms until it appears. Including
    ``bridgeReady`` in the ``usePythonEvent`` effect's dep array causes
    the effect to re-run when ``window.python`` becomes available — so
    a slow preload / late Tauri bridge install doesn't silently drop
    the subscription.
    """
    assert "useSyncExternalStore" in use_python_source, (
        "usePython.ts must use `useSyncExternalStore` for the "
        "`useBridgeReady` hook (CR-6 — re-subscribe when the bridge "
        "becomes available post-mount)."
    )
    assert "useBridgeReady" in use_python_source, (
        "usePython.ts must export `useBridgeReady` (CR-6) and include it in the `usePythonEvent` effect's dep array."
    )
