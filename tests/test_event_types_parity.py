"""This file is the regression guard for the **fourth allowlist** that was"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


def _repo_root() -> Path:
    """Return the lausu repo root (parent of the ``tests`` dir)."""
    return Path(__file__).resolve().parent.parent


REPO_ROOT = _repo_root()

EVENT_PROTOCOL_RS = REPO_ROOT / "src-tauri" / "src" / "sidecar" / "ws" / "event_protocol.rs"
PUSH_EVENTS_TS = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "types" / "ipc" / "push_events.ts"
REQUESTS_TS = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "types" / "ipc" / "requests.ts"
USE_PYTHON_TS = (
    REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "lib" / "python-bridge" / "known-event-types.ts"
)
ALLOWLIST_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds" / "allowlist.rs"
EVENT_BUS_PY = REPO_ROOT / "voice_typer" / "server" / "event_bus.py"


def _pack_event_types() -> frozenset[str]:
    """Return the canonical 13-event frozenset from ``service.offline_pack``."""
    from voice_typer.server.service.offline_pack import OFFLINE_PACK_EVENT_TYPES

    return OFFLINE_PACK_EVENT_TYPES


# The single request-type event (the other 12 are push events).
REQUEST_EVENT_NAME = "transcribe_offline"


def _push_event_types() -> set[str]:
    """Return the 12 push event names (OFFLINE_PACK_EVENT_TYPES minus the request)."""
    return set(_pack_event_types()) - {REQUEST_EVENT_NAME}


def _read_event_protocol_rs() -> str:
    """Read the Rust event-protocol source as text."""
    assert EVENT_PROTOCOL_RS.is_file(), (
        f"expected Tauri WS reader at {EVENT_PROTOCOL_RS}, file not found. "
        "The ws/event_protocol.rs path is the canonical gate for "
        "server-initiated event types (ALLOWED_EVENT_TYPES)."
    )
    return EVENT_PROTOCOL_RS.read_text(encoding="utf-8")


def _rust_allowed_event_types() -> set[str]:
    """Parse the ``ALLOWED_EVENT_TYPES`` slice from event_protocol.rs."""
    src = _read_event_protocol_rs()
    start_marker = "const ALLOWED_EVENT_TYPES: &[&str] = &["
    idx = src.find(start_marker)
    assert idx != -1, (
        "event_protocol.rs no longer declares the "
        "`const ALLOWED_EVENT_TYPES: &[&str] = &[` slice literal, "
        "update this parser to match."
    )
    slice_body = src[idx : src.find("];", idx)]
    return set(re.findall(r'"([a-z0-9_]+)"', slice_body))


def _ts_python_push_event_types() -> set[str]:
    """Parse the ``PythonPushEvent`` union for ``type: \"<name>\"`` literals."""
    src = PUSH_EVENTS_TS.read_text(encoding="utf-8")
    cut = src.find("export type PythonPushEvent =")
    assert cut != -1, (
        "push_events.ts: `export type PythonPushEvent =` declaration not found, the union was renamed or moved."
    )
    head = src[:cut]
    # Match `type: "<name>";`, the trailing `;` distinguishes
    return set(re.findall(r'type:\s*"([a-z0-9_]+)"\s*;', head))


def _ts_known_event_types() -> set[str]:
    """``lib/python-bridge/known-event-types.ts``."""
    src = USE_PYTHON_TS.read_text(encoding="utf-8")
    start = src.index("KNOWN_EVENT_TYPES: ReadonlySet<string> = new Set([")
    end = src.index("]);", start)
    block = src[start:end]
    # `[a-z0-9_]+` so digit-bearing names parse (see the Rust parser).
    return set(re.findall(r'"([a-z0-9_]+)"', block))


def _ts_python_request_types() -> set[str]:
    """Parse the ``PythonRequest`` union for ``type: \"<name>\"`` literals."""
    src = REQUESTS_TS.read_text(encoding="utf-8")
    return set(re.findall(r'type:\s*"([a-z_]+)"\s*;', src))


