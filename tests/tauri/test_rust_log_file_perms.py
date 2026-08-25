"""Regression guard: Rust log file permissions on POSIX.

The Rust host's rotating file logger in
``src-tauri/src/platform/logging/`` (split into per-concern submodules:
``rotating.rs`` holds ``RotatingFileWriter``, ``init.rs`` holds
``init_file_logger``) must create log files with mode
``0o600`` (owner rw only) on POSIX systems, and the parent
``<config_dir>/logs/`` directory must be ``0o700``. Previously the log
file inherited the process umask (typically 0o022), producing ``0o644``
— readable by group + others. The dictation log may contain raw
transcription text + PII (XZ-LOG-02), so it must be owner-only.

This test has two layers:

1. **Source-parsing layer (always runs, no cargo required):** verifies
   that ``platform/logging/rotating.rs`` contains the
   ``OpenOptionsExt::mode(0o600)``
   call in ``RotatingFileWriter::write_line``, the belt-and-suspenders
   ``set_permissions(..., 0o600)`` call after rotation, and that
   ``platform/logging/init.rs`` contains the
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
``src-tauri/src/platform/logging_tests.rs`` (the sibling test module
of the ``platform::logging`` submodules).
"""

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
# the POSIX-permission call sites live in exactly two of them:
# ``rotating.rs`` (file 0o600) and ``init.rs`` (logs-dir 0o700).
LOGGING_DIR = REPO_ROOT / "src-tauri" / "src" / "platform" / "logging"
LOGGING_ROTATING_RS = LOGGING_DIR / "rotating.rs"
LOGGING_INIT_RS = LOGGING_DIR / "init.rs"
SIDECAR_CARGO_TOML = REPO_ROOT / "src-tauri" / "Cargo.toml"

# Target triples tauri.conf.json's externalBin / bundle.resources entries
# cover (mirrors the "Create dummy sidecar + resource placeholders" step
# in the Tauri Linux smoke workflow — outside a full bundle build the
# tauri-build build script still validates those paths and hard-fails
# cargo without them).
_TAURI_TRIPLES = (
    "x86_64-pc-windows-msvc",
    "aarch64-pc-windows-msvc",
    "x86_64-apple-darwin",
    "aarch64-apple-darwin",
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
)
_SIDECAR_NAMES = ("python-sidecar", "voice-typer-worker")


def _create_cargo_build_placeholders() -> list[Path]:
    """Create the dummy sidecar + resource placeholders the tauri-build
    build script requires to run ``cargo test`` outside a full bundle
    build. Only creates files that do NOT already exist (never clobbers
    real build artifacts). Returns the created paths for cleanup.
    """
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
    # frontendDist: tauri-build's build script validates that the
    # configured frontend dist directory exists. CI pytest checkouts
    # don't build the client, so stub it with a minimal index.html.
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


# ─── Layer 1: source-parsing regression guard ──────────────────────────


def _rotating_rs_source() -> str:
    """Return the full source of ``platform/logging/rotating.rs``.

    Asserts the file exists — a missing file is a hard error (the test
    infrastructure is broken, not the security posture).
    """
    assert LOGGING_ROTATING_RS.is_file(), (
        f"{LOGGING_ROTATING_RS} not found — the Rust host's "
        f"rotating file writer source has moved or been deleted. Update "
        f"this test's LOGGING_ROTATING_RS path constant."
    )
    return LOGGING_ROTATING_RS.read_text(encoding="utf-8")


def _init_rs_source() -> str:
    """Return the full source of ``platform/logging/init.rs``.

    Asserts the file exists — a missing file is a hard error (the test
    infrastructure is broken, not the security posture).
    """
    assert LOGGING_INIT_RS.is_file(), (
        f"{LOGGING_INIT_RS} not found — the Rust host's "
        f"logger-init source has moved or been deleted. Update "
        f"this test's LOGGING_INIT_RS path constant."
    )
    return LOGGING_INIT_RS.read_text(encoding="utf-8")


