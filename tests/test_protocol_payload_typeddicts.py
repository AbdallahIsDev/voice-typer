"""Payload-shape contracts for the typed service-surface annotations.

These tests pin the TypedDict contracts introduced to replace the bare
``dict`` / ``list`` annotations on the config side-effect surface
(``config_applier.py``) and the list-returning service methods
(``providers.py`` :class:`ServiceProtocol`). Each test drives the REAL
producer (or its projection seam) and asserts the runtime payload keys
match the TypedDict field names — so a producer-side shape change fails
here and forces the contract type to be updated in lockstep, instead of
silently drifting away from the annotation.
"""

from __future__ import annotations

import sqlite3
import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest
from voice_typer.server.config_applier import (
    ConfigApplier,
    SideEffectStatus,
    apply_config_side_effects as module_level_apply_config_side_effects,
)
from voice_typer.server.history_db_internals.search import project_text_row
from voice_typer.server.providers import (
    HistoryEntry,
    MicrophoneEntry,
    ServiceProtocol,
    TemplateEntry,
)


@pytest.fixture
def fake_app() -> MagicMock:
    """Minimal VoiceTyperApp double for the config side-effect dispatch.

    Everything is auto-mocked; the sync handlers are additionally
    monkeypatched per-test (the ``startup_tasks`` module does real
    platform calls in production — see the handler docstrings).
    """
    app = MagicMock()
    app.config.autostart = False
    app.config.hotkey = "<f2>"
    app.config.show_notifications = True
    app.tray.invalidate_menu_cache = MagicMock()
    return app


# ── SideEffectStatus (config_applier) ────────────────────────────────


