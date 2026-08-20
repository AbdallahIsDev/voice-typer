"""Master plan §7.4 / §9.4 — IPC event-types parity test.

This file is the regression guard for the **fourth allowlist** that was
previously untested (master plan §9.4 / `PLAN_ONNX_INTEGRATION.md` §7.6):

    4. ``ALLOWED_EVENT_TYPES`` (Rust) —
       ``src-tauri/src/sidecar/ws/event_protocol.rs:49``
       (the server-initiated event-type allowlist that the Tauri WS
       reader consults on every inbound frame — a typo here silently
       drops the frame with a ``[WS-READER] dropping unknown event
       type:`` warning).

The other three IPC allowlists (``_COMMAND_REGISTRY`` Python +
``ALLOWED_COMMANDS`` TS + ``allowed_commands()`` Rust) are already
pinned by ``tests/test_command_registry_parity.py`` +
``tests/test_electron_ipc_and_build.py`` +
``tests/test_security_doc_command_count.py``. This file adds the
missing parity for the event-type allowlist.

§7.4 introduces 13 new IPC events for the slim-core / runtime-pack
split. The canonical Python-side source of truth is
``voice_typer/server/service/pack.py::OFFLINE_PACK_EVENT_TYPES`` (a
``frozenset[str]`` with all 13 names). The 13 events split into two
kinds:

* 1 REQUEST (renderer → slim core → worker): ``transcribe_offline``.
  This MUST be in the three COMMAND allowlists (Python registry +
  TS ALLOWED_COMMANDS + Rust allowed_commands()) AND in the TS
  ``PythonRequest`` discriminated union. It is ALSO listed in
  ``ALLOWED_EVENT_TYPES`` (the event allowlist) so the WS reader
  doesn't drop any future server-initiated variant of the name.

* 12 PUSH events (worker → slim core → renderer, published via
  ``event_bus.publish``):
  ``offline_pack_download_started`` / ``offline_pack_download_progress`` /
  ``offline_pack_download_completed`` / ``offline_pack_download_failed`` /
  ``offline_pack_verified`` / ``offline_pack_missing`` / ``offline_pack_corrupt`` /
  ``offline_pack_ready`` / ``worker_started`` / ``worker_crashed`` /
  ``worker_unloaded`` / ``transcribe_offline_result``.
  These MUST be in:
    - the Rust ``ALLOWED_EVENT_TYPES`` slice (so the WS reader lets
      the frames through to the renderer);
    - the TS ``PythonPushEvent`` discriminated union (so
      ``usePythonEvent("offline_pack_download_started", ...)`` typechecks);
    - the TS ``KNOWN_EVENT_TYPES`` runtime Set (so the dev-time
      typo warning doesn't false-positive on the legitimate new
      events — pinned by the TS-side
      ``usePython-known-event-types-parity.test.ts``);
    - the ``event_bus.py`` canonical catalogue docstring (the
      source-of-truth anchor referenced by ADR-0020 §2).

This test asserts all four allowlists agree on the 13 new events
(per their kind) AND that the cross-layer event-side parity holds
(Rust allowlist ↔ TS union ↔ TS runtime set).

The tests are HEADLESS: they read the source files as TEXT (Python
cannot import Rust or TS modules). They are safe to run in parallel
with other fix sub-agents.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ─── path helpers ─────────────────────────────────────────────────────────


def _repo_root() -> Path:
    """Return the voice-typer repo root (parent of the ``tests`` dir)."""
    return Path(__file__).resolve().parent.parent


REPO_ROOT = _repo_root()

EVENT_PROTOCOL_RS = REPO_ROOT / "src-tauri" / "src" / "sidecar" / "ws" / "event_protocol.rs"
PUSH_EVENTS_TS = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "types" / "ipc" / "push_events.ts"
REQUESTS_TS = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "types" / "ipc" / "requests.ts"
USE_PYTHON_TS = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "hooks" / "usePython.ts"
ALLOWED_COMMANDS_TS = REPO_ROOT / "voice_typer" / "client" / "src" / "main" / "allowed-commands.ts"
ALLOWLIST_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds" / "allowlist.rs"
EVENT_BUS_PY = REPO_ROOT / "voice_typer" / "server" / "event_bus.py"


# ─── the 13 new events from §7.4 ──────────────────────────────────────────


# Single source of truth: the canonical Python ``OFFLINE_PACK_EVENT_TYPES`` in
# ``voice_typer/server/service/pack.py``. We import it rather than
# hardcoding the list here so a future rename in ``pack.py`` flows
# through to this test (the alternative — hardcoding the 13 strings
# here — would silently drift if ``pack.py`` is updated and this test
# isn't).
def _pack_event_types() -> frozenset[str]:
    """Return the canonical 13-event frozenset from ``service.offline_pack``.

    ``OFFLINE_PACK_EVENT_TYPES`` is the schema anchor referenced by the
    ``event_bus.py`` catalogue docstring + the Rust
    ``ALLOWED_EVENT_TYPES`` slice + the TS ``PythonPushEvent`` union.
    """
    from voice_typer.server.service.offline_pack import OFFLINE_PACK_EVENT_TYPES

    return OFFLINE_PACK_EVENT_TYPES


# The single request-type event (the other 12 are push events).
# Used to verify the COMMAND allowlists (registry + TS + Rust) contain
# this one and ONLY this one of the 13 new events.
REQUEST_EVENT_NAME = "transcribe_offline"


def _push_event_types() -> set[str]:
    """Return the 12 push event names (OFFLINE_PACK_EVENT_TYPES minus the request)."""
    return set(_pack_event_types()) - {REQUEST_EVENT_NAME}


# ─── source-text parsers ──────────────────────────────────────────────────


def _read_event_protocol_rs() -> str:
    """Read the Rust event-protocol source as text.

    Python cannot import Rust; we treat the file as a string and
    regex-match the ``ALLOWED_EVENT_TYPES: &[&str] = &[ ... ];`` slice
    literal. The slice was extracted from the former ``ws.rs``
    monolith (FZ-24 / ZR-86 module split) and now lives in
    ``event_protocol.rs``.
    """
    assert EVENT_PROTOCOL_RS.is_file(), (
        f"expected Tauri WS reader at {EVENT_PROTOCOL_RS} — file not found. "
        "The ws/event_protocol.rs path is the canonical gate for "
        "server-initiated event types (ALLOWED_EVENT_TYPES)."
    )
    return EVENT_PROTOCOL_RS.read_text(encoding="utf-8")


def _rust_allowed_event_types() -> set[str]:
    """Parse the ``ALLOWED_EVENT_TYPES`` slice from event_protocol.rs.

    Mirrors the parsing approach in
    ``tests/test_tray_fallback_notification_allowlist.py`` — same
    slice-literal marker, same regex. The slice is declared as::

        pub(super) const ALLOWED_EVENT_TYPES: &[&str] = &[ ... ];

    We extract everything between ``&[`` and ``];`` and find all
    quoted string literals.
    """
    src = _read_event_protocol_rs()
    start_marker = "const ALLOWED_EVENT_TYPES: &[&str] = &["
    idx = src.find(start_marker)
    assert idx != -1, (
        "event_protocol.rs no longer declares the "
        "`const ALLOWED_EVENT_TYPES: &[&str] = &[` slice literal — "
        "update this parser to match."
    )
    slice_body = src[idx : src.find("];", idx)]
    # Match quoted strings — the slice entries are `"name",` with
    # optional trailing comments after `//`. The regex captures only
    # the quoted string content.
    return set(re.findall(r'"([a-z_]+)"', slice_body))


def _ts_python_push_event_types() -> set[str]:
    """Parse the ``PythonPushEvent`` union for ``type: "<name>"`` literals.

    The union is declared in ``push_events.ts`` as::

        export type PythonPushEvent =
            | StatusChangeEvent
            | ErrorEvent
            ...

    Each member interface declares ``type: "<name>";``. We extract
    every ``type: "..."`` literal declared BEFORE the ``export type
    PythonPushEvent =`` line — this excludes the ``AuthFrame`` /
    ``ProtocolVersionMismatchError`` interfaces that live AFTER the
    union (they are NOT push events — they are auth/version-mismatch
    frame shapes that happen to also use a ``type`` literal).
    """
    src = PUSH_EVENTS_TS.read_text(encoding="utf-8")
    # Cut the source at the `export type PythonPushEvent =` line so
    # we don't pick up `type:` literals from AuthFrame etc. below.
    cut = src.find("export type PythonPushEvent =")
    assert cut != -1, (
        "push_events.ts: `export type PythonPushEvent =` declaration not found — the union was renamed or moved."
    )
    head = src[:cut]
    # Match `type: "<name>";` — the trailing `;` distinguishes
    # interface members from the union's `| MemberName` lines.
    return set(re.findall(r'type:\s*"([a-z_]+)"\s*;', head))


def _ts_known_event_types() -> set[str]:
    """Parse the ``KNOWN_EVENT_TYPES`` Set literal in usePython.ts.

    The Set is declared as::

        export const KNOWN_EVENT_TYPES: ReadonlySet<string> = new Set([
            "status_change",
            ...
        ]);

    We extract everything between ``new Set([`` and ``]);`` and find
    all quoted string literals.
    """
    src = USE_PYTHON_TS.read_text(encoding="utf-8")
    start = src.index("KNOWN_EVENT_TYPES: ReadonlySet<string> = new Set([")
    end = src.index("]);", start)
    block = src[start:end]
    return set(re.findall(r'"([a-z_]+)"', block))


def _ts_python_request_types() -> set[str]:
    """Parse the ``PythonRequest`` union for ``type: "<name>"`` literals."""
    src = REQUESTS_TS.read_text(encoding="utf-8")
    return set(re.findall(r'type:\s*"([a-z_]+)"\s*;', src))


def _ts_allowed_commands() -> set[str]:
    """Parse the TS ``ALLOWED_COMMANDS = new Set([...])`` literal."""
    src = ALLOWED_COMMANDS_TS.read_text(encoding="utf-8")
    start = src.index("ALLOWED_COMMANDS = new Set([")
    end = src.index("]);", start)
    block = src[start:end]
    return set(re.findall(r'"([a-z_]+)"', block))


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


# ─── 1. the 13 new events are in the Rust ALLOWED_EVENT_TYPES slice ──────


class TestRustAllowlistContainsAllNewEvents:
    """The Rust ``ALLOWED_EVENT_TYPES`` slice MUST list every one of the
    13 new §7.4 events.

    Master plan §9.4: the ``ALLOWED_EVENT_TYPES`` slice is the
    "fourth allowlist" that previously had NO parity test. Without
    this assertion, adding a Python event without adding it here
    silently drops the frame at the WS reader (logged at
    ``[WS-READER] dropping unknown event type:``).
    """

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
        """Sanity: the Rust allowlist should have AT LEAST 13 more
        entries than the pre-§7.4 baseline (40 entries per §9.4 /
        PLAN_ONNX_INTEGRATION.md §7.6). After §7.4 it should be ≥ 53.
        """
        rust = _rust_allowed_event_types()
        # Pre-§7.4 baseline was 40 (PLAN_ONNX_INTEGRATION.md §7.6
        # cites "40 entries"). The 13 new events bump it to 53.
        # Use >= so future unrelated additions don't break this test.
        assert len(rust) >= 53, (
            "§7.4 / §9.4: Rust ALLOWED_EVENT_TYPES slice has "
            f"{len(rust)} entries — expected at least 53 (40 pre-§7.4 "
            "baseline + 13 new pack/worker events). The slice was "
            "probably not updated to include the new events."
        )


# ─── 2. the 1 request event is in all 3 COMMAND allowlists ────────────────


class TestRequestEventInCommandAllowlists:
    """The 1 request-type event ``transcribe_offline`` MUST be in all
    three COMMAND allowlists (Python registry + TS ALLOWED_COMMANDS +
    Rust allowed_commands()) so the renderer's
    ``call('transcribe_offline', ...)`` dispatches cleanly through
    every layer.
    """

    def test_in_python_command_registry(self) -> None:
        registry = _python_command_registry()
        assert REQUEST_EVENT_NAME in registry, (
            f"§7.4: '{REQUEST_EVENT_NAME}' MUST be in the Python "
            "_COMMAND_REGISTRY (voice_typer/server/ipc/registry.py) "
            "so the dispatcher routes it. The TS ALLOWED_COMMANDS + "
            "Rust allowed_commands() literals must be updated in "
            "lockstep."
        )

    def test_in_ts_allowed_commands(self) -> None:
        ts = _ts_allowed_commands()
        assert REQUEST_EVENT_NAME in ts, (
            f"§7.4: '{REQUEST_EVENT_NAME}' MUST be in the TS "
            "ALLOWED_COMMANDS Set "
            "(voice_typer/client/src/main/allowed-commands.ts) — "
            "the renderer's call('transcribe_offline', ...) would "
            "otherwise be silently rejected by the main process's "
            "sendToPython gate (SEC-019)."
        )

    def test_in_rust_allowed_commands(self) -> None:
        rust = _rust_allowed_commands()
        assert REQUEST_EVENT_NAME in rust, (
            f"§7.4: '{REQUEST_EVENT_NAME}' MUST be in the Rust "
            "allowed_commands() literal "
            "(src-tauri/src/commands/sidecar_cmds/allowlist.rs) — "
            "the Tauri host's dispatch gate would otherwise reject "
            "the renderer's invoke('dispatch', {cmd:'transcribe_offline'}) "
            "with `disallowed_command` (ADR-0015 defense-in-depth)."
        )

    def test_in_python_request_ts_union(self) -> None:
        """The TS ``PythonRequest`` discriminated union MUST include
        ``transcribe_offline`` so the renderer's
        ``call('transcribe_offline', ...)`` typechecks (the typed
        ``call<T>`` overload narrows on ``PythonRequest['type']``)."""
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
        """The 12 PUSH events MUST NOT be in the command allowlists
        (they are server→renderer push events, not renderer→python
        commands). A push event leaking into the command allowlists
        would create a phantom command the dispatcher would reject
        with ``unknown_command``.
        """
        push_events = _push_event_types()
        registry = _python_command_registry()
        ts_cmds = _ts_allowed_commands()
        rust_cmds = _rust_allowed_commands()
        # None of the 12 push events should be in any of the 3
        # command allowlists.
        leaked_registry = push_events & registry
        leaked_ts = push_events & ts_cmds
        leaked_rust = push_events & rust_cmds
        assert not leaked_registry, (
            "§7.4: Python _COMMAND_REGISTRY contains push events "
            f"that should NOT be commands: {sorted(leaked_registry)}. "
            "Push events are published via event_bus.publish, not "
            "dispatched as commands."
        )
        assert not leaked_ts, (
            f"§7.4: TS ALLOWED_COMMANDS contains push events that should NOT be commands: {sorted(leaked_ts)}."
        )
        assert not leaked_rust, (
            f"§7.4: Rust allowed_commands() contains push events that should NOT be commands: {sorted(leaked_rust)}."
        )


# ─── 3. the 12 push events are in the TS push-event allowlists ────────────


class TestPushEventsInTsAllowlists:
    """The 12 push events MUST be in:
    - the TS ``PythonPushEvent`` discriminated union (so
      ``usePythonEvent("offline_pack_download_started", ...)`` typechecks);
    - the TS ``KNOWN_EVENT_TYPES`` runtime Set (so the dev-time typo
      warning doesn't false-positive on the legitimate new events).
    """

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
        """The ``transcribe_offline_result`` push event (worker → slim
        core → renderer) MUST be in both the TS union AND the runtime
        Set. Pinned explicitly because it's the result counterpart
        of the ``transcribe_offline`` REQUEST (and a contributor
        adding the request might forget to add the result push event
        to the renderer side)."""
        ts_union = _ts_python_push_event_types()
        known = _ts_known_event_types()
        assert "transcribe_offline_result" in ts_union, (
            "§7.4: 'transcribe_offline_result' MUST be in the TS "
            "PythonPushEvent union — it's the push counterpart of "
            "the 'transcribe_offline' request."
        )
        assert "transcribe_offline_result" in known, (
            "§7.4: 'transcribe_offline_result' MUST be in the TS KNOWN_EVENT_TYPES runtime Set."
        )

    def test_transcribe_offline_not_in_push_event_union(self) -> None:
        """The ``transcribe_offline`` REQUEST event MUST NOT be in the
        ``PythonPushEvent`` union (it's a request, not a push event).
        It lives in ``PythonRequest`` instead. A leak here would
        create a phantom push event the renderer could subscribe to
        but never receive.
        """
        ts_union = _ts_python_push_event_types()
        assert REQUEST_EVENT_NAME not in ts_union, (
            f"§7.4: '{REQUEST_EVENT_NAME}' is a REQUEST, not a push "
            "event — it should NOT be in the PythonPushEvent union. "
            "It lives in PythonRequest (requests.ts) instead."
        )


# ─── 4. cross-layer parity: Rust allowlist ↔ TS union ↔ TS runtime set ──


# Host-bridge-synthesized events that BYPASS the Python sidecar's WS
# reader path. These appear in the TS ``PythonPushEvent`` union (the
# renderer subscribes to them via ``usePythonEvent``) AND in the TS
# ``KNOWN_EVENT_TYPES`` runtime Set, but they are NOT published by the
# Python sidecar — they are synthesized by the host bridge (Tauri
# Rust ``src-tauri/src/sidecar/supervisor.rs`` or Electron main) when
# the transport layer detects a disconnect and enters the reconnect
# loop. The Rust ``ALLOWED_EVENT_TYPES`` slice correctly EXCLUDES
# them (the slice is the gate for Python-sidecar→renderer frames
# only — see the docstring on the slice in
# ``src-tauri/src/sidecar/ws/event_protocol.rs``). Without this
# documented exception set, the cross-layer parity test below
# false-positives on these host-bridge events.
#
# If a future host-bridge event is added to ``PythonPushEvent``, add
# it here too — OR (preferred) emit it from the Python sidecar so it
# flows through the standard event_bus.publish path and the Rust
# allowlist gate applies.
_HOST_BRIDGE_ONLY_EVENTS: frozenset[str] = frozenset({"reconnecting", "reconnected"})


class TestEventAllowlistCrossLayerParity:
    """The Rust ``ALLOWED_EVENT_TYPES`` slice is the WS-reader gate —
    every push event the TS side knows about MUST be in the Rust
    allowlist, or the WS reader silently drops the frame.

    This is the regression guard that the §9.4 / §7.6 "fourth
    allowlist has no parity test" gap was about. Before this test,
    a Python event published via ``event_bus.publish`` would be
    silently dropped if the Rust allowlist wasn't updated — no test
    caught the drift.

    Exception: host-bridge-synthesized events (``reconnecting`` /
    ``reconnected``) are NOT published by the Python sidecar and
    therefore correctly absent from the Rust allowlist. They are
    documented in ``_HOST_BRIDGE_ONLY_EVENTS`` above.
    """

    def test_rust_allowlist_is_superset_of_ts_push_event_union(self) -> None:
        """Every TS ``PythonPushEvent`` type MUST be in the Rust
        ``ALLOWED_EVENT_TYPES`` slice (modulo the host-bridge-only
        exceptions). A push event the renderer subscribes to but the
        Rust host drops is a silent UX bug."""
        rust = _rust_allowed_event_types()
        ts_union = _ts_python_push_event_types()
        # Exclude host-bridge-synthesized events from the cross-check
        # — they bypass the WS reader by design.
        python_side_events = ts_union - _HOST_BRIDGE_ONLY_EVENTS
        missing = python_side_events - rust
        assert not missing, (
            "§9.4 / §7.6 cross-layer parity drift: the TS "
            f"PythonPushEvent union contains {sorted(missing)} but "
            "the Rust ALLOWED_EVENT_TYPES slice does NOT. The Tauri "
            "WS reader will silently drop these frames (logged at "
            "`[WS-READER] dropping unknown event type:`) — the "
            "renderer's usePythonEvent subscribers will never fire. "
            "Add the missing entries to the slice in "
            "src-tauri/src/sidecar/ws/event_protocol.rs. (If the "
            "event is host-bridge-synthesized — i.e. NOT published "
            "by the Python sidecar — add it to the "
            "`_HOST_BRIDGE_ONLY_EVENTS` frozenset in this test file "
            "instead.)"
        )

    def test_rust_allowlist_is_superset_of_ts_known_event_types(self) -> None:
        """Every TS ``KNOWN_EVENT_TYPES`` entry MUST be in the Rust
        ``ALLOWED_EVENT_TYPES`` slice (modulo the host-bridge-only
        exceptions). The runtime Set is a hand-maintained mirror of
        the TS union; both must agree with the Rust slice."""
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
            "the event is host-bridge-synthesized — i.e. NOT "
            "published by the Python sidecar — add it to the "
            "`_HOST_BRIDGE_ONLY_EVENTS` frozenset in this test file "
            "instead.)"
        )

    def test_ts_push_event_union_equals_ts_known_event_types(self) -> None:
        """The TS ``PythonPushEvent`` union and the TS ``KNOWN_EVENT_TYPES``
        runtime Set MUST agree exactly. The Set is a hand-maintained
        mirror of the union (TS cannot enumerate union members at
        runtime); the TS-side parity test
        ``usePython-known-event-types-parity.test.ts`` pins this from
        the TS side, and this Python test re-pins it from the
        cross-layer side so a contributor who only edits the .ts
        files but doesn't run vitest still gets caught by pytest.
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
        """Sanity: every event in ``_HOST_BRIDGE_ONLY_EVENTS`` MUST
        actually appear in the TS ``PythonPushEvent`` union. If a
        future contributor removes ``reconnecting`` / ``reconnected``
        from the union but leaves it in the exception set, the
        exception set silently grows stale."""
        ts_union = _ts_python_push_event_types()
        stale = _HOST_BRIDGE_ONLY_EVENTS - ts_union
        assert not stale, (
            "_HOST_BRIDGE_ONLY_EVENTS contains events that are NOT "
            f"in the TS PythonPushEvent union: {sorted(stale)}. "
            "Remove them from the exception set in this test file "
            "— they no longer need an exception."
        )


# ─── 5. event_bus.py canonical catalogue docstring lists all 13 events ──


class TestEventBusCatalogueDocstring:
    """The ``event_bus.py`` canonical catalogue docstring MUST mention
    every one of the 13 new §7.4 events.

    The docstring is the code-side anchor for ADR-0020 §2's
    Sidecar→UI Event Table (per the docstring's own preamble).
    A contributor reading the docstring should NOT have to flip to
    ``service/pack.py`` to discover the new events.
    """

    def test_all_13_events_in_catalogue(self) -> None:
        docstring = _event_bus_docstring()
        pack = _pack_event_types()
        # Each event name should appear as a `` ``name`` `` token in
        # the docstring (RST inline-literal form). We just check the
        # raw name appears anywhere in the docstring text — that's
        # sufficient to catch a missed entry.
        missing = {name for name in pack if name not in docstring}
        assert not missing, (
            "§7.4: event_bus.py canonical catalogue docstring is "
            f"missing the following pack/worker events: "
            f"{sorted(missing)}. The docstring is the code-side "
            "anchor for ADR-0020 §2's Sidecar→UI Event Table — a "
            "contributor reading it should NOT have to flip to "
            "service/pack.py to discover the new events. Add each "
            "event with its payload shape to the docstring in "
            "voice_typer/server/event_bus.py."
        )

    def test_catalogue_mentions_pack_section_header(self) -> None:
        """The docstring MUST have a section header that mentions
        ``§7.4`` so a contributor grepping for the master plan
        reference can find the new events quickly."""
        docstring = _event_bus_docstring()
        assert "§7.4" in docstring or "7.4" in docstring, (
            "§7.4: event_bus.py canonical catalogue docstring should "
            "mention §7.4 (the master plan section that introduces "
            "the 13 new pack/worker events) so a contributor can "
            "grep for the reference."
        )


# ─── 6. the canonical OFFLINE_PACK_EVENT_TYPES source of truth exists ────────────


class TestPackEventTypesSourceOfTruth:
    """``voice_typer/server/service/pack.py::OFFLINE_PACK_EVENT_TYPES`` is the
    canonical frozenset that the parity tests above import. This
    test pins its existence + size so a future refactor that moves
    or renames ``OFFLINE_PACK_EVENT_TYPES`` fails HERE first (with a clear
    message) instead of in the import-error traceback of every
    other test class above.
    """

    def test_pack_event_types_exists_and_has_13_entries(self) -> None:
        pack = _pack_event_types()
        assert len(pack) == 13, (
            "§7.4: OFFLINE_PACK_EVENT_TYPES in voice_typer/server/service/pack.py "
            f"has {len(pack)} entries — expected exactly 13 (12 push + "
            "1 request). The master plan §7.4 catalogue is the source "
            "of truth. If you added/removed an event, update the master "
            "plan AND the parity assertions in this test file in the "
            "same PR."
        )

    def test_pack_event_types_contains_transcribe_offline(self) -> None:
        pack = _pack_event_types()
        assert REQUEST_EVENT_NAME in pack, (
            f"§7.4: OFFLINE_PACK_EVENT_TYPES must contain the request event "
            f"'{REQUEST_EVENT_NAME}' (the 13th event in §7.4 — it's "
            "the only request-type event in the set; the other 12 are "
            "push events)."
        )

    def test_pack_event_types_is_frozenset(self) -> None:
        """``OFFLINE_PACK_EVENT_TYPES`` MUST be a ``frozenset`` so it cannot
        be accidentally mutated at runtime (a mutable set could be
        silently extended by a stray ``.add()`` call, which would
        then pass the parity tests above without the corresponding
        allowlist updates)."""
        from voice_typer.server.service.offline_pack import OFFLINE_PACK_EVENT_TYPES

        assert isinstance(OFFLINE_PACK_EVENT_TYPES, frozenset), (
            "§7.4: OFFLINE_PACK_EVENT_TYPES in voice_typer/server/service/pack.py "
            "must be a frozenset (not a set or list) so it cannot be "
            "accidentally mutated at runtime."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