def _rust_allowed_commands() -> set[str]:
    """Parse the Rust ``allowed_commands()`` body for quoted command names."""
    src = ALLOWLIST_RS.read_text(encoding="utf-8")
    m_start = re.search(r"let\s+cmds:\s*&\[&str\]\s*=\s*&\[", src)
    assert m_start is not None, (
        "src-tauri/src/commands/sidecar_cmds/allowlist.rs no longer "
        "declares the `let cmds: &[&str] = &[` literal inside "
        "`allowed_commands()`. Update this parser to match."
    )
    body = src[m_start.end() :]
    m_end = re.search(r"\];", body)
    assert m_end is not None, (
        "src-tauri/src/commands/sidecar_cmds/allowlist.rs: could not "
        "find the closing `];` of the `let cmds: &[&str] = &[` literal."
    )
    literal = body[: m_end.start()]
    return set(re.findall(r'"([a-z_]+)"', literal))


def _python_command_registry() -> set[str]:
    """Return the set of command names in ``_COMMAND_REGISTRY``."""
    from voice_typer.server.ipc.registry import _COMMAND_REGISTRY

    return set(_COMMAND_REGISTRY.keys())


def _event_bus_docstring() -> str:
    """Return the ``event_bus.py`` module docstring as text."""
    src = EVENT_BUS_PY.read_text(encoding="utf-8")
    # The docstring is the first triple-quoted string in the file.
    m = re.search(r'"""(.*?)"""', src, re.DOTALL)
    assert m is not None, "event_bus.py: module docstring not found."
    return m.group(1)


class TestRustAllowlistContainsAllNewEvents:
    """13 new §7.4 events."""

    def test_all_13_pack_events_in_rust_allowlist(self) -> None:
        rust = _rust_allowed_event_types()
        pack = _pack_event_types()
        missing = pack - rust
        assert not missing, (
            "§7.4 / §9.4: Rust ALLOWED_EVENT_TYPES slice is missing "
            f"the following pack/worker events: {sorted(missing)}. "
            "Without these entries the Tauri WS reader will silently "
            "drop the published frames (logged at "
            "`[WS-READER] dropping unknown event type:`). Add them "
            "to the slice in "
            "src-tauri/src/sidecar/ws/event_protocol.rs."
        )

    def test_rust_allowlist_count_increased_by_13(self) -> None:
        """Sanity: the Rust allowlist should have AT LEAST 13 more"""
        rust = _rust_allowed_event_types()
        # Pre-§7.4 baseline was 40 (PLAN_ONNX_INTEGRATION.md §7.6
        assert len(rust) >= 53, (
            "§7.4 / §9.4: Rust ALLOWED_EVENT_TYPES slice has "
            f"{len(rust)} entries, expected at least 53 (40 pre-§7.4 "
            "baseline + 13 new pack/worker events). The slice was "
            "probably not updated to include the new events."
        )


class TestRequestEventInCommandAllowlists:
    """The 1 request-type event ``transcribe_offline`` MUST be in both"""

    def test_in_python_command_registry(self) -> None:
        registry = _python_command_registry()
        assert REQUEST_EVENT_NAME in registry, (
            f"§7.4: '{REQUEST_EVENT_NAME}' MUST be in the Python "
            "_COMMAND_REGISTRY (voice_typer/server/ipc/registry.py) "
            "so the dispatcher routes it. The Rust "
            "allowed_commands() literal must be updated in lockstep."
        )

    def test_in_rust_allowed_commands(self) -> None:
        rust = _rust_allowed_commands()
        assert REQUEST_EVENT_NAME in rust, (
            f"§7.4: '{REQUEST_EVENT_NAME}' MUST be in the Rust "
            "allowed_commands() literal "
            "(src-tauri/src/commands/sidecar_cmds/allowlist.rs), "
            "the Tauri host's dispatch gate would otherwise reject "
            "the renderer's invoke('dispatch', {cmd:'transcribe_offline'}) "
            "with `disallowed_command` (ADR-0015 defense-in-depth)."
        )

    def test_in_python_request_ts_union(self) -> None:
        """The TS ``PythonRequest`` discriminated union MUST include"""
        requests = _ts_python_request_types()
        assert REQUEST_EVENT_NAME in requests, (
            f"§7.4: '{REQUEST_EVENT_NAME}' MUST be in the TS "
            "PythonRequest discriminated union "
            "(voice_typer/client/src/renderer/src/types/ipc/requests.ts) "
            "so the renderer's typed call('transcribe_offline', ...) "
            "typechecks. Add a `type: 'transcribe_offline'` interface "
            "and append it to the union."
        )

    def test_only_request_event_in_command_allowlists(self) -> None:
        """The 12 PUSH events MUST NOT be in the command allowlists"""
        push_events = _push_event_types()
        registry = _python_command_registry()
        rust_cmds = _rust_allowed_commands()
        # None of the 12 push events should be in either command allowlist.
        leaked_registry = push_events & registry
        leaked_rust = push_events & rust_cmds
        assert not leaked_registry, (
            "§7.4: Python _COMMAND_REGISTRY contains push events "
            f"that should NOT be commands: {sorted(leaked_registry)}. "
            "Push events are published via event_bus.publish, not "
            "dispatched as commands."
        )
        assert not leaked_rust, (
            f"§7.4: Rust allowed_commands() contains push events that should NOT be commands: {sorted(leaked_rust)}."
        )


