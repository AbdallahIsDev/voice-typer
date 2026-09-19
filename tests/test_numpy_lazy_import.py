"""TY-2 (PERF-COLDSTART-001): regression tests for the lazy ``numpy`` import."""

from __future__ import annotations

import importlib
import sys

import pytest

# Each entry is ``(module_path, attribute_holding_np)``. The test clears
TARGET_MODULES: list[tuple[str, str]] = [
    ("voice_typer.server.audio_processor", "np"),
    ("voice_typer.server.audio_quality", "np"),
    ("voice_typer.server.transcription", "np"),
    ("voice_typer.server.recording", "np"),
]


def _purge_numpy_and_targets() -> bool:
    """Remove ``numpy`` and the target modules from ``sys.modules``."""
    if "numpy" in sys.modules:
        return False
    for mod_path, _ in TARGET_MODULES:
        if mod_path in sys.modules:
            return False
    # Drop the target modules first so a fresh ``import`` re-executes
    for mod_path, _ in TARGET_MODULES:
        # Also drop submodules of the recording package so a fresh
        if mod_path.endswith(".recording"):
            for key in list(sys.modules):
                if key.startswith(mod_path + ".") or key == mod_path:
                    del sys.modules[key]
        else:
            sys.modules.pop(mod_path, None)
    # Drop numpy + its submodules so the eager-import suppression
    for key in list(sys.modules):
        if key == "numpy" or key.startswith("numpy."):
            del sys.modules[key]
    return True


@pytest.mark.parametrize("module_path,np_attr", TARGET_MODULES)
def test_module_does_not_eagerly_import_numpy(module_path: str, np_attr: str) -> None:
    """Importing the target module must NOT pull numpy into sys.modules."""
    clean_baseline = _purge_numpy_and_targets()
    if clean_baseline:
        assert "numpy" not in sys.modules, (
            "test setup bug: numpy should be absent from sys.modules before the target import"
        )
    # Some targets may pull in numpy transitively via a sibling module
    from voice_typer.server._lazy_import import _LazyModule

    try:
        module = importlib.import_module(module_path)
    finally:
        # Don't leave the target module's import side-effects lingering
        pass

    np_proxy = getattr(module, np_attr, None)
    assert np_proxy is not None, (
        f"{module_path}.{np_attr} should exist after import (was it removed from the module body?)"
    )
    assert isinstance(np_proxy, _LazyModule), (
        f"TY-2 regression: {module_path}.{np_attr} is "
        f"{type(np_proxy).__name__!r}, expected _LazyModule. "
        f"The module likely has an eager ``import numpy as np`` "
        f"at the top again."
    )


# Modules whose import graph is FULLY numpy-free (so we can assert
_NP_FREE_MODULES: list[str] = [
    "voice_typer.server.audio_quality",
    "voice_typer.server.transcription",
]


@pytest.mark.parametrize("module_path", _NP_FREE_MODULES)
def test_module_import_keeps_numpy_out_of_sys_modules(module_path: str) -> None:
    """module must leave ``sys.modules`` without ``numpy``."""
    clean_baseline = _purge_numpy_and_targets()
    if clean_baseline:
        assert "numpy" not in sys.modules
    importlib.import_module(module_path)
    if clean_baseline:
        assert "numpy" not in sys.modules, (
            f"TY-2 regression: importing {module_path} pulled numpy into "
            f"sys.modules. Either the module has an eager ``import numpy`` "
            f"again, OR a sibling module in its import graph does. Run "
            f"``python -X importtime -c 'import {module_path}'`` to find "
            f"the offender."
        )


def test_proxy_supports_array_construction() -> None:
    """``np.array([1, 2, 3])`` must work after the lazy proxy is bound."""
    from voice_typer.server._lazy_import import lazy_module

    np = lazy_module("numpy")
    arr = np.array([1, 2, 3])
    assert arr.tolist() == [1, 2, 3]
    # After the first attribute access, numpy is now in sys.modules.
    assert "numpy" in sys.modules


def test_proxy_supports_dtype_attributes() -> None:
    """``np.float32`` and ``np.ndarray`` must resolve via the proxy."""
    from voice_typer.server._lazy_import import lazy_module

    np = lazy_module("numpy")
    assert np.float32 is not None
    assert np.ndarray is not None
    # Construct an array and verify the dtype matches.
    arr = np.array([1.0, 2.0], dtype=np.float32)
    assert arr.dtype == np.float32


