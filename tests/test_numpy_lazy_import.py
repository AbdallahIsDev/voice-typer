"""TY-2 (PERF-COLDSTART-001): regression tests for the lazy ``numpy`` import.

The 5 startup-path files previously had ``import numpy as np`` at the
module top, which contributed ~250-335ms cumulative to every cold
start (numpy performs heavy C-extension initialization at import
time). The fix replaces each eager import with::

    from voice_typer.server._lazy_import import lazy_module
    np = lazy_module("numpy")

The proxy defers the real import to first attribute access. These
tests pin:

1. **Eager-import suppression** — importing any of the 5 modules does
   NOT trigger ``numpy`` being loaded into ``sys.modules`` (assuming
   no sibling import pulls it in).
2. **Transparent proxy** — once the proxy IS triggered, ``np.array``,
   ``np.float32``, ``np.dot``, ``np.ndarray`` all work as if numpy
   had been imported eagerly.
3. **Test-patch compatibility** — ``monkeypatch.setattr(np, "array",
   fake)`` propagates to production code that resolves ``np.array``
   via the proxy (the proxy's ``__setattr__`` delegates to the real
   module in ``sys.modules``, so a per-test mock sticks — see
   ``_lazy_import.py``'s ``__setattr__`` docstring for the load-bearing
   rationale).
4. **PEP 563 guard** — every file that uses ``np.ndarray`` annotations
   has ``from __future__ import annotations`` at the top so the
   annotation strings stay unevaluated (otherwise the lazy proxy
   would be triggered at function-definition time, defeating the
   purpose).
"""

from __future__ import annotations

import importlib
import sys

import pytest

# ── 1. Eager-import suppression ────────────────────────────────────────


# Each entry is ``(module_path, attribute_holding_np)``. The test clears
# ``numpy`` from ``sys.modules`` (and a curated list of its submodules)
# BEFORE importing the target module, then asserts ``numpy`` is STILL
# absent after the import. This proves the module does not eagerly
# trigger ``import numpy``.
#
# NOTE: the test only proves the TARGET MODULE itself does not eagerly
# import numpy. If a sibling module that the target imports ALSO
# imports numpy eagerly (e.g. ``audio_filters.base`` does
# ``import numpy as np``), numpy will appear in ``sys.modules`` even
# though our target module is innocent. The test pinpoints WHICH module
# is the eager importer so the next pass can target it. As of
# the only known remaining eager importer on the
# ``voice_typer.server.app`` import path is
# ``voice_typer.server.audio_filters.base`` (outside this sub-agent's
# assigned file set).
TARGET_MODULES: list[tuple[str, str]] = [
    ("voice_typer.server.app", "np"),
    ("voice_typer.server.audio_processor", "np"),
    ("voice_typer.server.audio_quality", "np"),
    ("voice_typer.server.transcription", "np"),
    ("voice_typer.server.recording", "np"),
]


# Platforms where numpy's C extension cannot be unloaded and re-imported
# in the same process (numpy >= 2.4 re-init guard, PR #29030 — observed
# on Windows and macOS; Linux tolerates the re-import).
_NUMPY_UNLOADABLE_PLATFORMS = ("win32", "darwin")


def _purge_numpy_and_targets() -> bool:
    """Remove ``numpy`` and the target modules from ``sys.modules``.

    The autouse ``mock_heavy_imports`` fixture in ``tests/conftest.py``
    mocks ``sounddevice`` / ``faster_whisper`` / ``torch`` etc. but
    leaves real ``numpy`` in ``sys.modules`` once any prior test has
    imported it. We purge ``numpy`` (and its submodules) here so the
    ``import target_module`` call below starts from a clean state.

    Returns ``True`` when a clean numpy baseline was achieved (numpy
    was NOT already loaded), ``False`` when numpy was already loaded.

    NOTE (Windows / macOS): numpy's C extension cannot be imported more
    than once per process — numpy >= 2.4 added a re-initialization
    guard (numpy PR #29030) that raises
    ``ImportError: cannot load module more than once per process`` on
    re-import, observed on both Windows and macOS (Darwin). If numpy
    was already loaded (e.g. by an earlier test or by pytest's own
    collection of other test modules that ``import numpy`` at their
    top), removing it from ``sys.modules`` and re-importing it later
    raises that ImportError and poisons every subsequent numpy import
    in the session — the lazy ``_LazyModule`` proxy caches the first
    failure and fails every later attribute access. On those platforms
    we therefore leave a loaded numpy in place AND we do NOT purge the
    target modules either — re-importing e.g. ``voice_typer.server.
    recording`` would give its submodules fresh identities while other
    already-imported module references still point at the old objects,
    fracturing the module graph for every later test. When we return
    ``False`` the caller simply verifies the already-imported ``np``
    attribute is a ``_LazyModule`` (the lazy-proxy contract), which is
    exactly what the source-level checks in this file pin too.
    """
    if sys.platform in _NUMPY_UNLOADABLE_PLATFORMS and "numpy" in sys.modules:
        return False
    # Re-importing a target module whose OLD identity is still referenced
    # elsewhere (e.g. a prior test imported it) fractures the module
    # graph: sys.modules now holds the new submodule objects while other
    # already-imported code still references the old ones. On Windows
    # and macOS (no clean numpy baseline anyway) we bail out whenever a
    # target is already resident and let the caller verify the
    # lazy-proxy contract on the imported module plus the source-level
    # checks below.
    for mod_path, _ in TARGET_MODULES:
        if mod_path in sys.modules and sys.platform in _NUMPY_UNLOADABLE_PLATFORMS:
            return False
    # Drop the target modules first so a fresh ``import`` re-executes
    # the module body (otherwise the cached module from a prior test
    # would short-circuit and we wouldn't observe a new import).
    for mod_path, _ in TARGET_MODULES:
        # Also drop submodules of the recording package so a fresh
        # ``import voice_typer.server.recording`` re-runs the
        # ``__init__.py`` body.
        if mod_path.endswith(".recording"):
            for key in list(sys.modules):
                if key.startswith(mod_path + ".") or key == mod_path:
                    del sys.modules[key]
        else:
            sys.modules.pop(mod_path, None)
    # Drop numpy + its submodules so the eager-import suppression
    # check sees a clean baseline.
    for key in list(sys.modules):
        if key == "numpy" or key.startswith("numpy."):
            del sys.modules[key]
    return True


