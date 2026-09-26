"""Mock-level tests for ``scripts/verify_worker_handoff_preflight.py``."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_worker_handoff_preflight.py"


def _load():
    spec = importlib.util.spec_from_file_location("_vt_worker_preflight", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_passes_on_real_repo():
    module = _load()
    repo = Path(__file__).resolve().parents[1]
    passed, total = module.run_preflight(repo)
    assert passed == total, "preflight must pass on the current repo state"


def test_preflight_fails_on_empty_dir(tmp_path):
    module = _load()
    passed, total = module.run_preflight(tmp_path)
    assert passed == 0
    assert total == len(module.CHECKS)


def test_preflight_reports_each_check_by_name(tmp_path, capsys):
    module = _load()
    module.run_preflight(tmp_path)
    out = capsys.readouterr().out
    for name, _ in module.CHECKS:
        assert name in out
    assert "NEEDS HOST RUN" in out


def test_worker_module_check_detects_missing_file(tmp_path):
    module = _load()
    worker_dir = tmp_path / "voice_typer" / "worker"
    worker_dir.mkdir(parents=True)
    (worker_dir / "__main__.py").write_text("# stub", encoding="utf-8")
    ok, detail = module.check_worker_module(tmp_path)
    assert not ok
    assert "_ws_server.py" in detail


def test_supervisor_check_detects_missing_pins(tmp_path):
    module = _load()
    path = tmp_path / "src-tauri" / "src" / "sidecar"
    path.mkdir(parents=True)
    (path / "worker_supervisor.rs").write_text("// empty", encoding="utf-8")
    ok, detail = module.check_rust_supervisor(tmp_path)
    assert not ok
    assert "respawn_worker" in detail


def test_main_returns_zero_on_real_repo():
    module = _load()
    repo = str(Path(__file__).resolve().parents[1])
    assert module.main(["preflight", repo]) == 0


def test_main_returns_nonzero_on_empty_dir(tmp_path):
    module = _load()
    assert module.main(["preflight", str(tmp_path)]) == 1
