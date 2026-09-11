"""Tests for ``scripts/run_bench.py`` (the ``make bench`` driver).

The driver replaced a ~500-char inline Python heredoc in the Makefile
whose hand-maintained script list duplicated the contents of ``bench/``.
These tests pin the driver's output contract so the refactor cannot
silently change what the CI perf ratchet (``.github/workflows/perf.yml``)
consumes:

  * glob discovery of ``bench_*.py`` (sorted, non-matching files ignored),
  * per-script invocation ``<sys.executable> <script> --json``,
  * success → the script's parsed JSON,
  * non-zero exit → ``{"skipped": true, "error": <last stderr line>}``
    (or ``"failed"`` when stderr is empty),
  * missing script → ``{"skipped": true, "error": "missing"}``,
  * the output document shape: ``version``/``generated_at``/``results``
    with the UTC ISO-8601 seconds-precision ``Z`` timestamp,
  * ``main()`` writes ``bench-current.json`` (indent=2 + trailing
    newline) at the configured output path.

The subprocess runner is injected/faked, no real bench script runs.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

# Make the scripts/ directory importable so we can load run_bench as a
# module (same pattern as tests/test_coverage_ratchet_strict.py).
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_bench  # noqa: E402

_GENERATED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _fake_runner(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> tuple[Callable[..., subprocess.CompletedProcess], list[list[str]]]:
    """Build an injectable runner that records every argv it receives."""
    calls: list[list[str]] = []

    def runner(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    return runner, calls


class TestBuildBenchOutput:
    def test_runs_every_bench_script_sorted_and_passes_json_flag(self, tmp_path: Path):
        """Glob discovery: every bench_*.py runs once, sorted, with --json;
        non-matching files in the directory are ignored."""
        (tmp_path / "bench_zeta.py").touch()
        (tmp_path / "bench_alpha.py").touch()
        (tmp_path / "helper.py").touch()  # not a bench script, must be skipped
        runner, calls = _fake_runner(stdout='{"metric": 1}')

        out = run_bench.build_bench_output(tmp_path, runner=runner)

        assert list(out["results"]) == ["bench_alpha", "bench_zeta"]
        assert len(calls) == 2
        for cmd, stem in zip(calls, ("bench_alpha", "bench_zeta"), strict=True):
            assert cmd == [sys.executable, str(tmp_path / f"{stem}.py"), "--json"]

    def test_successful_bench_contributes_its_parsed_json(self, tmp_path: Path):
        script = tmp_path / "bench_ok.py"
        script.touch()
        runner, _ = _fake_runner(stdout='{"first_run_ms": 12.5}')

        out = run_bench.build_bench_output(tmp_path, runner=runner)

        assert out["results"]["bench_ok"] == {"first_run_ms": 12.5}

    def test_failed_bench_reports_skip_marker_with_last_stderr_line(self, tmp_path: Path):
        script = tmp_path / "bench_fail.py"
        script.touch()
        runner, _ = _fake_runner(returncode=1, stderr="noise line\nreal error")

        out = run_bench.build_bench_output(tmp_path, runner=runner)

        assert out["results"]["bench_fail"] == {"skipped": True, "error": "real error"}

    def test_failed_bench_with_empty_stderr_reports_failed(self, tmp_path: Path):
        script = tmp_path / "bench_fail.py"
        script.touch()
        runner, _ = _fake_runner(returncode=1, stderr="")

        out = run_bench.build_bench_output(tmp_path, runner=runner)

        assert out["results"]["bench_fail"] == {"skipped": True, "error": "failed"}

    def test_missing_script_reports_missing_without_spawning_a_process(self, tmp_path: Path):
        runner, calls = _fake_runner()

        entry = run_bench._run_one(tmp_path / "bench_absent.py", runner=runner)

        assert entry == {"skipped": True, "error": "missing"}
        assert calls == []  # no subprocess for a file the glob lost

    def test_output_document_shape(self, tmp_path: Path):
        (tmp_path / "bench_one.py").touch()
        runner, _ = _fake_runner(stdout="{}")

        out = run_bench.build_bench_output(tmp_path, runner=runner)

        assert out["version"] == 1
        assert _GENERATED_AT_RE.match(out["generated_at"]), out["generated_at"]
        assert set(out) == {"version", "generated_at", "results"}

    def test_empty_bench_directory_produces_empty_results(self, tmp_path: Path):
        runner, _ = _fake_runner()

        out = run_bench.build_bench_output(tmp_path, runner=runner)

        assert out["results"] == {}


class TestMain:
    def test_writes_bench_current_json_with_contract_format(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """main() delegates to build_bench_output with the module BENCH_DIR,
        writes <out>/bench-current.json: indent=2 + trailing newline, and
        prints the comparison pointer line."""
        output_path = tmp_path / "bench-current.json"
        canned = {
            "version": 1,
            "generated_at": "2026-09-10T12:00:00Z",
            "results": {"bench_a": {"v": 1}, "bench_b": {"v": 1}},
        }
        seen: dict[str, Path] = {}

        def fake_build(bench_dir: Path, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> dict:
            seen["bench_dir"] = bench_dir
            return canned

        monkeypatch.setattr(run_bench, "build_bench_output", fake_build)
        monkeypatch.setattr(run_bench, "BENCH_DIR", tmp_path / "bench")
        monkeypatch.setattr(run_bench, "OUTPUT_PATH", output_path)

        exit_code = run_bench.main()

        assert exit_code == 0
        assert seen["bench_dir"] == tmp_path / "bench"  # main passes the module BENCH_DIR through
        written = output_path.read_text(encoding="utf-8")
        assert written.endswith("\n") and not written.endswith("\n\n")
        doc = json.loads(written)
        assert doc == canned
        # indent=2 formatting (matches the pre-refactor heredoc output).
        assert '\n  "version": 1,' in written
        printed = capsys.readouterr().out
        assert "bench-current.json" in printed
        assert "bench/bench-baseline.json" in printed
