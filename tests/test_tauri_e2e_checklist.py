"""the mission TAURI-E2E headless checklist verification suite."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path

import pytest

_AUTOSTART = "voice_typer.server.server_platform.autostart"
_AUTH_TOKEN = "test-token-" + "a" * 32


@pytest.fixture
def ws_backend(tmp_config_dir, monkeypatch):
    """Real app + real IPC server in the Tauri ws-mode shape."""
    monkeypatch.setattr(f"{_AUTOSTART}.is_autostart_enabled", lambda: False, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.enable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.disable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(
        "voice_typer.server.config._enforce_windows_owner_only_acl",
        lambda *a, **k: None,
    )
    monkeypatch.setenv("TAURI_SIDECAR", "1")
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _AUTH_TOKEN)

    # The autouse ``mock_heavy_imports`` stub answers ``sd.query_devices``
    import sys as _sys

    _mock_sd = _sys.modules.get("sounddevice")
    if _mock_sd is not None:

        def _fake_query_devices(device=None, kind=None):
            if kind == "input":
                return {
                    "name": "Checklist Default Input",
                    "hostapi": 0,
                    "default_samplerate": 16000.0,
                    "max_input_channels": 1,
                }
            return []

        monkeypatch.setattr(_mock_sd, "query_devices", _fake_query_devices, raising=False)

    from voice_typer.server.app import VoiceTyperApp
    from voice_typer.server.providers import build_ipc_server

    app = VoiceTyperApp()
    server = build_ipc_server(app)
    server._tcp_mode = True
    server.start()
    try:
        yield app, server
    finally:
        server.stop()
        # Quiesce the crash-recovery save worker BEFORE the HistoryDB /
        with contextlib.suppress(Exception):
            app._crash_recovery.shutdown()
        with contextlib.suppress(Exception):
            if app.history_db is not None:
                app.history_db.close()
        with contextlib.suppress(Exception):
            app.tray._cancel_elapsed_timer()
        loader = getattr(app.models, "_model_load_thread", None)
        if loader is not None and loader.is_alive():
            loader.join(timeout=2.0)


@pytest.fixture
def bus_events():
    """Collect every event published on the event bus during a test."""
    from voice_typer.server import event_bus

    events: list[dict] = []

    def _collect(event: dict) -> None:
        events.append(event)

    event_bus.subscribe(_collect)
    try:
        yield events
    finally:
        event_bus.unsubscribe(_collect)


def _dispatch(server, cmd: str, data: object = None) -> dict:
    """Dispatch one command through the REAL registry path."""
    msg: dict = {"type": cmd}
    if data is not None:
        msg["data"] = data
    result: dict = server._dispatch(msg)
    return result


class TestConfigRoundTrip:
    """get_config → set_config → get_config → disk → config_changed push."""

    def test_set_config_reflects_persists_and_pushes(self, ws_backend, bus_events):
        app, server = ws_backend
        config_dir = Path(app.config.config_dir)

        before = {}
        server._handle_get_config(None, before)
        assert before["type"] == "config"
        assert before["data"]["theme_mode"] != "dark"

        resp = _dispatch(server, "set_config", {"theme_mode": "dark", "show_notifications": True})
        assert resp["type"] == "ack"

        after = {}
        server._handle_get_config(None, after)
        assert after["data"]["theme_mode"] == "dark"
        assert after["data"]["show_notifications"] is True

        # C-CONF-1: config.json is THE canonical store, the write must be
        on_disk = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        assert on_disk["theme_mode"] == "dark"
        assert on_disk["show_notifications"] is True

        # The renderer is notified via the config_changed push carrying
        pushes = [e for e in bus_events if e["type"] == "config_changed"]
        assert len(pushes) == 1
        assert pushes[0]["data"]["theme_mode"] == "dark"
        assert pushes[0]["data"]["show_notifications"] is True

    def test_set_config_drops_unknown_fields(self, ws_backend, tmp_path):
        """SEC-002: keys outside ``IPC_CONFIG_ALLOWLIST`` never apply."""
        app, server = ws_backend
        config_dir = Path(app.config.config_dir)

        resp = _dispatch(
            server,
            "set_config",
            {"theme_mode": "light", "not_a_real_field": 1},
        )
        assert resp["type"] == "ack"
        assert resp["data"]["rejected"] == ["not_a_real_field"]
        assert resp["data"]["accepted"] == ["theme_mode"]

        on_disk = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        assert "not_a_real_field" not in on_disk
        assert on_disk["theme_mode"] == "light"

    def test_set_config_rejects_invalid_value_atomically(self, ws_backend):
        """A type/enum violation aborts the whole payload (SEC-002)."""
        app, server = ws_backend
        config_dir = Path(app.config.config_dir)
        assert _dispatch(server, "set_config", {"theme_mode": "light"})["type"] == "ack"
        persisted = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        assert persisted["theme_mode"] == "light"

        resp = _dispatch(server, "set_config", {"theme_mode": "not-a-theme"})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_field"
        assert resp["data"]["errors"]  # full error list, not just errors[0]

        after = {}
        server._handle_get_config(None, after)
        assert after["data"]["theme_mode"] == "light"
        on_disk = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        assert on_disk == persisted


class TestWsTransportRoundTrip:
    """The full Tauri transport: auth → dispatch → push frames over WS."""

    @staticmethod
    async def _recv_until_id(client, request_id: int, stash: dict[str, dict], timeout: float = 5.0) -> dict:
        """Receive frames until the dispatch response with *request_id*."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"no response frame with id={request_id}")
            raw = await asyncio.wait_for(client.recv(), timeout=remaining)
            frame = json.loads(raw)
            if isinstance(frame, dict) and frame.get("id") == request_id:
                return frame
            if isinstance(frame, dict) and "id" not in frame:
                stash.setdefault(frame.get("type", ""), frame)

    @staticmethod
    async def _recv_type(client, event_type: str, stash: dict[str, dict], timeout: float = 5.0) -> dict:
        """Receive a server-initiated event of *event_type* (stash first)."""
        if event_type in stash:
            return stash.pop(event_type)
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"no push frame of type {event_type}")
            raw = await asyncio.wait_for(client.recv(), timeout=remaining)
            frame = json.loads(raw)
            if isinstance(frame, dict) and frame.get("type") == event_type:
                return frame

    def test_config_round_trip_and_push_over_ws(self, ws_backend):
        """Renderer-shaped settings flow on the exact Tauri transport."""
        app, server = ws_backend
        websockets = pytest.importorskip("websockets")
        from voice_typer.server import sidecar_ws

        dispatch = sidecar_ws._make_dispatch(server)

        async def _handler(ws):
            await sidecar_ws._handle_connection(ws, server, dispatch)

        async def _run() -> None:
            import websockets.asyncio.server as ws_server

            stash: dict[str, dict] = {}
            async with ws_server.serve(_handler, "127.0.0.1", 0) as srv:
                port = next(iter(srv.sockets)).getsockname()[1]
                async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
                    await client.send(json.dumps({"type": "auth", "token": _AUTH_TOKEN}))

                    # ready is the FIRST post-auth frame (C-WS-1).
                    ready = json.loads(await asyncio.wait_for(client.recv(), timeout=5.0))
                    assert ready["type"] == "ready"

                    await client.send(
                        json.dumps(
                            {
                                "type": "set_config",
                                "data": {"theme_mode": "dark"},
                                "id": 201,
                            }
                        )
                    )
                    resp = await self._recv_until_id(client, 201, stash)
                    assert resp["type"] == "ack"
                    assert resp["id"] == 201

                    # The push frame must arrive on this connection (order
                    push = await self._recv_type(client, "config_changed", stash)
                    assert push["data"]["theme_mode"] == "dark"

                    await client.send(json.dumps({"type": "get_config", "data": {}, "id": 202}))
                    cfg = await self._recv_until_id(client, 202, stash)
                    assert cfg["type"] == "config"
                    assert cfg["data"]["theme_mode"] == "dark"

        asyncio.run(_run())
        on_disk = json.loads((Path(app.config.config_dir) / "config.json").read_text(encoding="utf-8"))
        assert on_disk["theme_mode"] == "dark"


