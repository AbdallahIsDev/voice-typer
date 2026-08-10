"""/ Hard rule 8: doc-parity test for docs/ipc-reference.md.

Asserts that every command in ``_COMMAND_REGISTRY`` has a row in
``docs/ipc-reference.md`` and vice versa (modulo the explicit
"Removed / never-existed commands" list at the bottom of the doc —
those names are documented for searchability but are intentionally
absent from the registry).

Parses both sources the same way ``tests/test_security_doc_command_count.py``
parses them so the two tests stay consistent if the registry file shape
changes.

Asserts the documented "Commands (N total ...)" header count matches
the actual registry size (currently 69 = 67 renderer-reachable + 2
host-only).

Asserts the documented "Push events (N typed)" header count matches
the count of ``type: "<name>"`` literals in the renderer's
``types/ipc/push_events.ts`` (currently 36).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
IPC_REFERENCE_MD = REPO_ROOT / "docs" / "ipc-reference.md"
IPC_REGISTRY_PY = REPO_ROOT / "voice_typer" / "server" / "ipc" / "registry.py"
IPC_SERVER_PY = REPO_ROOT / "voice_typer" / "server" / "ipc_server.py"
PUSH_EVENTS_TS = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "types" / "ipc" / "push_events.ts"


def _command_registry_entries() -> set[str]:
    """Mirror of ``test_security_doc_command_count._command_registry_entries``.

    Kept inline (not imported) so this test has no cross-file dependency
    on the security-doc test module's private helpers.
    """
    sources = [IPC_REGISTRY_PY, IPC_SERVER_PY]
    src = None
    for candidate in sources:
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8")
            if "_COMMAND_REGISTRY" in text:
                src = text
                break
    assert src is not None, f"Could not find _COMMAND_REGISTRY in {IPC_REGISTRY_PY} or {IPC_SERVER_PY}."
    m_start = re.search(r"_COMMAND_REGISTRY\s*:\s*dict\[str,\s*str\]\s*=\s*\{", src)
    assert m_start is not None, "_COMMAND_REGISTRY literal not found."
    depth = 1
    i = m_start.end()
    while i < len(src) and depth > 0:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    body = src[m_start.end() : i - 1]
    return set(re.findall(r'"([a-z_]+)"\s*:\s*"_handle_', body))


def _doc_command_rows() -> set[str]:
    """Return command names from the doc's ``| <cmd> | _handle_... |`` rows."""
    text = IPC_REFERENCE_MD.read_text(encoding="utf-8")
    # Stop at the "Removed / never-existed commands" section so we don't
    # pick up the removed-command names as if they were live rows.
    removed_marker = "## Removed / never-existed commands"
    if removed_marker in text:
        text = text.split(removed_marker)[0]
    # A doc table row: ``| `command_name` | `_handle_...` | ... |``
    return set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|\s*`_handle_", text, re.MULTILINE))


def _doc_removed_commands_section() -> set[str]:
    """Return command names listed in the 'Removed / never-existed' section."""
    text = IPC_REFERENCE_MD.read_text(encoding="utf-8")
    removed_marker = "## Removed / never-existed commands"
    if removed_marker not in text:
        return set()
    # Slice from the removed-marker to the next ``## `` header.
    after = text.split(removed_marker, 1)[1]
    next_section = re.search(r"\n## ", after)
    if next_section:
        after = after[: next_section.start()]
    # The removed commands are listed as inline-code tokens:
    # `` `apply_vocabulary_suggestion`, `delete_all_personal_data`, ...``
    return set(re.findall(r"`([a-z_]+)`", after))


def _doc_commands_header_count() -> int | None:
    """Extract the documented total from the ``## Commands (N total ...)`` header."""
    text = IPC_REFERENCE_MD.read_text(encoding="utf-8")
    m = re.search(r"## Commands \((\d+)\s+total", text)
    return int(m.group(1)) if m else None


def _push_event_types() -> set[str]:
    """Return the set of ``type: "<name>"`` literals in push_events.ts.

    Excludes the WebSocket-transport auth/error frames (which appear
    near the bottom of the file in a separate interface declaration
    used only by the WS transport, not by the renderer's push-event
    union).
    """
    src = PUSH_EVENTS_TS.read_text(encoding="utf-8")
    # Slice at the ``// WebSocket transport auth frame`` marker (or
    # similar) so the WS-only ``type: "auth"`` and the duplicate
    # ``type: "error"`` near the bottom of the file are not counted.
    # If the marker isn't present, take the whole file — the test
    # will simply assert the larger count and we'll notice the drift.
    # Slice at the ``export const IPC_PROTOCOL_VERSION`` marker so the
    # WS-transport ``AuthFrame`` and ``ProtocolVersionMismatchError``
    # interfaces (which declare their own ``type: "auth"`` and
    # ``type: "error"`` literals) are not counted as push events.
    # The historical-comment mention of ``type: "relaunch_electron"``
    # lives in a ``*`` JSDoc line above this marker, so slicing here
    # also drops that.
    cutoff_markers = [
        "\nexport const IPC_PROTOCOL_VERSION",
        "\nexport interface AuthFrame",
        "\n// WebSocket transport",
        "\n// === WS auth",
        "\nexport interface WsAuth",
        "\ninterface WsAuth",
    ]
    cutoff = len(src)
    for marker in cutoff_markers:
        idx = src.find(marker)
        if idx != -1:
            cutoff = min(cutoff, idx)
    body = src[:cutoff]
    # Strip JSDoc / line-comment lines so historical mentions of
    # deleted types (e.g. ``*  : the legacy RelaunchElectronEvent
    # (type: "relaunch_electron")``) are not picked up as live type
    # literals. Only lines that begin with whitespace + ``type: "..."``
    # (inside an interface body, indented) count as real declarations.
    live_lines = [line for line in body.splitlines() if not line.lstrip().startswith(("*", "//", "/*"))]
    body = "\n".join(live_lines)
    return set(re.findall(r'type:\s*"([a-z_]+)"', body))


def _doc_push_events_header_count() -> int | None:
    """Extract the documented total from the ``## Push events (N typed)`` header."""
    text = IPC_REFERENCE_MD.read_text(encoding="utf-8")
    m = re.search(r"## Push events \((\d+)\s+typed", text)
    return int(m.group(1)) if m else None


def _doc_push_event_rows() -> set[str]:
    """Return event-type names from the doc's ``| `<name>` | <Interface> | ...`` rows."""
    text = IPC_REFERENCE_MD.read_text(encoding="utf-8")
    push_section = text.split("## Push events", 1)[1]
    # Stop at the next ``## `` header (the WebSocket transport section).
    next_section = re.search(r"\n## ", push_section)
    if next_section:
        push_section = push_section[: next_section.start()]
    return set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|", push_section, re.MULTILINE))


def test_ipc_reference_doc_has_row_for_every_registry_command() -> None:
    """Every ``_COMMAND_REGISTRY`` key MUST have a row in ipc-reference.md.

    Catches the regression where a new IPC command is added to the
    registry but the doc isn't updated. The inverse direction
    (doc has a row but the command isn't in the registry) is covered
    by the next test.
    """
    registry = _command_registry_entries()
    doc_rows = _doc_command_rows()
    missing_from_doc = registry - doc_rows
    assert not missing_from_doc, (
        f"_COMMAND_REGISTRY has {len(missing_from_doc)} command(s) with "
        f"no row in docs/ipc-reference.md: {sorted(missing_from_doc)}. "
        f"Add a row in the appropriate namespace section of the doc."
    )


def test_ipc_reference_doc_rows_are_all_in_registry_or_removed_section() -> None:
    """Every doc row MUST be in the registry OR in the removed-commands section.

    Catches the regression where the doc lists a command that was
    removed from the registry without being moved to the
    'Removed / never-existed commands' section. Also catches typos in
    command names.
    """
    registry = _command_registry_entries()
    doc_rows = _doc_command_rows()
    removed = _doc_removed_commands_section()
    unknown = doc_rows - registry - removed
    assert not unknown, (
        f"docs/ipc-reference.md lists {len(unknown)} command(s) that are "
        f"neither in _COMMAND_REGISTRY nor in the 'Removed / never-existed "
        f"commands' section: {sorted(unknown)}. Either add the command to "
        f"the registry or move the row to the removed section."
    )


def test_ipc_reference_doc_commands_header_count_matches_registry() -> None:
    """The '## Commands (N total ...)' header MUST match ``len(_COMMAND_REGISTRY)``."""
    actual = len(_command_registry_entries())
    documented = _doc_commands_header_count()
    assert documented is not None, (
        "docs/ipc-reference.md no longer has a '## Commands (N total ...)' header in a parseable form."
    )
    assert documented == actual, (
        f"docs/ipc-reference.md documents {documented} total commands but "
        f"_COMMAND_REGISTRY has {actual}. Update the header."
    )


def test_ipc_reference_doc_push_events_header_count_matches_source() -> None:
    """The '## Push events (N typed)' header MUST match the renderer's push-event count."""
    actual = len(_push_event_types())
    documented = _doc_push_events_header_count()
    assert documented is not None, (
        "docs/ipc-reference.md no longer has a '## Push events (N typed)' header in a parseable form."
    )
    assert documented == actual, (
        f"docs/ipc-reference.md documents {documented} typed push events "
        f"but the renderer's types/ipc/push_events.ts declares {actual} "
        f'(via `type: "<name>"` literals). Update the header.'
    )


def test_ipc_reference_doc_has_row_for_every_push_event_type() -> None:
    """Every push-event type in push_events.ts MUST have a row in ipc-reference.md."""
    source_types = _push_event_types()
    doc_rows = _doc_push_event_rows()
    missing_from_doc = source_types - doc_rows
    assert not missing_from_doc, (
        f"push_events.ts declares {len(missing_from_doc)} event type(s) "
        f"with no row in docs/ipc-reference.md: {sorted(missing_from_doc)}. "
        f"Add a row in the '## Push events' table."
    )


def test_ipc_reference_doc_push_event_rows_match_source_types() -> None:
    """Every row in the doc's Push events table MUST be in push_events.ts."""
    source_types = _push_event_types()
    doc_rows = _doc_push_event_rows()
    unknown = doc_rows - source_types
    assert not unknown, (
        f"docs/ipc-reference.md lists {len(unknown)} push-event type(s) "
        f"that are NOT in types/ipc/push_events.ts: {sorted(unknown)}. "
        f"Either add the type to the TS union or remove the row."
    )


def test_ipc_reference_doc_mentions_host_only_commands() -> None:
    """The two host-only commands MUST be present in the doc.

    ``shutdown`` and ``tray_click`` are host-only (routed by the Rust
    host directly, never via the renderer's ``dispatch`` path). They
    MUST be documented in the doc with the ``—`` allowlist marker so
    contributors don't accidentally add them to the renderer
    allowlist.
    """
    doc_rows = _doc_command_rows()
    host_only = {"shutdown", "tray_click"}
    missing = host_only - doc_rows
    assert not missing, (
        f"docs/ipc-reference.md is missing host-only command row(s): "
        f"{sorted(missing)}. Each host-only command must be listed in the "
        f"App-control table with the '—' allowlist marker."
    )


def test_ipc_reference_doc_removed_section_lists_known_dead_commands() -> None:
    """The 'Removed / never-existed commands' section lists the 16 dead names.

    These are commands that appeared in older drafts of the doc but
    were never in ``_COMMAND_REGISTRY``. The section exists so
    search-engine queries landing on the page find the canonical
    "this command does not exist" answer. If any of these names
    actually gets added to the registry, this test will fail loudly
    so the entry can be moved out of the removed section into a
    proper namespace table.
    """
    removed_section = _doc_removed_commands_section()
    expected_dead = {
        "apply_vocabulary_suggestion",
        "delete_all_personal_data",
        "dismiss_vocabulary_suggestion",
        "export_diagnostics",
        "export_gdpr_bundle",
        "get_audio_status",
        "get_rms_level",
        "get_vocabulary_suggestions",
        "level_monitor_status",
        "microphone_test_status",
        "onboarding_get_model_catalog",
        "onboarding_get_step",
        "onboarding_request_keyboard_permission",
        "refresh_microphones",
        "show_electron_notification",
        "test_llm_connection",
    }
    missing_from_section = expected_dead - removed_section
    assert not missing_from_section, (
        f"docs/ipc-reference.md's 'Removed / never-existed commands' "
        f"section is missing: {sorted(missing_from_section)}. Add them "
        f"back so search-engine queries find the canonical 'this command "
        f"does not exist' answer."
    )
    # None of the dead names should secretly have been added to the
    # registry — that would mean the doc's "Removed / never-existed"
    # claim is now a lie.
    registry = _command_registry_entries()
    resurrected = expected_dead & registry
    assert not resurrected, (
        f"_COMMAND_REGISTRY now contains {sorted(resurrected)}, which the "
        f"doc lists as 'Removed / never-existed commands'. Move these "
        f"out of the removed section and into a proper namespace table "
        f"(and add them to the renderer allowlist + Rust allowlist if "
        f"they should be renderer-reachable)."
    )