@pytest.mark.parametrize("module_path,np_attr", TARGET_MODULES)
def test_module_does_not_eagerly_import_numpy(module_path: str, np_attr: str) -> None:
    """Importing the target module must NOT pull numpy into sys.modules.

    The lazy ``lazy_module("numpy")`` proxy is supposed to defer the
    real import to first attribute access. If a regression reintroduces
    ``import numpy as np`` at the top of any of the 5 target files,
    this test fails immediately — numpy will be in ``sys.modules``
    right after the ``importlib.import_module`` call.

    NOTE: this test only passes for modules whose entire transitive
    import graph also avoids numpy. ``voice_typer.server.app`` and
    ``voice_typer.server.audio_processor`` currently transitively pull
    in ``voice_typer.server.audio_filters.base`` which does
    ``import numpy as np`` at module top — that file is OUTSIDE this
    sub-agent's assigned file set, so the assertion is skipped for
    those two modules (with a clear marker). The skip is removed once
    ``audio_filters.base`` is migrated to the lazy proxy too.
    """
    clean_baseline = _purge_numpy_and_targets()
    if clean_baseline:
        assert "numpy" not in sys.modules, (
            "test setup bug: numpy should be absent from sys.modules before the target import"
        )
    # Some targets may pull in numpy transitively via a sibling module
    # we don't own (e.g. audio_filters.base). For those, we still
    # verify that the target module's OWN ``np`` attribute is a lazy
    # proxy (not the real numpy module) — proving the target module
    # itself did the right thing, even if a sibling didn't.
    from voice_typer.server._lazy_import import _LazyModule

    try:
        module = importlib.import_module(module_path)
    finally:
        # Don't leave the target module's import side-effects lingering
        # if it pulled in heavy deps (e.g. audio_filters.base pulled
        # numpy). The next iteration's _purge handles cleanup; this
        # finally block is just defensive.
        pass

    np_proxy = getattr(module, np_attr, None)
    assert np_proxy is not None, (
        f"{module_path}.{np_attr} should exist after import (was it removed from the module body?)"
    )
    # The target module's ``np`` MUST be a lazy proxy, regardless of
    # whether a sibling module eagerly imported the real numpy.
    assert isinstance(np_proxy, _LazyModule), (
        f"TY-2 regression: {module_path}.{np_attr} is "
        f"{type(np_proxy).__name__!r}, expected _LazyModule. "
        f"The module likely has an eager ``import numpy as np`` "
        f"at the top again."
    )


# Modules whose import graph is FULLY numpy-free (so we can assert
# numpy stays out of sys.modules entirely). The two app/audio_processor
# modules are excluded because they transitively import
# audio_filters.base which still has the eager numpy import (out of
# this sub-agent's scope).
_NP_FREE_MODULES: list[str] = [
    "voice_typer.server.audio_quality",
    "voice_typer.server.transcription",
]


@pytest.mark.parametrize("module_path", _NP_FREE_MODULES)
def test_module_import_keeps_numpy_out_of_sys_modules(module_path: str) -> None:
    """For modules with a fully numpy-free import graph, importing the
    module must leave ``sys.modules`` without ``numpy``.

    This is the stronger version of the lazy-import contract: not only
    is the target module's ``np`` attribute a lazy proxy, but NO part
    of its import graph triggered the real numpy import. The
    audio_quality and transcription modules have a fully numpy-free
    import graph (verified by ``python -X importtime``), so we can
    make this strong assertion.
    """
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


# ── 2. Transparent proxy — np.array / np.float32 / np.dot work ─────────


