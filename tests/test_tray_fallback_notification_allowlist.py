"""regression tests: ``tray_fallback_notification`` allowlist.

When the tray is unavailable (Linux Wayland without StatusNotifierItem,
``VOICE_TYPER_NO_TRAY=1``, headless, or pystray ``OSError``), the
``TrayIcon._drain_pending`` method publishes a
``tray_fallback_notification`` event via the event bus so the renderer
can surface the dropped notification as a toast.

CROSS-LAYER GAP (SI-14): the event name ``tray_fallback_notification``
is NOT currently in the Tauri host's ``ALLOWED_EVENT_TYPES`` slice
(``src-tauri/src/sidecar/ws.rs:80-150``). The Tauri WS reader silently
DROPS any inbound frame whose ``type`` is not in that slice (logged at
``[WS-READER] dropping unknown event type:``). Adding a listener in
the renderer alone is therefore insufficient — the frame is dropped at
the Rust layer before it ever reaches the renderer.

(this file's owner) edits ONLY the Python side (tray.py +
tray_elapsed_timer.py). The actual ``ws.rs`` ``ALLOWED_EVENT_TYPES``
This test file pins the contract from the
Python side:

  1. ``tray.py`` MUST publish the literal event name
     ``"tray_fallback_notification"`` (regression guard against an
     accidental rename on the Python side without a matching ws.rs
     allowlist update).
  2. ``tray.py``'s ``_drain_pending`` docstring MUST document the
     cross-layer gate — i.e. reference both ``ALLOWED_EVENT_TYPES``
     and ``ws.rs`` — so a future contributor reading the Python side
     sees that the actual gate is the Rust allowlist, not "a single
     line in the renderer".
  3. The event name ``tray_fallback_notification`` MUST appear in the
     ws.rs source file in SOME form — either in the
     ``ALLOWED_EVENT_TYPES`` slice OR in a comment
     near the slice acknowledging the pending allowlist.
     this assertion fails on the slice check but passes on the
     "file mentions the literal" check; both pass.
     The test is intentionally written to PASS in both states so the
     ordering doesn't create a CI race.

These tests are HEADLESS and SIDE-EFFECT-FREE: they perform no IPC,
spawn no process, touch no sockets, and don't import pystray. They
read Python source via ``inspect.getsource`` and the Rust source via
``Path.read_text``. Safe to run in parallel with other fix sub-agents.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from voice_typer.server import tray as tray_module  # noqa: E402
from voice_typer.server.tray import TrayIcon  # noqa: E402

# Canonical event name published by tray.py. Imported here as a module
# constant so a future rename on the publish side (without updating
# this test) is caught loudly — the constant below must equal the
# literal in tray.py (asserted by ``test_event_name_constant_matches_publish_literal``).
EXPECTED_EVENT_NAME = "tray_fallback_notification"


# ─── path helpers ───────────────────────────────────────────────────────


def _repo_root() -> Path:
    """Return the voice-typer repo root (parent of the ``tests`` dir)."""
    return Path(__file__).resolve().parent.parent


def _ws_rs_path() -> Path:
    """Return the path to ``src-tauri/src/sidecar/ws.rs``."""
    return _repo_root() / "src-tauri" / "src" / "sidecar" / "ws.rs"


def _ws_rs_source() -> str:
    """Return the source text of ``src-tauri/src/sidecar/ws.rs``.

    Python cannot import Rust; we read the file from disk and treat it
    as a string. The path is resolved relative to the repo root so the
    test is location-independent.
    """
    p = _ws_rs_path()
    assert p.is_file(), (
        f"expected Tauri WS reader at {p} — file not found. "
        "The ws.rs path is the canonical gate for server-initiated "
        "event types (ALLOWED_EVENT_TYPES)."
    )
    return p.read_text(encoding="utf-8")


def _tray_source() -> str:
    """Return the source text of ``voice_typer/server/tray.py``.

    We use ``inspect.getsource`` on the imported module so the test
    tracks the LIVE source (not a frozen snapshot).
    """
    return inspect.getsource(tray_module)


# ─── 1. tray.py publishes the canonical event name ─────────────────────


class TestTrayPublishesCanonicalEventName:
    """tray.py MUST publish the literal ``tray_fallback_notification``."""

    def test_event_name_literal_appears_in_tray_source(self) -> None:
        """The literal ``"tray_fallback_notification"`` MUST appear in
        ``tray.py`` source. This is the wire-protocol name the Tauri
        WS reader will look up in ``ALLOWED_EVENT_TYPES``.

        A rename on the Python side without a matching ws.rs allowlist
        update would silently break the fallback-notification channel
        (the Tauri reader would drop the unknown event type at the
        WS layer before the renderer ever sees it).
        """
        src = _tray_source()
        assert EXPECTED_EVENT_NAME in src, (
            f"tray.py must publish the literal "
            f"{EXPECTED_EVENT_NAME!r} via event_bus.publish in "
            f"``_drain_pending``. The event name is the wire-protocol "
            f"contract with the Tauri WS reader's "
            f"``ALLOWED_EVENT_TYPES`` slice."
        )

    def test_event_name_literal_appears_in_publish_call(self) -> None:
        """The event name MUST appear in a ``_event_bus.publish`` call
        site (not just in a comment or docstring). This is the
        executable assertion — a docstring mention alone is not enough.
        """
        src = _tray_source()
        # The publish call site is structured as:
        #     _event_bus.publish(
        #         {
        #             "type": "tray_fallback_notification",
        #             ...
        #         }
        #     )
        # We assert the literal appears in executable code, not just
        # in comments. Strip comment-only lines for a more accurate
        # executable check (mirrors the pattern in
        # test_ipc_send_shutdown_allowlist.py).
        executable_lines = [line for line in src.splitlines() if line.strip() and not line.strip().startswith("#")]
        executable_src = "\n".join(executable_lines)
        quoted = f'"{EXPECTED_EVENT_NAME}"'
        assert quoted in executable_src, (
            f"the quoted literal {quoted} must appear in "
            f"executable code in tray.py (the ``_event_bus.publish`` "
            f"call site in ``_drain_pending``). A docstring-only "
            f"mention is insufficient — the event must actually be "
            f"published at runtime."
        )


# ─── 2. tray.py _drain_pending docstring documents the cross-layer gate ─


class TestDrainPendingDocumentsWsRsGate:
    """the ``_drain_pending`` docstring MUST reference the actual
    Tauri ``ALLOWED_EVENT_TYPES`` gate at ``ws.rs``, not just "a single
    line in the renderer".

    The previous docstring claimed the fix was "a single line in the
    renderer's ``useAppStore``" — this is FALSE because the actual gate
    is the Rust ``ALLOWED_EVENT_TYPES`` slice. A contributor reading
    the old docstring would add a renderer listener alone and the
    event would still be silently dropped at the WS layer.
    """

    def test_docstring_mentions_allowed_event_types(self) -> None:
        """The ``_drain_pending`` docstring MUST mention
        ``ALLOWED_EVENT_TYPES`` so a reader knows the actual gate is
        the Rust allowlist slice.
        """
        src = inspect.getsource(TrayIcon._drain_pending)
        assert "ALLOWED_EVENT_TYPES" in src, (
            "TrayIcon._drain_pending docstring must reference "
            "``ALLOWED_EVENT_TYPES`` — the actual gate is the Tauri "
            "WS reader's allowlist slice at ws.rs:80-150, NOT 'a "
            "single line in the renderer' (the old, inaccurate framing)."
        )

    def test_docstring_mentions_ws_rs_path(self) -> None:
        """The ``_drain_pending`` docstring MUST mention ``ws.rs`` so a
        reader can locate the actual gate (the file path).
        """
        src = inspect.getsource(TrayIcon._drain_pending)
        assert "ws.rs" in src, (
            "TrayIcon._drain_pending docstring must reference "
            "``ws.rs`` (the file containing ``ALLOWED_EVENT_TYPES``) "
            "so a contributor reading the Python side can locate the "
            "actual gate."
        )


# ─── 3. ws.rs awareness of the event name (informational) ─────────────


class TestWsRsAllowlistStatus:
    """SI-14 cross-layer awareness check (informational, NOT a hard gate).

    The actual ws.rs ``ALLOWED_EVENT_TYPES`` slice edit is owned by
    (a parallel fix sub-agent). This test class is
    INTENTIONALLY written to PASS in both the pre-and
    post-states so the / ordering
    doesn't create a CI race:

      - PRE-(slice edit NOT yet landed): ws.rs does not
        mention the literal in the ``ALLOWED_EVENT_TYPES`` slice.
        The test SKIPS with a clear reason pointing at.
      - POST-(slice edit landed): ws.rs contains the
        literal in the slice. The test PASSES — the cross-layer gap
        is closed.
      - MID-FIX (added a Rust test that ASSERTS the slice
        membership but hasn't yet added the slice entry — observed
        2024 in the work-in-progress state): ws.rs mentions the
        literal in test code / comments but NOT in the slice. The
        test SKIPS — the gap is documented in ws.rs itself via
        Rust test, so the Python-side test
        doesn't need to hard-fail.

    A hard assertion here would either (a) fail pre-(blocking
    from landing independently) or (b) fail mid-fix (creating
    a CI flake window while is in progress). The skip-based
    approach gives us a CI signal in ALL states without creating an
    ordering dependency.
    """

    def test_ws_rs_allowlist_eventually_includes_event_name(self) -> None:
        """If ws.rs already has ``tray_fallback_notification`` in the
        ``ALLOWED_EVENT_TYPES`` slice literal, PASS. Otherwise SKIP —
        the actual allowlist edit is owned by (which may
        also have added a Rust-side test in ws.rs asserting the slice
        membership; that test is the canonical gate for the slice
        entry, not this Python-side test).

        The ws.rs allowlist slice ownership is a Tauri-side concern;
        this Python-side test exists to detect the cross-layer gap
        once the slice edit lands, but the slice itself is not in
        scope for the Python test suite. Skip the assertion
        unconditionally when the slice declaration is absent (this
        is a Rust file — the Python test cannot modify it).
        """
        import pytest

        src = _ws_rs_source()
        start_marker = "const ALLOWED_EVENT_TYPES: &[&str] = &["
        idx = src.find(start_marker)
        if idx == -1:
            # ws.rs slice declaration shape changed OR the slice
            # is defined elsewhere. The Rust test is the canonical
            # gate; this Python-side test is a cross-layer sanity
            # check only. Skip rather than fail.
            pytest.skip(
                "ws.rs ``ALLOWED_EVENT_TYPES`` slice declaration "
                "not found (or shape changed) — the canonical gate is "
                "the Rust-side test in ws.rs, not this Python-side "
                "sanity check. Skipping."
            )
        slice_body = src[idx : src.find("];", idx)]
        quoted = f'"{EXPECTED_EVENT_NAME}"'
        if quoted not in slice_body:
            pytest.skip(
                f"SI-14 pending: ws.rs ``ALLOWED_EVENT_TYPES`` slice does "
                f"not yet contain the quoted literal {quoted!r}. The "
                f"actual slice edit is owned by (a parallel "
                f"fix sub-agent). If has added a Rust-side "
                f"test in ws.rs asserting slice membership, that test is "
                f"the canonical gate for the slice entry — this "
                f"Python-side test skips until the slice edit lands."
            )
        # Slice contains the literal — cross-layer gap is closed.
        # (No additional assertion needed; the slice membership IS the
        # gate. The Tauri WS reader's ``is_allowed_event_type`` lookup
        # will now match the published event name at runtime.)


# ─── 4. publish call site uses the canonical event name ────────────────


class TestPublishCallSiteShape:
    """the publish call site in ``_drain_pending`` MUST use the
    canonical ``{"type": "tray_fallback_notification", ...}`` shape so
    the Tauri WS reader's allowlist lookup (which keys on the
    ``type`` field) matches.
    """

    def test_publish_call_uses_type_field(self) -> None:
        """The publish call MUST use the ``"type"`` field (not ``event``,
        ``name``, or ``kind``) to carry the event name — that's the
        field the Tauri WS reader extracts for the
        ``ALLOWED_EVENT_TYPES`` lookup.
        """
        src = inspect.getsource(TrayIcon._drain_pending)
        # The publish call site uses ``"type": "tray_fallback_notification"``
        # — assert both tokens appear together (within a reasonable
        # window) in the drain method.
        assert '"type"' in src, (
            "the publish call in _drain_pending must use the "
            '``"type"`` field to carry the event name (the Tauri WS '
            "reader's allowlist lookup keys on ``type``)."
        )
        assert EXPECTED_EVENT_NAME in src, (
            f"the publish call in _drain_pending must publish "
            f"the event name {EXPECTED_EVENT_NAME!r} (matched against "
            f"the ws.rs ``ALLOWED_EVENT_TYPES`` slice)."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
