"""Regression tests for ``voice_typer.server.native_hotkeys.factory``.

XZ-R6-NH-02 (Low): the factory's ``create_native_backend`` discovers
and SHA-256-verifies a native binary Path, then MUST forward that
verified Path to each platform backend's constructor via
``binary_path=``. Pre-fix the factory discarded the verified Path
and the backend's ``__init__`` re-ran ``get_native_binary_path()``
from scratch — a TOCTOU window between the factory's verification
and the backend's spawn, plus a wasted lookup.

Post-fix: ``MacNativeHotkey`` / ``WindowsHookHotkey`` /
``LinuxEvdevHotkey`` are all constructed with
``binary_path=binary`` (the factory's verified Path). The base
class ``SubprocessHotkeyBackend.__init__`` still accepts
``binary_path=None`` for tests that construct backends directly,
but when the factory is the caller it always supplies the
verified Path.

These tests pin the contract by:

1. Monkeypatching ``get_native_binary_path`` to return a known
   sentinel Path and ``verify_native_binary_or_skip`` to return
   ``True`` (so we don't depend on a real compiled binary).
2. Forcing each platform flag (``is_linux`` / ``is_macos`` /
   ``is_windows``) to ``True`` one at a time.
3. Calling ``factory.create_native_backend("<f8>")``.
4. Asserting the returned backend's ``_binary_path`` attribute is
   referentially identical to the sentinel Path the factory
   verified — i.e. the backend did NOT re-discover via
   ``get_native_binary_path()``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture
def clean_native_env(monkeypatch):
    """Strip every VOICE_TYPER_NATIVE_* env var so the trusted-path
    override can't mask a regression in the factory's binary_path
    forwarding (the override path skips verification entirely)."""
    monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
    monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
    monkeypatch.delenv("VOICE_TYPER_NATIVE_TRUST", raising=False)


def _install_factory_stubs(monkeypatch, *, binary: Path, platform_true: str):
    """Patch ``factory`` so:
    - ``get_native_binary_path`` returns ``binary``
    - ``verify_native_binary_or_skip`` returns ``True``
    - the requested platform flag is True and the other two are False
    """
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
    # sys.platform is consulted by the platform backend's
    # ``_validate_platform``; align it with our forced flag so the
    # backend doesn't refuse to construct on a mismatched host.
    plat_map = {"linux": "linux", "macos": "darwin", "windows": "win32"}
    monkeypatch.setattr(sys, "platform", plat_map[platform_true])
    return factory_mod


class TestFactoryForwardsVerifiedBinaryPath:
    """XZ-R6-NH-02: the factory MUST hand the verified binary Path to
    each platform backend's constructor; the backend must NOT re-run
    ``get_native_binary_path()``.
    """

    def test_linux_backend_uses_factory_verified_path(self, monkeypatch, tmp_path, clean_native_env):
        sentinel = tmp_path / "linux-key-listener-x86_64"
        sentinel.write_bytes(b"#!/bin/sh\n# sentinel\n")
        factory_mod = _install_factory_stubs(monkeypatch, binary=sentinel, platform_true="linux")

        backend = factory_mod.create_native_backend("<f8>")
        assert backend is not None, "factory must return a backend when verification passes"
        assert type(backend).__name__ == "LinuxEvdevHotkey"
        # The backend MUST use the factory's verified Path verbatim —
        # not a re-discovered Path. Referential identity is the
        # strongest assertion (Path.__eq__ would also pass for a
        # freshly-constructed Path with the same string, but ``is``
        # proves the factory forwarded the same object).
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
        """Regression guard: when ``get_native_binary_path`` returns
        ``None`` the factory must short-circuit and return ``None``
        before reaching any backend constructor."""
        from voice_typer.server.native_hotkeys import factory as factory_mod

        monkeypatch.setattr(factory_mod, "get_native_binary_path", lambda: None)
        # If the factory accidentally falls through, this verifier
        # would be called with None — make it explode so the test
        # fails loudly instead of silently returning a backend.
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
        """Regression guard: when ``verify_native_binary_or_skip``
        returns ``False`` the factory must short-circuit and return
        ``None`` — never hand a tampered binary to a backend."""
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
        """The strongest guard: if the base class's ``__init__`` were
        to re-call ``get_native_binary_path()`` (the pre-fix
        regression), the second call would return a DIFFERENT Path
        (we patch it to rotate on each call) and the backend's
        ``_binary_path`` would not match the sentinel. By rotating
        the return value we make any re-discovery observable."""
        sentinel = tmp_path / "linux-key-listener-x86_64"
        sentinel.write_bytes(b"#!/bin/sh\n# sentinel\n")
        decoy = tmp_path / "decoy-linux-key-listener"
        decoy.write_bytes(b"#!/bin/sh\n# decoy\n")

        call_count = {"n": 0}

        def rotating_get_native_binary_path():
            call_count["n"] += 1
            # First call (factory) returns the sentinel; any subsequent
            # call (e.g. a buggy base.__init__ re-discovery) returns
            # the decoy — which would make the test fail below.
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
        # If base.__init__ re-discovered, _binary_path would be the
        # decoy, not the sentinel.
        assert backend._binary_path == sentinel, (
            f"backend._binary_path must be the factory's first-call sentinel "
            f"({sentinel}); got {backend._binary_path!r}. This means the base "
            f"class __init__ re-called get_native_binary_path() — the "
            f"XZ-R6-NH-02 regression."
        )
        # Sanity: get_native_binary_path was called exactly once
        # (by the factory). A second call would indicate re-discovery.
        assert call_count["n"] == 1, (
            f"get_native_binary_path must be called exactly once (by the "
            f"factory); got {call_count['n']} calls — base.__init__ is "
            f"re-discovering the binary."
        )
