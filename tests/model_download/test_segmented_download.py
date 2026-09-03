"""Tests for the segmented (multi-connection) download engine.

The engine (``voice_typer/server/segmented_download.py``) downloads a
single large file as N concurrent HTTP Range segments — the classic
aria2/ADM approach on the standard HTTP path (NOT xet), so the
pause/cancel transfer gate keeps working and no native code is
involved.

All network I/O goes through an injectable opener seam (``FakeOpener``):
no test touches the real network. Pause/abort use the REAL
``asr_setup`` pause/abort events (reset per test) so the gate semantics
under test are the production ones.
"""

import errno
import hashlib
import io  # noqa: F401 — referenced by string target in monkeypatch.setattr below
import os
import threading
import time
from pathlib import Path

import pytest
from voice_typer.server import asr_setup
from voice_typer.server.asr_setup import ModelDownloadAborted


@pytest.fixture(autouse=True)
def _fresh_gate_state():
    asr_setup.reset_download_pause_state()
    yield
    asr_setup.clear_download_pause_state()


# ── Fake HTTP transport ───────────────────────────────────────────────
#
# Duck-typed stand-ins for urllib's Request/opener/response surface used
# by the engine: ``opener.open(request, timeout=...)`` where request has
# ``full_url`` + ``headers``. Only what the engine touches is modeled.


class FakeResponse:
    def __init__(self, status, headers, body_iter):
        self.status = status
        self.headers = dict(headers)
        self._body_iter = body_iter

    def getheader(self, name, default=None):
        for k, v in self.headers.items():
            if k.lower() == name.lower():
                return v
        return default

    def read(self, amt=-1):
        try:
            return next(self._body_iter)
        except StopIteration:
            return b""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeRequest:
    def __init__(self, url, headers=None, method="GET"):
        self.full_url = url
        self.headers = dict(headers or {})
        self._method = method

    def get_method(self):
        return self._method

    def header_items(self):
        return list(self.headers.items())


