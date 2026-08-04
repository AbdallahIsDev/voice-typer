"""EC-3 regression tests: ``relaunch_app`` event name parity (Electron ↔ Python).

The Python sidecar publishes ``{"type": "relaunch_app"}`` via
``event_bus.publish`` (see ``voice_typer/server/app.py:1041`` and
``voice_typer/server/ipc_server.py:1946``).  The Tauri Rust host listens
for ``relaunch_app`` (``src-tauri/src/main.rs``).  The Electron main
process previously listened for ``relaunch_electron`` — a stale name
left over from the PVT-2 rename that updated the Python+Tauri side but
forgot the Electron listener.  EC-3 fixes the drift: the Electron
listener now matches on ``relaunch_app``.

These tests pin the parity contract at the SOURCE level so a future
contributor can't silently reintroduce the drift by copying an old diff
or reverting one side of the rename.  They read the source files as
TEXT (no import of the TypeScript module — that's impossible from
Python; we treat the .ts file as a string and assert on the literal
event name in the dispatch arm).

The tests are HEADLESS and SIDE-EFFECT-FREE: they perform no IPC, spawn
no process, and touch no sockets.  They are safe to run in parallel
with other fix sub-agents.

Assertions
----------
1. ``event_bus.py`` canonical event catalogue docstring MUST list
   ``relaunch_app`` and MUST NOT list ``relaunch_electron`` as the
   canonical event name (the catalogue is the source-of-truth anchor
   referenced by ADR-0020 §2).
2. ``handle-message.ts`` (the Electron-side dispatch arm for Python push
   events) MUST contain the literal ``"relaunch_app"`` and MUST NOT
   dispatch on ``"relaunch_electron"``.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

# ─── path helpers ─────────────────────────────────────────────────────────


def _repo_root() -> Path:
    """Return the voice-typer repo root (parent of the ``tests`` dir).

    ``tests/test_relaunch_event_name_parity.py`` lives at
    ``<repo>/tests/test_relaunch_event_name_parity.py``, so two ``parent``
    hops land on the repo root that contains both ``voice_typer/`` and
    ``tests/``.
    """
    return Path(__file__).resolve().parent.parent


def _event_bus_source() -> str:
    """Return the source text of ``voice_typer/server/event_bus.py``.

    We use ``inspect.getsource`` on the imported module so the test
    tracks the LIVE source (not a frozen snapshot) — if a future
    refactor moves the catalogue into a sibling module, this test will
    surface the change rather than silently passing against stale text.
    """
    import voice_typer.server.event_bus as event_bus

    return inspect.getsource(event_bus)


def _handle_message_ts_source() -> str:
    """Return the source text of the Electron ``handle-message.ts`` file.

    Python cannot import TypeScript; we read the file from disk and
    treat it as a string.  The path is resolved relative to the repo
    root so the test is location-independent.
    """
    ts_path = _repo_root() / "voice_typer" / "client" / "src" / "main" / "python" / "handle-message.ts"
    assert ts_path.is_file(), f"EC-3: expected Electron main module at {ts_path} — file not found"
    return ts_path.read_text(encoding="utf-8")


# ─── event_bus.py catalogue parity ────────────────────────────────────────


class TestEventBusCatalogueListsRelaunchApp:
    """EC-3: ``event_bus.py`` canonical event catalogue lists ``relaunch_app``.

    The catalogue docstring is the code-side anchor mirrored in ADR-0020
    §2's "Sidecar→UI Event Table".  It MUST list ``relaunch_app`` as the
    canonical event name (not the legacy ``relaunch_electron``).
    """

    def test_catalogue_lists_relaunch_app(self):
        """The literal ``relaunch_app`` MUST appear in the catalogue.

        We don't pin a specific line number (catalogue entries can
        shift as events are added) — we just require the canonical
        name to be present anywhere in the module source.  The presence
        of the entry in this module's docstring is what makes it the
        code-side anchor; the ADR is the spec-side anchor.
        """
        src = _event_bus_source()
        assert "relaunch_app" in src, (
            "EC-3: event_bus.py must reference 'relaunch_app' (the canonical "
            "event name published by app.py and ipc_server.py). Found neither."
        )

    def test_catalogue_does_not_list_relaunch_electron_as_canonical(self):
        """The legacy ``relaunch_electron`` literal MUST NOT appear in
        ``event_bus.py`` at all.

        EC-3 removed the stale "(renamed relaunch_app on the Tauri side)"
        framing from the catalogue docstring — there is no longer a
        rename; the canonical name on every runtime (Python, Electron,
        Tauri) is ``relaunch_app``.  Any remaining ``relaunch_electron``
        literal in this module is a documentation bug.
        """
        src = _event_bus_source()
        assert "relaunch_electron" not in src, (
            "EC-3: event_bus.py must not reference the legacy "
            "'relaunch_electron' name anywhere. The canonical name on "
            "every runtime is 'relaunch_app'."
        )


# ─── handle-message.ts dispatch arm parity ────────────────────────────────


class TestHandleMessageDispatchesOnRelaunchApp:
    """EC-3: Electron ``handle-message.ts`` dispatches on ``relaunch_app``.

    The Python backend publishes ``{"type": "relaunch_app"}`` (see
    ``voice_typer/server/app.py:1041``).  The Electron-side dispatch
    arm MUST match on the same literal — previously it matched on the
    stale ``"relaunch_electron"`` literal, which silently broke the
    event-driven restart path (only the exit-code-0 fallback worked).
    """

    def test_handle_message_contains_relaunch_app_literal(self):
        """The dispatch table in ``handle-message.ts`` MUST include
        ``relaunch_app`` (matching the Python publish call).

        This is the wire-protocol parity check: the string the Python
        side publishes must equal the string the Electron side matches.
        We accept either the quoted literal form (``"relaunch_app": () =>``)
        OR the object-key form (``relaunch_app: () =>``) — both resolve
        to the wire string ``"relaunch_app"`` at runtime, and the
        refactor from the quoted form to the object-key form (a
        Prettier-friendly shorthand) shouldn't fail the parity check.
        """
        src = _handle_message_ts_source()
        # Accept either the quoted literal or the bare identifier
        # (object-key shorthand). Both produce the wire string
        # ``"relaunch_app"`` at runtime.
        assert (
            '"relaunch_app"' in src or "relaunch_app:" in src or "relaunch_app :" in src
        ), (
            "EC-3: handle-message.ts must dispatch on the literal "
            '"relaunch_app" (matching the Python publish call). '
            "The wire-protocol event name must be identical on both sides."
        )

    def test_handle_message_does_not_dispatch_on_relaunch_electron(self):
        """The dispatch arm MUST NOT match on the legacy
        ``"relaunch_electron"`` literal.

        Belt-and-braces: even if a contributor adds a new comment that
        mentions ``relaunch_electron`` for historical context, the
        QUOTED literal (the form used in ``msg.type === "..."``) must
        not appear — that's the form that would silently break the
        dispatch if reintroduced.  We allow ``relaunch_electron`` in
        prose (unquoted) but forbid the quoted form outright.
        """
        src = _handle_message_ts_source()
        assert '"relaunch_electron"' not in src, (
            "EC-3: handle-message.ts must not dispatch on the legacy "
            "'relaunch_electron' literal. The Python backend publishes "
            "'relaunch_app'; matching on the old name silently breaks "
            "the event-driven restart path."
        )


# ─── cross-side parity smoke test ─────────────────────────────────────────


class TestRelaunchEventNameCrossSideParity:
    """EC-3: the event name on the wire is identical on both sides.

    This is the headline assertion of the fix: Python publishes
    ``relaunch_app`` AND Electron listens for ``relaunch_app``.  The
    drift bug existed because the two sides used different literals; this
    test makes the parity explicit so a future rename on one side
    without the other is caught immediately.
    """

    def test_both_sides_use_relaunch_app(self):
        py_src = _event_bus_source()
        ts_src = _handle_message_ts_source()
        assert "relaunch_app" in py_src, "EC-3: Python event_bus.py must reference 'relaunch_app'"
        # Accept either the quoted literal (``"relaunch_app"``) or the
        # object-key shorthand (``relaunch_app:``) — both resolve to
        # the wire string ``"relaunch_app"`` at runtime. The refactor
        # from quoted-literal to object-key (a Prettier-friendly
        # shorthand) shouldn't fail the parity check.
        assert (
            '"relaunch_app"' in ts_src or "relaunch_app:" in ts_src or "relaunch_app :" in ts_src
        ), (
            'EC-3: Electron handle-message.ts must dispatch on "relaunch_app" (matching Python\'s publish call)'
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