class TestPushEventsInTsAllowlists:
    """The 12 push events MUST be in:"""

    def test_in_python_push_event_union(self) -> None:
        ts_union = _ts_python_push_event_types()
        push_events = _push_event_types()
        missing = push_events - ts_union
        assert not missing, (
            "§7.4: TS PythonPushEvent union is missing the following "
            f"pack/worker push events: {sorted(missing)}. Without "
            "these entries the renderer's "
            "usePythonEvent('<name>', ...) call falls through to "
            "overload 2 (the any-string fallback) and the dev-time "
            "KNOWN_EVENT_TYPES warning fires on the legitimate new "
            "event. Add a `type: '<name>'` interface and append it "
            "to the union in "
            "voice_typer/client/src/renderer/src/types/ipc/push_events.ts."
        )

    def test_in_known_event_types_set(self) -> None:
        known = _ts_known_event_types()
        push_events = _push_event_types()
        missing = push_events - known
        assert not missing, (
            "§7.4: TS KNOWN_EVENT_TYPES runtime Set is missing the "
            f"following pack/worker push events: {sorted(missing)}. "
            "Without these entries the dev-time typo warning in "
            "usePythonEvent fires on the legitimate new events "
            "(false-positive), training developers to ignore the "
            "warning. Add each event to the Set in "
            "voice_typer/client/src/renderer/src/hooks/usePython.ts."
        )

    def test_transcribe_offline_result_in_both_ts_lists(self) -> None:
        """
        The ``transcribe_offline_result`` push event (worker → slim
        Set. Pinned explicitly because it's the result counterpart
        """
        ts_union = _ts_python_push_event_types()
        known = _ts_known_event_types()
        assert "transcribe_offline_result" in ts_union, (
            "§7.4: 'transcribe_offline_result' MUST be in the TS "
            "PythonPushEvent union, it's the push counterpart of "
            "the 'transcribe_offline' request."
        )
        assert "transcribe_offline_result" in known, (
            "§7.4: 'transcribe_offline_result' MUST be in the TS KNOWN_EVENT_TYPES runtime Set."
        )

    def test_transcribe_offline_not_in_push_event_union(self) -> None:
        """``PythonPushEvent`` union (it's a request, not a push event)."""
        ts_union = _ts_python_push_event_types()
        assert REQUEST_EVENT_NAME not in ts_union, (
            f"§7.4: '{REQUEST_EVENT_NAME}' is a REQUEST, not a push "
            "event, it should NOT be in the PythonPushEvent union. "
            "It lives in PythonRequest (requests.ts) instead."
        )


# Host-bridge-synthesized events that BYPASS the Python sidecar's WS
_HOST_BRIDGE_ONLY_EVENTS: frozenset[str] = frozenset({"reconnecting", "reconnected"})


