"""FIFO queue for concurrent model-download requests.

User decision (2026-09-08): a second download request arriving while a
gateable download is active (possibly paused) is QUEUED behind it
instead of being refused with an error. Transfers stay serialized
through the existing single-flight gate, the queue is the missing UX
layer, not a parallel-transfer mechanism.

Contract pinned here:

1. ``_download_whisper_family`` / ``_download_parakeet`` enqueue (and
   return a queued outcome) when the transfer gate is armed, they must
   NOT touch HuggingFace while queued.
2. The queue holds model NAMES only; duplicate enqueues are idempotent
   (same position, single entry).
3. Queued state reaches the renderer through the existing
   ``download_progress`` events plus one new ``queue_position`` field.
4. Cancel-anywhere: ``cancel_model_download(model_name)`` removes a
   model from the QUEUE without touching the active transfer; the
   no-argument form keeps its legacy semantics (cancel the ACTIVE
   transfer only, the queue drains on).
5. When the active transfer exits (success / failure / cancel), the
   next queued request auto-starts, but ONLY once the gate is free.
"""

from __future__ import annotations

import threading

import pytest
from voice_typer.server.asr_setup import (
    clear_download_pause_state,
    is_download_active,
    reset_download_pause_state,
)


@pytest.fixture(autouse=True)
def _fresh_download_state():
    """Isolate the module-level pause/abort events per test."""
    reset_download_pause_state()
    yield
    clear_download_pause_state()


def _make_service(tmp_config_dir):
    """Minimal VoiceTyperService (same pattern as the sibling download
    tests in this package)."""
    from unittest.mock import MagicMock

    from voice_typer.server import service as svc_mod

    class FakeApp:
        config = type("FakeConfig", (), {})()
        _microphones: list = []
        tray = MagicMock()

    return svc_mod.VoiceTyperService(FakeApp())


def _capture_progress_events(monkeypatch) -> list[dict]:
    """Record every ``event_bus.publish`` payload for later asserts."""
    from voice_typer.server import event_bus

    published: list[dict] = []
    monkeypatch.setattr(event_bus, "publish", lambda evt: published.append(evt))
    return published


def _queued_events_for(published: list[dict], model: str) -> list[dict]:
    """All download_progress event data dicts for ``model`` that carry a
    queue_position field."""
    return [
        evt["data"]
        for evt in published
        if evt.get("type") == "download_progress"
        and evt["data"].get("model") == model
        and "queue_position" in evt["data"]
    ]


class TestGuardQueuesInsteadOfRefusing:
    """The single-flight guard ENQUEUES the second request (the old
    behaviour (refusing with ``download_already_active``) is replaced
    by the queue)."""

    def test_whisper_branch_queues_second_download(self, tmp_config_dir, monkeypatch):
        from unittest.mock import MagicMock

        monkeypatch.setattr(
            "huggingface_hub.snapshot_download",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("queued download must not reach HuggingFace")),
        )
        published = _capture_progress_events(monkeypatch)
        svc = _make_service(tmp_config_dir)
        monkeypatch.setattr(svc, "_require_huggingface_consent", lambda name: None)

        outcome = svc._download_whisper_family("tiny", MagicMock())
        assert outcome["success"] is True
        assert outcome["queued"] is True
        assert outcome["model"] == "tiny"
        assert outcome["queue_position"] == 1
        assert svc._download_queue == ["tiny"]
        assert _queued_events_for(published, "tiny"), "enqueue must push a download_progress event with queue_position."

    def test_parakeet_branch_queues_second_download(self, tmp_config_dir, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.asr_setup.download_parakeet_weights",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("queued download must not reach download_parakeet_weights")
            ),
        )
        _capture_progress_events(monkeypatch)
        svc = _make_service(tmp_config_dir)
        monkeypatch.setattr(svc, "_require_huggingface_consent", lambda name: None)

        outcome = svc._download_parakeet("parakeet")
        assert outcome["success"] is True
        assert outcome["queued"] is True
        assert outcome["model"] == "parakeet"
        assert outcome["queue_position"] == 1
        assert svc._download_queue == ["parakeet"]

    def test_dispatcher_returns_queued_outcome_dict(self, tmp_config_dir, monkeypatch):
        """The public dispatcher converts the queued TypedDict to a plain
        dict (IPC shape) like every other outcome."""
        from voice_typer.server.asr_setup import is_download_active

        assert is_download_active() is True  # fixture armed the gate
        published = _capture_progress_events(monkeypatch)
        svc = _make_service(tmp_config_dir)
        monkeypatch.setattr(svc, "_require_huggingface_consent", lambda name: None)

        result = svc.download_model("parakeet")
        assert result["success"] is True
        assert result["queued"] is True
        assert result["model"] == "parakeet"
        assert result["queue_position"] == 1
        assert _queued_events_for(published, "parakeet")


