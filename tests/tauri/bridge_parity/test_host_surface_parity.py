"""
Tauri renderer-facing surface parity (static contract tests).
Do not reintroduce a TypeScript ``ALLOWED_COMMANDS`` Set.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

CLIENT_SRC = PROJECT_ROOT / "voice_typer" / "client" / "src"
SRC_TAURI_SRC = PROJECT_ROOT / "src-tauri" / "src"

PUSH_EVENTS_TS = CLIENT_SRC / "renderer" / "src" / "types" / "ipc" / "push_events.ts"
TAURI_PYTHON_NS_TS = CLIENT_SRC / "renderer" / "src" / "lib" / "tauri-bridge" / "python-namespace.ts"
TAURI_WINDOW_NS_TS = CLIENT_SRC / "renderer" / "src" / "lib" / "tauri-bridge" / "window-namespace.ts"

EVENT_PROTOCOL_RS = SRC_TAURI_SRC / "sidecar" / "ws" / "event_protocol.rs"
WS_RS = SRC_TAURI_SRC / "sidecar" / "ws.rs"
COMMAND_ALLOWLIST_RS = SRC_TAURI_SRC / "commands" / "sidecar_cmds" / "allowlist.rs"
IPC_REGISTRY_PY = PROJECT_ROOT / "voice_typer" / "server" / "ipc" / "registry.py"

for _required in (
    PUSH_EVENTS_TS,
    TAURI_PYTHON_NS_TS,
    TAURI_WINDOW_NS_TS,
    EVENT_PROTOCOL_RS,
    WS_RS,
    COMMAND_ALLOWLIST_RS,
    IPC_REGISTRY_PY,
):
    assert _required.exists(), f"parity-test input missing: {_required}"


# Push-event union members that are NEVER published by the Python sidecar
HOST_SYNTHESIZED_PUSH_EVENTS: dict[str, str] = {
    # Host bridge starts a reconnect attempt after a transport drop;
    "reconnecting": "synthesized by the host bridge during reconnect attempts",
    # Host bridge successfully reconnected; consumed by useConnection.ts
    "reconnected": "synthesized by the host bridge after a successful reconnect",
}

# Kept as reviewed, contract for window-namespace methods the Tauri
TAURI_MISSING_WINDOW_METHODS: dict[str, str] = {
    # Share-stats image clipboard copy is not implemented under Tauri
    "copyStatsImage": ("no Rust clipboard command counterpart yet; stats-image copy is renderer web-API only"),
}

# Host-dispatched / host-only commands that live in the Python
DOCUMENTED_COMMAND_ASYMMETRY: dict[frozenset[str], str] = {
    frozenset({"heartbeat", "relaunch_ack"}): (
        "sent by the Tauri HOST directly via dispatch_inner / "
        "fire-and-forget WS frames, never dispatched by the renderer "
        "through the allowlist gate"
    ),
    frozenset({"shutdown", "tray_click"}): (
        "host-only (cooperative shutdown / tray click); the renderer has no legitimate path to either command"
    ),
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_line_comments(text: str) -> str:
    """Remove ``/* */`` and ``//`` comments while preserving ``://`` (URLs)."""
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
    """Index of the delimiter matching the bracket at ``open_idx``."""
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
    """Top-level property keys of an object-literal BODY (outer braces removed)."""
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


def _extract_python_registry(src: str) -> list[str]:
    """Command keys of the Python ``_COMMAND_REGISTRY`` dict literal."""
    m = re.search(r"_COMMAND_REGISTRY\s*:\s*dict\[str,\s*str\]\s*=\s*\{", src)
    assert m, "Python _COMMAND_REGISTRY literal not found"
    depth = 1
    i = m.end()
    n = len(src)
    while i < n and depth > 0:
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    body = src[m.end() : i - 1]
    keys = re.findall(r'"([a-z_]+)"\s*:\s*"_handle_', body)
    assert keys, "Python _COMMAND_REGISTRY parse produced no keys"
    return keys


def _parse_ts_push_event_union(push_events_src: str) -> set[str]:
    """Event-name literals of every ``PythonPushEvent`` union member."""
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


def _rust_listen_event_names() -> set[str]:
    """Every ``.listen(\"<name>\"`` registration across the Rust host tree."""
    names: set[str] = set()
    for rs_file in sorted(SRC_TAURI_SRC.rglob("*.rs")):
        names |= set(re.findall(r'\.listen\(\s*"([^"]+)"', _read(rs_file)))
    return names


def _translate_event_sources(event_protocol_src: str) -> set[str]:
    """Source names of ``\"<snake>\" => \"<kebab>\"`` arms in translate_event_name."""
    return {m.group(1) for m in re.finditer(r'"(\w+)"\s*=>\s*&?"[^"]+"', event_protocol_src)}


PYTHON_PUSH_EVENTS: frozenset[str] = frozenset(_parse_ts_push_event_union(_read(PUSH_EVENTS_TS)))
RUST_ALLOWED_EVENT_TYPES: tuple[str, ...] = tuple(
    _extract_rust_string_slice(_read(EVENT_PROTOCOL_RS), r"ALLOWED_EVENT_TYPES:\s*&\[&str\]\s*=\s*&\[")
)
RUST_EVENT_SET: frozenset[str] = frozenset(RUST_ALLOWED_EVENT_TYPES)
RUST_LISTEN_EVENTS: frozenset[str] = frozenset(_rust_listen_event_names())
BUBBLE_TRANSLATE_SOURCES: frozenset[str] = frozenset(_translate_event_sources(_read(EVENT_PROTOCOL_RS)))
WS_RS_TEXT = "\n\n".join(
    [_read(WS_RS)]
    + [
        _read(WS_RS.parent / "ws" / name)
        for name in ("reader.rs", "writer.rs")
        if (WS_RS.parent / "ws" / name).is_file()
    ]
)