class TestEventAllowlistCrossLayerParity:
    """The Rust ``ALLOWED_EVENT_TYPES`` slice is the WS-reader gate —"""

    def test_rust_allowlist_is_superset_of_ts_push_event_union(self) -> None:
        """Every TS ``PythonPushEvent`` type MUST be in the Rust"""
        rust = _rust_allowed_event_types()
        ts_union = _ts_python_push_event_types()
        # Exclude host-bridge-synthesized events from the cross-check
        python_side_events = ts_union - _HOST_BRIDGE_ONLY_EVENTS
        missing = python_side_events - rust
        assert not missing, (
            "§9.4 / §7.6 cross-layer parity drift: the TS "
            f"PythonPushEvent union contains {sorted(missing)} but "
            "the Rust ALLOWED_EVENT_TYPES slice does NOT. The Tauri "
            "WS reader will silently drop these frames (logged at "
            "`[WS-READER] dropping unknown event type:`), the "
            "renderer's usePythonEvent subscribers will never fire. "
            "Add the missing entries to the slice in "
            "src-tauri/src/sidecar/ws/event_protocol.rs. (If the "
            "event is host-bridge-synthesized, i.e. NOT published "
            "by the Python sidecar, add it to the "
            "`_HOST_BRIDGE_ONLY_EVENTS` frozenset in this test file "
            "instead.)"
        )

    def test_rust_allowlist_is_superset_of_ts_known_event_types(self) -> None:
        """Every TS ``KNOWN_EVENT_TYPES`` entry MUST be in the Rust"""
        rust = _rust_allowed_event_types()
        known = _ts_known_event_types()
        # Exclude host-bridge-synthesized events from the cross-check.
        python_side_events = known - _HOST_BRIDGE_ONLY_EVENTS
        missing = python_side_events - rust
        assert not missing, (
            "§9.4 / §7.6 cross-layer parity drift: the TS "
            f"KNOWN_EVENT_TYPES runtime Set contains {sorted(missing)} "
            "but the Rust ALLOWED_EVENT_TYPES slice does NOT. The "
            "Tauri WS reader will silently drop these frames. (If "
            "the event is host-bridge-synthesized, i.e. NOT "
            "published by the Python sidecar, add it to the "
            "`_HOST_BRIDGE_ONLY_EVENTS` frozenset in this test file "
            "instead.)"
        )

    def test_ts_push_event_union_equals_ts_known_event_types(self) -> None:
        """
        The TS ``PythonPushEvent`` union and the TS ``KNOWN_EVENT_TYPES``
        ``usePython-known-event-types-parity.test.ts`` pins this from
        """
        ts_union = _ts_python_push_event_types()
        known = _ts_known_event_types()
        only_in_union = ts_union - known
        only_in_set = known - ts_union
        assert not only_in_union, (
            "§7.4 parity drift: TS PythonPushEvent union has "
            f"{sorted(only_in_union)} but KNOWN_EVENT_TYPES runtime "
            "Set does NOT. Add them to the Set in "
            "voice_typer/client/src/renderer/src/hooks/usePython.ts."
        )
        assert not only_in_set, (
            "§7.4 parity drift: TS KNOWN_EVENT_TYPES runtime Set has "
            f"{sorted(only_in_set)} but PythonPushEvent union does "
            "NOT. Add them to the union in "
            "voice_typer/client/src/renderer/src/types/ipc/push_events.ts."
        )

    def test_host_bridge_only_events_documented_in_ts_union(self) -> None:
        """Sanity: every event in ``_HOST_BRIDGE_ONLY_EVENTS`` MUST"""
        ts_union = _ts_python_push_event_types()
        stale = _HOST_BRIDGE_ONLY_EVENTS - ts_union
        assert not stale, (
            "_HOST_BRIDGE_ONLY_EVENTS contains events that are NOT "
            f"in the TS PythonPushEvent union: {sorted(stale)}. "
            "Remove them from the exception set in this test file "
            "— they no longer need an exception."
        )


class TestEventBusCatalogueDocstring:
    """The ``event_bus.py`` canonical catalogue docstring MUST mention"""

    def test_all_13_events_in_catalogue(self) -> None:
        docstring = _event_bus_docstring()
        pack = _pack_event_types()
        missing = {name for name in pack if name not in docstring}
        assert not missing, (
            "§7.4: event_bus.py canonical catalogue docstring is "
            f"missing the following pack/worker events: "
            f"{sorted(missing)}. The docstring is the code-side "
            "anchor for ADR-0020 §2's Sidecar→UI Event Table, a "
            "contributor reading it should NOT have to flip to "
            "service/pack.py to discover the new events. Add each "
            "event with its payload shape to the docstring in "
            "voice_typer/server/event_bus.py."
        )

    def test_catalogue_mentions_pack_section_header(self) -> None:
        """The docstring MUST have a section header that mentions"""
        docstring = _event_bus_docstring()
        assert "§7.4" in docstring or "7.4" in docstring, (
            "§7.4: event_bus.py canonical catalogue docstring should "
            "mention §7.4 (the master plan section that introduces "
            "the 13 new pack/worker events) so a contributor can "
            "grep for the reference."
        )


