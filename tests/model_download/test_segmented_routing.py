"""Segmented fast-lane routing: which files go where, and failover.

The service routes big, pinned files to the segmented engine and
everything else to the classic snapshot path. ANY segmented failure
must degrade to the classic full download (today's behavior) — never
to a user-facing error for a download the classic path could complete.

All network/HF collaborators are stubbed; no test touches the network.
"""

from unittest.mock import MagicMock

import pytest
from voice_typer.server import segmented_download as segdl


def _make_service(tmp_config_dir):
    from voice_typer.server import service as svc_mod

    class FakeApp:
        config = type("FakeConfig", (), {})()
        _microphones: list = []
        tray = MagicMock()

    return svc_mod.VoiceTyperService(FakeApp())


def _make_meta():
    meta = MagicMock()
    meta.repo_id = "org/target"
    meta.backend = "whisper"
    meta.download_size_mb = 3000
    return meta


@pytest.fixture(autouse=True)
def _clean_gate_state():
    import voice_typer.server.asr_setup as asr

    asr.clear_download_pause_state()
    yield
    asr.clear_download_pause_state()


def _stub_common(monkeypatch, tmp_config_dir):
    """Stub everything around the transfer: consent, events, tray."""
    import voice_typer.server.event_bus as event_bus

    monkeypatch.setattr(event_bus, "publish", lambda event: None)
    monkeypatch.setattr(
        "voice_typer.server.tray_models.invalidate_model_availability_cache",
        lambda: None,
    )


class TestWhisperRouting:
    def _drive_branch(self, svc, monkeypatch, plan, snapshot_calls):
        # NOTE: no manual reset/clear of the gate events here — the branch
        # arms its own lifecycle (guard → reset → … → clear). Pre-arming
        # would trip the single-flight guard (that's what it's for).

        def fake_snapshot(**kwargs):
            snapshot_calls.append(kwargs)
            # Cache probe must MISS (raise) to drive the branch into the
            # download path; real transfers return a path.
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not cached — drive into download branch")
            return "/cache/snap"

        def fake_retry(fn, **kwargs):
            return fn(**kwargs)

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)
        monkeypatch.setattr(
            "voice_typer.server.transcription._download_with_retry",
            fake_retry,
        )
        monkeypatch.setattr(
            "voice_typer.server.segmented_download.plan_segmented_files",
            lambda **kwargs: plan,
        )
        monkeypatch.setattr(svc, "_require_huggingface_consent", lambda name: None)
        monkeypatch.setattr(svc, "_invalidate_model_status_cache", lambda: None)
        return svc._download_whisper_family("tiny", _make_meta())

    def _phase_a_call(self, snapshot_calls):
        matches = [c for c in snapshot_calls if c.get("ignore_patterns")]
        assert matches, f"no phase-A snapshot call with ignore list: {snapshot_calls}"
        return matches[0]

    def test_big_files_excluded_from_classic_snapshot(self, tmp_config_dir, monkeypatch):
        """Phase A (classic snapshot) must ignore big files so both paths
        never fetch the same bytes; the big file goes segmented."""
        _stub_common(monkeypatch, tmp_config_dir)
        snapshot_calls: list = []
        seg_calls: list = []

        plan = [segdl.PlannedFile(filename="model.bin", size=3_000_000_000, blob_id="a" * 64, sha256="a" * 64)]

        def fake_phase(**kwargs):
            seg_calls.append(kwargs)
            return None

        monkeypatch.setattr(segdl, "run_segmented_phase", fake_phase)
        svc = _make_service(tmp_config_dir)
        outcome = self._drive_branch(svc, monkeypatch, plan, snapshot_calls)

        assert outcome["success"] is True
        assert self._phase_a_call(snapshot_calls).get("ignore_patterns") == ["model.bin"]
        assert len(seg_calls) == 1
        assert seg_calls[0]["seg_plan"] == plan

    def test_no_big_files_means_pure_classic(self, tmp_config_dir, monkeypatch):
        """Empty plan → byte-identical behavior to the pre-segmented
        code path (no ignore list, no phase B)."""
        _stub_common(monkeypatch, tmp_config_dir)
        snapshot_calls: list = []
        seg_calls: list = []

        monkeypatch.setattr(segdl, "run_segmented_phase", lambda **kwargs: seg_calls.append(kwargs))
        svc = _make_service(tmp_config_dir)
        outcome = self._drive_branch(svc, monkeypatch, [], snapshot_calls)

        assert outcome["success"] is True
        assert snapshot_calls[0].get("ignore_patterns") in (None, [])
        assert seg_calls == []

    def test_segmented_failure_falls_back_to_classic_full(self, tmp_config_dir, monkeypatch):
        """A segmented failure must NOT fail the download: the classic
        full-repo snapshot runs instead (today's behavior)."""
        _stub_common(monkeypatch, tmp_config_dir)
        snapshot_calls: list = []

        plan = [segdl.PlannedFile(filename="model.bin", size=3_000_000_000, blob_id="b" * 64, sha256="b" * 64)]

        def fake_phase(**kwargs):
            raise segdl.SegmentedDownloadError("simulated segment failure")

        monkeypatch.setattr(segdl, "run_segmented_phase", fake_phase)
        svc = _make_service(tmp_config_dir)
        outcome = self._drive_branch(svc, monkeypatch, plan, snapshot_calls)

        assert outcome["success"] is True
        assert self._phase_a_call(snapshot_calls).get("ignore_patterns") == ["model.bin"]
        full_calls = [c for c in snapshot_calls if not c.get("ignore_patterns") and not c.get("local_files_only")]
        assert full_calls, "fallback must run the classic FULL snapshot"

    def test_abort_during_segmented_maps_to_cancelled(self, tmp_config_dir, monkeypatch):
        """Cancel during phase B must resolve as a clean stop (NOT an
        error, NOT a retry of the transfer)."""
        import voice_typer.server.asr_setup as asr

        _stub_common(monkeypatch, tmp_config_dir)

        plan = [segdl.PlannedFile(filename="model.bin", size=3_000_000_000, blob_id="c" * 64, sha256="c" * 64)]

        def fake_phase(**kwargs):
            raise asr.ModelDownloadAborted("simulated cancel")

        monkeypatch.setattr(segdl, "run_segmented_phase", fake_phase)
        svc = _make_service(tmp_config_dir)
        outcome = self._drive_branch(svc, monkeypatch, plan, [])

        assert outcome["success"] is False
        assert outcome["cancelled"] is True
        assert outcome["model"] == "tiny"


