"""the mission TAURI-E2E headless checklist verification suite.

Mission (review.md entry #1): drive the REAL Python backend — the
exact code the Tauri sidecar runs — through the checklist items that are
verifiable headless via the IPC server harness, and pin every verified
contract with tests so regressions surface in CI.

Harness shape (mirrors ``tests/test_tauri_ws_microphone_population.py``):

- a REAL :class:`voice_typer.server.app.VoiceTyperApp` built with
  TAURI_SIDECAR=1 + the WS auth token (the two env vars the ws-mode
  entrypoint sets) and the autostart platform helpers stubbed;
- the REAL IPC server via :func:`voice_typer.server.providers.build_ipc_server`
  (real :class:`VoiceTyperService` over the real app) started in the
  ws-mode shape (``_tcp_mode = True``);
- real domain objects do the persistence (HistoryDB, TemplateManager,
  VocabularyManager, CorrectionUsageTracker, CrashRecovery) against the
  per-test temp config dir;
- the REAL dictation-pipeline steps (``_store_result``,
  ``_apply_vocabulary``, ``_apply_templates``) drive the dictation-shaped
  flows — the same step objects the 11-stage pipeline runs after a
  transcription;
- one test drives the full WebSocket transport (the path the Tauri host
  actually uses) through ``sidecar_ws._handle_connection``.

Mocked externals ONLY: autostart registry, the Windows icacls ACL helper
(no-op on POSIX), and the temp config dir. No audio devices, network, or
subprocess are touched by these flows.

Checklist coverage (the mission items, headless subset):

1. Config round-trip: get → set (SEC-002 allowlist) → get reflects →
   persists to ``config.json`` → ``config_changed`` push fires.
2. History persistence: dictation-shaped write via the pipeline storage
   step → get_history / search_history (FTS5) / get_today_stats; survives
   a HistoryDB reopen.
3. Templates CRUD: save_templates → get_templates round-trip; apply-to-text
   via the pipeline template step; survives manager restart.
4. Vocabulary: add → get reflects → apply_to_text replaces; bundled +
   user − deleted semantics (C-PERSIST-1: user file stores ONLY the diff +
   ``_deleted`` tombstones).
5. Export/import round-trips: the getters' data JSON-round-trips and
   re-imports to an equivalent state through the full-replace save paths
   (the server registry has no export commands — export is a Tauri-host
   Rust command consuming these getters' data).
6. Analytics counters: corrections + dictation counters move and persist
   (C-PERSIST-2: correction-usage.json is an independent file).
7. Crash recovery store: add → recovery.json → check_on_startup →
   mark_pasted lifecycle (C-PERSIST-3), including the pipeline gate on
   ``config.crash_recovery_enabled``.
8. get_status/state flow: the C-HOME-1 ``{status, message}`` tuple
   invariant at the backend level (get_status + the connect-time
   state_changed snapshot + tray_state/tray_menu pushes).
9. WS-mode startup is re-validated by running
   ``tests/test_tauri_ws_microphone_population.py`` (not duplicated here).
10. Log cleanliness: the happy-path flows emit no WARNING/ERROR records
    (pinned by ``TestLogCleanliness``).

Historical note: the headless checklist run found that ``status_change``
frames were never delivered on the WS transport (the tray-state hook
pushed them through the TCP-only ``IPCServer.push`` path). That defect
was FIXED (the hook now publishes through ``event_bus`` — see
``ipc/lifecycle.py``) and is pinned by the dedicated suite
``tests/test_tray_status_change_ws_delivery.py``. The other WS push
paths (``state_changed`` snapshot, ``tray_state``, ``tray_menu``,
``config_changed``) are pinned below.
"""

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


# ─── Harness fixture ───────────────────────────────────────────────────


@pytest.fixture
def ws_backend(tmp_config_dir, monkeypatch):
    """Real app + real IPC server in the Tauri ws-mode shape.

    Same mocking discipline as the ``ws_mode_app`` fixture in
    ``tests/test_tauri_ws_microphone_population.py`` (autostart platform
    helpers stubbed; everything else real) plus the Windows icacls no-op
    from ``tests/fixtures/app_helpers.py`` so ``Config.save()`` never
    spawns a subprocess. Teardown mirrors ``tests/app/conftest.py``'s
    ``app`` fixture (HistoryDB writer quiesced, tray elapsed-timer
    cancelled, model-load thread joined) and additionally stops the IPC
    server (unsubscribing its event-bus push fn + tray hook).
    """
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
    # with a bare list for EVERY call shape, so the system-default probe
    # (``sd.query_devices(kind="input")``) returns a list whose ``.get``
    # access fails → the device-rate fallback WARNING fires asynchronously
    # from the background device-cache prewarm and can land inside a test's
    # log-capture window (timing-dependent, host-dependent). A real OS
    # answers the default-input probe with a device dict — emulate that
    # here so the app under test sees production-like behavior and the
    # happy path stays deterministically log-clean (no raced warnings).
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
        # model-loader teardown (mirrors ``app.quit()``'s ordering) so the
        # instance's ``__del__`` flush does not race interpreter shutdown
        # (the ``_drain_crash_recovery_workers`` autouse fixture covers the
        # rest).
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
    """Collect every event published on the event bus during a test.

    The collector is registered BEFORE the flows run and removed after,
    so no state leaks between tests (the bus is module-global).
    """
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
    """Dispatch one command through the REAL registry path.

    ``IPCServer._dispatch`` is the exact entry the TCP loop, the WS
    dispatch pool, and the stdin runner all use (the WS coroutine in
    ``sidecar_ws._make_dispatch`` wraps this same method).
    """
    msg: dict = {"type": cmd}
    if data is not None:
        msg["data"] = data
    result: dict = server._dispatch(msg)
    return result


