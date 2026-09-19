"""
Nuitka ``--onefile`` tempdir-spec + cleanup validation.
Gaps documented (report, do NOT fix, out of scope for this gate check):
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Path from file → root:
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_DIR = PROJECT_ROOT / "scripts" / "build"

BUILD_SCRIPTS: dict[str, Path] = {
    "windows": BUILD_DIR / "build_sidecar_windows.sh",
    "macos": BUILD_DIR / "build_sidecar_macos.sh",
    "linux": BUILD_DIR / "build_sidecar_linux.sh",
}

PLATFORM_TEMPDIR_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "windows": [
        # {CACHE_DIR}/voice-typer/onefile-tmp, the Nuitka-documented token
        re.compile(r"\{CACHE_DIR\}[/\\]+voice-typer[/\\]+onefile-tmp"),
        # {CACHE_DIR} (see the mig15 pin).
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
FORBIDDEN_SYSTEM_TEMP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(^|[/\\])/tmp(/|$)", re.IGNORECASE),
    re.compile(r"(^|[/\\])/var/tmp(/|$)", re.IGNORECASE),
    re.compile(r"%TEMP%", re.IGNORECASE),
    re.compile(r"%TMP%", re.IGNORECASE),
    re.compile(r"\$TMPDIR", re.IGNORECASE),
    re.compile(r"(^|[/\\])/dev/shm(/|$)", re.IGNORECASE),
]


@pytest.fixture(scope="module")
def script_texts() -> dict[str, str]:
    """Read all 3 build scripts once per module; fail fast if any missing."""
    texts: dict[str, str] = {}
    for name, path in BUILD_SCRIPTS.items():
        assert path.is_file(), f"build_sidecar_{name}.sh not found at {path}. Did the project layout change?"
        texts[name] = path.read_text(encoding="utf-8")
    return texts


def _extract_tempdir_spec(text: str) -> str | None:
    """Extract the raw ``--onefile-tempdir-spec`` value from the script text."""
    # Match --onefile-tempdir-spec="VALUE" or --onefile-tempdir-spec='VALUE'.
    m = re.search(
        r"--onefile-tempdir-spec=(?P<quote>[\"'])(?P<value>[^\"'\n]*?)(?P=quote)",
        text,
    )
    if m:
        return m.group("value")
    return None


def _extract_resolved_tempdir_spec(text: str) -> str | None:
    """Extract the ``--onefile-tempdir-spec`` value, resolving ``$VAR`` refs."""
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


@pytest.mark.parametrize("platform", list(BUILD_SCRIPTS))
def test_script_uses_onefile_flag(script_texts: dict[str, str], platform: str):
    """All 3 scripts must use the bare ``--onefile`` flag."""
    text = script_texts[platform]
    # Bare --onefile flag: preceded by start-of-line/whitespace, followed by
    assert re.search(r"(^|\s)--onefile(?=\s|$)", text, re.MULTILINE), (
        f"build_sidecar_{platform}.sh must use the bare `--onefile` flag "
        "(ADR-0020 §4 mandates single-exe packaging for all 3 platforms). "
        "Note: --onefile-tempdir-spec is a DIFFERENT flag, the bare "
        "--onefile must also be present."
    )


@pytest.mark.parametrize("platform", list(BUILD_SCRIPTS))
def test_script_sets_onefile_tempdir_spec(script_texts: dict[str, str], platform: str):
    """All 3 scripts must set ``--onefile-tempdir-spec`` to a per-user dir."""
    text = script_texts[platform]
    assert "--onefile-tempdir-spec" in text, (
        f"build_sidecar_{platform}.sh must set --onefile-tempdir-spec "
        "(ADR-0020 §4 mandates a pinned extract dir to avoid /tmp bloat)."
    )
    spec = _extract_tempdir_spec(text)
    assert spec is not None and spec.strip() != "", (
        f"build_sidecar_{platform}.sh --onefile-tempdir-spec must have a "
        "non-empty value (the flag with no value is a no-op)."
    )


def test_windows_tempdir_spec_per_user_cache_dir(script_texts: dict[str, str]):
    """Windows tempdir-spec must be ``%LOCALAPPDATA%/voice-typer/onefile-tmp``."""
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
    """macOS tempdir-spec must be ``~/Library/Caches/voice-typer/onefile-tmp``."""
    text = script_texts["macos"]
    spec = _extract_resolved_tempdir_spec(text)
    assert spec is not None, "build_sidecar_macos.sh: could not extract --onefile-tempdir-spec value."
    patterns = PLATFORM_TEMPDIR_PATTERNS["macos"]
    assert any(p.search(spec) for p in patterns), (
        f"build_sidecar_macos.sh --onefile-tempdir-spec value `{spec}` "
        "does not match any accepted macOS pattern: " + ", ".join(p.pattern for p in patterns)
    )


def test_linux_tempdir_spec_per_user_cache_dir(script_texts: dict[str, str]):
    """Linux tempdir-spec must be ``$XDG_CACHE_HOME/voice-typer/onefile-tmp``."""
    text = script_texts["linux"]
    spec = _extract_resolved_tempdir_spec(text)
    assert spec is not None, "build_sidecar_linux.sh: could not extract --onefile-tempdir-spec value."
    patterns = PLATFORM_TEMPDIR_PATTERNS["linux"]
    assert any(p.search(spec) for p in patterns), (
        f"build_sidecar_linux.sh --onefile-tempdir-spec value `{spec}` "
        "does not match any accepted Linux pattern: " + ", ".join(p.pattern for p in patterns)
    )


@pytest.mark.parametrize("platform", list(BUILD_SCRIPTS))
def test_tempdir_spec_not_system_temp(script_texts: dict[str, str], platform: str):
    """The tempdir-spec must NOT be a system temp dir like ``/tmp`` or ``%TEMP%``."""
    text = script_texts[platform]
    spec = _extract_resolved_tempdir_spec(text)
    assert spec is not None, f"build_sidecar_{platform}.sh: could not extract --onefile-tempdir-spec value."
    for pat in FORBIDDEN_SYSTEM_TEMP_PATTERNS:
        assert not pat.search(spec), (
            f"build_sidecar_{platform}.sh --onefile-tempdir-spec value `{spec}` "
            f"matches forbidden system-temp pattern `{pat.pattern}`. System "
            "temp dirs are periodically cleaned by the OS, pin to a per-user "
            "cache dir (ADR-0020 §4)."
        )


@pytest.mark.parametrize("platform", list(BUILD_SCRIPTS))
def test_tempdir_spec_includes_voice_tyer(script_texts: dict[str, str], platform: str):
    """The tempdir-spec must include ``voice-typer`` in the path."""
    text = script_texts[platform]
    spec = _extract_resolved_tempdir_spec(text)
    assert spec is not None, f"build_sidecar_{platform}.sh: could not extract --onefile-tempdir-spec value."
    assert "voice-typer" in spec, (
        f"build_sidecar_{platform}.sh --onefile-tempdir-spec value `{spec}` "
        "must include `voice-typer` in the path to avoid collision with "
        "other Nuitka-frozen apps (ADR-0020 §4)."
    )


@pytest.mark.parametrize("platform", list(BUILD_SCRIPTS))
def test_assume_yes_for_downloads_set(script_texts: dict[str, str], platform: str):
    """All 3 scripts must set ``--assume-yes-for-downloads``."""
    text = script_texts[platform]
    assert "--assume-yes-for-downloads" in text, (
        f"build_sidecar_{platform}.sh must set --assume-yes-for-downloads "
        "(non-interactive CI build, Nuitka would hang on a download prompt "
        "without this flag)."
    )


def test_known_gap_macos_uses_application_support_not_caches(
    script_texts: dict[str, str],
):
    """KNOWN GAP (MIG-1.8): ``build_sidecar_macos.sh`` uses"""
    text = script_texts["macos"]
    spec = _extract_resolved_tempdir_spec(text)
    assert spec is not None, "build_sidecar_macos.sh: could not extract --onefile-tempdir-spec value."
    # Assert the gap is present: the spec uses "Application Support" not "Caches".
    assert "Application Support" in spec, (
        "build_sidecar_macos.sh no longer uses `Application Support` in "
        "--onefile-tempdir-spec, the GAP-1 may have been fixed. Update "
        "test_macos_tempdir_spec_per_user_cache_dir to remove the xfail "
        "marker, and remove this known-gap test."
    )
    assert "Caches" not in spec, (
        "build_sidecar_macos.sh now uses `Caches` in --onefile-tempdir-spec, "
        "the GAP-1 has been fixed. Update test_macos_tempdir_spec_per_user_cache_dir "
        "to remove the xfail marker, and remove this known-gap test."
    )