class TestPackEventTypesSourceOfTruth:
    """canonical frozenset that the parity tests above import. This"""

    def test_pack_event_types_exists_and_has_13_entries(self) -> None:
        pack = _pack_event_types()
        assert len(pack) == 13, (
            "§7.4: OFFLINE_PACK_EVENT_TYPES in voice_typer/server/service/pack.py "
            f"has {len(pack)} entries, expected exactly 13 (12 push + "
            "1 request). The master plan §7.4 catalogue is the source "
            "of truth. If you added/removed an event, update the master "
            "plan AND the parity assertions in this test file in the "
            "same PR."
        )

    def test_pack_event_types_contains_transcribe_offline(self) -> None:
        pack = _pack_event_types()
        assert REQUEST_EVENT_NAME in pack, (
            f"§7.4: OFFLINE_PACK_EVENT_TYPES must contain the request event "
            f"'{REQUEST_EVENT_NAME}' (the 13th event in §7.4, it's "
            "the only request-type event in the set; the other 12 are "
            "push events)."
        )

    def test_pack_event_types_is_frozenset(self) -> None:
        """``OFFLINE_PACK_EVENT_TYPES`` MUST be a ``frozenset`` so it cannot"""
        from voice_typer.server.service.offline_pack import OFFLINE_PACK_EVENT_TYPES

        assert isinstance(OFFLINE_PACK_EVENT_TYPES, frozenset), (
            "§7.4: OFFLINE_PACK_EVENT_TYPES in voice_typer/server/service/pack.py "
            "must be a frozenset (not a set or list) so it cannot be "
            "accidentally mutated at runtime."
        )


def _python_published_event_types() -> set[str]:
    """Return every event name literally published via ``event_bus.publish``."""
    names: set[str] = set()
    server_dir = REPO_ROOT / "voice_typer" / "server"
    assert server_dir.is_dir(), f"server tree not found at {server_dir}"
    for py in server_dir.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover - defensive
            raise AssertionError(f"{py} has a syntax error ({exc}); the published-event scan cannot run.") from exc
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "publish"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Dict):
                continue
            for key, value in zip(node.args[0].keys, node.args[0].values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "type"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    names.add(value.value)
    return names


def _python_event_types_registry() -> frozenset[str]:
    """Return the canonical Python ``event_bus.EVENT_TYPES`` registry."""
    from voice_typer.server.event_bus import EVENT_TYPES

    return EVENT_TYPES


class TestPythonPublishedEventParity:
    """The EMITTING direction of the four-way population diff."""

    def test_published_scanner_finds_a_meaningful_inventory(self) -> None:
        """Sanity: the AST scan must not silently return an empty/tiny set"""
        published = _python_published_event_types()
        assert len(published) >= 40, (
            "the published-event AST scan found only "
            f"{len(published)} names, the scanner itself is broken "
            "(expected the full ~50-name publish inventory under "
            "voice_typer/server)."
        )
        for anchor in ("ready", "state_changed", "status_change", "error"):
            assert anchor in published, f"scanner no longer finds the core '{anchor}' publisher."

    def test_published_events_subset_of_rust_allowlist(self) -> None:
        rust = _rust_allowed_event_types()
        published = _python_published_event_types()
        missing = published - rust
        assert not missing, (
            "Python publishes the following event names that the Rust "
            f"ALLOWED_EVENT_TYPES slice does NOT contain: {sorted(missing)}. "
            "The Tauri WS reader silently drops these frames (logged at "
            "`[WS-READER] dropping unknown event type:`), every "
            "renderer subscriber for them is dead end-to-end. Either "
            "wire the event (add it to the slice in "
            "src-tauri/src/sidecar/ws/event_protocol.rs + the TS union "
            "+ KNOWN_EVENT_TYPES + a renderer consumer) or delete the "
            "emit per the dead-code policy."
        )

    def test_published_events_subset_of_python_event_types_registry(self) -> None:
        registry = _python_event_types_registry()
        published = _python_published_event_types()
        missing = published - registry
        assert not missing, (
            "event_bus.EVENT_TYPES (the Python-side registry / dev-time "
            "assertion gate) is missing the following PUBLISHED names: "
            f"{sorted(missing)}. Add them to the frozenset in "
            "voice_typer/server/event_bus.py so the "
            "VOICE_TYPER_DEBUG_EVENTS gate doesn't false-positive on "
            "real call sites."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
