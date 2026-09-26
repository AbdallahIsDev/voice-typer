"""Targeted-write behavior of ``scripts/build/sync_versions.py``."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SYNC_VERSIONS_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "sync_versions.py"

_TAB_PACKAGE_JSON = (
    "{\n"
    '\t"name": "lausu-desktop",\n'
    '\t"version": "1.0.0",\n'
    '\t"private": true,\n'
    '\t"engines": {\n'
    '\t\t"node": ">=24"\n'
    "\t},\n"
    '\t"keywords": [\n'
    '\t\t"voice",\n'
    '\t\t"dictation"\n'
    "\t]\n"
    "}\n"
)


def _load_sync_versions():
    spec = importlib.util.spec_from_file_location("_vt_sync_versions_write_test", SYNC_VERSIONS_SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {SYNC_VERSIONS_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sync_versions():
    return _load_sync_versions()


def _write_package(sync_versions, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str) -> Path:
    path = tmp_path / "package.json"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    monkeypatch.setattr(sync_versions, "PACKAGE_JSON", path)
    return path


class TestWritePackageJsonVersion:
    def test_updates_only_the_version_value(self, sync_versions, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """A bump rewrites ONLY the top-level version value: the parsed"""
        path = _write_package(sync_versions, monkeypatch, tmp_path, _TAB_PACKAGE_JSON)

        sync_versions.write_package_json_version("2.0.0")

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == "2.0.0"
        assert data["name"] == "lausu-desktop"
        assert data["engines"] == {"node": ">=24"}
        # The read side must observe the new value too.
        assert sync_versions.read_package_json_version() == "2.0.0"
        raw = path.read_text(encoding="utf-8")
        assert '\t"name": "lausu-desktop",' in raw  # tabs preserved
        assert '\t\t"node": ">=24"' in raw  # nested tabs preserved
        assert '  "name"' not in raw  # no space-indentation churn
        assert '\t"version": "2.0.0",' in raw  # targeted line rewritten

    def test_nested_version_key_is_never_touched(self, sync_versions, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """A nested ``\"version\"`` key (deeper indentation, appearing before"""
        text = '{\n\t"build": {\n\t\t"version": "7.7.7"\n\t},\n\t"version": "1.0.0"\n}\n'
        path = _write_package(sync_versions, monkeypatch, tmp_path, text)

        sync_versions.write_package_json_version("2.0.0")

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == "2.0.0"
        assert data["build"]["version"] == "7.7.7"

    def test_writing_the_current_version_is_byte_identical(
        self, sync_versions, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """A no-op sync must not touch the file at all (no timestamp-free"""
        path = _write_package(sync_versions, monkeypatch, tmp_path, _TAB_PACKAGE_JSON)
        before = path.read_bytes()

        sync_versions.write_package_json_version("1.0.0")

        assert path.read_bytes() == before

    def test_file_without_version_key_is_left_unchanged(
        self, sync_versions, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """No top-level ``version`` key → no injection (mirrors the other"""
        text = '{\n\t"name": "lausu-desktop"\n}\n'
        path = _write_package(sync_versions, monkeypatch, tmp_path, text)
        before = path.read_bytes()

        sync_versions.write_package_json_version("2.0.0")

        assert path.read_bytes() == before

    def test_preserves_crlf_line_endings(self, sync_versions, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """CRLF files round-trip with CRLF (newline translation is disabled"""
        crlf = _TAB_PACKAGE_JSON.replace("\n", "\r\n")
        path = _write_package(sync_versions, monkeypatch, tmp_path, crlf)

        sync_versions.write_package_json_version("2.0.0")

        raw: str
        with open(path, encoding="utf-8", newline="") as fh:
            raw = fh.read()
        assert '\r\n\t"version": "2.0.0",' in raw
        assert '\r\n\t"name"' in raw

    def test_preserves_missing_trailing_newline(self, sync_versions, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """A file without a trailing newline keeps that shape (the old"""
        path = _write_package(sync_versions, monkeypatch, tmp_path, _TAB_PACKAGE_JSON.rstrip("\n"))

        sync_versions.write_package_json_version("2.0.0")

        raw = path.read_text(encoding="utf-8")
        assert not raw.endswith("\n")
        assert json.loads(raw)["version"] == "2.0.0"