class TestQueueMechanics:
    def test_positions_are_fifo(self, tmp_config_dir, monkeypatch):
        published = _capture_progress_events(monkeypatch)
        svc = _make_service(tmp_config_dir)

        svc._enqueue_download("tiny")
        svc._enqueue_download("base")
        svc._enqueue_download("small")

        assert svc._download_queue == ["tiny", "base", "small"]
        positions = {m: _queued_events_for(published, m)[-1]["queue_position"] for m in ("tiny", "base", "small")}
        assert positions == {"tiny": 1, "base": 2, "small": 3}

    def test_duplicate_enqueue_is_idempotent(self, tmp_config_dir, monkeypatch):
        published = _capture_progress_events(monkeypatch)
        svc = _make_service(tmp_config_dir)

        first = svc._enqueue_download("tiny")
        second = svc._enqueue_download("tiny")

        assert svc._download_queue == ["tiny"]
        assert first["queue_position"] == 1
        assert second["queue_position"] == 1
        assert second["queued"] is True
        # A duplicate enqueue still refreshes the queued event (position
        # unchanged) so the renderer state stays in sync.
        assert _queued_events_for(published, "tiny")

    def test_reclick_of_active_model_returns_already_active(self, tmp_config_dir, monkeypatch):
        """A download request for the model that is ALREADY downloading
        must NOT queue behind itself, it resolves as the
        ``download_already_active`` outcome (queueing it would drain
        later as a cache-hit no-op transfer)."""
        published = _capture_progress_events(monkeypatch)
        svc = _make_service(tmp_config_dir)
        active_id = svc._register_download("tiny")

        outcome = svc._enqueue_download("tiny")

        assert outcome["success"] is True
        assert outcome["download_already_active"] is True
        assert outcome["model"] == "tiny"
        assert "queued" not in outcome
        # The queue stays empty and no queued event was pushed.
        assert svc._download_queue == []
        assert _queued_events_for(published, "tiny") == []
        svc._unregister_download(active_id)

    def test_reclick_of_active_model_leaves_other_queues_intact(self, tmp_config_dir, monkeypatch):
        """The re-click guard only short-circuits the ACTIVE model, a
        DIFFERENT model still queues normally."""
        published = _capture_progress_events(monkeypatch)
        svc = _make_service(tmp_config_dir)
        active_id = svc._register_download("tiny")

        outcome = svc._enqueue_download("base")

        assert outcome["queued"] is True
        assert outcome["queue_position"] == 1
        assert svc._download_queue == ["base"]
        assert _queued_events_for(published, "base")
        svc._unregister_download(active_id)

    def test_whisper_branch_reclick_of_active_model_short_circuits(self, tmp_config_dir, monkeypatch):
        """The single-flight guard path (``_download_whisper_family``
        with the transfer gate armed) resolves a re-click of the active
        model as already-active instead of enqueuing it."""
        from unittest.mock import MagicMock

        published = _capture_progress_events(monkeypatch)
        svc = _make_service(tmp_config_dir)
        monkeypatch.setattr(svc, "_require_huggingface_consent", lambda name: None)
        active_id = svc._register_download("tiny")

        outcome = svc._download_whisper_family("tiny", MagicMock())

        assert outcome["success"] is True
        assert outcome["download_already_active"] is True
        assert svc._download_queue == []
        assert _queued_events_for(published, "tiny") == []
        svc._unregister_download(active_id)


class TestCancelAnywhere:
    """THE queue edge: cancel must be able to remove a QUEUED model
    without touching the active transfer."""

    def test_cancel_removes_queued_model_only(self, tmp_config_dir, monkeypatch):
        """Cancelling a queued model removes it from the queue and leaves
        the ACTIVE transfer (gate + its per-download event) untouched."""
        published = _capture_progress_events(monkeypatch)
        svc = _make_service(tmp_config_dir)
        active_id = svc._register_download("active-model")
        active_event = svc._download_cancel_events[active_id]
        svc._enqueue_download("tiny")
        svc._enqueue_download("base")

        result = svc.cancel_model_download("tiny")

        assert result["cancelled"] is True
        assert svc._download_queue == ["base"]
        assert not active_event.is_set(), "cancel of a queued model must not abort the active transfer."
        assert is_download_active() is True, "the active download must still be in flight."
        # The remaining model's position refreshed to 1.
        refreshed = _queued_events_for(published, "base")
        assert refreshed and refreshed[-1]["queue_position"] == 1

    def test_cancel_unknown_model_is_a_noop(self, tmp_config_dir):
        svc = _make_service(tmp_config_dir)
        svc._enqueue_download("tiny")
        result = svc.cancel_model_download("not-queued")
        assert result == {"cancelled": False}
        assert svc._download_queue == ["tiny"]

    def test_no_argument_cancel_keeps_legacy_active_only_semantics(self, tmp_config_dir):
        """The argumentless form (today's only IPC shape) cancels the
        ACTIVE transfer only, queued items stay queued and drain on."""
        svc = _make_service(tmp_config_dir)
        active_id = svc._register_download("active-model")
        active_event = svc._download_cancel_events[active_id]
        svc._enqueue_download("tiny")

        result = svc.cancel_model_download()

        assert result == {"cancelled": True}
        assert active_event.is_set()
        assert svc._download_queue == ["tiny"], (
            "the argumentless cancel must NOT clear the pending queue, items drain when the active transfer exits."
        )

    def test_cancel_by_name_of_active_model_cancels_it(self, tmp_config_dir):
        """Passing the ACTIVE model's name cancels the active transfer
        (the per-name form covers both states)."""
        svc = _make_service(tmp_config_dir)
        active_id = svc._register_download("tiny")
        active_event = svc._download_cancel_events[active_id]

        result = svc.cancel_model_download("tiny")

        assert result["cancelled"] is True
        assert active_event.is_set()
        svc._unregister_download(active_id)


