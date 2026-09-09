"""Tests for scripts/ci/write_ci_errors.py (the CI-errors.md generator).

Covers the three silent-miss regressions: illegal XML characters must
not nuke a whole file's parse, truncated files must degrade to
partial extraction (never a lone "unparseable" pseudo-entry), and
every leg's file must be parsed independently.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci" / "write_ci_errors.py"


def _load():
    spec = importlib.util.spec_from_file_location("write_ci_errors", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["write_ci_errors"] = mod
    spec.loader.exec_module(mod)
    return mod


def _junit(cases: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<testsuites><testsuite name="pytest" tests="1">{cases}</testsuite></testsuites>'
    ).encode()


def _failing_case() -> str:
    return (
        '<testcase classname="tests.test_x" name="test_boom">'
        '<failure message="AssertionError: boom">'
        "tests/test_x.py:12: in test_boom\n"
        "E   AssertionError: boom"
        "</failure></testcase>"
    )


def test_parses_failure_with_location(tmp_path: Path) -> None:
    mod = _load()
    f = tmp_path / "pytest-results.xml"
    f.write_bytes(_junit(_failing_case()))
    failures, note = mod._extract_failures(f)
    assert note == ""
    assert len(failures) == 1
    classname, name, leg, location, body = failures[0]
    assert (classname, name) == ("tests.test_x", "test_boom")
    assert location == "tests/test_x.py:12"
    assert "AssertionError: boom" in body


def test_illegal_xml_chars_do_not_break_parse(tmp_path: Path) -> None:
    """Raw control bytes in captured output must be sanitized away."""
    mod = _load()
    f = tmp_path / "pytest-results.xml"
    raw = _junit(_failing_case()).replace(b"boom", b"bo\x00om\x1b[31m")
    f.write_bytes(raw)
    failures, note = mod._extract_failures(f)
    assert note == ""
    assert len(failures) == 1


def test_truncated_file_falls_back_to_partial_extraction(tmp_path: Path) -> None:
    """A leg killed mid-write still surfaces its complete testcases."""
    mod = _load()
    f = tmp_path / "pytest-results.xml"
    case2 = _failing_case().replace("test_boom", "test_cut")
    head, sep, _ = _junit(_failing_case()).decode().partition("</testcase>")
    assert sep, "fixture must contain a complete first testcase"
    f.write_bytes((head + sep + case2[: len(case2) // 3]).encode())
    failures, note = mod._extract_failures(f)
    assert note != ""
    assert len(failures) == 1  # the complete first case survives
    assert failures[0][1] == "test_boom"


def test_empty_classname_renders_without_leading_dot(tmp_path: Path, capsys, monkeypatch) -> None:
    """Collection errors (empty classname) render as bare module path."""
    mod = _load()
    monkeypatch.setattr(mod, "OUTPUT", tmp_path / "CI-errors.md")
    d = tmp_path / "junit" / "pytest-results-ubuntu-22.04-3.12"
    d.mkdir(parents=True)
    f = d / "pytest-results.xml"
    f.write_bytes(
        _junit('<testcase classname="" name="tests.test_y"><error message="collection">oops</error></testcase>')
    )
    assert mod.main([str(f)]) == 0
    out = capsys.readouterr().out
    assert "wrote 1 failure" in out
    text = (tmp_path / "CI-errors.md").read_text(encoding="utf-8")
    assert "### 1. `tests.test_y`" in text
    assert "ubuntu-22.04-3.12" in text


def test_leg_label_from_artifact_layout(tmp_path: Path) -> None:
    mod = _load()
    assert mod._leg_of(tmp_path / "junit" / "pytest-results-windows-2022-3.13" / "x.xml") == "windows-2022-3.13"