class TestHistoryPersistence:
    """Dictation-shaped history writes are queryable and durable."""

    def test_storage_step_writes_queryable_history(self, ws_backend, bus_events):
        """The REAL pipeline Step 8 writes history + fires the UI push."""
        app, server = ws_backend
        from voice_typer.server.dictation_pipeline import DictationPipeline

        text = "the quick brown fox jumps over the lazy dog"
        pipeline = DictationPipeline(app)
        pipeline._store_result(text)
        app.history_db.flush()

        resp = {}
        server._handle_get_history({}, resp)
        assert resp["type"] == "history"
        rows = resp["data"]
        assert len(rows) == 1
        row = rows[0]
        assert row["text"] == text
        assert row["text_truncated"] is False
        assert row["model"] == app.config.model_size
        assert row["timestamp"]
        assert set(row) >= {
            "id",
            "text",
            "timestamp",
            "duration",
            "model",
            "device",
            "language",
            "favorite",
            "word_count",
            "char_count",
        }

        # FTS5 search finds the row by content.
        search = {}
        server._handle_search_history({"query": "quick brown"}, search)
        assert search["type"] == "history"
        assert [r["id"] for r in search["data"]] == [row["id"]]
        no_match = {}
        server._handle_search_history({"query": "zzz-no-such-words"}, no_match)
        assert no_match["data"] == []

        stats = {}
        server._handle_get_today_stats(None, stats)
        assert stats["data"]["count"] == 1
        assert stats["data"]["chars"] == len(text)
        assert stats["data"]["word_count"] == len(text.split())

        assert any(e["type"] == "transcription_final" for e in bus_events)

    def test_history_survives_historydb_reopen(self, ws_backend):
        """A fresh HistoryDB over the same file re-reads committed rows."""
        app, server = ws_backend
        text = "restart durability probe sentence"
        app.history_db.add_transcription(text, duration=1.5, model="tiny", device="cpu")
        app.history_db.flush()
        first_rows = app.history_db.get_recent(10, 0)
        assert len(first_rows) == 1
        app.history_db.close()

        from voice_typer.server.history_db import HistoryDB

        reopened = HistoryDB()
        try:
            rows = reopened.get_recent(10, 0)
            assert len(rows) == 1
            assert rows[0]["text"] == text
            found = reopened.search("durability probe", 10, 0)
            assert len(found) == 1
            assert found[0]["id"] == rows[0]["id"]
        finally:
            reopened.close()


