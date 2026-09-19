"""Regression tests for ``voice_typer.server.native_hotkeys.factory``."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# correctness guarantee. No-op when xdist isn't active. (C-TEST-5.)
pytestmark = pytest.mark.xdist_group("native_binary_path")


@pytest.fixture
def clean_native_env(monkeypatch):
    """Strip every VOICE_TYPER_NATIVE_* env var so the trusted-path"""
    monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
    monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
    monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)


def _install_factory_stubs(monkeypatch, *, binary: Path, platform_true: str):
    """Patch ``factory`` so:"""
    from voice_typer.server.native_hotkeys import factory as factory_mod

    monkeypatch.setattr(factory_mod, "get_native_binary_path", lambda: binary)
    monkeypatch.setattr(factory_mod, "verify_native_binary_or_skip", lambda _p: True)
    monkeypatch.setattr(
        factory_mod,
        "is_linux",
        lambda: platform_true == "linux",
    )
    monkeypatch.setattr(
        factory_mod,
        "is_macos",
        lambda: platform_true == "macos",
    )
    monkeypatch.setattr(
        factory_mod,
        "is_windows",
        lambda: platform_true == "windows",
    )
    plat_map = {"linux": "linux", "macos": "darwin", "windows": "win32"}
    monkeypatch.setattr(sys, "platform", plat_map[platform_true])
    return factory_mod


class TestFactoryForwardsVerifiedBinaryPath:
    """each platform backend's constructor; the backend must NOT re-run"""

    def test_linux_backend_uses_factory_verified_path(self, monkeypatch, tmp_path, clean_native_env):
        sentinel = tmp_path / "linux-key-listener-x86_64"
        sentinel.write_bytes(b"#!/bin/sh\n# sentinel\n")
        factory_mod = _install_factory_stubs(monkeypatch, binary=sentinel, platform_true="linux")

        backend = factory_mod.create_native_backend("<f8>")
        assert backend is not None, "factory must return a backend when verification passes"
        assert type(backend).__name__ == "LinuxEvdevHotkey"
        # The backend MUST use the factory's verified Path verbatim —
        assert backend._binary_path is sentinel, (
            f"LinuxEvdevHotkey._binary_path must be the factory's verified sentinel Path (got {backend._binary_path!r})"
        )

    def test_macos_backend_uses_factory_verified_path(self, monkeypatch, tmp_path, clean_native_env):
        sentinel = tmp_path / "macos-key-listener"
        sentinel.write_bytes(b"#!/bin/sh\n# sentinel\n")
        factory_mod = _install_factory_stubs(monkeypatch, binary=sentinel, platform_true="macos")

        backend = factory_mod.create_native_backend("<f8>")
        assert backend is not None, "factory must return a backend when verification passes"
        assert type(backend).__name__ == "MacNativeHotkey"
        assert backend._binary_path is sentinel, (
            f"MacNativeHotkey._binary_path must be the factory's verified sentinel Path (got {backend._binary_path!r})"
        )

    def test_windows_backend_uses_factory_verified_path(self, monkeypatch, tmp_path, clean_native_env):
        sentinel = tmp_path / "windows-key-listener-x86_64.exe"
        sentinel.write_bytes(b"#!/bin/sh\n# sentinel\n")
        factory_mod = _install_factory_stubs(monkeypatch, binary=sentinel, platform_true="windows")

        backend = factory_mod.create_native_backend("<f8>")
        assert backend is not None, "factory must return a backend when verification passes"
        assert type(backend).__name__ == "WindowsHookHotkey"
        assert backend._binary_path is sentinel, (
            f"WindowsHookHotkey._binary_path must be the factory's verified "
            f"sentinel Path (got {backend._binary_path!r})"
        )

    def test_factory_returns_none_when_binary_missing(self, monkeypatch, clean_native_env):
        """Regression guard: when ``get_native_binary_path`` returns"""
        from voice_typer.server.native_hotkeys import factory as factory_mod

        monkeypatch.setattr(factory_mod, "get_native_binary_path", lambda: None)
        # If the factory accidentally falls through, this verifier
        monkeypatch.setattr(
            factory_mod,
            "verify_native_binary_or_skip",
            lambda _p: pytest.fail("verifier must not be called when binary is None"),
        )
        monkeypatch.setattr(factory_mod, "is_linux", lambda: True)
        monkeypatch.setattr(factory_mod, "is_macos", lambda: False)
        monkeypatch.setattr(factory_mod, "is_windows", lambda: False)
        assert factory_mod.create_native_backend("<f8>") is None

    def test_factory_returns_none_when_verification_fails(self, monkeypatch, tmp_path, clean_native_env):
        """Regression guard: when ``verify_native_binary_or_skip``"""
        from voice_typer.server.native_hotkeys import factory as factory_mod

        bad = tmp_path / "linux-key-listener-x86_64"
        bad.write_bytes(b"tampered")
        monkeypatch.setattr(factory_mod, "get_native_binary_path", lambda: bad)
        monkeypatch.setattr(factory_mod, "verify_native_binary_or_skip", lambda _p: False)
        monkeypatch.setattr(factory_mod, "is_linux", lambda: True)
        monkeypatch.setattr(factory_mod, "is_macos", lambda: False)
        monkeypatch.setattr(factory_mod, "is_windows", lambda: False)
        assert factory_mod.create_native_backend("<f8>") is None

    def test_factory_does_not_rediscover_binary_in_base_init(self, monkeypatch, tmp_path, clean_native_env):
        """The strongest guard: if the base class's ``__init__`` were"""
        sentinel = tmp_path / "linux-key-listener-x86_64"
        sentinel.write_bytes(b"#!/bin/sh\n# sentinel\n")
        decoy = tmp_path / "decoy-linux-key-listener"
        decoy.write_bytes(b"#!/bin/sh\n# decoy\n")

        call_count = {"n": 0}

        def rotating_get_native_binary_path():
            call_count["n"] += 1
            # First call (factory) returns the sentinel; any subsequent
            return sentinel if call_count["n"] == 1 else decoy

        from voice_typer.server.native_hotkeys import factory as factory_mod

        monkeypatch.setattr(factory_mod, "get_native_binary_path", rotating_get_native_binary_path)
        monkeypatch.setattr(factory_mod, "verify_native_binary_or_skip", lambda _p: True)
        monkeypatch.setattr(factory_mod, "is_linux", lambda: True)
        monkeypatch.setattr(factory_mod, "is_macos", lambda: False)
        monkeypatch.setattr(factory_mod, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")

        backend = factory_mod.create_native_backend("<f8>")
        assert backend is not None
        assert backend._binary_path == sentinel, (
            f"backend._binary_path must be the factory's first-call sentinel "
            f"({sentinel}); got {backend._binary_path!r}. This means the base "
            f"class __init__ re-called get_native_binary_path(), the "
            f"XZ-R6-NH-02 regression."
        )
        # Sanity: get_native_binary_path was called exactly once
        assert call_count["n"] == 1, (
            f"get_native_binary_path must be called exactly once (by the "
            f"factory); got {call_count['n']} calls, base.__init__ is "
            f"re-discovering the binary."
        )
