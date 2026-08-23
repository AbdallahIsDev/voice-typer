r"""Electron↔Tauri renderer-facing surface parity (static contract tests).

Existing guards pin the *command* allowlists three ways (server registry ↔
Electron ``ALLOWED_COMMANDS`` ↔ Rust ``allowed_commands()``) and the WS
event protocol inside the Rust tree, but NOTHING enumerated the
RENDERER-FACING surface — the methods the preload installs on ``window``
and the ``PythonPushEvent`` union members the renderer types consume —
against BOTH runtime implementations. Parity gaps therefore surfaced
silently at runtime (e.g. ``setLocale`` missing on Tauri; ``show_window`` /
``notification`` unhandled on Tauri until host listeners were added).

This module closes that gap by PARSING (regex / string-slicing only — no
TS compilation, no cargo/npm build) both implementations of every
renderer-facing surface and asserting they agree:

Enumerated surfaces
-------------------

1. ``voice_typer/client/src/preload/index.ts``
   — the Electron preload's ``exposeInMainWorld("python", ...)`` and
   ``exposeInMainWorld("window_", ...)`` method sets + every
   ``ipcRenderer.invoke(<channel>)`` reference.
2. ``voice_typer/client/src/renderer/src/types/ipc/push_events.ts``
   — the ``PythonPushEvent`` discriminated union members (resolved from
   the union membership list to each interface's ``type: "..."`` literal,
   so future additions are auto-covered).
3. ``voice_typer/client/src/renderer/src/lib/tauri-bridge/{python,
   window}-namespace.ts`` — the Tauri-side implementations of the same
   namespaces.
4. ``voice_typer/client/src/main/`` — the Electron main-process side:
   every ``ipcMain.handle(...)`` registration (channel constants resolved
   through ``main/ipc/channels.ts``), the dedicated push-event dispatch
   table in ``main/python/handle-message.ts``, and the bubble-only event
   filter in ``main/ipc/bubble-handlers.ts``.
5. ``src-tauri/src/`` — the Rust host side: ``ALLOWED_EVENT_TYPES`` in
   ``sidecar/ws/event_protocol.rs`` (the WS-reader allowlist that decides
   which server events reach the renderer as Tauri events), the
   ``translate_event_name`` bubble renames, the per-event ``app.listen``
   host handlers (``host_events.rs``, ``main.rs``, ``tray.rs``), and the
   renderer→sidecar command allowlist literal in
   ``commands/sidecar_cmds/allowlist.rs``.

Assertions
----------

* Every ``PythonPushEvent`` union member must be allowlisted in the Rust
  WS reader (or be an explicitly reviewed host-synthesized exception).
* Every Electron-main-dedicated push handler must have a Tauri host-side
  counterpart (an ``app.listen`` registration, or a bubble rename arm /
  explicit emit for the bubble-only events).
* Every preload-exposed namespace method must exist on BOTH runtimes —
  or be listed in the reviewed, commented exceptions below with a reason.
* Every channel the preload invokes must have an ``ipcMain.handle``
  registration in the Electron main process.
* The TS and Rust command allowlists stay in lockstep modulo their one
  documented asymmetry.

Robustness
----------

All parsers normalize comments/whitespace, brace-match object literals
(string-aware), and fail loudly on empty parses so a regex regression can
never silently turn these into vacuous passes. A dedicated self-check
class proves the enumerators detect planted synthetic drift.

These tests run on every platform (pure text inspection; no build, no
runtime). They complement — not replace — the runtime host validation.
"""

from __future__ import annotations

import re
from pathlib import Path

# ── Project paths ────────────────────────────────────────────────────────
# This file lives at tests/tauri/bridge_parity/test_electron_tauri_surface_parity.py.
# parents[0] = bridge_parity/, parents[1] = tauri/, parents[2] = tests/,
# parents[3] = <project root>.
PROJECT_ROOT = Path(__file__).resolve().parents[3]

CLIENT_SRC = PROJECT_ROOT / "voice_typer" / "client" / "src"
SRC_TAURI_SRC = PROJECT_ROOT / "src-tauri" / "src"