class TestSideEffectStatus:
    def test_module_level_entry_point_returns_empty_status(self) -> None:
        """The module-level delegation seam returns the all-None status
        dict with exactly the two documented keys."""
        result = module_level_apply_config_side_effects({}, None)
        assert set(result) == set(SideEffectStatus.__annotations__)
        assert result["autostart_status"] is None
        assert result["prewarm_status"] is None

    def test_dispatch_result_keys_match_typeddict(self, fake_app: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        """``ConfigApplier.apply_config_side_effects`` accumulates a dict
        whose keys are exactly the ``SideEffectStatus`` fields."""
        from voice_typer.server import startup_tasks

        monkeypatch.setattr(
            startup_tasks,
            "sync_autostart",
            lambda app: {"registered": True, "error": None},
            raising=True,
        )
        monkeypatch.setattr(
            startup_tasks,
            "sync_prewarm_task",
            lambda app, **kw: {"registered": False, "error": "stub"},
            raising=True,
        )
        applier = ConfigApplier(fake_app)
        result = applier.apply_config_side_effects({"autostart": True, "fast_startup": True})
        assert set(result) == set(SideEffectStatus.__annotations__)
        assert result["autostart_status"] == {"registered": True, "error": None}
        assert result["prewarm_status"] == {"registered": False, "error": "stub"}

    def test_sync_failure_fallback_keeps_shape(self, fake_app: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        """When a sync raises, the handler's failure fallback still
        populates the SAME key set with a ``registered``/``error`` dict —
        the status shape never widens on the error path."""

        def _boom(app: Any) -> dict:
            raise RuntimeError("sync exploded")

        from voice_typer.server import startup_tasks

        monkeypatch.setattr(startup_tasks, "sync_autostart", _boom, raising=True)
        applier = ConfigApplier(fake_app)
        result = applier.apply_config_side_effects({"autostart": True})
        assert set(result) == set(SideEffectStatus.__annotations__)
        assert result["autostart_status"] == {
            "registered": False,
            "error": "sync exploded",
        }
        assert result["prewarm_status"] is None

    def test_apply_config_propagates_side_effect_status(
        self, fake_app: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``ConfigApplier.apply_config`` returns the status dict captured
        from ``apply_config_side_effects`` verbatim (early-raise safety:
        the all-None initializer keeps the return shape stable)."""
        sentinel: SideEffectStatus = {
            "autostart_status": {"registered": True, "error": None},
            "prewarm_status": None,
        }
        applier = ConfigApplier(fake_app)
        monkeypatch.setattr(applier, "apply_config_side_effects", lambda updates: sentinel)
        fake_app.config.save_strict.return_value = None
        result = applier.apply_config({"show_notifications": False})
        assert result is sentinel
        assert set(result) == set(SideEffectStatus.__annotations__)


# ── HistoryEntry (providers) ─────────────────────────────────────────

# The column list mirrors the SELECT statements in
# ``history_db_internals/search.py`` (get_recent / search / get_favorites
# share it). ``project_text_row`` is the projection seam every one of the
# three list methods runs each row through — pinning its output keys pins
# the dict the service layer hands to the IPC layer.
_HISTORY_SELECT_COLUMNS = [
    "id",
    "text",
    "text_full_length",
    "text_is_encrypted",
    "timestamp",
    "duration",
    "model",
    "device",
    "word_count",
    "char_count",
    "favorite",
    "language",
]


class TestHistoryEntry:
    def test_projected_row_keys_match_typeddict(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT " + ", ".join(f"1 AS {c}" for c in _HISTORY_SELECT_COLUMNS) + " FROM (SELECT 1)"
        ).fetchone()
        assert row is not None
        projected = project_text_row(row)
        assert set(projected) == set(HistoryEntry.__annotations__), (
            "project_text_row output keys drifted from the HistoryEntry "
            "TypedDict — update the contract type (providers.py) or the "
            "SELECT/projection (history_db_internals/search.py) together."
        )
        # The internal encryption marker must NOT cross the IPC boundary.
        assert "text_is_encrypted" not in projected

    def test_service_protocol_annotations_are_typed(self) -> None:
        """The protocol methods now declare element-typed lists instead of
        bare ``list`` (annotations are strings under
        ``from __future__ import annotations``)."""
        for method in ("get_history", "search_history", "get_favorites"):
            ann = getattr(ServiceProtocol, method).__annotations__["return"]
            assert ann == "list[HistoryEntry]", (
                f"ServiceProtocol.{method} must stay in sync with the HistoryEntry payload contract (got {ann!r})."
            )


# ── MicrophoneEntry (providers) ──────────────────────────────────────


class TestMicrophoneEntry:
    def test_enumerated_device_keys_match_typeddict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Drive the real PortAudio enumeration with a fake sounddevice
        module and pin the device-dict keys to ``MicrophoneEntry``."""
        from voice_typer.server.server_platform import microphone_list

        def query_devices(kind: str | None = None) -> Any:
            if kind == "input":
                return {"index": 5, "name": "Test Input"}
            return [
                {
                    "index": 5,
                    "name": "Test Mic",
                    "hostapi": 0,
                    "max_input_channels": 2,
                    "default_samplerate": 48000.0,
                }
            ]

        def query_hostapis() -> list[dict]:
            return [{"name": "ALSA", "default_input_device": 5}]

        # MagicMock (not types.ModuleType + attribute assignment, which
        # pyrefly 1.1.1 flags) — same pattern as the ws mic-population
        # tests' fake sounddevice.
        fake_sd = MagicMock()
        fake_sd.query_devices = query_devices
        fake_sd.query_hostapis = query_hostapis
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
        # Module-identity check in the TTL cache treats the swapped
        # module as stale — plus an explicit invalidation for determinism.
        monkeypatch.setattr(microphone_list, "_LIST_MICS_CACHE", None)
        mics = microphone_list.list_microphones()
        assert mics, "fake sounddevice should yield one input device"
        assert set(mics[0]) == set(MicrophoneEntry.__annotations__), (
            "list_microphones device-dict keys drifted from the "
            "MicrophoneEntry TypedDict — update the contract type "
            "(providers.py) or the device construction "
            "(server_platform/microphone_list.py) together."
        )

    def test_service_protocol_annotations_are_typed(self) -> None:
        for method in ("get_microphones", "refresh_microphones"):
            ann = getattr(ServiceProtocol, method).__annotations__["return"]
            assert ann == "list[MicrophoneEntry]", (
                f"ServiceProtocol.{method} must stay in sync with the MicrophoneEntry payload contract (got {ann!r})."
            )


# ── TemplateEntry (providers) ────────────────────────────────────────


class TestTemplateEntry:
    def test_service_projection_keys_match_typeddict(self) -> None:
        """``TemplateMixin.get_templates`` strips internal fields and
        returns exactly the three TemplateEntry keys."""
        from voice_typer.server.service.template import TemplateMixin

        app = MagicMock()
        app._template_manager = types.SimpleNamespace(
            templates=[
                {
                    "trigger": "sig",
                    "output": "signature text",
                    "match_mode": "exact",
                    "created_at": "2026-09-01T00:00:00",
                }
            ]
        )
        mixin = TemplateMixin.__new__(TemplateMixin)
        # ``_app`` is provided by ``ServiceMixinBase`` in production;
        # injected directly on the bare instance for this test.
        mixin._app = app
        entries = mixin.get_templates()
        assert entries, "one template in -> one entry out"
        assert set(entries[0]) == set(TemplateEntry.__annotations__), (
            "get_templates projection keys drifted from the TemplateEntry "
            "TypedDict — update the contract type (providers.py) or the "
            "projection (service/template.py) together."
        )
        # Internal fields must not cross the IPC boundary.
        assert "created_at" not in entries[0]

    def test_service_protocol_annotations_are_typed(self) -> None:
        assert ServiceProtocol.get_templates.__annotations__["return"] == "list[TemplateEntry]"
        # ``save_templates`` mirrors the impl's ``list[dict]`` parameter.
        assert ServiceProtocol.save_templates.__annotations__["templates"] == "list[dict]"