class FakeOpener:
    """Serves byte ranges of an in-memory body, with programmable faults.

    ``faults`` maps a hook name to behavior:
      - "ignore_range_once"/"ignore_range_always": answer 200 full body
        instead of 206 (server ignores Range).
      - "no_length": omit Content-Length on HEAD/probe.
      - "rate_limit_once": first GET answers 429 (+Retry-After), then 206.
      - "drop_after": close the stream after N body bytes (once), then
        behave normally on retry (drives resume-from-part-size).
      - "chunk_delay": sleep N seconds per 64 KiB wire chunk (makes
        in-flight pause/cancel tests deterministic).
    Records every request (method, url, headers) in ``requests``.
    """

    def __init__(self, body: bytes, faults=None):
        self.body = body
        self.faults = dict(faults or {})
        self.requests: list[tuple[str, str, dict]] = []
        self._dropped_once = False
        self._limited_once = False

    # -- request plumbing ------------------------------------------------
    def open(self, request, timeout=None):
        method = request.get_method()
        headers = dict(request.header_items())
        self.requests.append((method, request.full_url, headers))
        if method == "HEAD":
            return self._head_response()
        return self._get_response(headers)

    def _head_response(self):
        headers = {"Content-Length": str(len(self.body)), "ETag": '"abc123"'}
        if self.faults.get("no_length"):
            headers.pop("Content-Length")
        return FakeResponse(200, headers, iter(()))

    def _parse_range(self, headers):
        rng = None
        for k, v in headers.items():
            if k.lower() == "range":
                rng = v
                break
        if not rng:
            return None
        assert rng.startswith("bytes=")
        start_s, _, end_s = rng[len("bytes=") :].partition("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else len(self.body) - 1
        return (start, end)

    def _get_response(self, headers):
        if self.faults.get("rate_limit_once") and not self._limited_once:
            self._limited_once = True
            return FakeResponse(429, {"Retry-After": "0", "Content-Length": "0"}, iter(()))
        if self.faults.get("ignore_range_always") or (
            self.faults.get("ignore_range_once") and not getattr(self, "_ignored_once", False)
        ):
            self._ignored_once = True
            # Server ignores Range: full body with 200.
            return FakeResponse(
                200,
                {"Content-Length": str(len(self.body))},
                self._chunk_iter(self.body),
            )
        parsed = self._parse_range(headers)
        if parsed is None:
            return FakeResponse(
                200,
                {"Content-Length": str(len(self.body))},
                self._chunk_iter(self.body),
            )
        start, end = parsed
        end = min(end, len(self.body) - 1)
        total = len(self.body)
        chunk = self.body[start : end + 1]
        if self.faults.get("drop_after") is not None and not self._dropped_once:
            self._dropped_once = True
            chunk = chunk[: self.faults["drop_after"]]
        return FakeResponse(
            206,
            {
                "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{total}",
                "Content-Length": str(len(chunk)),
            },
            self._chunk_iter(chunk),
        )

    def _chunk_iter(self, data: bytes):
        # 64 KiB wire chunks; chunk_delay slows delivery so in-flight
        # pause/cancel tests can park workers deterministically.
        delay = self.faults.get("chunk_delay", 0)
        step = 64 * 1024
        for i in range(0, len(data), step):
            if delay:
                time.sleep(delay)
            yield data[i : i + step]

    # -- assertions -------------------------------------------------------
    def range_requests(self):
        return [h for _, _, h in self.requests if "Range" in h or "range" in h]


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


BODY_3MB = bytes(i % 251 for i in range(3 * 1024 * 1024))


def _engine_kwargs(opener, scratch: Path, body: bytes = BODY_3MB, **over):
    from voice_typer.server import segmented_download as seg

    kw = dict(
        url="https://cdn.example/models/model.bin",
        filename="model.bin",
        total_size=len(body),
        etag='"abc123"',
        expected_sha256=_sha(body),
        scratch_dir=scratch,
        progress_cb=None,
        gate_check=asr_setup.check_download_gate,
        num_segments=3,
        opener_factory=lambda: opener,
        timeout_s=10,
    )
    kw.update(over)
    return seg, kw


# ── Planning ───────────────────────────────────────────────────────────


class TestPlanSegments:
    def test_even_split_boundaries(self):
        from voice_typer.server.segmented_download import plan_segments

        segs = plan_segments(300, segment_target=100, max_segments=6)
        assert [(s.start, s.end) for s in segs] == [(0, 99), (100, 199), (200, 299)]

    def test_remainder_goes_to_last_segment(self):
        from voice_typer.server.segmented_download import plan_segments

        segs = plan_segments(250, segment_target=100, max_segments=6)
        assert [(s.start, s.end) for s in segs] == [(0, 99), (100, 199), (200, 249)]
        # Contiguous, gapless, full coverage.
        assert segs[0].start == 0
        assert segs[-1].end == 249
        for a, b in zip(segs, segs[1:], strict=False):
            assert b.start == a.end + 1

    def test_max_segments_cap(self):
        from voice_typer.server.segmented_download import plan_segments

        segs = plan_segments(10 * 1024**3, segment_target=100, max_segments=4)
        assert len(segs) == 4
        assert segs[-1].end == 10 * 1024**3 - 1

    def test_tiny_file_is_single_segment(self):
        from voice_typer.server.segmented_download import plan_segments

        segs = plan_segments(10, segment_target=100, max_segments=6)
        assert [(s.start, s.end) for s in segs] == [(0, 9)]


class TestPlanSegmentedFiles:
    def _plan(self, **over):
        from voice_typer.server import segmented_download as seg

        kw = dict(
            repo_id="org/model",
            revision="abc123",
            allow_patterns=["*.bin", "*.json"],
            file_hashes={"model.bin": "aabb", "config.json": "ccdd"},
            threshold_bytes=100,
        )
        kw.update(over)
        return seg.plan_segmented_files(**kw)

    def test_routes_only_big_pinned_files(self):
        plan = self._plan(
            list_files=lambda: [
                ("model.bin", 500, "aabb"),
                ("config.json", 50, "ccdd"),
            ]
        )
        assert plan is not None
        assert [p.filename for p in plan] == ["model.bin"]
        assert plan[0].sha256 == "aabb"

    def test_skips_unpinned_files(self):
        plan = self._plan(
            list_files=lambda: [("weights.bin", 500, "eeff")],
            allow_patterns=["*.bin"],
        )
        assert plan == []

    def test_skips_pin_blob_mismatch(self):
        plan = self._plan(
            list_files=lambda: [("model.bin", 500, "DIFFERENT")],
        )
        assert plan == []

    def test_skips_unknown_sizes(self):
        plan = self._plan(
            list_files=lambda: [("model.bin", None, "aabb")],
        )
        assert plan == []

    def test_respects_allow_patterns(self):
        plan = self._plan(
            list_files=lambda: [("model.bin", 500, "aabb")],
            allow_patterns=["*.onnx"],
        )
        assert plan == []

    def test_lister_failure_degrades_to_none(self):
        def boom():
            raise RuntimeError("no network")

        assert self._plan(list_files=boom) is None


# ── Happy path ─────────────────────────────────────────────────────────


class TestHappyPath:
    def test_downloads_segments_concurrently_and_assembles(self, tmp_path):
        opener = FakeOpener(BODY_3MB)
        progress: list = []
        seg, kw = _engine_kwargs(opener, tmp_path, progress_cb=lambda d, t: progress.append((d, t)))
        out = seg.download_file_segmented(**kw)
        assert out.read_bytes() == BODY_3MB
        # Every byte fetched via Range requests (no full-body GET).
        ranges = opener.range_requests()
        assert len(ranges) >= 3
        # Progress callbacks are monotonic and end at total.
        assert progress
        assert progress[-1] == (len(BODY_3MB), len(BODY_3MB))
        done = [p[0] for p in progress]
        assert done == sorted(done), "progress must be monotonic"
        # State + parts cleaned up on success (only the verified file
        # may remain).
        leftovers = [p for p in tmp_path.iterdir() if p != out]
        assert not leftovers, f"success must clean parts/state, left: {leftovers}"

    def test_needs_no_gate_by_default(self, tmp_path):
        opener = FakeOpener(BODY_3MB)
        seg, kw = _engine_kwargs(opener, tmp_path, gate_check=None)
        out = seg.download_file_segmented(**kw)
        assert out.read_bytes() == BODY_3MB


# ── Server quirks ──────────────────────────────────────────────────────


class TestServerQuirks:
    def test_200_on_range_raises_for_classic_fallback(self, tmp_path):
        from voice_typer.server import segmented_download as seg

        opener = FakeOpener(BODY_3MB, faults={"ignore_range_always": True})
        _, kw = _engine_kwargs(opener, tmp_path, num_segments=3)
        # A server that ignores Range cannot be segmented: the engine
        # must fail over (SegmentedDownloadError) so the caller runs the
        # classic single-stream path, which handles Range-less servers
        # natively.
        with pytest.raises(seg.SegmentedDownloadError):
            seg.download_file_segmented(**kw)

    def test_429_retries_with_backoff_then_succeeds(self, tmp_path, monkeypatch):
        opener = FakeOpener(BODY_3MB, faults={"rate_limit_once": True})
        sleeps: list = []
        monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
        seg, kw = _engine_kwargs(opener, tmp_path, num_segments=1)
        out = seg.download_file_segmented(**kw)
        assert out.read_bytes() == BODY_3MB
        assert sleeps, "expected a backoff sleep before retry"

    def test_mid_stream_drop_resumes_segment_from_part_size(self, tmp_path):
        opener = FakeOpener(BODY_3MB, faults={"drop_after": 50_000})
        seg, kw = _engine_kwargs(opener, tmp_path, num_segments=1)
        out = seg.download_file_segmented(**kw)
        assert out.read_bytes() == BODY_3MB
        # Second attempt must resume (Range start > 0), not restart.
        ranges = [h.get("Range", h.get("range", "")) for h in opener.range_requests()]
        assert any(not r.startswith("bytes=0-") for r in ranges[1:]), f"expected a resumed Range request, got: {ranges}"


# ── Pause / cancel ─────────────────────────────────────────────────────


class TestGateIntegration:
    def test_pause_blocks_then_resumes_without_byte_loss(self, tmp_path):
        # chunk_delay stretches the 3 MB download to ~2.4 s so the pause
        # reliably lands mid-flight (no timing race with fast fakes).
        opener = FakeOpener(BODY_3MB, faults={"chunk_delay": 0.05})
        seg, kw = _engine_kwargs(opener, tmp_path, num_segments=2)
        done: dict = {}
        errors: dict = {}

        def run():
            try:
                done["path"] = seg.download_file_segmented(**kw)
            except BaseException as e:  # noqa: BLE001 — test must surface anything
                errors["err"] = e

        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(0.3)
        assert t.is_alive()
        asr_setup.set_download_paused(True)
        time.sleep(1.0)
        assert t.is_alive(), "workers must stay parked while paused"
        assert "path" not in done
        asr_setup.set_download_paused(False)
        t.join(timeout=20)
        assert not t.is_alive()
        assert "err" not in errors
        assert done["path"].read_bytes() == BODY_3MB

    def test_cancel_aborts_and_keeps_parts_for_retry(self, tmp_path):
        opener = FakeOpener(BODY_3MB, faults={"chunk_delay": 0.05})
        seg, kw = _engine_kwargs(opener, tmp_path, num_segments=2)
        raised: dict = {}

        def run():
            try:
                seg.download_file_segmented(**kw)
            except ModelDownloadAborted as e:
                raised["abort"] = e

        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(0.3)
        assert t.is_alive()
        asr_setup.request_download_abort()
        t.join(timeout=20)
        assert not t.is_alive()
        assert "abort" in raised, "cancel must unwind via ModelDownloadAborted"
        # Parts + state survive cancel so a retry resumes (ADM semantics).
        assert any(tmp_path.rglob("*.part*")) or any(tmp_path.rglob("*.state.json")), (
            "cancel must preserve resume state, not wipe it"
        )


# ── Resume across restarts ─────────────────────────────────────────────


class TestResume:
    def test_completed_segments_are_not_refetched(self, tmp_path):
        from voice_typer.server import segmented_download as seg

        opener = FakeOpener(BODY_3MB)
        _, kw = _engine_kwargs(opener, tmp_path, num_segments=3)
        # Simulate a previous run that finished segment 0 (part file +
        # state on disk) then died.
        plan = seg.plan_segments(len(BODY_3MB), segment_target=1024 * 1024, max_segments=3)
        first = plan[0]
        part0 = tmp_path / "model.bin.part0"
        part0.write_bytes(BODY_3MB[first.start : first.end + 1])
        seg.write_state(
            tmp_path / "model.bin.state.json",
            url=kw["url"],
            etag=kw["etag"],
            total_size=len(BODY_3MB),
            expected_sha256=kw["expected_sha256"],
            segments=[{"index": s.index, "start": s.start, "end": s.end, "done": i == 0} for i, s in enumerate(plan)],
        )
        opener2 = FakeOpener(BODY_3MB)
        _, kw2 = _engine_kwargs(opener2, tmp_path, num_segments=3, segment_target=1024 * 1024)
        out = seg.download_file_segmented(**kw2)
        assert out.read_bytes() == BODY_3MB
        # Segment 0 bytes must not have been requested again.
        for _, _, headers in opener2.requests:
            rng = headers.get("Range", headers.get("range", ""))
            if rng.startswith("bytes="):
                start = int(rng[len("bytes=") :].split("-")[0])
                assert start >= first.end + 1, f"refetched completed segment: {rng}"

    def test_stale_state_is_discarded(self, tmp_path):
        from voice_typer.server import segmented_download as seg

        _, kw = _engine_kwargs(FakeOpener(BODY_3MB), tmp_path, num_segments=3)
        seg.write_state(
            tmp_path / "model.bin.state.json",
            url="https://cdn.example/models/OTHER.bin",  # different URL
            etag=kw["etag"],
            total_size=len(BODY_3MB),
            expected_sha256=kw["expected_sha256"],
            segments=[{"index": 0, "start": 0, "end": 10, "done": True}],
        )
        opener = FakeOpener(BODY_3MB)
        _, kw2 = _engine_kwargs(opener, tmp_path, num_segments=3)
        out = seg.download_file_segmented(**kw2)
        assert out.read_bytes() == BODY_3MB


# ── Integrity ──────────────────────────────────────────────────────────


class TestIntegrity:
    def test_sha_mismatch_discards_parts_and_raises(self, tmp_path):
        from voice_typer.server import segmented_download as seg

        opener = FakeOpener(BODY_3MB)
        _, kw = _engine_kwargs(opener, tmp_path, expected_sha256="0" * 64)
        with pytest.raises(seg.SegmentedDownloadError):
            seg.download_file_segmented(**kw)
        # Poisoned parts must not linger for a later retry to trust.
        assert not list(tmp_path.rglob("*.part"))

    def test_expected_sha_required(self, tmp_path):
        from voice_typer.server import segmented_download as seg

        opener = FakeOpener(BODY_3MB)
        _, kw = _engine_kwargs(opener, tmp_path, expected_sha256=None)
        with pytest.raises((seg.SegmentedDownloadError, TypeError, ValueError)):
            seg.download_file_segmented(**kw)


# ── Cache layout ───────────────────────────────────────────────────────


class TestCacheLayout:
    def _layout_args(self, tmp_path):
        from voice_typer.server import segmented_download as seg

        cache = tmp_path / "hub"
        assembled = tmp_path / "assembled.bin"
        assembled.write_bytes(BODY_3MB)
        return seg, dict(
            cache_dir=cache,
            repo_id="Systran/faster-whisper-tiny",
            commit="abc123",
            filename="model.bin",
            blob_sha256=_sha(BODY_3MB),
            assembled_path=assembled,
        )

    def test_writes_blob_and_symlink(self, tmp_path):
        seg, kw = self._layout_args(tmp_path)
        snap_file = seg.install_blob_into_hf_cache(**kw)
        assert snap_file.read_bytes() == BODY_3MB
        assert (kw["cache_dir"] / "blobs" / kw["blob_sha256"]).exists()

    def test_symlink_fallback_copies_when_symlinks_unsupported(self, tmp_path, monkeypatch):
        seg, kw = self._layout_args(tmp_path)
        monkeypatch.setattr(os, "symlink", lambda *a, **k: (_ for _ in ()).throw(OSError("privilege")))
        snap_file = seg.install_blob_into_hf_cache(**kw)
        assert snap_file.read_bytes() == BODY_3MB
        assert not snap_file.is_symlink()


# ── Disk-full ──────────────────────────────────────────────────────────


class TestDiskFull:
    def test_enospc_surfaces_as_error_not_retry_loop(self, tmp_path, monkeypatch):
        from voice_typer.server import segmented_download as seg

        opener = FakeOpener(BODY_3MB)
        _, kw = _engine_kwargs(opener, tmp_path, num_segments=1)

        def failing_open(*a, **k):
            raise OSError(errno.ENOSPC, "No space left on device")

        # Fail part-file writes: ENOSPC must surface, not loop.
        monkeypatch.setattr("io.open", failing_open)
        with pytest.raises(OSError):
            seg.download_file_segmented(**kw)
