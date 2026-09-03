"""XV-1 / XV-2 / XV-5 service.py fix regression tests.

Dedicated coverage for the three fixes applied to
``voice_typer/server/service.py`` in this FA1-fix pass:

* **XV-1 (High)**: ``_check_parakeet_deps`` and ``_check_qwen_deps`` use
  :func:`importlib.util.find_spec` instead of
  :func:`importlib.import_module`.  ``find_spec`` only resolves the
  module's file path on disk — it does NOT execute the module body — so
  probing for ``torch`` (which allocates 500 MB–1 GB of RSS when
  imported) no longer loads torch into the process.  These helpers are
  called via :meth:`_compute_model_status` on every cache miss of
  :meth:`get_model_status`, polled every ~2 s by the renderer.

* **XV-2 (Medium)**: ``download_model``'s progress-polling loop walks
  ONLY the in-progress repo's HF cache subdir
  (``cache_dir / f"models--{repo_id.replace('/', '--')}"``) instead of
  the entire HF hub cache tree (``cache_dir.rglob("*")``).  The old
  form re-stat'd every file of every other cached repo on each 1 s
  poll iteration.

* **XV-5 (Low)**: ``_microphones_cache`` is initialised to ``None``
  (not ``[]``) and the truthiness check in
  :meth:`refresh_microphones` uses ``is not None`` so a legitimately
  empty device list (0 mics) is served from cache instead of
  re-querying PortAudio on every refresh call.

The tests use ``unittest.mock`` to monkeypatch ``importlib.util.find_spec``
(for XV-1) and to stub the HuggingFace cache directory layout (for XV-2)
so no real torch / qwen_asr / network is required.
"""

from __future__ import annotations

import inspect
import time
from pathlib import Path
from unittest.mock import MagicMock

# ── Helpers ───────────────────────────────────────────────────────────


def _make_service(tmp_config_dir):
    """Build a ``VoiceTyperService`` against a minimal fake app.

    ``tmp_config_dir`` (from ``conftest.py``) pins the config dir to a
    tmp path so no real user-config dir is touched.
    """
    from voice_typer.server import service as svc_mod

    class FakeApp:
        config = type("FakeConfig", (), {})()
        _microphones: list = []
        tray = MagicMock()

    return svc_mod.VoiceTyperService(FakeApp())


# XV-1 deps-probe tests removed 2026-08-15: ``_check_qwen_deps`` /
# ``_check_parakeet_deps`` were deleted with the torch engine — both
# backends are ONNX now (onnxruntime + onnx-asr are base deps), so the
# Models-page ``deps_ok`` is a constant True with no module probe.


# download_model polling walks only the per-repo subdir ──────


