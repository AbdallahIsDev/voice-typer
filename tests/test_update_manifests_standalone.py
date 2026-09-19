"""Standalone-contract pins for ``scripts/build/update_native_manifests.py``."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_BUILD = Path(__file__).resolve().parent.parent / "scripts" / "build"
if str(_SCRIPTS_BUILD) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_BUILD))

import update_native_manifests as unm  # noqa: E402  (path inserted above)

_BUILD_SCRIPTS = (
    "compile_native.sh",
    "compile_native.ps1",
    "build_native_listener_linux.sh",
    "build_native_listener_windows.sh",
    "build_native_listener_macos.sh",
)


def _read_script(name: str) -> str:
    path = _SCRIPTS_BUILD / name
    assert path.is_file(), f"build script missing: {path}"
    return path.read_text(encoding="utf-8")


class TestUpdaterIsStandalone:
    def test_compile_native_sh_does_not_invoke_updater(self) -> None:
        assert "update_native_manifests" not in _read_script("compile_native.sh")

    def test_compile_native_ps1_does_not_invoke_updater(self) -> None:
        assert "update_native_manifests" not in _read_script("compile_native.ps1")

    def test_listener_wrappers_do_not_invoke_updater(self) -> None:
        for name in (
            "build_native_listener_linux.sh",
            "build_native_listener_windows.sh",
            "build_native_listener_macos.sh",
        ):
            assert "update_native_manifests" not in _read_script(name), (
                f"{name} must not invoke the manifest updater (standalone contract: run it explicitly after the build)"
            )

    def test_docstring_states_standalone_contract(self) -> None:
        doc = unm.__doc__ or ""
        assert "STANDALONE" in doc
        assert "do NOT invoke it" in doc

    def test_docstring_does_not_claim_automatic_invocation(self) -> None:
        doc = unm.__doc__ or ""
        assert "is invoked by" not in doc
        assert "as the final build step" not in doc