class TestTemplatesCrud:
    """save_templates → get_templates round-trip + dictation apply."""

    def _save(self, server, templates: list[dict]) -> dict:
        resp = _dispatch(server, "save_templates", {"templates": templates})
        assert resp["type"] == "ack"
        return resp

    def test_templates_round_trip_and_apply(self, ws_backend):
        app, server = ws_backend
        self._save(
            server,
            [
                {"trigger": "my sig", "output": "Best regards, Alex", "match_mode": "exact"},
                {"trigger": "ticket id", "output": "TICKET-42", "match_mode": "contains"},
            ],
        )

        resp = {}
        server._handle_get_templates(None, resp)
        assert resp["type"] == "templates"
        got = resp["data"]["templates"]
        assert {(t["trigger"], t["output"], t["match_mode"]) for t in got} == {
            ("my sig", "Best regards, Alex", "exact"),
            ("ticket id", "TICKET-42", "contains"),
        }

        on_disk = json.loads((Path(app.config.config_dir) / "templates.json").read_text(encoding="utf-8"))
        assert len(on_disk["templates"]) == 2

        from voice_typer.server.dictation_pipeline import DictationPipeline

        pipeline = DictationPipeline(app)
        assert pipeline._apply_templates("My SIG") == "Best regards, Alex"
        assert pipeline._apply_templates("here is the ticket id again") == "TICKET-42"
        assert pipeline._apply_templates("no template here") == "no template here"

    def test_templates_survive_manager_restart(self, ws_backend):
        app, server = ws_backend
        self._save(server, [{"trigger": "addr", "output": "42 Main Street", "match_mode": "exact"}])

        from voice_typer.server.templates import TemplateManager

        fresh = TemplateManager()
        assert fresh.match("addr") == "42 Main Street"