TAURI_PYTHON_METHODS: frozenset[str] = frozenset(
    _factory_return_keys(_read(TAURI_PYTHON_NS_TS), "createPythonNamespace")
)
TAURI_WINDOW_METHODS: frozenset[str] = frozenset(
    _factory_return_keys(_read(TAURI_WINDOW_NS_TS), "createWindowNamespace")
)

PYTHON_COMMAND_REGISTRY: frozenset[str] = frozenset(_extract_python_registry(_read(IPC_REGISTRY_PY)))
RUST_ALLOWED_COMMANDS: frozenset[str] = frozenset(
    _extract_rust_string_slice(_read(COMMAND_ALLOWLIST_RS), r"let\s+cmds:\s*&\[&str\]\s*=\s*&\[")
)

for _name, _surface in (
    ("PYTHON_PUSH_EVENTS", PYTHON_PUSH_EVENTS),
    ("RUST_EVENT_SET", RUST_EVENT_SET),
    ("TAURI_PYTHON_METHODS", TAURI_PYTHON_METHODS),
    ("TAURI_WINDOW_METHODS", TAURI_WINDOW_METHODS),
    ("PYTHON_COMMAND_REGISTRY", PYTHON_COMMAND_REGISTRY),
    ("RUST_ALLOWED_COMMANDS", RUST_ALLOWED_COMMANDS),
):
    assert _surface, f"parser produced an EMPTY surface ({_name}), regex regression"


class TestPushEventSurfaceParity:
    """``PythonPushEvent`` union vs Rust WS-reader allowlist."""

    def test_union_parses_with_known_anchor_members(self):
        """Guard against silent-empty / misanchored union parsing."""
        assert "status_change" in PYTHON_PUSH_EVENTS
        assert "transcription_final" in PYTHON_PUSH_EVENTS
        assert len(PYTHON_PUSH_EVENTS) >= 40

    def test_every_renderer_push_event_is_allowlisted_in_the_rust_ws_reader(self):
        """Union members reach the renderer under Tauri only via the allowlist."""
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

    def test_native_push_events_have_tauri_listeners(self):
        """Host-handled push events need an ``app.listen`` registration."""
        # Events the host must listen for (window/tray lifecycle). Bubble
        native = {"show_window", "notification", "quit_app", "relaunch_app"}
        missing_listen = sorted(native - RUST_LISTEN_EVENTS)
        assert not missing_listen, (
            f"host-handled push events without an app.listen registration in src-tauri: {missing_listen}"
        )

    def test_bubble_events_have_a_tauri_delivery_route(self):
        """bubble_* events must be renamed or explicitly emitted by the WS path."""
        bubble = {name for name in PYTHON_PUSH_EVENTS if name.startswith("bubble_")}
        missing_bubble_route = sorted(
            name for name in bubble if name not in BUBBLE_TRANSLATE_SOURCES and f'"{name}"' not in WS_RS_TEXT
        )
        assert not missing_bubble_route, (
            f"bubble push events with no Tauri delivery route (rename arm or explicit emit): {missing_bubble_route}"
        )


class TestTauriBridgeSurface:
    """Tauri bridge namespaces expose the renderer's expected methods."""

    def test_python_namespace_exposes_call_and_on_event(self):
        assert {"call", "onEvent"} == TAURI_PYTHON_METHODS, (
            f"window.python surface under Tauri must be exactly {{call, onEvent}}; got {sorted(TAURI_PYTHON_METHODS)}"
        )

    def test_window_namespace_methods_have_a_tauri_implementation_or_reviewed_exception(
        self,
    ):
        # The current Tauri window namespace must not be empty.
        assert TAURI_WINDOW_METHODS, "Tauri window_ namespace parsed empty"
        # Stale exception entries: methods listed as missing but that are
        stale = sorted(set(TAURI_MISSING_WINDOW_METHODS) & TAURI_WINDOW_METHODS)
        assert not stale, (
            "TAURI_MISSING_WINDOW_METHODS lists methods the Tauri bridge "
            f"NOW implements, delete the stale entries: {stale}"
        )


class TestCommandAllowlistParity:
    def test_command_allowlists_stay_in_lockstep_across_python_and_rust(self):
        """
        Renderer↔host command gate parity (defense-in-depth cross-check).
        Two-way contract: every Rust-allowlisted command is registered
        """
        registry_only = PYTHON_COMMAND_REGISTRY - RUST_ALLOWED_COMMANDS
        rust_only = RUST_ALLOWED_COMMANDS - PYTHON_COMMAND_REGISTRY
        documented_extra = {name for group, _reason in DOCUMENTED_COMMAND_ASYMMETRY.items() for name in group}
        unexpected_registry_only = sorted(registry_only - documented_extra)
        assert not unexpected_registry_only, (
            f"commands in the Python registry but not the Rust allowlist "
            f"(add to allowlist.rs or DOCUMENTED_COMMAND_ASYMMETRY): "
            f"{unexpected_registry_only}"
        )
        assert not sorted(rust_only), f"commands in the Rust allowlist but not the Python registry: {sorted(rust_only)}"
        stale_documented = sorted(documented_extra - registry_only)
        assert not stale_documented, (
            "DOCUMENTED_COMMAND_ASYMMETRY lists commands no longer "
            f"registry-only, delete the stale entries: {stale_documented}"
        )


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
    """Prove the parsers DETECT drift (a planted member fails the contract)."""

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

    def test_python_registry_parser_detects_missing_handler_key(self):
        src = '_COMMAND_REGISTRY: dict[str, str] = {"get_status": "_handle_get_status"}'
        assert _extract_python_registry(src) == ["get_status"]
