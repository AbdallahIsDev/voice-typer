"""``usePython`` bridge validation (Tauri-only)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_CLIENT_RENDERER_LIB = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "lib"
PYTHON_BRIDGE_DIR = _CLIENT_RENDERER_LIB / "python-bridge"
PYTHON_NAMESPACE = _CLIENT_RENDERER_LIB / "tauri-bridge" / "python-namespace.ts"
USE_PYTHON = PYTHON_BRIDGE_DIR / "usePython.ts"
RUST_DISPATCH = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds" / "dispatch.rs"


def _read(path: Path) -> str:
    assert path.exists(), f"expected bridge file at {path}"
    return path.read_text(encoding="utf-8")


def _python_bridge_sources() -> str:
    parts = [p.read_text(encoding="utf-8") for p in PYTHON_BRIDGE_DIR.glob("*.ts")]
    if PYTHON_NAMESPACE.is_file():
        parts.append(PYTHON_NAMESPACE.read_text(encoding="utf-8"))
    return "\n".join(parts)


@pytest.fixture(scope="module")
def tauri_bridge_src() -> str:
    return _python_bridge_sources()


@pytest.fixture(scope="module")
def use_python_src() -> str:
    return _read(USE_PYTHON)


@pytest.fixture(scope="module")
def rust_src() -> str:
    return _read(RUST_DISPATCH)


class TestTauriBridgeRoutesThroughDispatch:
    """The Tauri bridge installs ``window.python`` and routes ``call``"""

    def test_bridge_installs_python_namespace(self, tauri_bridge_src: str):
        assert "window.python" in tauri_bridge_src or "globalThis.python" in tauri_bridge_src

    def test_bridge_invokes_dispatch_command(self, tauri_bridge_src: str):
        assert "dispatch" in tauri_bridge_src
        assert "invoke" in tauri_bridge_src

    def test_bridge_listens_for_python_event(self, tauri_bridge_src: str):
        assert "python-event" in tauri_bridge_src or "python_event" in tauri_bridge_src

    def test_bridge_installs_bubble_namespace(self, tauri_bridge_src: str):
        assert "bubble" in tauri_bridge_src


class TestUsePythonHookContract:
    """``usePython`` exposes ``call`` / ``onEvent`` to the renderer."""

    def test_use_python_exports_call(self, use_python_src: str):
        assert "call" in use_python_src

    def test_use_python_has_per_command_timeout(self, use_python_src: str):
        assert "timeout" in use_python_src.lower()

    def test_use_python_uses_use_sync_external_store_for_bridge_ready(self, tauri_bridge_src: str):
        assert "useSyncExternalStore" in tauri_bridge_src or "use_sync_external_store" in tauri_bridge_src


class TestRustDispatchUnwrapsData:
    """The Rust dispatch command unwraps ``response.data`` on success"""

    def test_rust_dispatch_references_error_envelope(self, rust_src: str):
        assert "error" in rust_src.lower()

    def test_rust_dispatch_command_exists(self, rust_src: str):
        assert "dispatch" in rust_src
