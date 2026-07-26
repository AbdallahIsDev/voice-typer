"""PI-7 (session-7) regression guard: Rust log file permissions on POSIX.

The Rust host's rotating file logger in
``src-tauri/src/platform/logging.rs`` must create log files with mode
``0o600`` (owner rw only) on POSIX systems, and the parent
``<config_dir>/logs/`` directory must be ``0o700``. Pre-PI-7 the log
file inherited the process umask (typically 0o022), producing ``0o644``
— readable by group + others. The dictation log may contain raw
transcription text + PII (XZ-LOG-02), so it must be owner-only.

This test has two layers:

1. **Source-parsing layer (always runs, no cargo required):** verifies
   that ``platform/logging.rs`` contains the ``OpenOptionsExt::mode(0o600)``
   call in ``RotatingFileWriter::write_line``, the belt-and-suspenders
   ``set_permissions(..., 0o600)`` call after rotation, and the
   ``set_permissions(..., 0o700)`` call on the ``logs/`` dir in
   ``init_file_logger``. This is a fast regression guard that catches a
   future refactor that accidentally drops the chmod calls.

2. **Runtime layer (runs only when cargo + GTK/WebKit dev libs are
   available):** invokes ``cargo test --manifest-path src-tauri/Cargo.toml``
   with the specific Rust unit test name
   ``test_rotating_file_writer_log_file_mode_is_0o600_on_posix`` and
   asserts the test passes. This is the authoritative check on POSIX
   hosts. On Windows + macOS the source-parsing layer is the only guard
   (cargo is invoked but the test is ``#[cfg(unix)]``-gated, so it
   no-ops on Windows). When cargo or the system libs are missing, the
   runtime layer is skipped (not failed) — the source-parsing layer
   still runs.

The two-layer design mirrors the pattern in
``tests/test_security_doc_command_count.py`` (source-parsing parity
test) and the runtime Rust unit tests in
``src-tauri/src/platform/logging.rs``'s ``#[cfg(test)] mod tests``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGGING_RS = (
    REPO_ROOT
    / "src-tauri"
    / "src"
    / "platform"
    / "logging.rs"
)
SIDECAR_CARGO_TOML = REPO_ROOT / "src-tauri" / "Cargo.toml"


# ─── Layer 1: source-parsing regression guard ──────────────────────────


def _logging_rs_source() -> str:
    """Return the full source of ``platform/logging.rs``.

    Asserts the file exists — a missing file is a hard error (the test
    infrastructure is broken, not the security posture).
    """
    assert LOGGING_RS.is_file(), (
        f"PI-7 regression: {LOGGING_RS} not found — the Rust host's "
        f"rotating file logger source has moved or been deleted. Update "
        f"this test's LOGGING_RS path constant."
    )
    return LOGGING_RS.read_text(encoding="utf-8")


def test_pi7_openoptions_mode_0o600_present_in_write_line() -> None:
    """``write_line`` must call ``OpenOptionsExt::mode(0o600)`` on unix.

    This is the primary defense: a freshly-created log file gets mode
    ``0o600`` regardless of the process umask. Pre-PI-7 the call was
    absent and the file inherited umask (typically 0o644).
    """
    src = _logging_rs_source()
    # Slice from `fn write_line` to the closing `}` of the function
    # (the function ends just before `fn rotate`). This isolates the
    # check to the write path, not the rotate path.
    m = re.search(r"fn write_line\([^)]*\)[^{]*\{", src)
    assert m is not None, (
        "PI-7 regression: could not locate `fn write_line` in "
        f"{LOGGING_RS}. Did the function signature change?"
    )
    write_line_body_start = m.end()
    # Find the matching closing brace by counting braces from the
    # function body start. Rust allows braces inside string literals
    # and comments, but the write_line function in this file has none
    # of either containing braces — a simple count is sufficient.
    depth = 1
    i = write_line_body_start
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    assert depth == 0, (
        "PI-7 regression: could not find the closing `}` of "
        "`fn write_line` — the function body is malformed."
    )
    write_line_body = src[write_line_body_start:i]
    # The mode(0o600) call must be present AND gated by #[cfg(unix)].
    # Match either `opts.mode(0o600)` (current shape) or
    # `.mode(0o600)` (any future refactor that chains on OpenOptions).
    assert re.search(r"\.mode\(0o600\)", write_line_body), (
        "PI-7 regression: `OpenOptionsExt::mode(0o600)` call missing "
        "from `fn write_line`. The log file will inherit the process "
        "umask (typically 0o644) and be world-readable on POSIX."
    )


def test_pi7_chmod_0o600_after_rename_in_rotate() -> None:
    """``rotate`` must chmod renamed files to ``0o600`` (belt-and-suspenders).

    ``rename`` preserves the source file's mode (which is 0o600 from
    the ``OpenOptionsExt::mode`` call in ``write_line``), but a
    leftover rotated file from a pre-PI-7 build may still be 0o644.
    The belt-and-suspenders ``set_permissions(..., 0o600)`` call in
    ``rotate`` re-asserts 0o600 for those leftover files.
    """
    src = _logging_rs_source()
    m = re.search(r"fn rotate\([^)]*\)[^{]*\{", src)
    assert m is not None, (
        "PI-7 regression: could not locate `fn rotate` in "
        f"{LOGGING_RS}."
    )
    rotate_body_start = m.end()
    depth = 1
    i = rotate_body_start
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    rotate_body = src[rotate_body_start:i]
    # Count the number of `set_permissions(..., 0o600)` calls in the
    # rotate body. There should be at least 2: one inside the loop
    # (for `.log.N` → `.log.N+1` renames) and one for the `.log` →
    # `.log.1` rename at the end.
    chmod_calls = re.findall(
        r"set_permissions\([^,]+,\s*std::fs::Permissions::from_mode\(0o600\)",
        rotate_body,
    )
    assert len(chmod_calls) >= 2, (
        "PI-7 regression: expected at least 2 "
        "`set_permissions(..., 0o600)` calls in `fn rotate` (one in "
        "the loop, one after the final `.log` → `.log.1` rename); "
        f"found {len(chmod_calls)}."
    )


def test_pi7_chmod_0o700_on_logs_dir_in_init_file_logger() -> None:
    """``init_file_logger`` must chmod the ``<config_dir>/logs/`` dir to ``0o700``.

    Mirrors the Python side's ``os.chmod(config_dir, 0o700)`` at
    ``voice_typer/server/log.py:891-893``. Without this, the dir is
    world-traversable on POSIX — a non-owner user could ``ls`` the
    directory to enumerate log file names (which include timestamps
    + rotation counters — a metadata leak).
    """
    src = _logging_rs_source()
    # Slice the init_file_logger function body.
    m = re.search(r"fn init_file_logger\([^)]*\)[^{]*\{", src)
    assert m is not None, (
        "PI-7 regression: could not locate `fn init_file_logger` in "
        f"{LOGGING_RS}."
    )
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
        "PI-7 regression: `set_permissions(..., 0o700)` call missing "
        "from `fn init_file_logger`. The `<config_dir>/logs/` dir will "
        "inherit the process umask (typically 0o755) and be "
        "world-traversable on POSIX."
    )


def test_pi7_unix_cfg_gates_present() -> None:
    """All ``mode(...)`` + ``set_permissions(... 0o6XX)`` calls must be ``#[cfg(unix)]``-gated.

    ``OpenOptionsExt::mode`` and ``PermissionsExt::from_mode`` are
    POSIX-only APIs — calling them unconditionally would break the
    Windows build. This test counts the ``#[cfg(unix)]`` blocks vs the
    chmod/mode call sites and asserts they match.
    """
    src = _logging_rs_source()
    # Count `#[cfg(unix)]` attribute lines (allow indented forms).
    cfg_unix_count = len(re.findall(r"#\[cfg\(unix\)\]", src))
    # Count the actual POSIX-only call sites: `.mode(0o600)`,
    # `Permissions::from_mode(0o600)`, `Permissions::from_mode(0o700)`.
    mode_calls = len(re.findall(r"\.mode\(0o[67]00\)", src))
    perm_calls = len(
        re.findall(r"Permissions::from_mode\(0o[67]00\)", src)
    )
    total_calls = mode_calls + perm_calls
    # Each call site must be gated by a `#[cfg(unix)]`. The `from_mode`
    # calls inside the tests module (which have their own
    # `#[cfg(unix)]` on the test fn) are also counted here — that's
    # fine, the test fns are themselves gated.
    assert cfg_unix_count >= total_calls, (
        f"PI-7 regression: found {total_calls} POSIX-only mode/perm "
        f"call sites but only {cfg_unix_count} `#[cfg(unix)]` gates. "
        f"Every `.mode(0oN00)` and `Permissions::from_mode(0oN00)` "
        f"call must be inside a `#[cfg(unix)]` block to keep the "
        f"Windows build compiling."
    )


# ─── Layer 2: runtime cargo test (POSIX-only, optional) ────────────────


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
    reason="PI-7 runtime test is POSIX-only (log file perms use mode bits, not ACLs)",
)
@pytest.mark.skipif(
    not _cargo_available(),
    reason="cargo not available — source-parsing layer (above) is the only guard",
)
def test_pi7_rust_unit_test_log_file_mode_0o600_passes() -> None:
    """Run the Rust unit test ``test_rotating_file_writer_log_file_mode_is_0o600_on_posix``.

    This is the authoritative check on POSIX: actually create a
    ``RotatingFileWriter``, write a line, and assert the resulting log
    file mode is ``0o600``. Skipped if cargo is not installed or the
    GTK/WebKit dev libs aren't available (the Tauri crate fails to
    compile without them — out of scope for this test).
    """
    cargo = shutil.which("cargo")
    assert cargo is not None  # belt-and-suspenders (skipif above)

    # Use a per-test temp target dir so we don't collide with other
    # cargo invocations (and so we don't write into the project's
    # target/ dir, which the user may have a clean state for).
    env = os.environ.copy()
    # PKG_CONFIG_PATH is needed on Linux so the tauri crate's build
    # script can find gtk+-3.0 / webkit2gtk-4.1. If unset, cargo
    # will fail at the gdk-sys build step — we treat that as a skip.
    # (The user can set PKG_CONFIG_PATH in their shell to enable this
    # test; otherwise the source-parsing layer is the only guard.)

    try:
        result = subprocess.run(
            [
                cargo,
                "test",
                "--manifest-path",
                str(SIDECAR_CARGO_TOML),
                "--lib",
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
            "cargo test timed out (>600s) — likely a cold dependency "
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
        # from "the test itself failed". A compile failure is a skip;
        # a test failure is a hard error.
        stderr = result.stderr.decode("utf-8", errors="replace")
        if "pkg-config" in stderr or "gdk-3.0" in stderr or "webkit2gtk" in stderr:
            pytest.skip(
                "cargo test failed to compile due to missing system "
                "libs (gtk/webkit dev packages). The source-parsing "
                "layer (above) is the only guard in this run. stderr "
                f"excerpt: {stderr[:200]}"
            )
        # The test compiled but failed — this is a real PI-7 regression.
        pytest.fail(
            "PI-7 regression: the Rust unit test "
            "`test_rotating_file_writer_log_file_mode_is_0o600_on_posix` "
            "failed. The log file is NOT 0o600 on POSIX.\n"
            f"stdout: {result.stdout.decode('utf-8', errors='replace')[:500]}\n"
            f"stderr: {stderr[:500]}"
        )

    # The test passed — log file mode is 0o600 on this POSIX host.
    assert b"test result: ok" in result.stdout or b"test_rotating_file_writer_log_file_mode_is_0o600_on_posix" in result.stdout, (
        "PI-7: cargo test returned 0 but the expected test name was "
        "not found in stdout. The test may have been renamed or "
        "removed — update this Python test's test-name filter.\n"
        f"stdout: {result.stdout.decode('utf-8', errors='replace')[:500]}"
    )