PRELOAD_TS = CLIENT_SRC / "preload" / "index.ts"
PUSH_EVENTS_TS = CLIENT_SRC / "renderer" / "src" / "types" / "ipc" / "push_events.ts"
TAURI_PYTHON_NS_TS = CLIENT_SRC / "renderer" / "src" / "lib" / "tauri-bridge" / "python-namespace.ts"
TAURI_WINDOW_NS_TS = CLIENT_SRC / "renderer" / "src" / "lib" / "tauri-bridge" / "window-namespace.ts"
HANDLE_MESSAGE_TS = CLIENT_SRC / "main" / "python" / "handle-message.ts"
MAIN_IPC_DIR = CLIENT_SRC / "main" / "ipc"
CHANNELS_TS = MAIN_IPC_DIR / "channels.ts"
BUBBLE_HANDLERS_TS = MAIN_IPC_DIR / "bubble-handlers.ts"
MAIN_WINDOW_TS = CLIENT_SRC / "main" / "windows" / "main-window.ts"
ALLOWED_COMMANDS_TS = CLIENT_SRC / "main" / "allowed-commands.ts"

EVENT_PROTOCOL_RS = SRC_TAURI_SRC / "sidecar" / "ws" / "event_protocol.rs"
WS_RS = SRC_TAURI_SRC / "sidecar" / "ws.rs"
COMMAND_ALLOWLIST_RS = SRC_TAURI_SRC / "commands" / "sidecar_cmds" / "allowlist.rs"

for _required in (
    PRELOAD_TS,
    PUSH_EVENTS_TS,
    TAURI_PYTHON_NS_TS,
    TAURI_WINDOW_NS_TS,
    HANDLE_MESSAGE_TS,
    CHANNELS_TS,
    BUBBLE_HANDLERS_TS,
    MAIN_WINDOW_TS,
    ALLOWED_COMMANDS_TS,
    EVENT_PROTOCOL_RS,
    WS_RS,
    COMMAND_ALLOWLIST_RS,
):
    assert _required.exists(), f"parity-test input missing: {_required}"

# ── Reviewed exceptions ──────────────────────────────────────────────────

# Push-event union members that are NEVER published by the Python sidecar
# (`event_bus.publish`). Each is SYNTHESIZED by the host bridge when the
# transport layer drops/re-establishes the connection — under Electron by
# the main process reconnect machinery, under Tauri by
# `lib/tauri-bridge/python-namespace.ts` translating the supervisor
# `supervisor_relaunching` / `supervisor_reconnected` host events. They are
# deliberately absent from the Rust WS-reader allowlist because no inbound
# WS frame ever carries them. If a name here ever gains a server-side
# publisher, remove it from this map — the parity test will then require it
# in `ALLOWED_EVENT_TYPES`.
HOST_SYNTHESIZED_PUSH_EVENTS: dict[str, str] = {
    # Host bridge starts a reconnect attempt after a transport drop;
    # consumed by hooks/useConnection.ts to flip the UI to "restarting".
    "reconnecting": "synthesized by the host bridge during reconnect attempts",
    # Host bridge successfully reconnected; consumed by useConnection.ts
    # to restore the "connected" UI state.
    "reconnected": "synthesized by the host bridge after a successful reconnect",
}

# Preload-exposed `window.window_` methods with NO Tauri implementation.
# Each entry documents WHY the gap exists today (mirroring the optionality
# rationale in types/ipc/bridge.ts). This list is REVIEWED CONTRACT, not a
# dumping ground: adding a method here without implementing it under Tauri
# requires updating the WindowBridge docstring too, and implementing a
# method under Tauri REQUIRES deleting its entry (the staleness assertion
# fails while the entry survives).
TAURI_MISSING_WINDOW_METHODS: dict[str, str] = {
    # Renderer pushes its locale so Electron main can localize native
    # dialogs. The Tauri host localizes its dialogs via the OS locale, so
    # the bridge does not install this method (tracked gap in review.md).
    "setLocale": (
        "Tauri-side native dialogs localize via the OS locale; the "
        "renderer-pushed locale has no consumer on the Rust host yet"
    ),
    # Restarts ONLY the Python backend process while Electron stays alive.
    # The Rust host owns the sidecar lifecycle itself (supervisor +
    # respawn scheduler), so there is no renderer-triggered restart path.
    "restartBackend": (
        "the Tauri Rust host owns backend lifecycle via its supervisor; "
        "no main-process spawn surface exists for the renderer to trigger"
    ),
    # Share-stats image platform operations. Under Tauri the renderer
    # falls back to an anchor download; the clipboard/reveal operations
    # have no Rust command counterparts yet.
    "saveStatsImage": ("no Rust command counterpart yet; the renderer falls back to an anchor download under Tauri"),
    "copyStatsImage": ("no Rust clipboard command counterpart yet; stats-image copy is Electron-only"),
    "revealStatsImage": ("no Rust shell-reveal command counterpart yet; stats-image reveal is Electron-only"),
}

