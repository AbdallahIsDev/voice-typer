"""Focused tests for segmented-download helper contracts.

Covers two previously-divergent spots in ``voice_typer/server/segmented_download.py``:
the lengthless-HEAD Range-probe's ETag source, and the retry classification
that must go through the shared ``_is_transient_http`` helper (E7).
"""

from __future__ import annotations

import pytest
from voice_typer.server import segmented_download as seg


class _Response:
    def __init__(self, status: int, headers: dict[str, str], body: bytes = b""):
        self.status = status
        self.headers = dict(headers)
        self._body = body

    def getheader(self, name, default=None):
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return default

    def read(self, amt=-1):
        data, self._body = self._body, b""
        return data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Opener:
    """Returns queued responses; falls back to the last one when exhausted."""

    def __init__(self, *responses: _Response):
        self._responses = list(responses)
        self.requests: list[tuple[str, str, dict]] = []

    def open(self, request, timeout=None):
        method = request.get_method()
        self.requests.append((method, request.full_url, dict(request.header_items())))
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def test_range_probe_reads_etag_from_response_not_request(monkeypatch):
    """Lengthless HEAD + non-206 probe → the probe response's ETag is returned."""
    monkeypatch.setattr(seg, "MAX_REDIRECTS", 0)
    opener = _Opener(
        _Response(200, {}),  # lengthless HEAD
        _Response(200, {"ETag": '"probe-etag"'}),  # probe answered 200, not 206
    )

    final_url, total, etag = seg.resolve_download(
        "https://example.invalid/model.bin",
        opener_factory=lambda: opener,
    )

    assert final_url == "https://example.invalid/model.bin"
    assert total is None
    assert etag == '"probe-etag"', "ETag must come from the response, not the request headers"
    # The probe request carries only the Range header — never an ETag.
    assert "ETag" not in opener.requests[-1][2]


def test_range_probe_still_reads_etag_on_206(monkeypatch):
    """206 probe keeps resolving total + ETag from the response."""
    monkeypatch.setattr(seg, "MAX_REDIRECTS", 0)
    opener = _Opener(
        _Response(200, {}),
        _Response(206, {"Content-Range": "bytes 0-0/4096", "ETag": '"partial"'}, b"x"),
    )

    _url, total, etag = seg.resolve_download(
        "https://example.invalid/model.bin",
        opener_factory=lambda: opener,
    )

    assert total == 4096
    assert etag == '"partial"'


def test_fetch_segment_classifies_retries_through_shared_helper(tmp_path, monkeypatch):
    """The retry decision must call ``_is_transient_http`` (single source of truth)."""
    calls: list[int] = []
    real_helper = seg._is_transient_http

    def spy(status: int) -> bool:
        calls.append(status)
        return real_helper(status)

    monkeypatch.setattr(seg, "_is_transient_http", spy)
    monkeypatch.setattr(seg, "RETRY_BACKOFF_S", (0.0,))

    opener = _Opener(
        _Response(503, {"Retry-After": "0"}),  # transient → retry
        _Response(206, {"Content-Range": "bytes 0-9/10"}, b"0123456789"),
    )
    part_path = tmp_path / "seg0.part"
    segment = seg.SegmentRange(index=0, start=0, end=9)

    written = seg._fetch_segment(
        opener=opener,
        url="https://example.invalid/model.bin",
        seg=segment,
        part_path=part_path,
        headers={},
        timeout_s=5.0,
        gate_check=None,
        on_bytes=lambda _n: None,
    )

    assert written == 10
    assert part_path.read_bytes() == b"0123456789"
    assert 503 in calls, "the transient-HTTP helper must be the classification path"


def test_resolve_within_allows_nested_relative_path(tmp_path):
    root = tmp_path / "snapshots"
    root.mkdir()
    resolved = seg._resolve_within(root, "model/weights.bin")
    assert resolved == (root / "model" / "weights.bin").resolve()
    assert root.resolve() in resolved.parents


@pytest.mark.parametrize(
    "bad",
    ["../escape.bin", "a/../../escape.bin", "/etc/passwd", "C:/windows/system32/x.dll"],
)
def test_resolve_within_rejects_escapes(tmp_path, bad):
    root = tmp_path / "snapshots"
    root.mkdir()
    with pytest.raises(seg.SegmentedDownloadError):
        seg._resolve_within(root, bad)


def test_install_blob_rejects_traversal_filename(tmp_path):
    cache = tmp_path / "hf-cache"
    assembled = tmp_path / "assembled.bin"
    assembled.write_bytes(b"payload")

    with pytest.raises(seg.SegmentedDownloadError):
        seg.install_blob_into_hf_cache(
            cache_dir=cache,
            repo_id="org/model",
            commit="abc123",
            filename="../escaped.bin",
            blob_sha256="deadbeef",
            assembled_path=assembled,
        )

    assert not (cache / "escaped.bin").exists()


def test_install_blob_places_nested_snapshot_file(tmp_path):
    cache = tmp_path / "hf-cache"
    assembled = tmp_path / "assembled.bin"
    assembled.write_bytes(b"payload")

    placed = seg.install_blob_into_hf_cache(
        cache_dir=cache,
        repo_id="org/model",
        commit="abc123",
        filename="model/weights.bin",
        blob_sha256="cafebabe",
        assembled_path=assembled,
    )

    assert placed == (cache / "snapshots" / "abc123" / "model" / "weights.bin").resolve()
    assert placed.exists()
    assert placed.read_bytes() == b"payload"