# ─── 1. Config round-trip ──────────────────────────────────────────────


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
        # all-accepted payload → plain ack (accepted/rejected echo only
        # appears when something was dropped).
        assert resp["type"] == "ack"

        after = {}
        server._handle_get_config(None, after)
        assert after["data"]["theme_mode"] == "dark"
        assert after["data"]["show_notifications"] is True

        # C-CONF-1: config.json is THE canonical store — the write must be
        # on disk, not just in memory.
        on_disk = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        assert on_disk["theme_mode"] == "dark"
        assert on_disk["show_notifications"] is True

        # The renderer is notified via the config_changed push carrying
        # the validated updates (no extra get_config round-trip needed).
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
        # unknown key is echoed as rejected, the known one as accepted.
        assert resp["data"]["rejected"] == ["not_a_real_field"]
        assert resp["data"]["accepted"] == ["theme_mode"]

        on_disk = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        assert "not_a_real_field" not in on_disk
        assert on_disk["theme_mode"] == "light"

    def test_set_config_rejects_invalid_value_atomically(self, ws_backend):
        """A type/enum violation aborts the whole payload (SEC-002)."""
        app, server = ws_backend
        config_dir = Path(app.config.config_dir)
        # establish the on-disk file with one valid save first (mirrors a
        # real settings session; a fresh install has no config.json until
        # the first save).
        assert _dispatch(server, "set_config", {"theme_mode": "light"})["type"] == "ack"
        persisted = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        assert persisted["theme_mode"] == "light"

        resp = _dispatch(server, "set_config", {"theme_mode": "not-a-theme"})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_field"
        assert resp["data"]["errors"]  # full error list, not just errors[0]

        # nothing was applied — the payload is rejected atomically, both
        # in memory and on disk.
        after = {}
        server._handle_get_config(None, after)
        assert after["data"]["theme_mode"] == "light"
        on_disk = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        assert on_disk == persisted


class TestWsTransportRoundTrip:
    """The full Tauri transport: auth → dispatch → push frames over WS."""

    @staticmethod
    async def _recv_until_id(client, request_id: int, stash: dict[str, dict], timeout: float = 5.0) -> dict:
        """Receive frames until the dispatch response with *request_id*.

        The sidecar pushes server-initiated events (``ready``,
        ``state_changed``, ``tray_menu``, ``tray_state``, ``config_changed``)
        around the dispatch response; a push frame that arrives AHEAD of
        the response is STASHED by type (not discarded) so a later
        ``_recv_type`` call for it cannot time out on a frame that was
        already consumed — the response and the push are sent from
        different threads (dispatch pool vs event-bus subscriber), so
        their wire order is not deterministic.
        """
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
        """Renderer-shaped settings flow on the exact Tauri transport.

        The Rust host connects, authenticates with the bearer token, and
        forwards ``invoke('dispatch', ...)`` envelopes. A set_config must
        (1) get its dispatch response with the numeric id echoed and
        (2) ALSO deliver the ``config_changed`` push frame to the SAME
        connection (the renderer's App.tsx updates UI-local state from
        that push, per C-CONF-1).
        """
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
                    # vs the dispatch response is not deterministic — the
                    # stash covers the push-first interleaving).
                    push = await self._recv_type(client, "config_changed", stash)
                    assert push["data"]["theme_mode"] == "dark"

                    await client.send(json.dumps({"type": "get_config", "data": {}, "id": 202}))
                    cfg = await self._recv_until_id(client, 202, stash)
                    assert cfg["type"] == "config"
                    assert cfg["data"]["theme_mode"] == "dark"

        asyncio.run(_run())
        # persisted through the WS path too
        on_disk = json.loads((Path(app.config.config_dir) / "config.json").read_text(encoding="utf-8"))
        assert on_disk["theme_mode"] == "dark"


