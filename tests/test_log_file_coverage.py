"""Pin the cross-runtime log-file coverage contract (review.md MO-108)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_diagnostics_module():
    """Import ``scripts/diagnostics.py`` (not a package module)."""
    spec = importlib.util.spec_from_file_location("vt_diagnostics_under_test", REPO_ROOT / "scripts" / "diagnostics.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(relative: str) -> str:
    path = REPO_ROOT / relative
    assert path.is_file(), f"missing source file: {relative}"
    return path.read_text(encoding="utf-8")


def test_diagnostics_collects_every_log_in_logs_dir(tmp_path: Path) -> None:
    """Every regular file in ``logs/`` lands in the bundle under its"""
    module = _load_diagnostics_module()
    config_dir = tmp_path / "config"
    logs_dir = config_dir / "logs"
    logs_dir.mkdir(parents=True)

    # Ground truth on-disk set (no zip-side rename):
    on_disk = [
        "voice-typer.log",
        "voice-typer.log.1",
        "voice-typer-rust.log",
        "sidecar.log",
        "worker.log",
        "startup-error.log",
        "voice-typer-crash-buffer.log",
        "native-windows.log",
    ]
    for name in on_disk:
        (logs_dir / name).write_text(f"content of {name}\n", encoding="utf-8")
    (logs_dir / "voice-typer.log.lock").write_text("1", encoding="utf-8")
    (logs_dir / "nested").mkdir()  # directory: not a log file

    dest = tmp_path / "bundle"
    dest.mkdir()
    collected = module._collect_logs_into(config_dir, dest)

    # Every on-disk log ships under its exact on-disk basename (no
    assert sorted(collected) == sorted(on_disk), (
        "the diagnostics bundle must collect EVERY log in logs/ under its "
        "on-disk basename; a hardcoded name list silently drops new logs"
    )
    for name in on_disk:
        assert (dest / name).is_file(), f"{name} missing from the bundle"
        assert (dest / name).read_text(encoding="utf-8") == f"content of {name}\n"
    assert not (dest / "voice-typer.log.lock").exists(), "inter-process truncation locks must not ship in the bundle"


def test_diagnostics_legacy_root_and_current_python_do_not_collide(tmp_path: Path) -> None:
    """Legacy root ``<config>/voice-typer.log`` and the current Python log"""
    module = _load_diagnostics_module()
    config_dir = tmp_path / "config"
    (config_dir / "logs").mkdir(parents=True)
    (config_dir / "voice-typer.log").write_text("legacy python\n", encoding="utf-8")
    (config_dir / "logs" / "voice-typer.log").write_text("current python\n", encoding="utf-8")
    (config_dir / "logs" / "voice-typer-rust.log").write_text("rust host\n", encoding="utf-8")

    dest = tmp_path / "bundle"
    dest.mkdir()
    collected = module._collect_logs_into(config_dir, dest)

    # ASCII: '-' (0x2D) < '.' (0x2E), so voice-typer-rust sorts first.
    assert sorted(collected) == [
        "voice-typer-rust.log",
        "voice-typer.log",
        "voice-typer.log-2",
    ]
    assert (dest / "voice-typer.log").read_text(encoding="utf-8") == "legacy python\n"
    assert (dest / "voice-typer.log-2").read_text(encoding="utf-8") == "current python\n"
    assert (dest / "voice-typer-rust.log").read_text(encoding="utf-8") == "rust host\n"


def test_diagnostics_unexpected_name_collision_is_not_silently_overwritten(
    tmp_path: Path,
) -> None:
    """A file that would collide with an already-collected zip name is"""
    module = _load_diagnostics_module()
    # Direct unit: `_unique_zip_name` is the never-overwrite backstop.
    taken = {"voice-typer.log"}
    assert module._unique_zip_name("voice-typer.log", taken) == "voice-typer.log-2"
    taken.add("voice-typer.log-2")
    assert module._unique_zip_name("voice-typer.log", taken) == "voice-typer.log-3"
    assert module._unique_zip_name("sidecar.log", taken) == "sidecar.log"

    # Integration: two same-basename logs cannot coexist on disk in one
    config_dir = tmp_path / "config"
    (config_dir / "logs").mkdir(parents=True)
    (config_dir / "logs" / "voice-typer.log").write_text("current\n", encoding="utf-8")
    (config_dir / "logs" / "voice-typer-rust.log").write_text("rust host\n", encoding="utf-8")

    dest = tmp_path / "bundle"
    dest.mkdir()
    collected = module._collect_logs_into(config_dir, dest)

    # ASCII: '-' < '.', so voice-typer-rust sorts first.
    assert sorted(collected) == ["voice-typer-rust.log", "voice-typer.log"]
    contents = {name: (dest / name).read_text(encoding="utf-8") for name in collected}
    assert contents["voice-typer.log"] == "current\n"
    assert contents["voice-typer-rust.log"] == "rust host\n"


def test_diagnostics_collects_legacy_root_log(tmp_path: Path) -> None:
    """A pre-migration profile keeps ``<config_dir>/voice-typer.log`` at"""
    module = _load_diagnostics_module()
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "voice-typer.log").write_text("legacy\n", encoding="utf-8")

    dest = tmp_path / "bundle"
    dest.mkdir()
    collected = module._collect_logs_into(config_dir, dest)

    assert "voice-typer.log" in collected
    assert (dest / "voice-typer.log").is_file()


def test_diagnostics_collector_has_no_hardcoded_log_glob() -> None:
    """missed ``voice-typer-rust.log`` (the Rust host's log), ``sidecar.log``,"""
    source = _read("scripts/diagnostics.py")
    assert 'glob("voice-typer.log*")' not in source
    assert "_collect_logs_into" in source


def test_python_sweep_is_directory_scoped() -> None:
    source = _read("voice_typer/server/log/setup.py")
    # The sweep walks the logs dir and skips only the ``.lock`` files.
    assert "root.iterdir()" in source
    assert 'f.name.endswith(".lock")' in source
    # A hardcoded per-name sweep list would be the regression.
    assert "_LOGS_TO_SWEEP" not in source


def test_rust_sweep_is_directory_scoped() -> None:
    source = _read("src-tauri/src/platform/logging/init.rs")
    assert "std::fs::read_dir(logs_dir)" in source
    assert 'name.ends_with(".lock")' in source


def test_rust_host_log_basename_matches_diagnostics_expectation() -> None:
    """The Rust host writes ``voice-typer-rust`` — the name the bundle"""
    init_source = _read("src-tauri/src/platform/logging/init.rs")
    assert 'RotatingFileWriter::new(logs_dir.clone(), "voice-typer-rust")' in init_source

    child_log_source = _read("src-tauri/src/sidecar/child_log.rs")
    assert "sidecar.log" in child_log_source, (
        "the MO-104 child tee basename must stay sidecar.log (the bundle + runbooks reference it)"
    )


def test_open_logs_target_is_the_logs_dir() -> None:
    source = _read("src-tauri/src/commands/system_cmds/dialogs.rs")
    assert 'config_dir.join("logs")' in source, (
        "Open logs must open <config_dir>/logs, the same directory the sweeps and the diagnostics bundle read"
    )
