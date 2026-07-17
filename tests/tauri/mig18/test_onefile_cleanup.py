"""MIG-1.8 Phase 1 — Nuitka ``--onefile`` tempdir-spec + cleanup validation.

This test file validates the ``--onefile`` tempdir-spec + cleanup behavior
across all 3 Nuitka sidecar build scripts (Windows, macOS, Linux).
ADR-0020 §4 mandates that the onefile extraction dir is:

  - Pinned to a per-user cache dir (NOT system ``/tmp`` — avoids OS cleanup
    cycles that would force a re-extract on every launch).
  - Namespaced with ``voice-typer`` (avoids collision with other
    Nuitka-frozen apps that might use the same cache dir).
  - Set via ``--onefile-tempdir-spec`` (deterministic + cleanable by
    the installer/uninstaller).

Per-platform spec (per the MIG-1.8 task brief):

  - Windows: ``%LOCALAPPDATA%/voice-typer/onefile-tmp``
             (or ``$XDG_CACHE_HOME/voice-typer/onefile-tmp``)
  - macOS:   ``~/Library/Caches/voice-typer/onefile-tmp``
             (or ``$XDG_CACHE_HOME/voice-typer/onefile-tmp``)
  - Linux:   ``$XDG_CACHE_HOME/voice-typer/onefile-tmp``
             (or ``~/.cache/voice-typer/onefile-tmp``)

The Linux sandbox CANNOT run a real Nuitka build (no MSVC / Xcode /
python-build-standalone). These tests therefore validate the *structure*
of the build scripts — they parse the ``--onefile-tempdir-spec`` value
from each script and assert it matches the spec. The actual onefile
extraction + cleanup behavior MUST be verified on a real host using
the ``VALIDATE ON HOST`` commands below.

VALIDATE ON HOST (Windows — after building the sidecar):
    1. Build the sidecar:
         bash scripts/build/build_sidecar_windows.sh
    2. Launch the sidecar (it will self-extract to the tempdir-spec):
         src-tauri/bin/python-sidecar-x86_64-pc-windows-msvc.exe
    3. In another terminal, inspect the extract dir:
         dir "%LOCALAPPDATA%\\voice-typer\\onefile-tmp"
       Expected: ONE ``onefile_<PID>_<TIMESTAMP>`` subdir containing the
       extracted Python interpreter + .pyd + .dll payload.
    4. Exit the sidecar (Ctrl+C or taskkill /PID <pid>).
    5. Verify NO system-temp bloat (proves the tempdir-spec pin worked):
         dir "%TEMP%\\onefile_*"
       Expected: "File Not Found" (the onefile extract went to
       %LOCALAPPDATA%\\voice-typer\\onefile-tmp, NOT %TEMP%).
    6. Re-launch the sidecar — Nuitka should REUSE the existing extract
       dir (the tempdir-spec is deterministic, so re-extraction is
       skipped on the second launch → ~10× faster cold start).
       Verify only ONE ``onefile_*`` subdir exists (not many):
         dir "%LOCALAPPDATA%\\voice-typer\\onefile-tmp"
    7. Uninstall verification: the uninstaller should purge
       ``%LOCALAPPDATA%\\voice-typer\\onefile-tmp`` (match by the Voice
       Typer binary signature, NOT by dir name alone — other apps could
       reuse the ``voice-typer`` namespace).

VALIDATE ON HOST (macOS — after building the sidecar):
    1. Build the sidecar:
         bash scripts/build/build_sidecar_macos.sh aarch64
    2. Launch:
         src-tauri/bin/python-sidecar-aarch64-apple-darwin
    3. Inspect the extract dir:
         ls -la "$HOME/Library/Application Support/voice-typer/onefile-tmp"
       Expected: ONE ``onefile_<PID>_<TIMESTAMP>`` subdir.
    4. Exit the sidecar (Ctrl+C or ``kill <pid>``).
    5. Verify NO system-temp bloat:
         ls -d /tmp/onefile_* 2>/dev/null
       Expected: no output (the onefile extract went to
       ``$HOME/Library/Application Support/voice-typer/onefile-tmp``,
       NOT ``/tmp`` or ``$TMPDIR``).
    6. Verify the extract dir is user-owned (not root — would indicate
       the sidecar was launched with ``sudo``, which is a bug):
         stat -f "%Su" "$HOME/Library/Application Support/voice-typer/onefile-tmp"
       Expected: the current user's name (NOT ``root``).
    7. Re-launch + verify only ONE extract subdir exists (see Windows §6).

VALIDATE ON HOST (Linux — after building the sidecar):
    1. Build the sidecar:
         bash scripts/build/build_sidecar_linux.sh x86_64
    2. Launch:
         src-tauri/bin/python-sidecar-x86_64-unknown-linux-gnu
    3. Inspect the extract dir:
         ls -la "${XDG_CACHE_HOME:-$HOME/.cache}/voice-typer/onefile-tmp"
       Expected: ONE ``onefile_<PID>_<TIMESTAMP>`` subdir.
    4. Exit the sidecar (Ctrl+C).
    5. Verify NO system-temp bloat:
         ls -d /tmp/onefile_* 2>/dev/null
       Expected: no output.
    6. Verify the extract dir is user-owned:
         stat -c "%U" "${XDG_CACHE_HOME:-$HOME/.cache}/voice-typer/onefile-tmp"
       Expected: the current user's name (NOT ``root``).
    7. Verify systemd-tmpfiles will NOT purge the extract dir (it's
       under ``~/.cache``, NOT ``/tmp``, so systemd-tmpfiles-clean.service
       leaves it alone):
         systemctl status systemd-tmpfiles-clean.service
         # then check /usr/lib/tmpfiles.d/tmp.conf for the ~/.cache path
         # (it should NOT be listed — only /tmp and /var/tmp are cleaned).

References:
  - ADR-0020 §4.2 — Windows Nuitka ``--onefile`` tempdir-spec.
  - ADR-0020 §4.3 — macOS Nuitka ``--onefile`` tempdir-spec.
  - ADR-0020 §4.4 — Linux Nuitka ``--onefile`` tempdir-spec.
  - scripts/build/build_sidecar_{windows,macos,linux}.sh — the 3 scripts
    under test.
  - tests/tauri/mig15/test_nuitka_windows_build.py — sibling MIG-1.5 test.
  - tests/tauri/mig16/test_nuitka_macos_build.py — sibling MIG-1.6 test.
  - tests/tauri/mig17/test_nuitka_linux_build.py — sibling MIG-1.7 test.

Gaps documented (report, do NOT fix — out of scope for this gate check):
  - GAP-1 (macOS): ``build_sidecar_macos.sh`` uses
    ``--onefile-tempdir-spec="$HOME/Library/Application Support/voice-typer/onefile-tmp"``
    but the MIG-1.8 task spec requires ``~/Library/Caches/voice-typer/onefile-tmp``
    (or ``$XDG_CACHE_HOME/voice-typer/onefile-tmp``). "Application Support"
    IS a per-user dir (so the per-user + non-system-temp +
    voice-typer-namespaced checks all PASS), but it's NOT a "cache" dir
    per Apple's macOS conventions: ``~/Library/Caches`` is the standard
    cache location (periodically purgeable by macOS Storage Management),
    while ``~/Library/Application Support`` is for persistent app data
    that the app manages itself. ADR-0020 §4.3 (line ~436) explicitly
    says "Application Support" — there's a divergence between the ADR
    and the MIG-1.8 task spec. The macOS extract dir will accumulate
    stale onefile extracts across version upgrades and is never
    auto-purged by macOS. See ``test_known_gap_macos_uses_application_support_not_caches``
    and ``test_macos_tempdir_spec_per_user_cache_dir`` (xfail).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ─── Project paths ───────────────────────────────────────────────────────────
# This test file lives at tests/tauri/mig18/test_onefile_cleanup.py.
# Path from file → root:
#   parents[0] = mig18/
#   parents[1] = tauri/
#   parents[2] = tests/
#   parents[3] = <project root> (voice-typer/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_DIR = PROJECT_ROOT / "scripts" / "build"

BUILD_SCRIPTS: dict[str, Path] = {
    "windows": BUILD_DIR / "build_sidecar_windows.sh",
    "macos": BUILD_DIR / "build_sidecar_macos.sh",
    "linux": BUILD_DIR / "build_sidecar_linux.sh",
}

# Per-platform acceptable tempdir-spec patterns (per MIG-1.8 task spec).
# Each pattern is a regex that matches the ``--onefile-tempdir-spec`` value
# as it appears literally in the bash script (we do NOT expand bash vars;
# patterns use the script's own variable syntax like ``%LOCALAPPDATA%`` /
# ``$HOME`` / ``$XDG_CACHE_HOME``).
PLATFORM_TEMPDIR_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "windows": [
        # %LOCALAPPDATA%\voice-typer\onefile-tmp — the bash script source uses
        # \\ (escaped backslash in double quotes) so the file content has TWO
        # backslash chars; [/\\]+ matches one or more / or \ to handle both
        # the source (\\) and the runtime-expanded (\) forms.
        re.compile(r"%LOCALAPPDATA%[/\\]+voice-typer[/\\]+onefile-tmp"),
        # $XDG_CACHE_HOME/voice-typer/onefile-tmp (rare on Windows but allowed)
        re.compile(r"\$XDG_CACHE_HOME/voice-typer/onefile-tmp"),
    ],
    "macos": [
        # ~/Library/Caches/voice-typer/onefile-tmp
        re.compile(r"~/Library/Caches/voice-typer/onefile-tmp"),
        # $HOME/Library/Caches/voice-typer/onefile-tmp
        re.compile(r"\$HOME/Library/Caches/voice-typer/onefile-tmp"),
        # $XDG_CACHE_HOME/voice-typer/onefile-tmp (XDG override)
        re.compile(r"\$XDG_CACHE_HOME/voice-typer/onefile-tmp"),
    ],
    "linux": [
        # $XDG_CACHE_HOME/voice-typer/onefile-tmp
        re.compile(r"\$XDG_CACHE_HOME/voice-typer/onefile-tmp"),
        # $HOME/.cache/voice-typer/onefile-tmp
        re.compile(r"\$HOME/\.cache/voice-typer/onefile-tmp"),
        # ~/.cache/voice-typer/onefile-tmp
        re.compile(r"~/\.cache/voice-typer/onefile-tmp"),
        # ${XDG_CACHE_HOME:-$HOME/.cache}/voice-typer/onefile-tmp (bash default-expansion)
        re.compile(r"\$\{XDG_CACHE_HOME:-\$HOME/\.cache\}/voice-typer/onefile-tmp"),
    ],
}

# Forbidden system-temp patterns (the tempdir-spec must NOT match any of these).
# If the spec points at a system temp dir, the OS will periodically clean it
# (systemd-tmpfiles on Linux, macOS Storage Management, Windows Disk Cleanup),
# forcing a re-extract on the next launch (~10-15s cold-start latency) and
# potentially deleting files the sidecar is actively using mid-run.
FORBIDDEN_SYSTEM_TEMP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(^|[/\\])/tmp(/|$)", re.IGNORECASE),
    re.compile(r"(^|[/\\])/var/tmp(/|$)", re.IGNORECASE),
    re.compile(r"%TEMP%", re.IGNORECASE),
    re.compile(r"%TMP%", re.IGNORECASE),
    re.compile(r"\$TMPDIR", re.IGNORECASE),
    re.compile(r"(^|[/\\])/dev/shm(/|$)", re.IGNORECASE),
]


# ─── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def script_texts() -> dict[str, str]:
    """Read all 3 build scripts once per module; fail fast if any missing."""
    texts: dict[str, str] = {}
    for name, path in BUILD_SCRIPTS.items():
        assert path.is_file(), f"build_sidecar_{name}.sh not found at {path}. Did the project layout change?"
        texts[name] = path.read_text(encoding="utf-8")
    return texts


# ─── Helper: extract + resolve the --onefile-tempdir-spec value ──────────────
def _extract_tempdir_spec(text: str) -> str | None:
    """Extract the raw ``--onefile-tempdir-spec`` value from the script text.

    Returns the value with surrounding quotes stripped, or None if the flag
    is not present. Does NOT expand bash variables — the value is returned
    as it appears in the script source (e.g. ``$ONEFILE_TEMPDIR`` stays as
    ``$ONEFILE_TEMPDIR``).

    The regex REQUIRES the value to be quoted (``"..."`` or ``'...'``).
    This skips comment lines like ``# --onefile-tempdir-spec=$XDG_CACHE_HOME/...``
    which have an unquoted value — we only want the ACTUAL command-line
    invocation, not documentation comments.
    """
    # Match --onefile-tempdir-spec="VALUE" or --onefile-tempdir-spec='VALUE'.
    # The quote is REQUIRED (not optional) so comment lines with unquoted
    # values are skipped. The value char class excludes quotes + newline so
    # the regex can't run past the closing quote or off the line.
    m = re.search(
        r"--onefile-tempdir-spec=(?P<quote>[\"'])(?P<value>[^\"'\n]*?)(?P=quote)",
        text,
    )
    if m:
        return m.group("value")
    return None


def _extract_resolved_tempdir_spec(text: str) -> str | None:
    """Extract the ``--onefile-tempdir-spec`` value, resolving ``$VAR`` refs.

    If the spec value is a bare ``$VAR`` or ``${VAR}`` reference, look up
    ``VAR=...`` in the script text and return the variable's definition.
    Otherwise return the spec value as-is.

    This handles the Linux script's indirection:
        ONEFILE_TEMPDIR="${XDG_CACHE_HOME:-$HOME/.cache}/voice-typer/onefile-tmp"
        --onefile-tempdir-spec="$ONEFILE_TEMPDIR"
    → returns ``${XDG_CACHE_HOME:-$HOME/.cache}/voice-typer/onefile-tmp``.
    """
    raw = _extract_tempdir_spec(text)
    if raw is None:
        return None
    raw = raw.strip()
    # Check if the raw value is a pure $VAR or ${VAR} reference.
    var_match = re.fullmatch(r"\$\{?(?P<var>[A-Za-z_][A-Za-z0-9_]*)\}?", raw)
    if var_match:
        var_name = var_match.group("var")
        # Look for `VAR="..."` or `VAR='...'` or `VAR=...` assignment.
        var_def = re.search(
            r"(?m)^\s*" + re.escape(var_name) + r"\s*=\s*(?P<quote>[\"']?)(?P<defval>[^\"'\n]*?)(?P=quote)\s*$",
            text,
        )
        if var_def:
            return var_def.group("defval")
    return raw


# ─── 1. --onefile flag ───────────────────────────────────────────────────────
@pytest.mark.parametrize("platform", list(BUILD_SCRIPTS))
def test_script_uses_onefile_flag(script_texts: dict[str, str], platform: str):
    """All 3 scripts must use the bare ``--onefile`` flag.

    ``--onefile`` packages the entire Python interpreter + deps + app code
    into a single native binary. ADR-0020 §4 mandates this for all 3
    platforms (Windows §4.2, macOS §4.3, Linux §4.4).

    The regex ensures we match the BARE ``--onefile`` flag (as a standalone
    word), not just ``--onefile-tempdir-spec`` (which is a different flag).
    """
    text = script_texts[platform]
    # Bare --onefile flag: preceded by start-of-line/whitespace, followed by
    # end-of-line/whitespace (NOT followed by `-` which would make it a
    # different flag like --onefile-tempdir-spec).
    assert re.search(r"(^|\s)--onefile(?=\s|$)", text, re.MULTILINE), (
        f"build_sidecar_{platform}.sh must use the bare `--onefile` flag "
        "(ADR-0020 §4 mandates single-exe packaging for all 3 platforms). "
        "Note: --onefile-tempdir-spec is a DIFFERENT flag — the bare "
        "--onefile must also be present."
    )


# ─── 2. --onefile-tempdir-spec flag present ──────────────────────────────────
@pytest.mark.parametrize("platform", list(BUILD_SCRIPTS))
def test_script_sets_onefile_tempdir_spec(script_texts: dict[str, str], platform: str):
    """All 3 scripts must set ``--onefile-tempdir-spec`` to a per-user dir.

    Without this flag, Nuitka extracts to ``/tmp/onefile_*`` (Linux/macOS)
    or ``%TEMP%/onefile_*`` (Windows) on every launch, accumulating
    gigabytes of stale extracts across crashes/restarts. Pinning to a
    per-user cache dir makes the extract deterministic + cleanable
    (ADR-0020 §4).
    """
    text = script_texts[platform]
    assert "--onefile-tempdir-spec" in text, (
        f"build_sidecar_{platform}.sh must set --onefile-tempdir-spec "
        "(ADR-0020 §4 mandates a pinned extract dir to avoid /tmp bloat)."
    )
    # Verify the flag has a VALUE (not just `--onefile-tempdir-spec` with
    # nothing after the `=`).
    spec = _extract_tempdir_spec(text)
    assert spec is not None and spec.strip() != "", (
        f"build_sidecar_{platform}.sh --onefile-tempdir-spec must have a "
        "non-empty value (the flag with no value is a no-op)."
    )


# ─── 3. Per-platform per-user cache dir spec ─────────────────────────────────
def test_windows_tempdir_spec_per_user_cache_dir(script_texts: dict[str, str]):
    """Windows tempdir-spec must be ``%LOCALAPPDATA%/voice-typer/onefile-tmp``.

    Per MIG-1.8 task spec: Windows accepts either
    ``$XDG_CACHE_HOME/voice-typer/onefile-tmp`` (rare on Windows) or
    ``%LOCALAPPDATA%/voice-typer/onefile-tmp`` (standard per-user cache).

    The actual script uses:
        --onefile-tempdir-spec="%LOCALAPPDATA%\\voice-typer\\onefile-tmp"
    which matches the spec (the script's ``\\`` is bash-escaped backslash
    in a double-quoted string; Nuitka receives a single backslash).
    """
    text = script_texts["windows"]
    spec = _extract_resolved_tempdir_spec(text)
    assert spec is not None, "build_sidecar_windows.sh: could not extract --onefile-tempdir-spec value."
    patterns = PLATFORM_TEMPDIR_PATTERNS["windows"]
    assert any(p.search(spec) for p in patterns), (
        f"build_sidecar_windows.sh --onefile-tempdir-spec value `{spec}` "
        "does not match any accepted Windows pattern: " + ", ".join(p.pattern for p in patterns)
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "GAP-1 (MIG-1.8): build_sidecar_macos.sh uses "
        "`$HOME/Library/Application Support/voice-typer/onefile-tmp` but the "
        "MIG-1.8 task spec requires `~/Library/Caches/voice-typer/onefile-tmp` "
        "(or `$XDG_CACHE_HOME/voice-typer/onefile-tmp`). See "
        "test_known_gap_macos_uses_application_support_not_caches. When this "
        "xfail becomes xpass (strict=True → suite fails), the gap has been "
        "fixed: remove the xfail marker here AND delete the known-gap test."
    ),
)
def test_macos_tempdir_spec_per_user_cache_dir(script_texts: dict[str, str]):
    """macOS tempdir-spec must be ``~/Library/Caches/voice-typer/onefile-tmp``.

    Per MIG-1.8 task spec: macOS accepts either
    ``~/Library/Caches/voice-typer/onefile-tmp`` (standard macOS cache
    location) or ``$XDG_CACHE_HOME/voice-typer/onefile-tmp`` (XDG override).

    KNOWN GAP: the actual script uses ``~/Library/Application Support/...``
    which is a per-user dir but NOT a "Caches" dir per macOS conventions.
    See the module docstring GAP-1 note + the known-gap test
    ``test_known_gap_macos_uses_application_support_not_caches``.
    """
    text = script_texts["macos"]
    spec = _extract_resolved_tempdir_spec(text)
    assert spec is not None, "build_sidecar_macos.sh: could not extract --onefile-tempdir-spec value."
    patterns = PLATFORM_TEMPDIR_PATTERNS["macos"]
    assert any(p.search(spec) for p in patterns), (
        f"build_sidecar_macos.sh --onefile-tempdir-spec value `{spec}` "
        "does not match any accepted macOS pattern: " + ", ".join(p.pattern for p in patterns)
    )


def test_linux_tempdir_spec_per_user_cache_dir(script_texts: dict[str, str]):
    """Linux tempdir-spec must be ``$XDG_CACHE_HOME/voice-typer/onefile-tmp``.

    Per MIG-1.8 task spec: Linux accepts either
    ``$XDG_CACHE_HOME/voice-typer/onefile-tmp`` (XDG standard) or
    ``~/.cache/voice-typer/onefile-tmp`` (default when XDG_CACHE_HOME unset).

    The actual script uses bash default-expansion:
        ONEFILE_TEMPDIR="${XDG_CACHE_HOME:-$HOME/.cache}/voice-typer/onefile-tmp"
        --onefile-tempdir-spec="$ONEFILE_TEMPDIR"
    which matches BOTH spec alternatives (expands to ``$XDG_CACHE_HOME/...``
    when XDG_CACHE_HOME is set, or ``$HOME/.cache/...`` when unset).
    """
    text = script_texts["linux"]
    spec = _extract_resolved_tempdir_spec(text)
    assert spec is not None, "build_sidecar_linux.sh: could not extract --onefile-tempdir-spec value."
    patterns = PLATFORM_TEMPDIR_PATTERNS["linux"]
    assert any(p.search(spec) for p in patterns), (
        f"build_sidecar_linux.sh --onefile-tempdir-spec value `{spec}` "
        "does not match any accepted Linux pattern: " + ", ".join(p.pattern for p in patterns)
    )


# ─── 4. NOT a system temp dir (avoids OS cleanup) ────────────────────────────
@pytest.mark.parametrize("platform", list(BUILD_SCRIPTS))
def test_tempdir_spec_not_system_temp(script_texts: dict[str, str], platform: str):
    """The tempdir-spec must NOT be a system temp dir like ``/tmp`` or ``%TEMP%``.

    System temp dirs are periodically cleaned by the OS:
      - Linux: ``systemd-tmpfiles-clean.service`` purges ``/tmp`` files
        older than 10 days (default).
      - macOS: Storage Management purges ``/tmp`` and ``$TMPDIR`` on low disk.
      - Windows: Disk Cleanup + Storage Sense purge ``%TEMP%`` on schedule.

    If the onefile extract lives in a system temp dir, a cleanup cycle would
    force a re-extract on the next launch (~10-15s cold-start latency) and
    could mid-run delete files the sidecar is actively using.

    ADR-0020 §4: pin to a per-user cache dir specifically to avoid this.
    """
    text = script_texts[platform]
    spec = _extract_resolved_tempdir_spec(text)
    assert spec is not None, f"build_sidecar_{platform}.sh: could not extract --onefile-tempdir-spec value."
    for pat in FORBIDDEN_SYSTEM_TEMP_PATTERNS:
        assert not pat.search(spec), (
            f"build_sidecar_{platform}.sh --onefile-tempdir-spec value `{spec}` "
            f"matches forbidden system-temp pattern `{pat.pattern}`. System "
            "temp dirs are periodically cleaned by the OS — pin to a per-user "
            "cache dir (ADR-0020 §4)."
        )


# ─── 5. "voice-typer" in path (avoids collision with other apps) ─────────────
@pytest.mark.parametrize("platform", list(BUILD_SCRIPTS))
def test_tempdir_spec_includes_voice_tyer(script_texts: dict[str, str], platform: str):
    """The tempdir-spec must include ``voice-typer`` in the path.

    Without the app name in the path, Nuitka would extract to a generic
    dir like ``$HOME/.cache/onefile-tmp`` which could collide with another
    Nuitka-frozen app's onefile extract (if another app used the same
    path). Namespacing with ``voice-typer`` makes the extract dir unique
    to this app — the installer/uninstaller can also safely purge the
    dir by signature without touching other apps' extracts.

    ADR-0020 §4: all 3 platforms pin to ``<per-user-cache>/voice-typer/onefile-tmp``.
    """
    text = script_texts[platform]
    spec = _extract_resolved_tempdir_spec(text)
    assert spec is not None, f"build_sidecar_{platform}.sh: could not extract --onefile-tempdir-spec value."
    assert "voice-typer" in spec, (
        f"build_sidecar_{platform}.sh --onefile-tempdir-spec value `{spec}` "
        "must include `voice-typer` in the path to avoid collision with "
        "other Nuitka-frozen apps (ADR-0020 §4)."
    )


# ─── 6. --assume-yes-for-downloads (non-interactive) ─────────────────────────
@pytest.mark.parametrize("platform", list(BUILD_SCRIPTS))
def test_assume_yes_for_downloads_set(script_texts: dict[str, str], platform: str):
    """All 3 scripts must set ``--assume-yes-for-downloads``.

    Nuitka occasionally needs to download helper tools at build time:
      - Windows: ``depends.exe`` (Dependency Walker) for DLL analysis.
      - Linux: ``objdump`` (from binutils) for dependency scanning.
      - macOS: ``otool`` (from Xcode CLT) for dylib analysis.

    Without ``--assume-yes-for-downloads``, Nuitka prompts interactively
    for download confirmation, which hangs CI (no TTY → infinite wait).

    ADR-0020 §4: all 3 platforms set this flag for non-interactive CI.
    """
    text = script_texts[platform]
    assert "--assume-yes-for-downloads" in text, (
        f"build_sidecar_{platform}.sh must set --assume-yes-for-downloads "
        "(non-interactive CI build — Nuitka would hang on a download prompt "
        "without this flag)."
    )


# ─── 7. Known gap: macOS uses Application Support, not Caches ─────────────────
def test_known_gap_macos_uses_application_support_not_caches(
    script_texts: dict[str, str],
):
    """KNOWN GAP (MIG-1.8): ``build_sidecar_macos.sh`` uses
    ``$HOME/Library/Application Support/voice-typer/onefile-tmp`` instead of
    the MIG-1.8-spec-mandated ``~/Library/Caches/voice-typer/onefile-tmp``.

    The MIG-1.8 task spec requires the macOS tempdir-spec to be either:
      - ``~/Library/Caches/voice-typer/onefile-tmp`` (standard macOS cache), OR
      - ``$XDG_CACHE_HOME/voice-typer/onefile-tmp`` (XDG override)

    The actual script uses
    ``$HOME/Library/Application Support/voice-typer/onefile-tmp`` which is:
      - per-user (under ``$HOME``) ✓
      - NOT a system temp dir ✓
      - namespaced with ``voice-typer`` ✓
      - but NOT a "Caches" dir per macOS conventions ✗

    ADR-0020 §4.3 (line ~436) explicitly documents "Application Support"
    as the macOS tempdir-spec, so there's a divergence between the ADR
    and the MIG-1.8 task spec. Apple's ``~/Library/Caches`` is the
    standard cache location (periodically purgeable by macOS Storage
    Management); ``~/Library/Application Support`` is for persistent app
    data that the app manages itself — the macOS extract dir will
    accumulate stale onefile extracts across version upgrades and is
    never auto-purged by macOS.

    This test ASSERTS the gap is present (so a future fix will flip
    ``test_macos_tempdir_spec_per_user_cache_dir`` from xfail to xpass,
    which will fail the suite under ``strict=True`` and alert the
    developer). DO NOT fix this gap as part of MIG-1.8 — report it to
    the primary agent.

    When fixing the gap:
      1. Update ``build_sidecar_macos.sh`` to use
         ``$HOME/Library/Caches/voice-typer/onefile-tmp``.
      2. Update ADR-0020 §4.3 to match.
      3. Remove the ``@pytest.mark.xfail`` marker on
         ``test_macos_tempdir_spec_per_user_cache_dir``.
      4. Delete this known-gap test.

    See:
      - build_sidecar_macos.sh line 132: the actual --onefile-tempdir-spec value.
      - ADR-0020 §4.3 line ~436: the ADR's macOS tempdir-spec claim.
      - test_macos_tempdir_spec_per_user_cache_dir: the xfail spec test.
    """
    text = script_texts["macos"]
    spec = _extract_resolved_tempdir_spec(text)
    assert spec is not None, "build_sidecar_macos.sh: could not extract --onefile-tempdir-spec value."
    # Assert the gap is present: the spec uses "Application Support" not "Caches".
    assert "Application Support" in spec, (
        "build_sidecar_macos.sh no longer uses `Application Support` in "
        "--onefile-tempdir-spec — the GAP-1 may have been fixed. Update "
        "test_macos_tempdir_spec_per_user_cache_dir to remove the xfail "
        "marker, and remove this known-gap test."
    )
    assert "Caches" not in spec, (
        "build_sidecar_macos.sh now uses `Caches` in --onefile-tempdir-spec — "
        "the GAP-1 has been fixed. Update test_macos_tempdir_spec_per_user_cache_dir "
        "to remove the xfail marker, and remove this known-gap test."
    )