class TestVocabularyFlow:
    """Add → get reflects → apply_to_text (C-PERSISthe mission semantics)."""

    @staticmethod
    def _save(server, payload: dict) -> dict:
        resp = _dispatch(server, "save_vocabulary", payload)
        assert resp["type"] == "ack"
        return resp

    def test_user_entry_applies_and_persists_as_diff(self, ws_backend):
        app, server = ws_backend
        # Read the full merged payload the renderer round-trips...
        resp = {}
        server._handle_get_vocabulary(None, resp)
        assert resp["type"] == "vocabulary"
        base = resp["data"]
        assert "misspellings" in base  # bundled defaults are present
        assert "teh" not in base["misspellings"]

        # ...add a user entry, save the FULL merged list back.
        payload = {k: v for k, v in base.items() if not k.startswith("_")}
        payload["misspellings"] = dict(payload["misspellings"])
        payload["misspellings"]["teh"] = "the"
        self._save(server, payload)

        after = {}
        server._handle_get_vocabulary(None, after)
        assert after["data"]["misspellings"]["teh"] == "the"

        # C-PERSIST-1: the user file stores ONLY the diff vs bundled —
        user_file = json.loads((Path(app.config.config_dir) / "vocabulary.json").read_text(encoding="utf-8"))
        assert user_file["misspellings"] == {"teh": "the"}

        from voice_typer.server.dictation_pipeline import DictationPipeline

        pipeline = DictationPipeline(app)
        assert pipeline._apply_vocabulary("i write teh code") == "i write the code"

    def test_bundled_entry_deletion_persists_tombstone(self, ws_backend):
        app, server = ws_backend
        resp = {}
        server._handle_get_vocabulary(None, resp)
        base = resp["data"]
        bundled_key = next(iter(base["misspellings"]))
        bundled_fix = base["misspellings"][bundled_key]

        # The bundled correction applies before the deletion.
        from voice_typer.server.dictation_pipeline import DictationPipeline

        pipeline = DictationPipeline(app)
        assert pipeline._apply_vocabulary(bundled_key) == bundled_fix

        # Delete it: renderer sends the FULL merged list minus the entry.
        payload = {k: v for k, v in base.items() if not k.startswith("_")}
        payload["misspellings"] = {k: v for k, v in base["misspellings"].items() if k != bundled_key}
        self._save(server, payload)

        after = {}
        server._handle_get_vocabulary(None, after)
        assert bundled_key not in after["data"]["misspellings"]

        user_file = json.loads((Path(app.config.config_dir) / "vocabulary.json").read_text(encoding="utf-8"))
        assert bundled_key in user_file["_deleted"]["misspellings"]

        from voice_typer.server.vocabulary import VocabularyManager

        fresh = VocabularyManager(config_dir=Path(app.config.config_dir))
        assert bundled_key not in fresh.get_all()["misspellings"]
        assert fresh.apply_to_text(bundled_key, track_usage=False) == bundled_key


class TestExportImportRoundTrips:
    """Export shapes re-import to an equivalent state."""

    def test_templates_export_reimports_equivalent(self, ws_backend):
        app, server = ws_backend
        _dispatch(
            server,
            "save_templates",
            {
                "templates": [
                    {"trigger": "sig", "output": "Best regards", "match_mode": "exact"},
                ]
            },
        )
        exported = {}
        server._handle_get_templates(None, exported)
        wire = json.loads(json.dumps(exported["data"]))

        resp = _dispatch(server, "save_templates", wire)
        assert resp["type"] == "ack"

        reimported = {}
        server._handle_get_templates(None, reimported)
        assert reimported["data"] == exported["data"]

    def test_vocabulary_export_reimports_equivalent(self, ws_backend):
        app, server = ws_backend
        resp = {}
        server._handle_get_vocabulary(None, resp)
        payload = {k: v for k, v in resp["data"].items() if not k.startswith("_")}
        payload["misspellings"] = dict(payload["misspellings"])
        payload["misspellings"]["definately-x"] = "definitely"
        _dispatch(server, "save_vocabulary", payload)

        exported = {}
        server._handle_get_vocabulary(None, exported)
        wire = json.loads(json.dumps(exported["data"]))

        reimport_payload = {k: v for k, v in wire.items() if not k.startswith("_")}
        resp2 = _dispatch(server, "save_vocabulary", reimport_payload)
        assert resp2["type"] == "ack"

        again = {}
        server._handle_get_vocabulary(None, again)
        assert {k: v for k, v in again["data"].items() if not k.startswith("_")} == reimport_payload

    def test_history_export_shape_is_valid_json(self, ws_backend):
        """No server-side history import exists (single-record restore"""
        app, server = ws_backend
        app.history_db.add_transcription("export shape row", duration=2.0, model="tiny")
        app.history_db.flush()

        resp = {}
        server._handle_get_history({}, resp)
        rows = resp["data"]
        assert rows
        wire = json.loads(json.dumps(rows))
        assert wire == rows
        for row in wire:
            assert isinstance(row["id"], int)
            assert isinstance(row["text"], str)
            assert isinstance(row["timestamp"], str)
            assert isinstance(row["favorite"], int)


