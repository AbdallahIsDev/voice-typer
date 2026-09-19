"""``_do_cleanup`` parallel batch."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

_CONTROLLER_TEARDOWNS_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown_controller",
    "_teardowns.py",
)
_PLANS_BODY_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown",
    "plan.py",
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


def _controller_teardowns_src() -> str:
    with open(_CONTROLLER_TEARDOWNS_PATH, encoding="utf-8") as f:
        return f.read()


def _controller_plans_src() -> str:
    """Source of the extracted plan-builder bodies (``shutdown/plan.py``)."""
    with open(_PLANS_BODY_PATH, encoding="utf-8") as f:
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


class TestTeardownAsrModelsContract:
    """``_teardown_asr_models`` is wired into the parallel batch"""

    def test_teardown_asr_models_method_exists(self) -> None:
        """``ShutdownController`` (the delegate) AND as a standalone"""
        s = _controller_teardowns_src()
        # Method definition with the exact name + ``self`` first arg.
        assert "def _teardown_asr_models(self" in s, "_teardown_asr_models(self) method must be defined"
        # The extracted body must also exist.
        body = _teardown_asr_models_body()
        assert "def teardown_asr_models(controller)" in body

    def test_teardown_asr_models_is_first_in_parallel_batch(self) -> None:
        """The helper must be the FIRST entry in the parallel batch"""
        s = _controller_plans_src()
        # Find the ``parallel_items`` block in ``build_parallel_plan``
        parallel_open_idx = s.find("parallel_items")
        assert parallel_open_idx > -1, "build_parallel_plan must define a parallel_items list for the parallel batch"
        # Find the opening ``[`` after ``parallel_items``.
        bracket_open = s.find("[", parallel_open_idx)
        assert bracket_open > -1
        # Find the first ``("teardown_`` entry inside the bracket.
        first_entry_idx = s.find('("teardown_', bracket_open)
        assert first_entry_idx > -1, "parallel_items must contain at least one teardown entry"
        # Slice a small window to read the entry name.
        first_entry = s[first_entry_idx : first_entry_idx + 40]
        assert first_entry.startswith('("teardown_asr_models",'), (
            "_teardown_asr_models must be the FIRST entry in the "
            "parallel_items list (so GPU memory is freed before any "
            f"other parallel teardown); got: {first_entry!r}"
        )

    def test_teardown_asr_models_calls_asr_registry_unload(self) -> None:
        """The helper must call ``registry.unload()`` (no-arg form —"""
        body = _teardown_asr_models_body()
        assert "registry.unload()" in body or "asr_registry.unload()" in body, (
            "_teardown_asr_models must call registry.unload() (no-arg form, unloads the active backend)"
        )

    def test_teardown_asr_models_guards_torch_cuda_with_hasattr_and_is_available(
        self,
    ) -> None:
        """The helper must guard ``torch.cuda.empty_cache()`` with BOTH"""
        body = _teardown_asr_models_body()
        # Accept either inline guards OR a call to release_gpu_memory
        if "release_gpu_memory" in body:
            return
        assert 'hasattr(torch, "cuda")' in body or "hasattr(torch, 'cuda')" in body, (
            "_teardown_asr_models must guard torch.cuda with hasattr(torch, "
            "'cuda') (or call release_gpu_memory which encapsulates the guard)"
        )

    def test_teardown_asr_models_calls_empty_cache_and_synchronize(self) -> None:
        """cache clear is observable before any other subsystem reads GPU"""
        body = _teardown_asr_models_body()
        if "release_gpu_memory" in body:
            return
        assert "torch.cuda.empty_cache()" in body, (
            "_teardown_asr_models must call torch.cuda.empty_cache() (or release_gpu_memory)"
        )
        assert "torch.cuda.synchronize()" in body or (
            'hasattr(torch.cuda, "synchronize")' in body and "torch.cuda.synchronize()" in body
        ), (
            "_teardown_asr_models must call torch.cuda.synchronize() "
            "(guarded with hasattr for older torch versions), or release_gpu_memory"
        )

    def test_teardown_asr_models_torch_import_is_inside_try(self) -> None:
        """The ``import torch`` (or the call to ``release_gpu_memory``,"""
        body = _teardown_asr_models_body()
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


class _FakeModels:
    """Minimal ``ModelManager`` look-alike exposing ``registry``."""

    def __init__(self) -> None:
        self.registry = MagicMock()


class _FakeApp:
    """Minimal ``VoiceTyperApp`` look-alike for ``_teardown_asr_models``."""

    def __init__(self) -> None:
        self.models = _FakeModels()


class TestTeardownAsrModelsDynamic:
    """Dynamic test: actually invoke ``_teardown_asr_models`` and verify"""

    def test_teardown_asr_models_calls_unload(self) -> None:
        """Construct a minimal ``ShutdownController`` and verify"""
        from voice_typer.server.shutdown_controller import ShutdownController

        fake_app = _FakeApp()
        # ``ShutdownController.__init__`` reads attributes off ``app``
        ctrl = ShutdownController.__new__(ShutdownController)
        ctrl._app = fake_app
        # Invoke the helper. ``import torch`` will ImportError (test
        ctrl._teardown_asr_models()
        fake_app.models.registry.unload.assert_called_once_with()

    def test_teardown_asr_models_noop_when_no_registry(self) -> None:
        """When ``app.models.registry`` is None (sidecar crashed during"""
        from voice_typer.server.shutdown_controller import ShutdownController

        fake_app = _FakeApp()
        fake_app.models.registry = None
        ctrl = ShutdownController.__new__(ShutdownController)
        ctrl._app = fake_app
        # Must not raise.
        ctrl._teardown_asr_models()