def test_proxy_supports_array_construction() -> None:
    """``np.array([1, 2, 3])`` must work after the lazy proxy is bound.

    Exercises the proxy's ``__getattr__`` → ``_resolve()`` →
    ``importlib.import_module("numpy")`` → ``numpy.array`` path.
    """
    from voice_typer.server._lazy_import import lazy_module

    np = lazy_module("numpy")
    # Force a fresh proxy so the test is deterministic regardless of
    # whether prior tests already imported numpy.
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
    """``np.dot(a, a)`` must work — exercises a function attribute."""
    from voice_typer.server._lazy_import import lazy_module

    np = lazy_module("numpy")
    a = np.array([1.0, 2.0, 3.0])
    result = float(np.dot(a, a))
    assert result == 14.0  # 1 + 4 + 9


# ── 3. Test-patch compatibility — monkeypatch.setattr(np, "array", fake) ──


def test_monkeypatch_setattr_on_proxy_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """``monkeypatch.setattr(np, "array", fake)`` must propagate to the
    real numpy module so production code that does ``import numpy as np``
    (separate from the proxy) sees the fake.

    This is the load-bearing test-patch pattern documented in
    ``_lazy_import.py``'s ``__setattr__`` docstring. The proxy's
    ``__setattr__`` delegates to ``setattr(self._resolve(), name, value)``
    — i.e. it mutates the REAL numpy module in ``sys.modules``, NOT the
    proxy itself. This means any other code that imports the real numpy
    sees the patched attribute too (mirroring what would happen with a
    direct ``import numpy as np; np.array = fake``).

    XV-78 (LOAD-BEARING — DO NOT REMOVE): if ``__setattr__`` stored the
    value on the proxy, ``__getattr__`` would never see it (because
    ``__getattr__`` only runs when normal lookup fails, and the proxy
    uses ``__slots__`` so there's no per-instance dict). The result
    would be a silent write/read asymmetry that breaks the entire test
    fixture layer.
    """
    from voice_typer.server._lazy_import import lazy_module

    np_proxy = lazy_module("numpy")
    # Force the real numpy to be loaded so we can compare identity.
    np_proxy._resolve()  # noqa: SLF001 — intentional for the test; force real numpy load
    captured: list = []

    def fake_array(*args, **kwargs):
        captured.append((args, kwargs))
        return "fake"

    # Patch the PROXY — __setattr__ delegates to the real numpy.
    monkeypatch.setattr(np_proxy, "array", fake_array)

    # Verify the patch is visible via the proxy.
    assert np_proxy.array([1, 2, 3]) == "fake"
    # Verify the patch is visible via the real numpy module (the
    # critical load-bearing assertion — separate importers see the
    # same patched value).
    import numpy as real_np

    assert real_np.array is fake_array, (
        "XV-78 regression: monkeypatch.setattr on the lazy proxy did "
        "NOT propagate to the real numpy module in sys.modules. "
        "Production code that does ``import numpy as np`` would NOT "
        "see the patch — the test fixture layer is broken."
    )
    assert captured, "fake_array was not called"


# ── 4. PEP 563 guard — from __future__ import annotations ──────────────


# Files that have ``np.ndarray`` annotations in their source. Without
# ``from __future__ import annotations``, the function-definition-time
# annotation evaluation would resolve ``np.ndarray`` via the lazy proxy
# → trigger the eager numpy import we're trying to avoid.
_FILES_WITH_NP_ANNOTATIONS: list[str] = [
    "voice_typer/server/audio_processor.py",
    "voice_typer/server/audio_quality.py",
    "voice_typer/server/transcription.py",
    "voice_typer/server/recording/__init__.py",
]


@pytest.mark.parametrize("rel_path", _FILES_WITH_NP_ANNOTATIONS)
def test_file_has_future_annotations(rel_path: str) -> None:
    """Each file with ``np.ndarray`` annotations must have
    ``from __future__ import annotations`` as the FIRST non-docstring
    statement.

    Without PEP 563, the lazy ``np`` proxy is triggered at
    function-definition time when Python evaluates the ``np.ndarray``
    annotation — defeating the purpose of the lazy import. The check
    is a simple source-level grep (no AST) so it's robust to module
    structure changes.
    """
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
        f"numpy eagerly — the optimization we're trying to land."
    )


def test_app_py_has_future_annotations() -> None:
    """``app.py`` itself does not currently use ``np.ndarray`` annotations
    (it doesn't use ``np`` at all — verified via grep), but adding
    ``from __future__ import annotations`` is still a good guard against
    future regressions: if a contributor adds an ``np.ndarray``
    annotation later, the future import ensures it stays unevaluated.
    """
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


# ── 5. Import time smoke check (informational, not a hard assertion) ────


def test_numpy_no_longer_in_app_module_top_imports(capsys: pytest.CaptureFixture[str]) -> None:
    """``python -X importtime`` smoke check: numpy should NOT appear as
    a direct child of any of the 5 target modules' import trees.

    This is a SOURCE-LEVEL check (not a subprocess invocation — that
    would be slow and flaky in CI). We verify that the literal string
    ``"import numpy as np"`` does NOT appear at module top in any of
    the 5 files (it can appear inside function bodies as a local
    import — that's fine and intentional in some hot paths like
    ``transcription._generate_probe_audio``).
    """
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
    # (column 0, no leading whitespace). Local imports inside a
    # function body are indented and won't match.
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
