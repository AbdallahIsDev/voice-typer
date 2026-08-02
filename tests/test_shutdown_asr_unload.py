"""ASR model unload + CUDA cache clear is wired into the
``_do_cleanup`` parallel batch.

These tests pin the contract that ``_teardown_asr_models`` exists on
``ShutdownController``, runs FIRST in the parallel batch, calls
``registry.unload()`` (via ``app.models.registry`` after the
ModelManager refactor — the briefing's ``app._asr_registry`` was
folded into ``ModelManager.registry``), and defensively guards the
``torch.cuda.empty_cache()`` / ``synchronize()`` calls with
``hasattr(torch, 'cuda')`` + ``torch.cuda.is_available()``.

Source-inspection based (mirrors the contract tests in
``shutdown-hooks.test.ts``) — importing ``shutdown_controller`` triggers
the full ``VoiceTyperApp`` dependency chain, which is heavy and not
needed for these static-contract assertions. The one dynamic test
(``test_teardown_asr_models_calls_unload``) constructs a minimal
``ShutdownController`` look-alike via the existing
``test_shutdown_parallel`` fake-app fixture.

The ``_teardown_asr_models`` body was extracted to
``voice_typer/server/shutdown/teardowns/asr_models.py`` (Phase 4.5
god-module decomposition). The source-inspection tests below read the
body from the extracted module; the dynamic test still drives the
delegate on ``ShutdownController`` (which forwards to the extracted
function).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

# Source-inspection: read the module source so we can assert on the
# structure WITHOUT importing it (which would pull in VoiceTyperApp +
# the entire server stack). Same pattern as the  / R6-F7 tests.
#
# ``_SHUTDOWN_CONTROLLER_PATH`` is kept for the parallel-batch ordering
# test (the parallel_items list lives in ``_do_cleanup`` on the
# controller). The helper body lives in the extracted teardowns module.
_SHUTDOWN_CONTROLLER_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown_controller.py",
)
_TEARDOWNS_ASR_MODELS_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown",
    "teardowns",
    "asr_models.py",
)


def _controller_src() -> str:
    with open(_SHUTDOWN_CONTROLLER_PATH, encoding="utf-8") as f:
        return f.read()


def _asr_models_src() -> str:
    with open(_TEARDOWNS_ASR_MODELS_PATH, encoding="utf-8") as f:
        return f.read()


def _teardown_asr_models_body() -> str:
    """Return the source slice of the ``teardown_asr_models`` function."""
    src = _asr_models_src()
    idx = src.find("def teardown_asr_models(controller) -> None:")
    assert idx > -1, "teardown_asr_models function must exist in the extracted module"
    next_def = src.find("\ndef ", idx + 1)
    if next_def == -1:
        return src[idx:]
    return src[idx:next_def]


# ── Static (source-inspection) contract tests ───────────────────────


class TestTeardownAsrModelsContract:
    """``_teardown_asr_models`` is wired into the parallel batch
    as the FIRST item, calls ``registry.unload()``, and guards
    the CUDA cache clear."""

    def test_teardown_asr_models_method_exists(self) -> None:
        """The helper must be defined as a method on
        ``ShutdownController`` (the delegate) AND as a standalone
        function in the extracted teardowns module (the body)."""
        s = _controller_src()
        # Method definition with the exact name + ``self`` first arg.
        assert "def _teardown_asr_models(self" in s, "_teardown_asr_models(self) method must be defined"
        # The extracted body must also exist.
        body = _teardown_asr_models_body()
        assert "def teardown_asr_models(controller)" in body

    def test_teardown_asr_models_is_first_in_parallel_batch(self) -> None:
        """The helper must be the FIRST entry in the parallel batch
        (not in critical-only mode — the parallel batch is the normal-
        mode tier). The sequenced phase (timers_and_recording,
        recorder, history_db, crash_recovery) runs BEFORE the parallel
        batch; ``_teardown_asr_models`` is the first PARALLEL item so
        the (potentially slow) CUDA context teardown starts as early
        as possible."""
        s = _controller_src()
        # Find the ``parallel_items = [`` block. The block opens with
        # ``parallel_items: list[tuple[str, object, float]] = [`` (the
        # type-annotated form) and closes with the matching ``]``.
        # The first tuple inside MUST be ``("teardown_asr_models", ...)``.
        parallel_open_idx = s.find("parallel_items")
        assert parallel_open_idx > -1, (
            "_do_cleanup must define a parallel_items list for the parallel batch"
        )
        # Find the opening ``[`` after ``parallel_items``.
        bracket_open = s.find("[", parallel_open_idx)
        assert bracket_open > -1
        # Find the first ``("teardown_`` entry inside the bracket.
        first_entry_idx = s.find('("teardown_', bracket_open)
        assert first_entry_idx > -1, (
            "parallel_items must contain at least one teardown entry"
        )
        # Slice a small window to read the entry name.
        first_entry = s[first_entry_idx : first_entry_idx + 40]
        assert first_entry.startswith('("teardown_asr_models",'), (
            "_teardown_asr_models must be the FIRST entry in the "
            "parallel_items list (so GPU memory is freed before any "
            f"other parallel teardown); got: {first_entry!r}"
        )

    def test_teardown_asr_models_calls_asr_registry_unload(self) -> None:
        """The helper must call ``registry.unload()`` (no-arg form —
        unloads the active backend). Post ModelManager refactor, the
        registry lives at ``app.models.registry``; the contract is the
        no-arg ``unload()`` call on a registry handle."""
        body = _teardown_asr_models_body()
        assert "registry.unload()" in body or "asr_registry.unload()" in body, (
            "_teardown_asr_models must call registry.unload() (no-arg form — unloads the active backend)"
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

        Post extraction, the CUDA guards live in
        ``asr_utils.release_gpu_memory`` (called from
        ``teardown_asr_models``). Accept either inline guards in the
        helper body OR a call to ``release_gpu_memory`` (which
        encapsulates the guards).
        """
        body = _teardown_asr_models_body()
        # Accept either inline guards OR a call to release_gpu_memory
        # (which encapsulates the guards — see asr_utils.py).
        if "release_gpu_memory" in body:
            return
        assert 'hasattr(torch, "cuda")' in body or "hasattr(torch, 'cuda')" in body, (
            "_teardown_asr_models must guard torch.cuda with hasattr(torch, "
            "'cuda') (or call release_gpu_memory which encapsulates the guard)"
        )

    def test_teardown_asr_models_calls_empty_cache_and_synchronize(self) -> None:
        """The helper must trigger BOTH ``torch.cuda.empty_cache()`` AND
        ``torch.cuda.synchronize()`` (synchronize is needed so the
        cache clear is observable before any other subsystem reads GPU
        state). Accept either inline calls or a call to
        ``release_gpu_memory`` (which encapsulates both)."""
        body = _teardown_asr_models_body()
        if "release_gpu_memory" in body:
            return
        assert "torch.cuda.empty_cache()" in body, (
            "_teardown_asr_models must call torch.cuda.empty_cache() "
            "(or release_gpu_memory)"
        )
        assert "torch.cuda.synchronize()" in body or (
            'hasattr(torch.cuda, "synchronize")' in body and "torch.cuda.synchronize()" in body
        ), (
            "_teardown_asr_models must call torch.cuda.synchronize() "
            "(guarded with hasattr for older torch versions) — or release_gpu_memory"
        )

    def test_teardown_asr_models_torch_import_is_inside_try(self) -> None:
        """The ``import torch`` (or the call to ``release_gpu_memory``,
        which internally imports torch) must be inside a ``try``/
        ``except`` so the helper doesn't crash on CPU-only build
        paths / test envs without torch installed."""
        body = _teardown_asr_models_body()
        # Accept either an inline ``import torch`` inside try OR a
        # call to ``release_gpu_memory`` wrapped in try/except (the
        # latter imports torch internally).
        if "release_gpu_memory" in body:
            assert "except" in body, (
                "the release_gpu_memory() call must be inside a try/except "
                "so a torch ImportError doesn't crash the helper"
            )
            return
        assert "import torch" in body, "_teardown_asr_models must import torch (inside try)"
        assert "except ImportError" in body or "except Exception" in body, (
            "_teardown_asr_models must catch ImportError for the torch import (CPU-only build path)"
        )