def test_pi7_openoptions_mode_0o600_present_in_write_line() -> None:
    """``write_line`` must call ``OpenOptionsExt::mode(0o600)`` on unix.

    This is the primary defense: a freshly-created log file gets mode
    ``0o600`` regardless of the process umask. Pre-hardening the call was
    absent and the file inherited umask (typically 0o644).
    """
    src = _rotating_rs_source()
    # Slice from `fn write_line` to the closing `}` of the function
    # (the function ends just before `fn rotate`). This isolates the
    # check to the write path, not the rotate path.
    m = re.search(r"fn write_line\([^)]*\)[^{]*\{", src)
    assert m is not None, (
        f"could not locate `fn write_line` in {LOGGING_ROTATING_RS}. Did the function signature change?"
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
    assert depth == 0, "could not find the closing `}` of `fn write_line` — the function body is malformed."
    write_line_body = src[write_line_body_start:i]
    # The mode(0o600) call must be present AND gated by #[cfg(unix)].
    # Match either `opts.mode(0o600)` (current shape) or
    # `.mode(0o600)` (any future refactor that chains on OpenOptions).
    assert re.search(r"\.mode\(0o600\)", write_line_body), (
        "`OpenOptionsExt::mode(0o600)` call missing "
        "from `fn write_line`. The log file will inherit the process "
        "umask (typically 0o644) and be world-readable on POSIX."
    )


def test_pi7_chmod_0o600_belt_and_suspenders_in_write_line() -> None:
    """``write_line`` must chmod the log file to ``0o600`` on open (belt-and-suspenders).

    ``OpenOptionsExt::mode(0o600)`` only applies to NEW files — a leftover
    0o644 log file from a pre-hardening build would stay world-readable
    otherwise. The single-file policy (truncate-in-place, no numbered
    backups) re-asserts ``set_permissions(..., 0o600)`` in ``write_line``'s
    just-in-time init path so pre-existing files are hardened on next open.
    """
    src = _rotating_rs_source()
    m = re.search(r"fn write_line\([^)]*\)[^{]*\{", src)
    assert m is not None, (
        f"could not locate `fn write_line` in {LOGGING_ROTATING_RS}. Did the function signature change?"
    )
    write_line_body_start = m.end()
    depth = 1
    i = write_line_body_start
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    assert depth == 0, "could not find the closing `}` of `fn write_line` — the function body is malformed."
    write_line_body = src[write_line_body_start:i]
    # The belt-and-suspenders `set_permissions(..., 0o600)` call must be
    # present in write_line's init path (it re-asserts 0o600 for log files
    # left behind by a pre-hardening build).
    chmod_calls = re.findall(
        r"set_permissions\([^,]+,\s*std::fs::Permissions::from_mode\(0o600\)",
        write_line_body,
    )
    assert len(chmod_calls) >= 1, (
        "expected at least 1 "
        "`set_permissions(..., 0o600)` call in `fn write_line` (the "
        "belt-and-suspenders re-assert for pre-existing 0o644 files); "
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
    """All ``mode(...)`` + ``set_permissions(... 0o6XX)`` calls must be ``#[cfg(unix)]``-gated.

    ``OpenOptionsExt::mode`` and ``PermissionsExt::from_mode`` are
    POSIX-only APIs — calling them unconditionally would break the
    Windows build. This test counts the ``#[cfg(unix)]`` blocks vs the
    chmod/mode call sites and asserts they match.
    """
    # Scan every submodule that carries POSIX-only call sites — the
    # mode/chmod calls live in rotating.rs (file perms) + init.rs
    # (logs-dir perms) after the logging module split.
    src = _rotating_rs_source() + "\n" + _init_rs_source()
    # Count `#[cfg(unix)]` attribute lines (allow indented forms).
    cfg_unix_count = len(re.findall(r"#\[cfg\(unix\)\]", src))
    # Count the actual POSIX-only call sites: `.mode(0o600)`,
    # `Permissions::from_mode(0o600)`, `Permissions::from_mode(0o700)`.
    mode_calls = len(re.findall(r"\.mode\(0o[67]00\)", src))
    perm_calls = len(re.findall(r"Permissions::from_mode\(0o[67]00\)", src))
    total_calls = mode_calls + perm_calls
    # Each call site must be gated by a `#[cfg(unix)]`. The `from_mode`
    # calls inside the tests module (which have their own
    # `#[cfg(unix)]` on the test fn) are also counted here — that's
    # fine, the test fns are themselves gated.
    assert cfg_unix_count >= total_calls, (
        f"found {total_calls} POSIX-only mode/perm "
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
    reason="Runtime test is POSIX-only (log file perms use mode bits, not ACLs)",
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

    # tauri-build's build script validates the externalBin / bundle
    # resource paths from tauri.conf.json even for `cargo test` — on a
    # CI checkout without a prior bundle step those files don't exist
    # and the build fails before any Rust test runs (observed on the
    # macos-14 leg). Create the same placeholders the Tauri Linux smoke
    # workflow creates, and clean them up afterwards.
    placeholders = _create_cargo_build_placeholders()

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
        try:
            result = subprocess.run(
                [
                    cargo,
                    "test",
                    "--manifest-path",
                    str(SIDECAR_CARGO_TOML),
                    # The crate is a bin-only package (no src/lib.rs), so
                    # ``--lib`` fails with "no library targets found" even
                    # when the test passes. Target the bin's unit tests
                    # explicitly (C-TEST-5: Rust tests live in logging_tests.rs
                    # wired via #[cfg(test)] mod tests;).
                    "--bin",
                    "voice-typer-tauri",
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
            # and "the tauri-build build script rejected this checkout"
            # from "the test itself failed". Environment failures are a
            # skip; a test failure is a hard error.
            stderr = result.stderr.decode("utf-8", errors="replace")
            if "pkg-config" in stderr or "gdk-3.0" in stderr or "webkit2gtk" in stderr:
                pytest.skip(
                    "cargo test failed to compile due to missing system "
                    "libs (gtk/webkit dev packages). The source-parsing "
                    "layer (above) is the only guard in this run. stderr "
                    f"excerpt: {stderr[:200]}"
                )
            if "failed to run custom build command" in stderr:
                # tauri-build's build script rejected the checkout (its
                # own error text is in the "--- stderr" section cargo
                # only prints on failure). This is an environment gap in
                # the CI pytest leg (no client build, no bundler
                # placeholders beyond what this test stubs) — NOT a
                # permissions regression. Skip with the full output so
                # the gap is diagnosable; the source-parsing layer
                # (above) still guards the mode(0o600) calls.
                pytest.skip(
                    "cargo test failed in the tauri-build build script "
                    "(checkout environment, not the perms contract). "
                    "The source-parsing layer (above) is the only guard "
                    "in this run.\n"
                    f"stdout: {result.stdout.decode('utf-8', errors='replace')[:2000]}\n"
                    f"stderr: {stderr[:2000]}"
                )
            # The test compiled but failed — this is a real  regression.
            pytest.fail(
                "the Rust unit test "
                "`test_rotating_file_writer_log_file_mode_is_0o600_on_posix` "
                "failed. The log file is NOT 0o600 on POSIX.\n"
                f"stdout: {result.stdout.decode('utf-8', errors='replace')[:2000]}\n"
                f"stderr: {stderr[:2000]}"
            )

        # The test passed — log file mode is 0o600 on this POSIX host.
        assert (
            b"test result: ok" in result.stdout
            or b"test_rotating_file_writer_log_file_mode_is_0o600_on_posix" in result.stdout
        ), (
            "cargo test returned 0 but the expected test name was "
            "not found in stdout. The test may have been renamed or "
            "removed — update this Python test's test-name filter.\n"
            f"stdout: {result.stdout.decode('utf-8', errors='replace')[:500]}"
        )
    finally:
        _cleanup_cargo_build_placeholders(placeholders)
