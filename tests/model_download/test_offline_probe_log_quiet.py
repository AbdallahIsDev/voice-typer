"""Quiet offline probe misses: no traceback for expected cache misses.

A fresh install has no (or only partial) HuggingFace snapshots, so the
local-only probes (``local_files_only=True``) routinely raise
``IncompleteSnapshotError`` / ``LocalEntryNotFoundError`` /
``CacheNotFound``. That is the expected state, not a fault: exactly one
concise DEBUG line, no traceback. Truly unexpected errors keep
``exc_info`` so they stay diagnosable.
"""

from __future__ import annotations

import logging

import pytest
import voice_typer.server.transcription_download as td
from huggingface_hub.errors import (
    CacheNotFound,
    IncompleteSnapshotError,
    LocalEntryNotFoundError,
)

_LOGGER = "voice_typer.server.transcription"


def _miss_records(caplog: pytest.LogCaptureFixture) -> list:
    return [r for r in caplog.records if "probe miss" in r.message or "probe failed" in r.message]


class TestProbeCacheQuietMiss:
    def test_incomplete_snapshot_logs_concise_line_without_traceback(self, caplog):
        def fake_snapshot(**kwargs):
            raise IncompleteSnapshotError("cached snapshot is incomplete: 1 file(s) missing", snapshot_path="/x")

        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            local_dir, integrity_failed = td.probe_cache(object(), fake_snapshot, "org/repo", "main", [], "tiny")
        assert (local_dir, integrity_failed) == (None, False)
        assert len(_miss_records(caplog)) == 1
        assert _miss_records(caplog)[0].exc_info is None
        assert "Traceback" not in caplog.text

    def test_unexpected_error_keeps_traceback(self, caplog):
        def fake_snapshot(**kwargs):
            raise RuntimeError("disk on fire")

        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            assert td.probe_cache(object(), fake_snapshot, "org/repo", "main", [], "tiny") == (None, False)
        assert len(_miss_records(caplog)) == 1
        assert _miss_records(caplog)[0].exc_info is not None


class TestWhisperSizeCachedQuietMiss:
    def test_local_entry_miss_logs_concise_line_without_traceback(self, tmp_config_dir, monkeypatch, caplog):
        def fake_snapshot(**kwargs):
            raise LocalEntryNotFoundError("no cached snapshot")

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            assert td.whisper_size_cached(object(), "tiny") is False
        assert len(_miss_records(caplog)) == 1
        assert _miss_records(caplog)[0].exc_info is None
        assert "Traceback" not in caplog.text


class TestSnapshotCompleteQuietMiss:
    def test_incomplete_snapshot_logs_concise_line_without_traceback(self, tmp_config_dir, monkeypatch, caplog):
        from voice_typer.server.config import _config_dir

        repo_dir = _config_dir() / "huggingface" / "hub" / "models--Systran--faster-whisper-tiny"
        repo_dir.mkdir(parents=True)

        def fake_snapshot(**kwargs):
            raise IncompleteSnapshotError(
                "The cached snapshot is incomplete: 1 file(s) are missing "
                "(vocabulary.json). Outgoing traffic is disabled ('local_files_only=True').",
                snapshot_path=str(repo_dir),
            )

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            assert td.is_model_snapshot_complete("Systran/faster-whisper-tiny") is False
        assert len(_miss_records(caplog)) == 1
        assert _miss_records(caplog)[0].exc_info is None
        assert "Traceback" not in caplog.text

    def test_cache_not_found_logs_concise_line_without_traceback(self, tmp_config_dir, monkeypatch, caplog):
        from voice_typer.server.config import _config_dir

        repo_dir = _config_dir() / "huggingface" / "hub" / "models--Systran--faster-whisper-tiny"
        repo_dir.mkdir(parents=True)

        def fake_snapshot(**kwargs):
            raise CacheNotFound("no cache entry", cache_dir=str(repo_dir))

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            assert td.is_model_snapshot_complete("Systran/faster-whisper-tiny") is False
        assert _miss_records(caplog)[0].exc_info is None
        assert "Traceback" not in caplog.text
