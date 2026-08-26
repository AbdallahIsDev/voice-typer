"""Startup reconciliation of the persisted microphone selection.

Pins the contract that ``app.config.microphone`` (the canonical
``config.json`` value) is validated against the live device list during
startup — BEFORE any consumer (tray, recorder, renderer) reads it:

- stale/unavailable id  → silently fall back to System Default,
  persist ``null``, emit one WARNING diagnostic line;
- valid stable id       → left untouched, healthy INFO line;
- legacy id shape that still resolves → migrated in-place to the
  canonical stable id;
- ``None``              → already System Default, no write;
- empty enumeration     → never used as evidence for a fallback.

The user-facing recovery must be SILENT (no tray notify): only the
diagnostic log line describes what was recovered. The renderer learns
about the correction via the same ``config_changed`` push event the IPC
``set_config`` path uses, so it never needs the Microphone page to
discover a stale config.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from voice_typer.server import startup_tasks

STABLE_ID = "WASAPI|USB Microphone"
STALE_ID = "mic-42"


def make_app(microphone: object, *, with_lock: bool = False) -> Any:
    # ``Any``: the double intentionally satisfies only the narrow surface
    # reconciliation touches; AppProtocol's full shape isn't needed here.
    """Minimal app double exposing just what reconciliation touches."""
    saves: list[bool] = []

    def save() -> bool:
        saves.append(True)
        return True

    config = SimpleNamespace(microphone=microphone, save=save)
    app = SimpleNamespace(
        config=config,
        tray=SimpleNamespace(set_microphones=lambda mics: None),
        _microphones=[],
    )
    if with_lock:
        import threading

        app._config_mutation_lock = threading.RLock()
    return app, saves


@pytest.fixture()
def resolver():
    """Patch find_microphone_by_id at its call-time lookup path."""

    class Resolver:
        def __init__(self) -> None:
            self.result: dict | None = None
            self.calls: list[str] = []

        def resolve_to(self, device_id: str, name: str = "USB Microphone") -> None:
            self.result = {
                "id": device_id,
                "index": 0,
                "name": name,
                "host_api": "Windows WASAPI",
                "channels": 1,
                "default": True,
                "is_bluetooth": False,
            }

    r = Resolver()
    with patch(
        "voice_typer.server.server_platform.microphone_list.find_microphone_by_id",
        side_effect=lambda mic_id: (r.calls.append(str(mic_id)), r.result)[1],
    ):
        yield r


class TestReconcileConfiguredMicrophone:
    def test_stale_id_falls_back_silently_and_persists(self, resolver, caplog) -> None:
        resolver.result = None  # unresolvable on this machine
        app, saves = make_app(STALE_ID)

        caplog.set_level(logging.WARNING, logger="voice_typer.server.startup_tasks")
        with patch("voice_typer.server.event_bus.publish") as mock_publish:
            startup_tasks._reconcile_configured_microphone(app, [{"id": "WASAPI|Other"}])

        assert app.config.microphone is None
        assert saves == [True]
        # Exactly ONE diagnostic publish — the config_changed envelope.
        assert mock_publish.call_count == 1
        evt = mock_publish.call_args[0][0]
        assert evt["type"] == "config_changed"
        assert evt["data"] == {"microphone": None}
        warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert STALE_ID in warnings[0].getMessage()
        assert "System Default" in warnings[0].getMessage()

    def test_valid_stable_id_is_untouched(self, resolver, caplog) -> None:
        resolver.resolve_to(STABLE_ID)
        app, saves = make_app(STABLE_ID)

        caplog.set_level(logging.INFO, logger="voice_typer.server.startup_tasks")
        with patch("voice_typer.server.event_bus.publish") as mock_publish:
            startup_tasks._reconcile_configured_microphone(app, [{"id": STABLE_ID}])

        assert app.config.microphone == STABLE_ID
        assert saves == []  # nothing rewritten
        assert mock_publish.call_count == 0
        infos = [
            rec
            for rec in caplog.records
            if rec.levelno == logging.INFO and "configured device available" in rec.getMessage()
        ]
        assert len(infos) == 1

    def test_none_means_system_default_no_write(self, resolver) -> None:
        app, saves = make_app(None)

        with patch("voice_typer.server.event_bus.publish") as mock_publish:
            startup_tasks._reconcile_configured_microphone(app, [{"id": STABLE_ID}])

        assert app.config.microphone is None
        assert saves == []
        assert mock_publish.call_count == 0

    def test_legacy_id_resolving_live_device_is_migrated(self, resolver) -> None:
        resolver.resolve_to(STABLE_ID, name="USB Microphone")
        app, saves = make_app("3")  # legacy bare-index shape

        with patch("voice_typer.server.event_bus.publish") as mock_publish:
            startup_tasks._reconcile_configured_microphone(app, [{"id": STABLE_ID}])

        assert app.config.microphone == STABLE_ID
        assert saves == [True]
        assert mock_publish.call_count == 1
        evt = mock_publish.call_args[0][0]
        assert evt["data"] == {"microphone": STABLE_ID}

    def test_empty_enumeration_never_falls_back(self, resolver) -> None:
        resolver.result = None
        app, saves = make_app(STALE_ID)

        with patch("voice_typer.server.event_bus.publish") as mock_publish:
            startup_tasks._reconcile_configured_microphone(app, [])

        # A failed/empty PortAudio query is NOT evidence the device is
        # gone — the stale id must survive until a real enumeration.
        assert app.config.microphone == STALE_ID
        assert saves == []
        assert mock_publish.call_count == 0

    def test_non_string_value_is_left_alone(self, resolver) -> None:
        sentinel = object()
        app, saves = make_app(sentinel)

        with patch("voice_typer.server.event_bus.publish") as mock_publish:
            startup_tasks._reconcile_configured_microphone(app, [{"id": STABLE_ID}])

        assert app.config.microphone is sentinel
        assert saves == []
        assert mock_publish.call_count == 0

    def test_lock_used_when_present(self, resolver) -> None:
        resolver.result = None
        app, _saves = make_app(STALE_ID, with_lock=True)

        entered = []

        class TrackingLock:
            def __enter__(self):
                entered.append(True)
                return self

            def __exit__(self, *exc):
                return False

        app._config_mutation_lock = TrackingLock()

        startup_tasks._reconcile_configured_microphone(app, [{"id": "WASAPI|Other"}])
        assert entered  # mutation ran under the config lock like set_config

    def test_never_raises_on_resolver_crash(self, resolver) -> None:
        app, saves = make_app(STALE_ID)

        with (
            patch(
                "voice_typer.server.server_platform.microphone_list.find_microphone_by_id",
                side_effect=RuntimeError("portaudio exploded"),
            ),
            patch("voice_typer.server.event_bus.publish"),
        ):
            # Enumeration succeeded but resolution crashed — recovery
            # still applies (fail-safe toward System Default).
            startup_tasks._reconcile_configured_microphone(app, [{"id": STABLE_ID}])

        assert app.config.microphone is None
        assert saves == [True]


class TestLoadMicrophonesIntegration:
    def test_reconciliation_runs_inside_load_microphones(self, resolver) -> None:
        """The public startup task must reconcile BEFORE publishing
        microphones_changed / updating the tray — so no consumer ever
        sees the stale persisted value."""
        resolver.result = None
        app, saves = make_app(STALE_ID)

        with (
            patch(
                "voice_typer.server.server_platform.microphone_list.list_microphones",
                return_value=[{"id": "WASAPI|Other", "name": "Other"}],
            ),
            patch("voice_typer.server.event_bus.publish") as mock_publish,
        ):
            startup_tasks.load_microphones(app)

        assert app.config.microphone is None
        assert saves == [True]
        types = [call[0][0]["type"] for call in mock_publish.call_args_list]
        assert "config_changed" in types

    def test_reconciliation_failure_does_not_break_mic_loading(self, resolver) -> None:
        app, _saves = make_app(STALE_ID)

        def boom(_app, _mics):
            raise RuntimeError("reconciler bug")

        with (
            patch(
                "voice_typer.server.server_platform.microphone_list.list_microphones",
                return_value=[{"id": "WASAPI|Other", "name": "Other"}],
            ),
            patch.object(startup_tasks, "_reconcile_configured_microphone", boom),
        ):
            # Must not raise — enumeration/tray update still completes.
            startup_tasks.load_microphones(app)

        assert app._microphones == [{"id": "WASAPI|Other", "name": "Other"}]