class TestParakeetRouting:
    def test_big_onnx_files_go_segmented(self, tmp_config_dir, monkeypatch):
        """Parakeet: classic snapshot ignores the big ONNX files; the
        segmented phase fetches them; integrity still gates success."""

        snapshot_calls: list = []
        phase_calls: list = []
        messages: list = []

        plan = [
            segdl.PlannedFile(
                filename="encoder-model.fp16.onnx",
                size=800_000_000,
                blob_id="d" * 64,
                sha256="d" * 64,
            )
        ]

        probe_hits: list = []

        def fake_snapshot(**kwargs):
            snapshot_calls.append(kwargs)
            # First local_files_only call is the cache probe → MISS
            # (raise) to drive the download path. The SECOND one is the
            # post-segmented self-verify → HIT (the segmented files
            # completed the snapshot).
            if kwargs.get("local_files_only"):
                probe_hits.append(1)
                if len(probe_hits) == 1:
                    raise FileNotFoundError("not cached — drive into download path")
                return "/cache/snap"
            return "/cache/snap"

        def fake_retry(fn, **kwargs):
            return fn(**kwargs)

        def fake_phase(**kwargs):
            phase_calls.append(kwargs)
            return None

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)
        monkeypatch.setattr("voice_typer.server.transcription._download_with_retry", fake_retry)
        monkeypatch.setattr(
            "voice_typer.server.segmented_download.plan_segmented_files",
            lambda **kwargs: plan,
        )
        monkeypatch.setattr(segdl, "run_segmented_phase", fake_phase)
        monkeypatch.setattr(
            "voice_typer.server.asr_setup._verify_model_integrity",
            lambda repo_id, local_dir: (True, {}),
        )
        monkeypatch.setattr(
            "voice_typer.server.transcription._check_disk_space_for_download",
            lambda *a, **k: None,
        )

        from voice_typer.server.asr_setup import download_parakeet_weights

        result = download_parakeet_weights(config=None, progress_callback=messages.append, force=True)
        assert result[0] is True, f"expected success, got: {result}"
        phase_a = next(c for c in snapshot_calls if c.get("ignore_patterns"))
        assert phase_a.get("ignore_patterns") == ["encoder-model.fp16.onnx"]
        assert len(phase_calls) == 1
        assert phase_calls[0]["seg_plan"] == plan