class TestDownloadPollScopedToModelDir:
    """XV-2: ``download_model``'s progress-polling loop walks ONLY the
    in-progress repo's HF cache subdir, not the entire HF cache tree."""

    def test_poll_uses_per_repo_subdir_construction(self):
        """Source guard: the polling loop must construct
        ``model_dir = cache_dir / f"models--{repo_id.replace('/', '--')}"``
        and walk ``model_dir.rglob("*")`` (not ``cache_dir.rglob("*")``)."""
        from voice_typer.server.service import VoiceTyperService

        src = inspect.getsource(VoiceTyperService.download_model)
        assert 'model_dir = cache_dir / f"models--{repo_id.replace' in src, (
            "XV-2: download_model must construct the per-repo subdir "
            "via cache_dir / f\"models--{repo_id.replace('/', '--')}\" "
            "before walking it."
        )
        assert 'model_dir.rglob("*")' in src, (
            "XV-2: progress polling must walk model_dir (the per-repo subdir), not the whole cache_dir."
        )
        # The old wide-scan form must NOT appear in the polling block.
        assert 'cache_dir.rglob("*")' not in src, (
            "XV-2 regression: download_model still walks the entire cache_dir tree via cache_dir.rglob('*')."
        )

    def test_poll_does_not_stat_unrelated_repos(self, tmp_config_dir, monkeypatch):
        """End-to-end: when the HF cache contains an unrelated repo's
        files, the progress loop must NOT stat them.  We construct a
        real on-disk HF-cache-like layout under ``tmp_config_dir`` and
        verify only the in-progress repo's files are stat'd.

        ``download_model`` is driven with every external collaborator
        stubbed (model registry, huggingface_hub.snapshot_download,
        asr_setup pause helpers, consent gate, per-download
        cancellation plumbing, event bus, tray-models cache) so the
        test is hermetic — no network, no torch, no real audio.
        """

        # Build a fake HF hub cache with TWO repos:
        #   models--unrelated--other  (already fully downloaded)
        #   models--org--target       (the one being downloaded)
        cache_root = tmp_config_dir / "huggingface" / "hub"
        other_repo = cache_root / "models--unrelated--other" / "snapshots" / "rev1"
        other_repo.mkdir(parents=True)
        other_file = other_repo / "model.safetensors"
        other_file.write_bytes(b"\x00" * 4096)  # 4 KB sentinel file

        target_repo = cache_root / "models--org--target" / "snapshots" / "rev1"
        target_repo.mkdir(parents=True)
        target_file = target_repo / "model.safetensors"
        target_file.write_bytes(b"\x01" * 8192)  # 8 KB

        # Track every explicit Path.stat call during the poll.
        stat_paths: list[str] = []
        real_stat = Path.stat

        def spy_stat(self, *args, **kwargs):
            stat_paths.append(str(self))
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", spy_stat)

        # Stub the model registry: download_model needs a meta with
        # backend in ("whisper", "distil-whisper") and repo_id == "org/target".
        fake_meta = MagicMock()
        fake_meta.repo_id = "org/target"
        fake_meta.backend = "whisper"
        fake_meta.download_size_mb = 1
        monkeypatch.setattr(
            "voice_typer.server.model_registry.get_model_metadata",
            lambda name: fake_meta,
        )

        # Stub huggingface_hub.snapshot_download: the local-files-only
        # probe raises (driving us into the polling branch); the real
        # threaded download is a no-op that returns immediately.
        def fake_snapshot_download(*args, **kwargs):
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not cached — drive into polling branch")
            return None

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot_download)

        # Stub the retry wrapper so the download thread stays alive just
        # long enough for the polling loop's ``while t.is_alive():`` guard
        # to enter the body at least once (the stat block we're verifying
        # lives inside that body). A 0.3 s sleep is deterministic and
        # well under the 30 s test timeout.
        import threading as _threading

        _dl_barrier = _threading.Event()

        def _fake_download_with_retry(fn, **kwargs):
            # Block briefly so the main thread's first ``t.is_alive()``
            # check returns True and the polling body executes.
            _dl_barrier.wait(timeout=0.5)

        monkeypatch.setattr(
            "voice_typer.server.transcription._download_with_retry",
            _fake_download_with_retry,
        )

        # Stub the asr_setup pause helpers (imported locally inside
        # download_model).
        monkeypatch.setattr("voice_typer.server.asr_setup.clear_download_pause_state", lambda: None)
        monkeypatch.setattr("voice_typer.server.asr_setup.is_download_paused", lambda: False)
        monkeypatch.setattr("voice_typer.server.asr_setup.reset_download_pause_state", lambda: None)
        monkeypatch.setattr("voice_typer.server.asr_setup.wait_while_paused", lambda timeout_s=1.0: None)

        # Stub segmented planning to "no big files": this test pins the
        # classic poll loop's stat behavior — the segmented fast lane
        # (which would call HfApi over the network) is out of scope here.
        monkeypatch.setattr(
            "voice_typer.server.segmented_download.plan_segmented_files",
            lambda **kwargs: [],
        )

        # Stub the tray-models cache invalidator (imported locally in
        # the success path).
        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        # Stub event_bus.publish so _push_progress doesn't touch real
        # subscribers.
        monkeypatch.setattr("voice_typer.server.event_bus.publish", lambda event: None)

        svc = _make_service(tmp_config_dir)
        # Stub the per-download cancellation plumbing.
        monkeypatch.setattr(svc, "_register_download", lambda name: "dlid")
        monkeypatch.setattr(svc, "_unregister_download", lambda dlid: None)
        monkeypatch.setattr(svc, "_is_download_cancelled", lambda dlid: False)
        # Stub the HF consent gate (return None = consent granted).
        monkeypatch.setattr(svc, "_require_huggingface_consent", lambda name: None)
        # Stub model-status cache invalidation (success path).
        monkeypatch.setattr(svc, "_invalidate_model_status_cache", lambda: None)

        # Drive download_model: the polling loop runs at least once
        # before the (blocked) download thread's barrier times out. We
        # don't assert on the return value; we only care that the
        # unrelated repo's file was NEVER stat'd.
        try:
            svc.download_model("target-model")
        except Exception:
            # Any residual stubbing gap is fine — the stat spy has
            # already captured the polling iteration's file accesses.
            pass
        finally:
            # Release the barrier so no thread lingers past the test.
            _dl_barrier.set()

        # The unrelated repo's file must never appear in stat_paths.
        assert not any("models--unrelated--other" in p for p in stat_paths), (
            "XV-2: download_model's progress loop stat'd a file in an "
            "UNRELATED repo (models--unrelated--other). The polling loop "
            "must only walk the per-repo subdir. Stats seen: "
            f"{stat_paths[:5]}..."
        )
        # And at least one file in the target repo WAS stat'd (proving
        # we actually exercised the new per-repo scan).
        assert any("models--org--target" in p for p in stat_paths), (
            "XV-2: expected the target repo's file to be stat'd during "
            "the progress poll, but it wasn't. Stats seen: "
            f"{stat_paths[:5]}..."
        )