# ─── 2. History persistence ────────────────────────────────────────────


class TestHistoryPersistence:
    """Dictation-shaped history writes are queryable and durable."""

    def test_storage_step_writes_queryable_history(self, ws_backend, bus_events):
        """The REAL pipeline Step 8 writes history + fires the UI push.

        ``DictationPipeline._store_result`` is the exact code the dictation
        flow runs after transcription: history DB write (gated on
        ``history_enabled``), crash-recovery buffer, dictation-usage
        counter, and the ``transcription_final`` push that makes
        Home/Dashboard/History refresh proactively.
        """
        app, server = ws_backend
        from voice_typer.server.dictation_pipeline import DictationPipeline

        text = "the quick brown fox jumps over the lazy dog"
        pipeline = DictationPipeline(app)
        pipeline._store_result(text)

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
        # the export-visible row shape (pinned for the Rust export command)
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

        # today's stats (the Dashboard's "today" cards).
        stats = {}
        server._handle_get_today_stats(None, stats)
        assert stats["data"]["count"] == 1
        assert stats["data"]["chars"] == len(text)
        assert stats["data"]["word_count"] == len(text.split())

        # the renderer refresh push fired.
        assert any(e["type"] == "transcription_final" for e in bus_events)

    def test_history_survives_historydb_reopen(self, ws_backend):
        """A fresh HistoryDB over the same file re-reads committed rows.

        Simulates the app restart: the writer thread of the first instance
        is closed (its WAL checkpointed), a new instance opens the same
        ``history.db`` and must serve the previously written row — and its
        FTS index must still answer content queries.
        """
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


# ─── 3. Templates CRUD ─────────────────────────────────────────────────


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

        # persisted to the config dir (survives app-data resets — the
        # documented reason the store lives in templates.json).
        on_disk = json.loads((Path(app.config.config_dir) / "templates.json").read_text(encoding="utf-8"))
        assert len(on_disk["templates"]) == 2

        # apply-to-text through the REAL dictation pipeline Step 5.
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


# ─── 4. Vocabulary ─────────────────────────────────────────────────────


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

        # get_vocabulary reflects the user entry.
        after = {}
        server._handle_get_vocabulary(None, after)
        assert after["data"]["misspellings"]["teh"] == "the"

        # C-PERSIST-1: the user file stores ONLY the diff vs bundled —
        # bundled entries are not duplicated into vocabulary.json.
        user_file = json.loads((Path(app.config.config_dir) / "vocabulary.json").read_text(encoding="utf-8"))
        assert user_file["misspellings"] == {"teh": "the"}

        # apply_to_text replaces occurrences (the dictation path step 4).
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

        # merged view no longer contains the deleted bundled entry
        after = {}
        server._handle_get_vocabulary(None, after)
        assert bundled_key not in after["data"]["misspellings"]

        # the tombstone is persisted so the deletion survives reloads
        user_file = json.loads((Path(app.config.config_dir) / "vocabulary.json").read_text(encoding="utf-8"))
        assert bundled_key in user_file["_deleted"]["misspellings"]

        # a fresh manager (restart) applies the tombstone too
        from voice_typer.server.vocabulary import VocabularyManager

        fresh = VocabularyManager(config_dir=Path(app.config.config_dir))
        assert bundled_key not in fresh.get_all()["misspellings"]
        assert fresh.apply_to_text(bundled_key, track_usage=False) == bundled_key


# ─── 5. Export/import round-trips ──────────────────────────────────────


class TestExportImportRoundTrips:
    """Export shapes re-import to an equivalent state.

    The Tauri host's export commands (export_history / export_vocabulary /
    export_templates — Rust, src-tauri/src/commands/export.rs) write the
    JSON the renderer passes them, sourced from these Python getters. The
    import side on Python is the full-replace save path (save_templates /
    save_vocabulary full merged list), so a round-trip here proves the
    export payload is lossless for the writable domains.
    """

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
        # export bytes: JSON round-trip is valid
        wire = json.loads(json.dumps(exported["data"]))

        # re-import through the same write path the renderer's import
        # hook uses (full replace)
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

        # re-import the exported (minus the _user_file meta key, which is
        # a renderer-side display field, not vocabulary data)
        reimport_payload = {k: v for k, v in wire.items() if not k.startswith("_")}
        resp2 = _dispatch(server, "save_vocabulary", reimport_payload)
        assert resp2["type"] == "ack"

        again = {}
        server._handle_get_vocabulary(None, again)
        assert {k: v for k, v in again["data"].items() if not k.startswith("_")} == reimport_payload

    def test_history_export_shape_is_valid_json(self, ws_backend):
        """No server-side history import exists (single-record restore
        only) — pin the export shape instead: rows JSON-round-trip and
        carry the fields the Rust export + History page rely on."""
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


