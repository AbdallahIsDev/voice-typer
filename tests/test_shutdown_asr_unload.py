"""DJ-7: ASR model unload + CUDA cache clear is wired into the
``_do_cleanup`` parallel batch.

These tests pin the contract that ``_teardown_asr_models`` exists on
``ShutdownController``, runs FIRST in the parallel batch, calls
``app._asr_registry.unload()``, and defensively guards the
``torch.cuda.empty_cache()`` / ``synchronize()`` calls with
``hasattr(torch, 'cuda')`` + ``torch.cuda.is_available()``.

Source-inspection based (mirrors the DJ-10 contract tests in
``shutdown-hooks.test.ts``) — importing ``shutdown_controller`` triggers
the full ``VoiceTyperApp`` dependency chain, which is heavy and not
needed for these static-contract assertions. The one dynamic test
(``test_teardown_asr_models_calls_unload``) constructs a minimal
``ShutdownController`` look-alike via the existing
``test_shutdown_parallel`` fake-app fixture.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

# Source-inspection: read the module source so we can assert on the
# structure WITHOUT importing it (which would pull in VoiceTyperApp +
# the entire server stack). Same pattern as the DJ-10 / R6-F7 tests.
_SHUTDOWN_CONTROLLER_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown_controller.py",
)


def _src() -> str:
    with open(_SHUTDOWN_CONTROLLER_PATH, encoding="utf-8") as f:
        return f.read()


# ── Static (source-inspection) contract tests ───────────────────────


class TestDJ7TeardownAsrModelsContract:
    """DJ-7: ``_teardown_asr_models`` is wired into the parallel batch
    as the FIRST item, calls ``app._asr_registry.unload()``, and guards
    the CUDA cache clear."""

    def test_teardown_asr_models_method_exists(self) -> None:
        """The helper must be defined as a method on
        ``ShutdownController``."""
        s = _src()
        # Method definition with the exact name + ``self`` first arg.
        assert "def _teardown_asr_models(self" in s, "DJ-7: _teardown_asr_models(self) method must be defined"

    def test_teardown_asr_models_is_first_in_parallel_batch(self) -> None:
        """The helper must be the FIRST entry in the parallel batch
        (not in critical-only mode — the parallel batch is the normal-
        mode tier)."""
        s = _src()
        # Find the ``parallel_items = [`` block in the non-critical
        # branch (i.e. the ``else:`` after ``if critical_only:``).
        # Anchor on the ``("teardown_asr_models",`` tuple — it must
        # appear BEFORE ``("teardown_timers_and_recording",`` (the
        # previous first item) in the parallel batch.
        asr_idx = s.find('("teardown_asr_models",')
        assert asr_idx > -1, "DJ-7: _teardown_asr_models must be in the parallel batch"
        timers_idx = s.find('("teardown_timers_and_recording",')
        assert timers_idx > -1, "DJ-7: _teardown_timers_and_recording must still be in the parallel batch"
        assert asr_idx < timers_idx, (
            "DJ-7: _teardown_asr_models must be the FIRST item in the parallel batch "
            "(before _teardown_timers_and_recording) so GPU memory is freed before "
            "any audio-stack teardown"
        )

    def test_teardown_asr_models_calls_asr_registry_unload(self) -> None:
        """The helper must call ``app._asr_registry.unload()`` (NOT
        ``unload(name)`` — the no-arg form unloads the ACTIVE backend)."""
        s = _src()
        # Find the helper body, then assert it calls unload.
        helper_idx = s.find("def _teardown_asr_models(self")
        assert helper_idx > -1
        # Slice up to the next ``def `` (end of the helper body).
        next_def = s.find("\n    def ", helper_idx + 1)
        body = s[helper_idx:next_def]
        assert "asr_registry.unload()" in body, (
            "DJ-7: _teardown_asr_models must call asr_registry.unload() (no-arg form — unloads the active backend)"
        )

    def test_teardown_asr_models_guards_torch_cuda_with_hasattr_and_is_available(
        self,
    ) -> None:
        """The helper must guard ``torch.cuda.empty_cache()`` with BOTH
        ``hasattr(torch, 'cuda')`` AND ``torch.cuda.is_available()``.
        Either guard alone is insufficient:
          - ``hasattr`` only: still raises on CPU-only torch builds
            where ``torch.cuda`` is a stub module.
          - ``is_available()`` only: raises ``AttributeError`` on
            torch builds without ``cuda`` at all.
        """
        s = _src()
        helper_idx = s.find("def _teardown_asr_models(self")
        assert helper_idx > -1
        next_def = s.find("\n    def ", helper_idx + 1)
        body = s[helper_idx:next_def]
        assert 'hasattr(torch, "cuda")' in body, (
            "DJ-7: _teardown_asr_models must guard torch.cuda with hasattr(torch, 'cuda')"
        )
        assert "torch.cuda.is_available()" in body, (
            "DJ-7: _teardown_asr_models must guard torch.cuda with torch.cuda.is_available()"
        )

    def test_teardown_asr_models_calls_empty_cache_and_synchronize(self) -> None:
        """The helper must call BOTH ``torch.cuda.empty_cache()`` AND
        ``torch.cuda.synchronize()`` (synchronize is needed so the
        cache clear is observable before any other subsystem reads GPU
        state)."""
        s = _src()
        helper_idx = s.find("def _teardown_asr_models(self")
        assert helper_idx > -1
        next_def = s.find("\n    def ", helper_idx + 1)
        body = s[helper_idx:next_def]
        assert "torch.cuda.empty_cache()" in body, "DJ-7: _teardown_asr_models must call torch.cuda.empty_cache()"
        assert "torch.cuda.synchronize()" in body or (
            'hasattr(torch.cuda, "synchronize")' in body and "torch.cuda.synchronize()" in body
        ), (
            "DJ-7: _teardown_asr_models must call torch.cuda.synchronize() "
            "(guarded with hasattr for older torch versions)"
        )

    def test_teardown_asr_models_torch_import_is_inside_try(self) -> None:
        """The ``import torch`` must be inside a ``try``/``except
        ImportError`` so the helper doesn't crash on CPU-only build
        paths / test envs without torch installed."""
        s = _src()
        helper_idx = s.find("def _teardown_asr_models(self")
        assert helper_idx > -1
        next_def = s.find("\n    def ", helper_idx + 1)
        body = s[helper_idx:next_def]
        assert "import torch" in body, "DJ-7: _teardown_asr_models must import torch (inside try)"
        assert "except ImportError" in body, (
            "DJ-7: _teardown_asr_models must catch ImportError for the torch import (CPU-only build path)"
        )


# ── Dynamic test: actually invoke the helper ────────────────────────


class _FakeApp:
    """Minimal ``VoiceTyperApp`` look-alike for ``_teardown_asr_models``."""

    def __init__(self) -> None:
        self._asr_registry = MagicMock()
        # The helper guards torch.cuda with is_available(); we don't
        # install torch in the test env, so the ImportError branch
        # runs and the CUDA cache clear is skipped. The unload() call
        # is the only observable side-effect we assert.


class TestDJ7TeardownAsrModelsDynamic:
    """Dynamic test: actually invoke ``_teardown_asr_models`` and verify
    the unload() call fires."""

    def test_teardown_asr_models_calls_unload(self) -> None:
        """Construct a minimal ``ShutdownController`` and verify
        ``_teardown_asr_models`` calls ``app._asr_registry.unload()``."""
        from voice_typer.server.shutdown_controller import ShutdownController

        fake_app = _FakeApp()
        # ``ShutdownController.__init__`` reads attributes off ``app``
        # (e.g. ``app._electron_pid_lock``) — use ``__new__`` to bypass
        # ``__init__`` and set just the attributes the helper needs.
        ctrl = ShutdownController.__new__(ShutdownController)
        ctrl._app = fake_app
        # Invoke the helper. ``import torch`` will ImportError (test
        # env) → caught → CUDA cache clear skipped → helper returns
        # cleanly.
        ctrl._teardown_asr_models()
        fake_app._asr_registry.unload.assert_called_once_with()

    def test_teardown_asr_models_noop_when_no_registry(self) -> None:
        """When ``app._asr_registry`` is None (sidecar crashed during
        model init), the helper must be a no-op (not raise)."""
        from voice_typer.server.shutdown_controller import ShutdownController

        fake_app = _FakeApp()
        fake_app._asr_registry = None
        ctrl = ShutdownController.__new__(ShutdownController)
        ctrl._app = fake_app
        # Must not raise.
        ctrl._teardown_asr_models()