# empty microphone list is served from cache ────────────────


class TestMicrophonesCacheEmptyList:
    """XV-5: ``_microphones_cache`` starts as ``None`` (not ``[]``) and
    a legitimately-empty device list is served from cache."""

    def test_cache_initialised_to_none(self, tmp_config_dir):
        svc = _make_service(tmp_config_dir)
        assert svc._microphones_cache is None, (
            "XV-5: _microphones_cache must be initialised to None (not "
            "[]) so the truthiness check doesn't bypass the cache when "
            "PortAudio returns an empty list."
        )

    def test_refresh_uses_is_not_none_source(self):
        """Source guard: the truthiness check must be ``is not None``,
        not a bare-truthiness ``if self._microphones_cache and ...``."""
        from voice_typer.server.service import VoiceTyperService

        src = inspect.getsource(VoiceTyperService.refresh_microphones)
        assert "self._microphones_cache is not None" in src, (
            "XV-5: refresh_microphones must use 'is not None' (not bare "
            "truthiness) so an empty cached list is still served from cache."
        )
        # Strip comments before checking the old buggy form isn't present
        # in actual code lines.
        code_lines = [line for line in src.splitlines() if line.lstrip() and not line.lstrip().startswith("#")]
        code_only = "\n".join(code_lines)
        assert "if self._microphones_cache and " not in code_only, (
            "XV-5 regression: refresh_microphones still uses bare-truthiness "
            "cache check — this skips the cache when PortAudio returns 0 mics."
        )

    def test_empty_list_served_from_cache(self, tmp_config_dir, monkeypatch):
        """When the cache holds an empty list (legitimate "0 mics"),
        ``refresh_microphones`` returns that cached empty list within
        the TTL window WITHOUT re-calling ``list_microphones``."""
        svc = _make_service(tmp_config_dir)
        svc._microphones_cache = []
        svc._microphones_cache_ts = time.monotonic()

        call_count = {"n": 0}

        def fake_list_microphones():
            call_count["n"] += 1
            return [{"name": "should-not-be-returned", "index": 0}]

        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            fake_list_microphones,
        )

        result = svc.refresh_microphones()
        assert result == [], (
            "XV-5: refresh_microphones must serve the cached empty list "
            "instead of re-querying PortAudio when the cache is empty."
        )
        assert call_count["n"] == 0, (
            "XV-5: list_microphones must NOT be called when the cache (even if empty) is fresh."
        )

    def test_non_empty_list_still_served_from_cache(self, tmp_config_dir, monkeypatch):
        """Sanity: a non-empty cache continues to be served (regression
        guard — the XV-5 fix must not break the non-empty path)."""
        svc = _make_service(tmp_config_dir)
        cached_mics = [{"name": "USB Mic", "index": 0}]
        svc._microphones_cache = cached_mics
        svc._microphones_cache_ts = time.monotonic()

        call_count = {"n": 0}

        def fake_list_microphones():
            call_count["n"] += 1
            return []

        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            fake_list_microphones,
        )

        result = svc.refresh_microphones()
        assert result == cached_mics
        assert call_count["n"] == 0

    def test_first_call_queries_portaudio(self, tmp_config_dir, monkeypatch):
        """When the cache is ``None`` (never queried), the first
        ``refresh_microphones`` call must actually query PortAudio and
        populate the cache."""
        svc = _make_service(tmp_config_dir)
        assert svc._microphones_cache is None

        call_count = {"n": 0}
        real_mics = [{"name": "Built-in Mic", "index": 0}]

        def fake_list_microphones():
            call_count["n"] += 1
            return real_mics

        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            fake_list_microphones,
        )

        result = svc.refresh_microphones()
        assert result == real_mics
        assert call_count["n"] == 1, (
            "XV-5: the first refresh_microphones call (cache is None) must query PortAudio exactly once."
        )
        assert svc._microphones_cache == real_mics
