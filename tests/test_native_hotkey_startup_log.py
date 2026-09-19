"""Startup-log defects in the native hotkey backend."""

from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path

import pytest
from voice_typer.server import native_hotkeys
from voice_typer.server.native_hotkeys import binary_path


@pytest.fixture(autouse=True)
def _clean_verification_cache():
    binary_path.clear_verified_cache()
    yield
    binary_path.clear_verified_cache()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
    monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
    monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)


def _make_windows_backend():
    monkeypatch_platform = pytest.MonkeyPatch()
    monkeypatch_platform.setattr(native_hotkeys, "is_windows", lambda: True)
    monkeypatch_platform.setattr(native_hotkeys, "is_macos", lambda: False)
    monkeypatch_platform.setattr(native_hotkeys, "is_linux", lambda: False)
    monkeypatch_platform.setattr(sys, "platform", "win32")
    try:
        from voice_typer.server.native_hotkeys import WindowsHookHotkey

        backend = WindowsHookHotkey("<f2>")
    finally:
        monkeypatch_platform.undo()
    return backend


_STARTUP_LINES = [
    "2026-09-18T12:00:00.000 [1234] windows-key-listener starting; spec=<f2>; log_file=C:\\logs\\native-windows.log",
    "2026-09-18T12:00:00.010 [1234] stdin reader thread started (PING/PONG enabled)",
    "2026-09-18T12:00:00.020 [1234] keyboard hook installed",
    "2026-09-18T12:00:00.030 [1234] READY emitted; version=1.0.0",
]


class TestDiagnosticLinesRecognized:
    def test_known_diagnostic_lines_not_unrecognized(self, caplog):
        backend = _make_windows_backend()
        with caplog.at_level(logging.DEBUG):
            caplog.clear()
            for line in _STARTUP_LINES:
                backend._handle_line(line)
        unrecognized = [r for r in caplog.records if "Unrecognized line" in r.getMessage()]
        assert unrecognized == []

    def test_diagnostic_lines_update_liveness_timestamp(self):
        backend = _make_windows_backend()
        backend._last_event_received_at = 0.0
        for line in _STARTUP_LINES:
            backend._handle_line(line)
            assert backend._last_event_received_at > 0.0
            backend._last_event_received_at = 0.0

    def test_ready_emitted_diagnostic_does_not_set_ready(self):
        backend = _make_windows_backend()
        assert backend._ready_event.is_set() is False
        backend._handle_line(_STARTUP_LINES[3])
        assert backend._ready_event.is_set() is False
        assert backend._binary_version is None

    def test_wire_ready_still_sets_ready(self):
        backend = _make_windows_backend()
        backend._handle_line("READY")
        assert backend._ready_event.is_set() is True

    def test_truly_unknown_lines_still_logged(self, caplog):
        backend = _make_windows_backend()
        with caplog.at_level(logging.DEBUG):
            caplog.clear()
            backend._handle_line("SOMETHING-NEW:totally-unknown-payload")
        unrecognized = [r for r in caplog.records if "Unrecognized line" in r.getMessage()]
        assert len(unrecognized) == 1


class TestVerificationCache:
    def _patch_expected_hash(self, monkeypatch, path: Path) -> str:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        monkeypatch.setattr(binary_path, "get_expected_sha256", lambda _name: digest)
        return digest

    def test_second_verify_of_same_file_skips_rehash(self, monkeypatch, tmp_path):
        candidate = tmp_path / "windows-key-listener-x86_64.exe"
        candidate.write_bytes(b"fake-binary-bytes")
        self._patch_expected_hash(monkeypatch, candidate)

        calls = {"n": 0}
        real_verify = binary_path.verify_native_binary

        def counting_verify(path, expected):
            calls["n"] += 1
            return real_verify(path, expected)

        monkeypatch.setattr(binary_path, "verify_native_binary", counting_verify)

        assert binary_path.verify_native_binary_or_skip(candidate) is True
        assert binary_path.verify_native_binary_or_skip(candidate) is True
        assert calls["n"] == 1

    def test_changed_file_is_reverified(self, monkeypatch, tmp_path):
        candidate = tmp_path / "windows-key-listener-x86_64.exe"
        candidate.write_bytes(b"first-bytes-padded........")
        first_digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        monkeypatch.setattr(binary_path, "get_expected_sha256", lambda _name: first_digest)

        assert binary_path.verify_native_binary_or_skip(candidate) is True

        candidate.write_bytes(b"second-bytes-different-size!")
        second_digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        monkeypatch.setattr(binary_path, "get_expected_sha256", lambda _name: second_digest)

        calls = {"n": 0}
        real_verify = binary_path.verify_native_binary

        def counting_verify(path, expected):
            calls["n"] += 1
            return real_verify(path, expected)

        monkeypatch.setattr(binary_path, "verify_native_binary", counting_verify)
        assert binary_path.verify_native_binary_or_skip(candidate) is True
        assert calls["n"] == 1

    def test_distinct_binary_is_verified_before_first_use(self, monkeypatch, tmp_path):
        first = tmp_path / "windows-key-listener-x86_64.exe"
        first.write_bytes(b"first-binary")
        second = tmp_path / "other-copy.exe"
        second.write_bytes(b"first-binary")

        digests = {first.name: hashlib.sha256(first.read_bytes()).hexdigest()}
        monkeypatch.setattr(
            binary_path,
            "get_expected_sha256",
            lambda name: digests.get(name, hashlib.sha256(second.read_bytes()).hexdigest()),
        )

        calls = {"n": 0}
        real_verify = binary_path.verify_native_binary

        def counting_verify(path, expected):
            calls["n"] += 1
            return real_verify(path, expected)

        monkeypatch.setattr(binary_path, "verify_native_binary", counting_verify)
        assert binary_path.verify_native_binary_or_skip(first) is True
        assert binary_path.verify_native_binary_or_skip(second) is True
        assert calls["n"] == 2

    def test_failed_verification_is_not_cached(self, monkeypatch, tmp_path):
        candidate = tmp_path / "windows-key-listener-x86_64.exe"
        candidate.write_bytes(b"tampered")
        monkeypatch.setattr(binary_path, "get_expected_sha256", lambda _name: "0" * 64)

        assert binary_path.verify_native_binary_or_skip(candidate) is False
        assert binary_path.verify_native_binary_or_skip(candidate) is False