class TestAnalyticsCounters:
    """Corrections/dictation counters move + persist (C-PERSIST-2)."""

    def test_usage_counters_update_and_files_stay_independent(self, ws_backend):
        app, server = ws_backend
        config_dir = Path(app.config.config_dir)

        resp = {}
        server._handle_get_vocabulary(None, resp)
        payload = {k: v for k, v in resp["data"].items() if not k.startswith("_")}
        payload["misspellings"] = dict(payload["misspellings"])
        payload["misspellings"]["recieve"] = "receive"
        _dispatch(server, "save_vocabulary", payload)

        from voice_typer.server.dictation_pipeline import DictationPipeline

        pipeline = DictationPipeline(app)
        out = pipeline._apply_vocabulary("please recieve the package")
        assert out == "please receive the package"

        app.correction_usage.record_dictation()

        usage = {}
        server._handle_get_correction_usage(None, usage)
        assert usage["type"] == "correction_usage"
        data = usage["data"]
        entry = data["entries"]["misspellings"]["recieve"]
        assert entry["count"] == 1
        assert entry["last_ts"] > 0
        today = time.strftime("%Y-%m-%d")
        assert data["corrections_by_day"][today] == 1
        assert data["dictations_by_day"][today] == 1

        # C-PERSIST-2: correction-usage.json is an INDEPENDENT file from
        assert (config_dir / "correction-usage.json").is_file()
        assert (config_dir / "vocabulary.json").is_file()
        usage_disk = json.loads((config_dir / "correction-usage.json").read_text(encoding="utf-8"))
        assert "misspellings" not in usage_disk  # it lives under "entries"
        assert usage_disk["entries"]["misspellings"]["recieve"]["count"] == 1

        from voice_typer.server.correction_usage import CorrectionUsageTracker

        fresh = CorrectionUsageTracker(config_dir)
        assert fresh.get_snapshot()["entries"]["misspellings"]["recieve"]["count"] == 1

    def test_vocabulary_preview_does_not_inflate_usage(self, ws_backend):
        """The \"Test corrections\" panel must not move real usage numbers."""
        app, server = ws_backend
        resp = {}
        server._handle_get_vocabulary(None, resp)
        payload = {k: v for k, v in resp["data"].items() if not k.startswith("_")}
        payload["misspellings"] = dict(payload["misspellings"])
        payload["misspellings"]["seperate"] = "separate"
        _dispatch(server, "save_vocabulary", payload)

        before = {}
        server._handle_get_correction_usage(None, before)

        preview = {}
        server._handle_test_vocabulary_correction({"text": "seperate items"}, preview)
        assert preview["type"] == "ack"
        assert preview["data"]["output"] == "separate items"
        assert preview["data"]["applied"] is True

        after = {}
        server._handle_get_correction_usage(None, after)
        assert after["data"]["entries"].get("misspellings", {}).get("seperate") is None


class TestCrashRecoveryStore:
    """recovery.json add/check_on_startup lifecycle (C-PERSIST-3)."""

    def test_recovery_lifecycle(self, ws_backend):
        app, server = ws_backend
        config_dir = Path(app.config.config_dir)

        app._crash_recovery.add("unpasted transcription one", pasted=False)
        app._crash_recovery.add("pasted transcription two", pasted=True)
        app._crash_recovery.flush(timeout=1.0)

        disk = json.loads((config_dir / "recovery.json").read_text(encoding="utf-8"))
        assert {e["text"] for e in disk["entries"]} == {
            "unpasted transcription one",
            "pasted transcription two",
        }

        from voice_typer.server.crash_recovery import CrashRecovery

        next_session = CrashRecovery(config_dir=config_dir)
        try:
            recovered = next_session.check_on_startup()
            assert recovered is not None
            assert [e["text"] for e in recovered] == ["unpasted transcription one"]

            assert next_session.mark_pasted(0) is True
            assert next_session.check_on_startup() is None
        finally:
            next_session.shutdown()

    def test_pipeline_gate_on_crash_recovery_enabled(self, ws_backend):
        """The dictation pipeline writes recovery only when the config"""
        app, server = ws_backend
        from voice_typer.server.dictation_pipeline import DictationPipeline

        pipeline = DictationPipeline(app)
        app.config.crash_recovery_enabled = True
        pipeline._store_result("gated recovery text")
        app._crash_recovery.flush(timeout=1.0)
        disk = json.loads((Path(app.config.config_dir) / "recovery.json").read_text(encoding="utf-8"))
        assert any(e["text"] == "gated recovery text" for e in disk["entries"])

        app.config.crash_recovery_enabled = False
        before = app._crash_recovery.count
        pipeline._store_result("not stored while disabled")
        assert app._crash_recovery.count == before


