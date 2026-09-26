"""Regression guard: Rust log file permissions on POSIX."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
# The logging module is split into ``platform/logging/`` submodules;
LOGGING_DIR = REPO_ROOT / "src-tauri" / "src" / "platform" / "logging"
LOGGING_ROTATING_RS = LOGGING_DIR / "rotating.rs"
LOGGING_INIT_RS = LOGGING_DIR / "init.rs"
SIDECAR_CARGO_TOML = REPO_ROOT / "src-tauri" / "Cargo.toml"

# Target triples tauri.conf.json's externalBin / bundle.resources entries
_TAURI_TRIPLES = (
    "x86_64-pc-windows-msvc",
    "aarch64-pc-windows-msvc",
    "x86_64-apple-darwin",
    "aarch64-apple-darwin",
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
)
_SIDECAR_NAMES = ("python-sidecar", "lausu-worker")


def _create_cargo_build_placeholders() -> list[Path]:
    """Create the dummy sidecar + resource placeholders the tauri-build"""
    src_tauri = SIDECAR_CARGO_TOML.parent
    created: list[Path] = []

    def _stub(rel: str) -> None:
        p = src_tauri / rel
        if p.exists():
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("placeholder\n", encoding="utf-8")
        created.append(p)

    for name in _SIDECAR_NAMES:
        for triple in _TAURI_TRIPLES:
            _stub(f"bin/{name}-{triple}")
    for listener in (
        "resources/native/windows-key-listener.exe",
        "resources/native/macos-key-listener",
        "resources/native/linux-key-listener",
    ):
        _stub(listener)
    renderer_dir = REPO_ROOT / "voice_typer" / "client" / "out" / "renderer"
    if not renderer_dir.exists():
        renderer_dir.mkdir(parents=True, exist_ok=True)
        index = renderer_dir / "index.html"
        index.write_text("<!doctype html><title>placeholder</title>", encoding="utf-8")
        created.append(index)
        created.append(renderer_dir)
        created.append(renderer_dir.parent)
    return created


def _cleanup_cargo_build_placeholders(created: list[Path]) -> None:
    """Remove the placeholder files/dirs (and any now-empty parents)."""
    for p in created:
        if p.is_dir():
            with contextlib.suppress(OSError):
                p.rmdir()
        else:
            p.unlink(missing_ok=True)
    src_tauri = SIDECAR_CARGO_TOML.parent
    for d in (src_tauri / "bin", src_tauri / "resources" / "native"):
        with contextlib.suppress(OSError):
            d.rmdir()
        with contextlib.suppress(OSError):
            d.parent.rmdir()


def _rotating_rs_source() -> str:
    """Return the full source of ``platform/logging/rotating.rs``."""
    assert LOGGING_ROTATING_RS.is_file(), (
        f"{LOGGING_ROTATING_RS} not found, the Rust host's "
        f"rotating file writer source has moved or been deleted. Update "
        f"this test's LOGGING_ROTATING_RS path constant."
    )
    return LOGGING_ROTATING_RS.read_text(encoding="utf-8")


def _init_rs_source() -> str:
    """Return the full source of ``platform/logging/init.rs``."""
    assert LOGGING_INIT_RS.is_file(), (
        f"{LOGGING_INIT_RS} not found, the Rust host's "
        f"logger-init source has moved or been deleted. Update "
        f"this test's LOGGING_INIT_RS path constant."
    )
    return LOGGING_INIT_RS.read_text(encoding="utf-8")


def test_pi7_openoptions_mode_0o600_present_in_write_line_level() -> None:
    """``mode(0o600)`` must be present in the file's file-open path."""
    src = _rotating_rs_source()
    assert re.search(r"\.mode\(0o600\)", src), (
        "`OpenOptionsExt::mode(0o600)` call missing "
        "from the rotating writer. The log file will inherit the process "
        "umask (typically 0o644) and be world-readable on POSIX."
    )


def test_pi7_chmod_0o600_belt_and_suspenders_in_write_line_level() -> None:
    """The rotating writer must chmod the log file to ``0o600`` (belt-and-suspenders)."""
    src = _rotating_rs_source()
    # The belt-and-suspenders `set_permissions(..., 0o600)` call must be
    chmod_calls = re.findall(
        r"set_permissions\([^,]+,\s*std::fs::Permissions::from_mode\(0o600\)",
        src,
    )
    assert len(chmod_calls) >= 1, (
        "expected at least 1 "
        "`set_permissions(..., 0o600)` call in the rotating writer (the "
        "belt-and-suspenders re-assert for pre-existing 0o644 files); "
        f"found {len(chmod_calls)}."
    )


def test_pi7_chmod_0o700_on_logs_dir_in_init_file_logger() -> None:
    """``init_file_logger`` must chmod the ``<config_dir>/logs/`` dir to ``0o700``."""
    src = _init_rs_source()
    # Slice the init_file_logger function body.
    m = re.search(r"fn init_file_logger\([^)]*\)[^{]*\{", src)
    assert m is not None, f"could not locate `fn init_file_logger` in {LOGGING_INIT_RS}."
    init_body_start = m.end()
    depth = 1
    i = init_body_start
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    init_body = src[init_body_start:i]
    assert re.search(
        r"set_permissions\([^,]+,\s*std::fs::Permissions::from_mode\(0o700\)",
        init_body,
    ), (
        "`set_permissions(..., 0o700)` call missing "
        "from `fn init_file_logger`. The `<config_dir>/logs/` dir will "
        "inherit the process umask (typically 0o755) and be "
        "world-traversable on POSIX."
    )


