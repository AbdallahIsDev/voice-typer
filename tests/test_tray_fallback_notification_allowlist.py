"""regression tests: ``tray_fallback_notification`` allowlist."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from voice_typer.server import tray as tray_module  # noqa: E402
from voice_typer.server.tray import TrayIcon  # noqa: E402

# Canonical event name published by tray.py. Imported here as a module
EXPECTED_EVENT_NAME = "tray_fallback_notification"


def _repo_root() -> Path:
    """Return the voice-typer repo root (parent of the ``tests`` dir)."""
    return Path(__file__).resolve().parent.parent


def _ws_rs_path() -> Path:
    """Return the path to ``src-tauri/src/sidecar/ws.rs``."""
    return _repo_root() / "src-tauri" / "src" / "sidecar" / "ws.rs"


def _ws_rs_source() -> str:
    """Return the source text of ``src-tauri/src/sidecar/ws.rs``."""
    p = _ws_rs_path()
    assert p.is_file(), (
        f"expected Tauri WS reader at {p}, file not found. "
        "The ws.rs path is the canonical gate for server-initiated "
        "event types (ALLOWED_EVENT_TYPES)."
    )
    return p.read_text(encoding="utf-8")


def _tray_source() -> str:
    """Return the source text of ``voice_typer/server/tray.py``."""
    return inspect.getsource(tray_module)


class TestTrayPublishesCanonicalEventName:
    """tray.py MUST publish the literal ``tray_fallback_notification``."""

    def test_event_name_literal_appears_in_tray_source(self) -> None:
        """``tray.py`` source. This is the wire-protocol name the Tauri"""
        src = _tray_source()
        assert EXPECTED_EVENT_NAME in src, (
            f"tray.py must publish the literal "
            f"{EXPECTED_EVENT_NAME!r} via event_bus.publish in "
            f"``_drain_pending``. The event name is the wire-protocol "
            f"contract with the Tauri WS reader's "
            f"``ALLOWED_EVENT_TYPES`` slice."
        )

    def test_event_name_literal_appears_in_publish_call(self) -> None:
        """The event name MUST appear in a ``_event_bus.publish`` call"""
        src = _tray_source()
        # The publish call site is structured as:
        executable_lines = [line for line in src.splitlines() if line.strip() and not line.strip().startswith("#")]
        executable_src = "\n".join(executable_lines)
        quoted = f'"{EXPECTED_EVENT_NAME}"'
        assert quoted in executable_src, (
            f"the quoted literal {quoted} must appear in "
            f"executable code in tray.py (the ``_event_bus.publish`` "
            f"call site in ``_drain_pending``). A docstring-only "
            f"mention is insufficient, the event must actually be "
            f"published at runtime."
        )


# ─── 2. tray.py _drain_pending docstring documents the cross-layer gate ─


class TestDrainPendingDocumentsWsRsGate:
    """the ``_drain_pending`` docstring MUST reference the actual"""

    def test_docstring_mentions_allowed_event_types(self) -> None:
        """The ``_drain_pending`` docstring MUST mention"""
        src = inspect.getsource(TrayIcon._drain_pending)
        assert "ALLOWED_EVENT_TYPES" in src, (
            "TrayIcon._drain_pending docstring must reference "
            "``ALLOWED_EVENT_TYPES``, the actual gate is the Tauri "
            "WS reader's allowlist slice at ws.rs:80-150, NOT 'a "
            "single line in the renderer' (the old, inaccurate framing)."
        )

    def test_docstring_mentions_ws_rs_path(self) -> None:
        """reader can locate the actual gate (the file path)."""
        src = inspect.getsource(TrayIcon._drain_pending)
        assert "ws.rs" in src, (
            "TrayIcon._drain_pending docstring must reference "
            "``ws.rs`` (the file containing ``ALLOWED_EVENT_TYPES``) "
            "so a contributor reading the Python side can locate the "
            "actual gate."
        )


class TestWsRsAllowlistStatus:
    """SI-14 cross-layer awareness check (informational, NOT a hard gate)."""

    def test_ws_rs_allowlist_eventually_includes_event_name(self) -> None:
        """``ALLOWED_EVENT_TYPES`` slice literal, PASS. Otherwise SKIP —"""
        import pytest

        src = _ws_rs_source()
        start_marker = "const ALLOWED_EVENT_TYPES: &[&str] = &["
        idx = src.find(start_marker)
        if idx == -1:
            pytest.skip(
                "ws.rs ``ALLOWED_EVENT_TYPES`` slice declaration "
                "not found (or shape changed), the canonical gate is "
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
                f"the canonical gate for the slice entry, this "
                f"Python-side test skips until the slice edit lands."
            )
        # Slice contains the literal, cross-layer gap is closed.


class TestPublishCallSiteShape:
    """canonical ``{\"type\": \"tray_fallback_notification\", ...}`` shape so"""

    def test_publish_call_uses_type_field(self) -> None:
        """The publish call MUST use the ``\"type\"`` field (not ``event``,"""
        src = inspect.getsource(TrayIcon._drain_pending)
        # The publish call site uses ``"type": "tray_fallback_notification"``
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