class TestStatusFlow:
    """C-HOME-1 tuple invariant + the pushes the WS path delivers."""

    def test_get_status_and_state_changed_carry_same_tuple(self, ws_backend, bus_events):
        app, server = ws_backend
        from voice_typer.server.tray_types import AppState

        app.tray.set_state(AppState.ERROR, "No speech model is selected.")

        status = {}
        server._handle_get_status(None, status)
        assert status["type"] == "status"
        data = status["data"]
        assert data["status"] == "error"
        assert data["message"] == "No speech model is selected."
        assert set(data) >= {
            "status",
            "message",
            "xruns_since_start",
            "loaded_via",
            "config_dir",
            "offline_pack",
        }

        # pair, the C-HOME-1 backend-level invariant.
        from voice_typer.server.sidecar_ws_internals.connection import (
            _emit_initial_state_snapshot,
        )

        bus_events.clear()
        _emit_initial_state_snapshot(server)
        snapshots = [e for e in bus_events if e["type"] == "state_changed"]
        assert len(snapshots) == 1
        assert snapshots[0]["data"]["status"] == data["status"]
        assert snapshots[0]["data"]["message"] == data["message"]

    def test_tray_state_and_menu_reach_the_bus_in_tauri_mode(self, ws_backend, bus_events):
        """TAURI_SIDECAR=1: every tray transition publishes ``tray_state``"""
        app, server = ws_backend
        from voice_typer.server.tray_types import AppState

        bus_events.clear()
        app.tray.set_state(AppState.RECORDING, "dictating")
        tray_state = [e for e in bus_events if e["type"] == "tray_state"]
        assert tray_state
        assert tray_state[0]["data"]["icon"] == "recording"
        assert "dictating" in tray_state[0]["data"]["tooltip"]
        # RECORDING enter flips the menu labels (Start → Stop Dictation).
        assert any(e["type"] == "tray_menu" for e in bus_events)


class TestLogCleanliness:
    """checklist item 16: the happy-path flows leave no WARNING/ERROR records."""

    def test_happy_path_flows_are_log_clean(self, ws_backend, caplog):
        app, server = ws_backend
        with caplog.at_level(logging.WARNING):
            # Snapshot the records that already exist (fixture setup —
            setup_len = len(caplog.records)

            # config round-trip
            _dispatch(server, "set_config", {"theme_mode": "dark"})
            from voice_typer.server.dictation_pipeline import DictationPipeline

            pipeline = DictationPipeline(app)
            pipeline._store_result("log cleanliness probe text")
            # templates
            _dispatch(
                server,
                "save_templates",
                {"templates": [{"trigger": "sig", "output": "regards", "match_mode": "exact"}]},
            )
            server._handle_get_templates(None, {})
            # vocabulary + usage
            resp = {}
            server._handle_get_vocabulary(None, resp)
            payload = {k: v for k, v in resp["data"].items() if not k.startswith("_")}
            payload["misspellings"] = dict(payload["misspellings"])
            payload["misspellings"]["teh"] = "the"
            _dispatch(server, "save_vocabulary", payload)
            pipeline._apply_vocabulary("fix teh word")
            app.correction_usage.record_dictation()
            server._handle_get_correction_usage(None, {})
            # recovery
            app._crash_recovery.add("log probe recovery", pasted=False)
            app._crash_recovery.flush(timeout=1.0)
            # status
            from voice_typer.server.tray_types import AppState

            app.tray.set_state(AppState.RECORDING, "probe")
            server._handle_get_status(None, {})

        offenders = [
            r for r in caplog.records[setup_len:] if r.levelno >= logging.WARNING and r.name.startswith("voice_typer.")
        ]
        assert offenders == [], "\n".join(f"{r.levelname} {r.name}: {r.message}" for r in offenders)