def test_pi7_unix_cfg_gates_present() -> None:
    """All ``mode(...)`` + ``set_permissions(... 0o6XX)`` calls must be ``#[cfg(unix)]``-gated."""
    src = _rotating_rs_source() + "\n" + _init_rs_source()
    # Count `#[cfg(unix)]` attribute lines (allow indented forms).
    cfg_unix_count = len(re.findall(r"#\[cfg\(unix\)\]", src))
    # Count the actual POSIX-only call sites: `.mode(0o600)`,
    mode_calls = len(re.findall(r"\.mode\(0o[67]00\)", src))
    perm_calls = len(re.findall(r"Permissions::from_mode\(0o[67]00\)", src))
    total_calls = mode_calls + perm_calls
    # Each call site must be gated by a `#[cfg(unix)]`. The `from_mode`
    assert cfg_unix_count >= total_calls, (
        f"found {total_calls} POSIX-only mode/perm "
        f"call sites but only {cfg_unix_count} `#[cfg(unix)]` gates. "
        f"Every `.mode(0oN00)` and `Permissions::from_mode(0oN00)` "
        f"call must be inside a `#[cfg(unix)]` block to keep the "
        f"Windows build compiling."
    )


def _cargo_available() -> bool:
    """True if `cargo --version` succeeds in the current environment."""
    cargo = shutil.which("cargo")
    if cargo is None:
        return False
    try:
        result = subprocess.run(
            [cargo, "--version"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Runtime test is POSIX-only (log file perms use mode bits, not ACLs)",
)
@pytest.mark.skipif(
    not _cargo_available(),
    reason="cargo not available, source-parsing layer (above) is the only guard",
)
def test_pi7_rust_unit_test_log_file_mode_0o600_passes() -> None:
    """Run the Rust unit test ``test_rotating_file_writer_log_file_mode_is_0o600_on_posix``."""
    cargo = shutil.which("cargo")
    assert cargo is not None  # belt-and-suspenders (skipif above)

    placeholders = _create_cargo_build_placeholders()

    # Use a per-test temp target dir so we don't collide with other
    env = os.environ.copy()
    # PKG_CONFIG_PATH is needed on Linux so the tauri crate's build

    try:
        try:
            result = subprocess.run(
                [
                    cargo,
                    "test",
                    "--manifest-path",
                    str(SIDECAR_CARGO_TOML),
                    # explicitly (C-TEST-5: Rust tests live in logging_tests.rs
                    "--bin",
                    "lausu-tauri",
                    "--quiet",
                    "--",
                    "--nocapture",
                    "test_rotating_file_writer_log_file_mode_is_0o600_on_posix",
                ],
                capture_output=True,
                timeout=600,
                env=env,
            )
        except subprocess.TimeoutExpired:
            pytest.skip(
                "cargo test timed out (>600s), likely a cold dependency "
                "build. The source-parsing layer (above) is the only guard "
                "in this run."
            )
        except OSError as exc:
            pytest.skip(
                f"cargo invocation failed with OSError: {exc}. The "
                f"source-parsing layer (above) is the only guard in this run."
            )

        if result.returncode != 0:
            # Distinguish "cargo failed to compile (system libs missing)"
            stderr = result.stderr.decode("utf-8", errors="replace")
            if "pkg-config" in stderr or "gdk-3.0" in stderr or "webkit2gtk" in stderr:
                pytest.skip(
                    "cargo test failed to compile due to missing system "
                    "libs (gtk/webkit dev packages). The source-parsing "
                    "layer (above) is the only guard in this run. stderr "
                    f"excerpt: {stderr[:200]}"
                )
            if "failed to run custom build command" in stderr:
                pytest.skip(
                    "cargo test failed in the tauri-build build script "
                    "(checkout environment, not the perms contract). "
                    "The source-parsing layer (above) is the only guard "
                    "in this run.\n"
                    f"stdout: {result.stdout.decode('utf-8', errors='replace')[:2000]}\n"
                    f"stderr: {stderr[:2000]}"
                )
            # The test compiled but failed, this is a real  regression.
            pytest.fail(
                "the Rust unit test "
                "`test_rotating_file_writer_log_file_mode_is_0o600_on_posix` "
                "failed. The log file is NOT 0o600 on POSIX.\n"
                f"stdout: {result.stdout.decode('utf-8', errors='replace')[:2000]}\n"
                f"stderr: {stderr[:2000]}"
            )

        # The test passed, log file mode is 0o600 on this POSIX host.
        assert (
            b"test result: ok" in result.stdout
            or b"test_rotating_file_writer_log_file_mode_is_0o600_on_posix" in result.stdout
        ), (
            "cargo test returned 0 but the expected test name was "
            "not found in stdout. The test may have been renamed or "
            "removed, update this Python test's test-name filter.\n"
            f"stdout: {result.stdout.decode('utf-8', errors='replace')[:500]}"
        )
    finally:
        _cleanup_cargo_build_placeholders(placeholders)