# Documented asymmetry between the TS renderer allowlist and the Rust host
# allowlist (see allowlist.rs): both commands are sent by the ELECTRON MAIN
# process directly to the Python sidecar (never by the renderer through the
# Tauri dispatch gate), so they exist in the TS set but intentionally not in
# the Rust literal. Any OTHER divergence fails the lockstep test.
DOCUMENTED_COMMAND_ASYMMETRY: dict[frozenset[str], str] = {
    frozenset({"heartbeat", "relaunch_ack"}): (
        "sent by the Electron/Tauri HOST processes directly over the "
        "transport, never dispatched by the renderer through the gate"
    ),
}


# ── Parsing helpers ──────────────────────────────────────────────────────


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_line_comments(text: str) -> str:
    """Remove ``/* */`` and ``//`` comments while preserving ``://`` (URLs).

    Block comments are stripped FIRST so JSDoc prose (which quotes code
    like ``ALLOWED_COMMANDS = new Set([``) can never bind a parser anchor
    or leak quoted words into an extracted literal slice.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?<!:)//[^\n]*", "", text)


_STRING_TOKEN_RE = re.compile(
    r'"(?:[^"\\\n]|\\.)*"'  # double-quoted string
    r"|'(?:[^'\\\n]|\\.)*'"  # single-quoted string
    r"|`(?:[^`\\]|\\.)*`",  # template literal (may span lines, holds ${})
    re.DOTALL,
)

_KEY_RE = re.compile(r"([A-Za-z_$][\w$]*)\s*:")


def _match_delimiter(text: str, open_idx: int) -> int:
    """Index of the delimiter matching the bracket at ``open_idx``.

    String-aware so braces/parens inside literals never skew the count.
    """
    depth = 0
    i = open_idx
    n = len(text)
    opener = text[open_idx]
    closer = {"{": "}", "[": "]", "(": ")"}[opener]
    while i < n:
        s = _STRING_TOKEN_RE.match(text, i)
        if s:
            i = s.end()
            continue
        ch = text[i]
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError(f"unbalanced '{opener}' at offset {open_idx}")


def _object_literal_keys(body: str) -> list[str]:
    """Top-level property keys of an object-literal BODY (outer braces removed).

    Tracks brace nesting AND paren grouping so type annotations inside
    arrow-function parameter lists (``(msg: { type: string }) => ...``)
    never leak pseudo-keys. A candidate key must start at brace depth 0
    and paren depth 0, and the previous significant character must be a
    `,` or `{` (or body start) — this rejects ternary branches like
    ``cond ? a : b``.
    """
    keys: list[str] = []
    brace_depth = 0
    paren_depth = 0
    i = 0
    n = len(body)
    while i < n:
        s = _STRING_TOKEN_RE.match(body, i)
        if s:
            i = s.end()
            continue
        ch = body[i]
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
        elif ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth -= 1
        elif brace_depth == 0 and paren_depth == 0:
            j = i - 1
            while j >= 0 and body[j] in " \t\n\r":
                j -= 1
            boundary_ok = j < 0 or body[j] in ",{"
            if boundary_ok:
                m = _KEY_RE.match(body, i)
                if m:
                    keys.append(m.group(1))
                    i = m.end()
                    continue
        i += 1
    return keys


def _extract_rust_string_slice(src: str, anchor: str) -> list[str]:
    """Quoted strings inside the ``&[...]`` / ``[...]`` slice at ``anchor``."""
    src = _strip_line_comments(src)
    m = re.search(anchor, src)
    assert m, f"Rust anchor not found: {anchor!r}"
    end = src.index("];", m.end())
    return re.findall(r'"([^"]*)"', src[m.end() : end])


def _extract_ts_string_set(src: str, anchor: str) -> list[str]:
    """Quoted strings inside the ``new Set([...])`` literal at ``anchor``."""
    src = _strip_line_comments(src)
    m = re.search(anchor, src)
    assert m, f"TS Set anchor not found: {anchor!r}"
    end = src.index("]);", m.end())
    return re.findall(r'"([^"]*)"', src[m.end() : end])


def _parse_ts_push_event_union(push_events_src: str) -> set[str]:
    """Event-name literals of every ``PythonPushEvent`` union member.

    Resolves the union membership list (interface NAMES) through each
    interface's declared ``type: "<literal>"`` so future additions to the
    union are picked up automatically.
    """
    text = _strip_line_comments(push_events_src)
    anchor = re.search(r"export\s+type\s+PythonPushEvent\s*=", text)
    assert anchor, "PythonPushEvent union declaration not found"
    members: list[str] = []
    for line in text[anchor.end() :].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = re.fullmatch(r"\|?\s*(\w+)\s*(;?)", stripped)
        if not m:
            continue
        members.append(m.group(1))
        if m.group(2):
            break
    assert members, "union membership parse produced no members"
    interfaces = {}
    for im in re.finditer(r"export\s+interface\s+(\w+)[^{]*\{", text):
        try:
            close = _match_delimiter(text, im.end() - 1)
        except ValueError:
            continue
        body = text[im.end() : close]
        t = re.search(r'\btype\s*:\s*"([^"]+)"', body)
        if t:
            interfaces[im.group(1)] = t.group(1)
    unresolved = [name for name in members if name not in interfaces]
    assert not unresolved, f"union members without parsed interface: {unresolved}"
    return {interfaces[name] for name in members}


def _preload_namespace_methods(preload_src: str, namespace: str) -> set[str]:
    """Method keys exposed via ``exposeInMainWorld("<namespace>", {...})``."""
    text = _strip_line_comments(preload_src)
    m = re.search(rf'exposeInMainWorld\(\s*"{namespace}"\s*,\s*\{{', text)
    assert m, f'exposeInMainWorld("{namespace}") not found in preload'
    close = _match_delimiter(text, m.end() - 1)
    return set(_object_literal_keys(text[m.end() : close]))


def _factory_return_keys(ts_src: str, factory_name: str) -> set[str]:
    """Property keys of the object returned by ``function <factory_name>``."""
    text = _strip_line_comments(ts_src)
    fac = re.search(rf"function\s+{factory_name}\b", text)
    assert fac, f"factory {factory_name} not found"
    ret = re.search(r"return\s*\{", text[fac.end() :])
    assert ret, f"{factory_name} has no object return"
    open_idx = fac.end() + ret.end() - 1
    close = _match_delimiter(text, open_idx)
    return set(_object_literal_keys(text[open_idx + 1 : close]))


def _channel_constants(channels_src: str) -> dict[str, str]:
    """Flatten ``export const X = { key: "value", ... }`` blocks to X.key→value."""
    mapping: dict[str, str] = {}
    for m in re.finditer(r"export\s+const\s+(\w+)\s*=\s*\{([^}]*)\}", channels_src):
        const = m.group(1)
        for key, value in re.findall(r'(\w+)\s*:\s*"([^"]+)"', m.group(2)):
            mapping[f"{const}.{key}"] = value
    return mapping


def _electron_main_handled_channels(main_ipc_dir: Path) -> set[str]:
    """Channel names registered via ``ipcMain.handle(...)`` across main/ipc."""
    channels = _channel_constants(_read(CHANNELS_TS))
    resolved: set[str] = set()
    for ts_file in sorted(main_ipc_dir.glob("*.ts")):
        text = _strip_line_comments(_read(ts_file))
        for m in re.finditer(r"ipcMain\.handle\s*\(\s*([A-Za-z_][\w.]*)", text):
            ref = m.group(1)
            resolved.add(channels.get(ref, ref))
    return resolved


def _preload_invoked_channels(preload_src: str) -> set[str]:
    """Channel names referenced by ``ipcRenderer.invoke(...)`` in the preload."""
    channels = _channel_constants(_read(CHANNELS_TS))
    text = _strip_line_comments(preload_src)
    resolved: set[str] = set()
    for m in re.finditer(r"ipcRenderer\.invoke\(\s*([A-Za-z_][\w.]*)", text):
        ref = m.group(1)
        resolved.add(channels.get(ref, ref))
    return resolved


def _rust_listen_event_names() -> set[str]:
    """Every ``.listen("<name>"`` registration across the Rust host tree."""
    names: set[str] = set()
    for rs_file in sorted(SRC_TAURI_SRC.rglob("*.rs")):
        names |= set(re.findall(r'\.listen\(\s*"([^"]+)"', _read(rs_file)))
    return names


def _translate_event_sources(event_protocol_src: str) -> set[str]:
    """Source names of ``"<snake>" => "<kebab>"`` arms in translate_event_name."""
    return {m.group(1) for m in re.finditer(r'"(\w+)"\s*=>\s*&?"[^"]+"', event_protocol_src)}


def _push_handler_keys(handle_message_src: str) -> set[str]:
    """Keys of the dedicated push-event dispatch table in handle-message.ts."""
    text = _strip_line_comments(handle_message_src)
    m = re.search(r"PUSH_HANDLERS[^={]*=\s*\{", text)
    assert m, "PUSH_HANDLERS dispatch table not found"
    close = _match_delimiter(text, m.end() - 1)
    return set(_object_literal_keys(text[m.end() : close]))


# ── Parsed surfaces (module-level; fail collection loudly if empty) ─────

_PRELOAD_TEXT = _strip_line_comments(_read(PRELOAD_TS))

PYTHON_PUSH_EVENTS: frozenset[str] = frozenset(_parse_ts_push_event_union(_read(PUSH_EVENTS_TS)))
RUST_ALLOWED_EVENT_TYPES: tuple[str, ...] = tuple(
    _extract_rust_string_slice(_read(EVENT_PROTOCOL_RS), r"ALLOWED_EVENT_TYPES:\s*&\[&str\]\s*=\s*&\[")
)
RUST_EVENT_SET: frozenset[str] = frozenset(RUST_ALLOWED_EVENT_TYPES)
RUST_LISTEN_EVENTS: frozenset[str] = frozenset(_rust_listen_event_names())
BUBBLE_TRANSLATE_SOURCES: frozenset[str] = frozenset(_translate_event_sources(_read(EVENT_PROTOCOL_RS)))
WS_RS_TEXT = _read(WS_RS)

ELECTRON_PUSH_HANDLERS: frozenset[str] = frozenset(_push_handler_keys(_read(HANDLE_MESSAGE_TS)))
ELECTRON_BUBBLE_ONLY_TYPES: frozenset[str] = frozenset(
    _extract_ts_string_set(
        _read(BUBBLE_HANDLERS_TS),
        r"BUBBLE_ONLY_TYPES[^=]*=\s*new\s+Set\(",
    )
)

PRELOAD_PYTHON_METHODS: frozenset[str] = frozenset(_preload_namespace_methods(_read(PRELOAD_TS), "python"))
PRELOAD_WINDOW_METHODS: frozenset[str] = frozenset(_preload_namespace_methods(_read(PRELOAD_TS), "window_"))
TAURI_PYTHON_METHODS: frozenset[str] = frozenset(
    _factory_return_keys(_read(TAURI_PYTHON_NS_TS), "createPythonNamespace")
)
TAURI_WINDOW_METHODS: frozenset[str] = frozenset(
    _factory_return_keys(_read(TAURI_WINDOW_NS_TS), "createWindowNamespace")
)

ELECTRON_MAIN_HANDLED_CHANNELS: frozenset[str] = frozenset(_electron_main_handled_channels(MAIN_IPC_DIR))
PRELOAD_INVOKED_CHANNELS: frozenset[str] = frozenset(_preload_invoked_channels(_read(PRELOAD_TS)))

TS_ALLOWED_COMMANDS: frozenset[str] = frozenset(
    _extract_ts_string_set(_read(ALLOWED_COMMANDS_TS), r"ALLOWED_COMMANDS\s*=\s*new\s+Set[^([]*\(")
)
RUST_ALLOWED_COMMANDS: frozenset[str] = frozenset(
    _extract_rust_string_slice(_read(COMMAND_ALLOWLIST_RS), r"let\s+cmds:\s*&\[&str\]\s*=\s*&\[")
)

for _name, _surface in (
    ("PYTHON_PUSH_EVENTS", PYTHON_PUSH_EVENTS),
    ("RUST_EVENT_SET", RUST_EVENT_SET),
    ("ELECTRON_PUSH_HANDLERS", ELECTRON_PUSH_HANDLERS),
    ("ELECTRON_BUBBLE_ONLY_TYPES", ELECTRON_BUBBLE_ONLY_TYPES),
    ("PRELOAD_PYTHON_METHODS", PRELOAD_PYTHON_METHODS),
    ("PRELOAD_WINDOW_METHODS", PRELOAD_WINDOW_METHODS),
    ("TAURI_PYTHON_METHODS", TAURI_PYTHON_METHODS),
    ("TAURI_WINDOW_METHODS", TAURI_WINDOW_METHODS),
    ("ELECTRON_MAIN_HANDLED_CHANNELS", ELECTRON_MAIN_HANDLED_CHANNELS),
    ("PRELOAD_INVOKED_CHANNELS", PRELOAD_INVOKED_CHANNELS),
    ("TS_ALLOWED_COMMANDS", TS_ALLOWED_COMMANDS),
    ("RUST_ALLOWED_COMMANDS", RUST_ALLOWED_COMMANDS),
):
    assert _surface, f"parser produced an EMPTY surface ({_name}) — regex regression"


# ═════════════════════════════════════════════════════════════════════════
# Push-event surface parity
# ═════════════════════════════════════════════════════════════════════════


class TestPushEventSurfaceParity:
    """``PythonPushEvent`` union vs Rust WS-reader allowlist vs Electron main."""

    def test_union_parses_with_known_anchor_members(self):
        """Guard against silent-empty / misanchored union parsing."""
        assert "status_change" in PYTHON_PUSH_EVENTS
        assert "transcription_final" in PYTHON_PUSH_EVENTS
        assert len(PYTHON_PUSH_EVENTS) >= 40

    def test_every_renderer_push_event_is_allowlisted_in_the_rust_ws_reader(self):
        """Union members reach the renderer under Tauri only via the allowlist.

        The Rust WS reader DROPS any inbound frame whose ``type`` is not in
        ``ALLOWED_EVENT_TYPES`` — so an event typed in TS but missing there
        silently never fires on Tauri while working under Electron.
        """
        missing = sorted(PYTHON_PUSH_EVENTS - RUST_EVENT_SET - set(HOST_SYNTHESIZED_PUSH_EVENTS))
        assert not missing, (
            f"PythonPushEvent members absent from ALLOWED_EVENT_TYPES (dropped by the Tauri WS reader): {missing}"
        )

    def test_host_synthesized_exceptions_are_documented_and_typed(self):
        """The exception set stays minimal, typed, and actually used."""
        synthesized = set(HOST_SYNTHESIZED_PUSH_EVENTS)
        assert synthesized <= PYTHON_PUSH_EVENTS, "stale exception entries"
        assert synthesized.isdisjoint(RUST_EVENT_SET), (
            "events now allowlisted server-side must leave the host-synthesized exception set"
        )

    def test_rust_event_allowlist_contains_no_duplicate_names(self):
        duplicates = [name for name in set(RUST_ALLOWED_EVENT_TYPES) if RUST_ALLOWED_EVENT_TYPES.count(name) > 1]
        assert not duplicates, f"duplicate entries: {duplicates}"

    def test_electron_main_broadcast_delivery_machinery_is_present(self):
        """Non-bubble events rely on the unconditional broadcast fall-through.

        Every push event NOT consumed by a dedicated handler or filtered as
        bubble-only reaches the main renderer via
        ``broadcastToMainWindow(PythonChannels.event, ...)`` — the Electron
        half of the delivery contract.
        """
        assert re.search(
            r"broadcastToMainWindow\s*\(\s*PythonChannels\.event",
            _strip_line_comments(_read(HANDLE_MESSAGE_TS)),
        ), "the unconditional python-event broadcast disappeared from handle-message.ts"

    def test_bubble_only_filter_covers_exactly_the_bubble_handlers(self):
        """Events routed exclusively to the bubble window are exactly the five
        bubble_* dispatch-table keys (SEC-017 keeps everything else off that path)."""
        bubble_handlers = ELECTRON_PUSH_HANDLERS & ELECTRON_BUBBLE_ONLY_TYPES
        expected = {name for name in ELECTRON_PUSH_HANDLERS if name.startswith("bubble_")}
        assert bubble_handlers == expected

    def test_electron_dedicated_push_handlers_have_tauri_counterparts(self):
        """Dedicated Electron-main handling implies Tauri host handling.

        - Non-bubble handlers (show_window, notification, quit_app,
          relaunch_app) MUST have an ``app.listen("<name>")`` registration
          in the Rust host (host_events.rs / main.rs).
        - Bubble handlers MUST either be renamed by
          ``translate_event_name`` to the kebab-case names the Tauri bubble
          window listens for, or be explicitly emitted by the WS reader
          (``bubble_level``'s coalescing path emits the raw snake name).
        """
        native = ELECTRON_PUSH_HANDLERS - ELECTRON_BUBBLE_ONLY_TYPES
        missing_listen = sorted(native - RUST_LISTEN_EVENTS)
        assert not missing_listen, (
            f"Electron-main-dedicated push handlers without an app.listen registration in src-tauri: {missing_listen}"
        )
        missing_bubble_route = sorted(
            name
            for name in ELECTRON_PUSH_HANDLERS & ELECTRON_BUBBLE_ONLY_TYPES
            if name not in BUBBLE_TRANSLATE_SOURCES and f'"{name}"' not in WS_RS_TEXT
        )
        assert not missing_bubble_route, (
            "bubble-only push handlers with no Tauri delivery route "
            f"(rename arm or explicit emit): {missing_bubble_route}"
        )


# ═════════════════════════════════════════════════════════════════════════
# Renderer bridge namespace parity
# ═════════════════════════════════════════════════════════════════════════


class TestRendererBridgeSurfaceParity:
    """Preload-exposed namespaces vs both runtime implementations."""

    def test_python_namespace_matches_on_both_runtimes(self):
        assert {"call", "onEvent"} == PRELOAD_PYTHON_METHODS
        assert TAURI_PYTHON_METHODS == PRELOAD_PYTHON_METHODS, (
            "window.python surface diverges between the Electron preload "
            f"({sorted(PRELOAD_PYTHON_METHODS)}) and the Tauri bridge "
            f"({sorted(TAURI_PYTHON_METHODS)})"
        )

    def test_window_namespace_methods_have_a_tauri_implementation_or_reviewed_exception(
        self,
    ):
        missing = PRELOAD_WINDOW_METHODS - TAURI_WINDOW_METHODS
        undocumented = sorted(missing - set(TAURI_MISSING_WINDOW_METHODS))
        assert not undocumented, (
            "preload window_ methods with NO Tauri implementation and NO "
            f"entry in TAURI_MISSING_WINDOW_METHODS: {undocumented}"
        )
        stale = sorted(set(TAURI_MISSING_WINDOW_METHODS) - missing)
        assert not stale, (
            "TAURI_MISSING_WINDOW_METHODS lists methods the Tauri bridge "
            f"NOW implements — delete the stale entries: {stale}"
        )

    def test_preload_invoke_channels_all_have_electron_main_handlers(self):
        missing = sorted(PRELOAD_INVOKED_CHANNELS - ELECTRON_MAIN_HANDLED_CHANNELS)
        assert not missing, (
            f"channels invoked by the preload without an ipcMain.handle registration in main/ipc: {missing}"
        )

    def test_maximized_changed_push_channel_is_wired_by_electron_main(self):
        """onMaximizedChanged is a main→renderer PUSH (no invoke); the main
        process must still send the channel the preload listens on."""
        assert re.search(
            r"webContents\.send\s*\(\s*WindowChannels\.maximizedChanged",
            _strip_line_comments(_read(MAIN_WINDOW_TS)),
        ), "maximized-changed push channel lost from main-window.ts"

    def test_command_allowlists_stay_in_lockstep_across_ts_and_rust(self):
        """Renderer↔host command gate parity (defense-in-depth cross-check).

        Existing guards pin TS↔Python-registry and Rust↔count; this pins
        the exact TS↔Rust entry sets, allowing ONLY the documented
        host-only asymmetry (heartbeat / relaunch_ack).
        """
        ts_only = TS_ALLOWED_COMMANDS - RUST_ALLOWED_COMMANDS
        rust_only = RUST_ALLOWED_COMMANDS - TS_ALLOWED_COMMANDS
        documented_extra = {name for group, _reason in DOCUMENTED_COMMAND_ASYMMETRY.items() for name in group}
        unexpected_ts_only = sorted(ts_only - documented_extra)
        assert not unexpected_ts_only, (
            f"commands in the TS allowlist but not the Rust allowlist "
            f"(add to allowlist.rs or DOCUMENTED_COMMAND_ASYMMETRY): "
            f"{unexpected_ts_only}"
        )
        assert not sorted(rust_only), f"commands in the Rust allowlist but not the TS allowlist: {sorted(rust_only)}"
        stale_documented = sorted(documented_extra - ts_only)
        assert not stale_documented, (
            "DOCUMENTED_COMMAND_ASYMMETRY lists commands no longer "
            f"TS-only — delete the stale entries: {stale_documented}"
        )


# ═════════════════════════════════════════════════════════════════════════
# Enumerator self-check (fail-capability proof)
# ═════════════════════════════════════════════════════════════════════════


_SYNTHETIC_UNION = """
export interface FakeAlphaEvent {
	type: "fake_alpha";
}
export interface RealBetaEvent {
	type: "real_beta";
}
export type PythonPushEvent =
	| FakeAlphaEvent
	| RealBetaEvent;
"""

_SYNTHETIC_RUST_ANCHOR = r"ALLOWED_EVENT_TYPES:\s*&\[&str\]\s*=\s*&\["


class TestEnumeratorSelfCheck:
    """Prove the parsers DETECT drift (a planted member fails the contract).

    These run the SAME helpers against synthetic fixtures — committed green
    by construction — so a future regex regression that made the parsers
    vacuous would fail here instead of silently passing the real surfaces.
    """

    def test_planted_member_without_rust_allowlist_entry_is_detected(self):
        events = _parse_ts_push_event_union(_SYNTHETIC_UNION)
        assert events == {"fake_alpha", "real_beta"}
        rust = frozenset(
            _extract_rust_string_slice(
                'const ALLOWED_EVENT_TYPES: &[&str] = &["real_beta"];',
                _SYNTHETIC_RUST_ANCHOR,
            )
        )
        gaps = events - rust - set(HOST_SYNTHESIZED_PUSH_EVENTS)
        assert gaps == {"fake_alpha"}, "enumerator failed to detect planted drift"

    def test_planted_member_allowlisted_on_both_sides_passes(self):
        events = _parse_ts_push_event_union(_SYNTHETIC_UNION)
        rust = frozenset(
            _extract_rust_string_slice(
                'const ALLOWED_EVENT_TYPES: &[&str] = &["fake_alpha", "real_beta"];',
                _SYNTHETIC_RUST_ANCHOR,
            )
        )
        assert not (events - rust - set(HOST_SYNTHESIZED_PUSH_EVENTS))

    def test_object_key_parser_ignores_annotations_and_ternaries(self):
        body = (
            "call: (msg: { type: string }) => ipcRenderer.invoke(Chan.call, msg),\n"
            "pick: (a: number, b: number) => a,\n"
            "flag: true ? 1 : 2,\n"
            "realKey: () => {},\n"
        )
        assert _object_literal_keys(body) == ["call", "pick", "flag", "realKey"]