# ─── 6. Analytics counters ─────────────────────────────────────────────


class TestAnalyticsCounters:
    """Corrections/dictation counters move + persist (C-PERSIST-2)."""

    def test_usage_counters_update_and_files_stay_independent(self, ws_backend):
        app, server = ws_backend
        config_dir = Path(app.config.config_dir)

        # seed a user correction the dictation path will fire
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

        # one completed dictation (the rate's denominator)
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
        # vocabulary.json (never merged).
        assert (config_dir / "correction-usage.json").is_file()
        assert (config_dir / "vocabulary.json").is_file()
        usage_disk = json.loads((config_dir / "correction-usage.json").read_text(encoding="utf-8"))
        assert "misspellings" not in usage_disk  # it lives under "entries"
        assert usage_disk["entries"]["misspellings"]["recieve"]["count"] == 1

        # a fresh tracker (app restart) reads the flushed file
        from voice_typer.server.correction_usage import CorrectionUsageTracker

        fresh = CorrectionUsageTracker(config_dir)
        assert fresh.get_snapshot()["entries"]["misspellings"]["recieve"]["count"] == 1

    def test_vocabulary_preview_does_not_inflate_usage(self, ws_backend):
        """The "Test corrections" panel must not move real usage numbers."""
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


# ─── 7. Crash recovery store ───────────────────────────────────────────


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

        # next session: a fresh CrashRecovery over the same file reports
        # ONLY the unpasted entry to the user.
        from voice_typer.server.crash_recovery import CrashRecovery

        next_session = CrashRecovery(config_dir=config_dir)
        try:
            recovered = next_session.check_on_startup()
            assert recovered is not None
            assert [e["text"] for e in recovered] == ["unpasted transcription one"]

            # after the user pastes it, the startup check is clean.
            assert next_session.mark_pasted(0) is True
            assert next_session.check_on_startup() is None
        finally:
            next_session.shutdown()

    def test_pipeline_gate_on_crash_recovery_enabled(self, ws_backend):
        """The dictation pipeline writes recovery only when the config
        gate is on (the documented C-PERSIST-3 add() gate)."""
        app, server = ws_backend
        from voice_typer.server.dictation_pipeline import DictationPipeline

        pipeline = DictationPipeline(app)
        app.config.crash_recovery_enabled = True
        pipeline._store_result("gated recovery text")
        app._crash_recovery.flush(timeout=1.0)
        disk = json.loads((Path(app.config.config_dir) / "recovery.json").read_text(encoding="utf-8"))
        assert any(e["text"] == "gated recovery text" for e in disk["entries"])

        # gate OFF → the buffer does not grow.
        app.config.crash_recovery_enabled = False
        before = app._crash_recovery.count
        pipeline._store_result("not stored while disabled")
        assert app._crash_recovery.count == before


# ─── 8. get_status / state flow ────────────────────────────────────────


class TestStatusFlow:
    """C-HOME-1 tuple invariant + the pushes the WS path delivers.

    Historical note: the checklist run found ``status_change`` never
    reached the WS client (TCP-only push) — FIXED (the tray hook now
    publishes through ``event_bus``; pinned by
    tests/test_tray_status_change_ws_delivery.py). The delivery paths
    below pin the WS-transport behavior that was always healthy.
    """

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
        # the full get_status contract (consumed by the renderer's
        # initial probe + background health poll)
        assert set(data) >= {
            "status",
            "message",
            "xruns_since_start",
            "loaded_via",
            "config_dir",
            "offline_pack",
        }

        # The connect-time snapshot the WS transport publishes on every
        # authenticated connection must carry the SAME {status, message}
        # pair — the C-HOME-1 backend-level invariant.
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
        """TAURI_SIDECAR=1: every tray transition publishes ``tray_state``
        (icon + tooltip for the host tray) and the menu push on the label
        visibility flip — both flow to the WS host via the event bus."""
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


# ─── 10. Log cleanliness ───────────────────────────────────────────────


class TestLogCleanliness:
    """checklist item 16: the happy-path flows leave no WARNING/ERROR records.

    Environmental noise from the test harness itself (fake sounddevice
    fallbacks, autostart stubs) is excluded via the logger allowlist —
    only production loggers are asserted.
    """

    def test_happy_path_flows_are_log_clean(self, ws_backend, caplog):
        app, server = ws_backend
        with caplog.at_level(logging.WARNING):
            # Snapshot the records that already exist (fixture setup —
            # app construction probes the (fake) default device and can
            # legitimately warn in a headless sandbox). Only records
            # emitted by the flows below are asserted.
            setup_len = len(caplog.records)

            # config round-trip
            _dispatch(server, "set_config", {"theme_mode": "dark"})
            # history write via the real pipeline step
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