class TestQueueDrain:
    """When the active transfer exits, the next queued request
    auto-starts, through the SAME single-flight gate, serialized."""

    def test_drain_starts_next_queued_when_gate_free(self, tmp_config_dir, monkeypatch):
        svc = _make_service(tmp_config_dir)
        started: list[str] = []
        started_evt = threading.Event()

        def _recording_download(name):
            started.append(name)
            started_evt.set()
            return {"success": True, "model": name}

        monkeypatch.setattr(svc, "download_model", _recording_download)
        svc._download_queue.extend(["tiny", "base"])
        clear_download_pause_state()

        svc._start_next_queued_download()

        assert started_evt.wait(timeout=5.0)
        assert started == ["tiny"]
        assert svc._download_queue == ["base"]

    def test_drain_skips_while_gate_active(self, tmp_config_dir, monkeypatch):
        """While a gateable transfer is still in flight the drain must
        NOT start anything, the live download's own exit path drains."""
        svc = _make_service(tmp_config_dir)
        started: list[str] = []

        monkeypatch.setattr(svc, "download_model", lambda name: started.append(name) or {})
        svc._enqueue_download("tiny")
        assert is_download_active() is True  # fixture armed the gate

        svc._start_next_queued_download()

        assert started == []
        assert svc._download_queue == ["tiny"]

    def test_drain_noop_on_empty_queue(self, tmp_config_dir):
        clear_download_pause_state()
        svc = _make_service(tmp_config_dir)
        svc._start_next_queued_download()  # must not raise

    def test_download_model_exit_drains_queue(self, tmp_config_dir, monkeypatch):
        """The public dispatcher must drain on EVERY exit (success,
        failure, cancel), a queued request auto-starts once the
        current download_model call finishes."""
        svc = _make_service(tmp_config_dir)
        monkeypatch.setattr(svc, "_require_huggingface_consent", lambda name: None)
        started: list[str] = []
        started_evt = threading.Event()
        real_download_model = svc.download_model

        def _recording_download(name):
            started.append(name)
            started_evt.set()
            if name == "parakeet":
                return real_download_model(name)
            # The queued follow-up: record only (its real transfer path
            # is covered by the sibling download tests).
            return {"success": True, "model": name}

        monkeypatch.setattr(svc, "download_model", _recording_download)
        monkeypatch.setattr(svc, "_download_parakeet", lambda name: {"success": True, "model": name})
        svc._download_queue.append("tiny")
        clear_download_pause_state()

        result = svc.download_model("parakeet")

        assert result["success"] is True
        assert started_evt.wait(timeout=5.0)
        assert started == ["parakeet", "tiny"]
        assert svc._download_queue == []


class TestQueueSnapshot:
    """``get_download_queue``, read-only snapshot for mount hydration."""

    def test_empty_queue_returns_empty_list(self, tmp_config_dir):
        svc = _make_service(tmp_config_dir)
        assert svc.get_download_queue() == {"queue": []}

    def test_snapshot_returns_fifo_order(self, tmp_config_dir):
        svc = _make_service(tmp_config_dir)
        svc._enqueue_download("tiny")
        svc._enqueue_download("base")

        assert svc.get_download_queue() == {"queue": ["tiny", "base"]}

    def test_snapshot_is_a_copy(self, tmp_config_dir):
        """Mutating the returned list must not corrupt live queue state."""
        svc = _make_service(tmp_config_dir)
        svc._enqueue_download("tiny")

        snapshot = svc.get_download_queue()
        snapshot["queue"].append("forged")

        assert svc._download_queue == ["tiny"]
