"""pytest configuration for the Tauri sidecar tests."""

import os
import sys

import pytest

_PLATFORM_FOR_FILE_TOKEN: list[tuple[str, str]] = [
    ("linux", "linux"),
    ("macos", "darwin"),
    ("windows", "win32"),
]

# Directories that use file-name-based platform detection. : now
_MIXED_PLATFORM_PREFIXES: frozenset[str] = frozenset(
    {
        "mig15",
        "mig16",
        "mig17",
        "mig18",
    }
)


def _required_platform(module_path: str) -> str | None:
    directory = os.path.basename(os.path.dirname(module_path))
    # All Tauri migration-test directories use file-name-based detection
    if any(directory.startswith(prefix) for prefix in _MIXED_PLATFORM_PREFIXES):
        filename = os.path.basename(module_path).lower()
        for token, plat in _PLATFORM_FOR_FILE_TOKEN:
            if token in filename:
                return plat
        return None  # no platform token → runs on all platforms
    return None


# The Tauri build needs placeholder binaries under ``src-tauri/bin/`` +
_STUB_TRIPLES = (
    "x86_64-pc-windows-msvc",
    "aarch64-pc-windows-msvc",
    "x86_64-apple-darwin",
    "aarch64-apple-darwin",
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
)
_STUB_NATIVE = (
    "windows-key-listener.exe",
    "macos-key-listener",
    "linux-key-listener",
)
_session_stub_paths: list[str] = []


def _canonical_stub_paths() -> list[str]:
    """Every binary-stub path owned by ``gen_tauri_icons_stub.py``."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    paths = []
    for triple in _STUB_TRIPLES:
        ext = ".exe" if "windows" in triple else ""
        paths.append(os.path.join(root, "src-tauri", "bin", f"python-sidecar-{triple}{ext}"))
        paths.append(os.path.join(root, "src-tauri", "bin", f"voice-typer-worker-{triple}{ext}"))
    for name in _STUB_NATIVE:
        paths.append(os.path.join(root, "src-tauri", "resources", "native", name))
    return paths


def pytest_sessionstart(session):
    """Snapshot pre-existing stubs so sessionfinish can restore them."""
    global _session_stub_paths
    try:
        _session_stub_paths = [p for p in _canonical_stub_paths() if os.path.exists(p)]
    except Exception:
        _session_stub_paths = []


def pytest_sessionfinish(session, exitstatus):
    """Re-create stubs deleted mid-session (killed-worker gap)."""
    try:
        if getattr(session.config, "workerinput", None) is not None:
            return
        missing = [p for p in _session_stub_paths if not os.path.exists(p)]
        if not missing:
            return
        import subprocess

        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        script = os.path.join(root, "scripts", "gen_tauri_icons_stub.py")
        for _ in range(3):
            subprocess.run([sys.executable, script], capture_output=True, cwd=root)
            missing = [p for p in _session_stub_paths if not os.path.exists(p)]
            if not missing:
                return
        print(f"[stub-guard] WARNING: {len(missing)} stub(s) still missing after session: {missing}")
    except Exception as exc:  # never fail the suite from a safety net
        print(f"[stub-guard] WARNING: session-finish stub restore skipped: {exc}")


def pytest_collection_modifyitems(config, items):
    """Skip platform-specific Tauri migration tests on the wrong OS."""
    for item in items:
        required = _required_platform(str(item.module.__file__))
        if required is not None and sys.platform != required:
            item.add_marker(
                pytest.mark.skip(reason=f"platform-specific Tauri test (target={required}); running on {sys.platform}")
            )