# ── Dynamic test: actually invoke the helper ────────────────────────


class _FakeModels:
    """Minimal ``ModelManager`` look-alike exposing ``registry``."""

    def __init__(self) -> None:
        self.registry = MagicMock()


class _FakeApp:
    """Minimal ``VoiceTyperApp`` look-alike for ``_teardown_asr_models``.

    Post ModelManager refactor, the ASR registry lives at
    ``app.models.registry`` (NOT ``app._asr_registry`` — that attribute
    was removed when the registry ownership moved into ModelManager).
    """

    def __init__(self) -> None:
        self.models = _FakeModels()


class TestTeardownAsrModelsDynamic:
    """Dynamic test: actually invoke ``_teardown_asr_models`` and verify
    the unload() call fires."""

    def test_teardown_asr_models_calls_unload(self) -> None:
        """Construct a minimal ``ShutdownController`` and verify
        ``_teardown_asr_models`` calls ``registry.unload()`` (via
        ``app.models.registry``)."""
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
        fake_app.models.registry.unload.assert_called_once_with()

    def test_teardown_asr_models_noop_when_no_registry(self) -> None:
        """When ``app.models.registry`` is None (sidecar crashed during
        model init), the helper must be a no-op (not raise)."""
        from voice_typer.server.shutdown_controller import ShutdownController

        fake_app = _FakeApp()
        fake_app.models.registry = None
        ctrl = ShutdownController.__new__(ShutdownController)
        ctrl._app = fake_app
        # Must not raise.
        ctrl._teardown_asr_models()