def test_proxy_supports_dot() -> None:
    """``np.dot(a, a)`` must work, exercises a function attribute."""
    from voice_typer.server._lazy_import import lazy_module

    np = lazy_module("numpy")
    a = np.array([1.0, 2.0, 3.0])
    result = float(np.dot(a, a))
    assert result == 14.0  # 1 + 4 + 9


def test_monkeypatch_setattr_on_proxy_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """real numpy module so production code that does ``import numpy as np``"""
    from voice_typer.server._lazy_import import lazy_module

    np_proxy = lazy_module("numpy")
    # Force the real numpy to be loaded so we can compare identity.
    np_proxy._resolve()  # noqa: SLF001, intentional for the test; force real numpy load
    captured: list = []

    def fake_array(*args, **kwargs):
        captured.append((args, kwargs))
        return "fake"

    # Patch the PROXY, __setattr__ delegates to the real numpy.
    monkeypatch.setattr(np_proxy, "array", fake_array)

    # Verify the patch is visible via the proxy.
    assert np_proxy.array([1, 2, 3]) == "fake"
    import numpy as real_np

    assert real_np.array is fake_array, (
        "XV-78 regression: monkeypatch.setattr on the lazy proxy did "
        "NOT propagate to the real numpy module in sys.modules. "
        "Production code that does ``import numpy as np`` would NOT "
        "see the patch, the test fixture layer is broken."
    )
    assert captured, "fake_array was not called"


# Files that have ``np.ndarray`` annotations in their source. Without
_FILES_WITH_NP_ANNOTATIONS: list[str] = [
    "voice_typer/server/audio_processor.py",
    "voice_typer/server/audio_quality.py",
    "voice_typer/server/transcription.py",
    "voice_typer/server/recording/__init__.py",
]


@pytest.mark.parametrize("rel_path", _FILES_WITH_NP_ANNOTATIONS)
def test_file_has_future_annotations(rel_path: str) -> None:
    """Each file with ``np.ndarray`` annotations must have"""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    abs_path = repo_root / rel_path
    assert abs_path.is_file(), f"test setup bug: {abs_path} does not exist"
    src = abs_path.read_text(encoding="utf-8")
    assert "from __future__ import annotations" in src, (
        f"TY-2 regression: {rel_path} uses ``np.ndarray`` annotations "
        f"but is missing ``from __future__ import annotations``. "
        f"Without PEP 563, the annotations are evaluated at function-"
        f"definition time, triggering the lazy proxy and pulling in "
        f"numpy eagerly, the optimization we're trying to land."
    )


def test_app_py_has_future_annotations() -> None:
    """``app.py`` itself does not currently use ``np.ndarray`` annotations"""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    abs_path = repo_root / "voice_typer" / "server" / "app.py"
    src = abs_path.read_text(encoding="utf-8")
    assert "from __future__ import annotations" in src, (
        "TY-2 regression: app.py is missing ``from __future__ import "
        "annotations``. The lazy ``np`` proxy is bound at module top, "
        "so any future ``np.ndarray`` annotation in this file would "
        "trigger the eager import we're trying to avoid."
    )


def test_numpy_no_longer_in_app_module_top_imports(capsys: pytest.CaptureFixture[str]) -> None:
    """a direct child of any of the 5 target modules' import trees."""
    import pathlib
    import re

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    target_files = [
        repo_root / "voice_typer" / "server" / "app.py",
        repo_root / "voice_typer" / "server" / "audio_processor.py",
        repo_root / "voice_typer" / "server" / "audio_quality.py",
        repo_root / "voice_typer" / "server" / "transcription.py",
        repo_root / "voice_typer" / "server" / "recording" / "__init__.py",
    ]
    # Pattern matches a top-level ``import numpy as np`` statement
    top_level_import_re = re.compile(r"^import\s+numpy\s+as\s+np\s*$", re.MULTILINE)
    for path in target_files:
        src = path.read_text(encoding="utf-8")
        matches = top_level_import_re.findall(src)
        assert not matches, (
            f"TY-2 regression: {path.name} has a top-level "
            f"``import numpy as np`` statement. Replace it with "
            f"``from voice_typer.server._lazy_import import lazy_module`` "
            f'+ ``np = lazy_module("numpy")``.'
        )
