"""Windows-specific NVIDIA CUDA DLL path setup.

This module exposes NVIDIA wheel DLL directories (cuBLAS / cuDNN / nvRTC
shipped by the ``nvidia-*`` pip packages or bundled under ``torch/lib``)
to the Windows loader so that faster-whisper's CTK runtime can locate
them at model-load time.

On non-Windows platforms every entry point is a no-op — the inner
implementation early-returns when ``is_windows()`` is false, so callers
on Linux / macOS can invoke ``_configure_nvidia_dll_paths()`` unconditionally.

Extracted from ``voice_typer.server.transcription`` so the DLL-search
logic lives in a single, focused module. The public names are re-exported
from ``transcription`` for backward compatibility with callers and tests
that import them from there.

State ownership: the mutable state that these functions mutate —
``_nvidia_dll_path_handles`` (the open DLL-directory handles),
``_nvidia_dll_paths_configured`` (one-shot latch), and
``_nvidia_config_lock`` (RACE-029 serializer) — STAYS declared at
module level in ``transcription`` so existing tests and callers that
read/write ``transcription._nvidia_dll_path_handles`` continue to work.
The ``_NvidiaDllPathManager`` class encapsulates the operations on that
state (free handles / configure) behind a single object so the
module-level globals are no longer touched directly by the
implementation. A module-level singleton ``transcription._nvidia_dll_paths``
(default-constructed with no ``state_dict`` argument) binds to
``transcription``'s module globals via late binding, so existing tests
that rebind ``transcription._nvidia_dll_path_handles`` continue to see
their replacement list reflected through ``_nvidia_dll_paths.handles``.
The public functions ``_free_nvidia_dll_path_handles`` and
``_configure_nvidia_dll_paths`` delegate to that singleton.

Thread-safety: ``_configure_nvidia_dll_paths`` is serialized by
``_nvidia_config_lock`` (RACE-029) to prevent concurrent loads from
corrupting ``_nvidia_dll_path_handles`` and ``os.environ["PATH"]``.
"""

from __future__ import annotations

import logging
import os
import site
import sys
import time
from typing import Any

from voice_typer.server.duration import format_duration
from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)


class _NvidiaDllPathManager:
    """Encapsulates the mutable NVIDIA DLL-path state.

    The state lives in a ``state_dict`` mapping the three canonical
    keys — ``_nvidia_dll_path_handles`` (list),
    ``_nvidia_dll_paths_configured`` (bool), ``_nvidia_config_lock``
    (threading.Lock) — to their current values. When ``state_dict`` is
    ``None`` (the default for the production singleton), the manager
    reads/writes through the ``voice_typer.server.transcription``
    module's globals via late binding, so existing tests that poke
    ``transcription._nvidia_dll_path_handles`` directly continue to
    work (the singleton sees the rebound value on the next attribute
    access).

    Constructing a manager with an explicit ``state_dict`` (a plain
    dict) is useful for unit tests that want a fresh, isolated state
    without touching module globals — see
    ``tests/test_transcription_phase_helpers.py``.
    """

    def __init__(self, state_dict: dict[str, Any] | None = None) -> None:
        self._state_dict: dict[str, Any] | None = state_dict

    # ── state_dict accessors ──────────────────────────────────────

    def _get(self, key: str) -> Any:
        """Read a state value, late-binding to ``transcription`` globals
        when no explicit ``state_dict`` was provided."""
        if self._state_dict is not None:
            return self._state_dict[key]
        # Late binding: read through the ``transcription`` module's
        # __dict__ so rebinds (e.g. by tests) are reflected immediately.
        from voice_typer.server import transcription as _t

        return getattr(_t, key)

    def _set(self, key: str, value: Any) -> None:
        """Write a state value, late-binding to ``transcription`` globals
        when no explicit ``state_dict`` was provided."""
        if self._state_dict is not None:
            self._state_dict[key] = value
            return
        from voice_typer.server import transcription as _t

        setattr(_t, key, value)

    # ── public properties ─────────────────────────────────────────

    @property
    def handles(self) -> list[Any]:
        """The list of open DLL-directory handles (mutable)."""
        return self._get("_nvidia_dll_path_handles")

    @property
    def configured(self) -> bool:
        """One-shot latch: True once configuration has run successfully."""
        return self._get("_nvidia_dll_paths_configured")

    @configured.setter
    def configured(self, value: bool) -> None:
        self._set("_nvidia_dll_paths_configured", value)

    @property
    def lock(self) -> Any:
        """RACE-029 serializer lock for ``configure()``."""
        return self._get("_nvidia_config_lock")

    # ── operations ────────────────────────────────────────────────

    def free_handles(self) -> None:
        """Release DLL directory handles opened by ``configure()``.

        ``os.add_dll_directory`` returns a handle that holds a reference
        to the OS-level DLL directory entry. Previously these handles
        were stored and never freed, so the process held phantom DLL
        directory refs even after the model was unloaded. We now
        iterate and call each handle's ``close()`` method (the
        documented way to release the directory entry) and clear the
        list in place. Called from ``TranscriptionEngine.unload()`` on
        shutdown.
        """
        handles = self.handles
        for handle in list(handles):
            try:
                close = getattr(handle, "close", None)
                if close is not None:
                    close()
                else:
                    # Some Python versions return a path string instead
                    # of a handle object; nothing to close in that case.
                    pass
            except Exception as exc:
                log.debug("[CUDA-DLL] Error closing handle %s: %s", handle, exc)
        # Mutate the existing list in place so external references
        # (e.g. tests that grabbed ``mod._nvidia_dll_path_handles``
        # before calling us) see the cleared state.
        handles.clear()

    def configure(self) -> None:
        """Expose NVIDIA wheel DLL directories to the Windows loader.

        RACE-029: serialized by ``_nvidia_config_lock`` to prevent
        concurrent calls from corrupting ``handles`` and ``PATH``.
        """
        with self.lock:
            self._configure_locked()

    def _configure_locked(self) -> None:
        """Inner implementation, called under ``lock``."""
        if self.configured or not is_windows():
            return
        # C-LOG-2: report the DLL scan + prepend duration on the
        # completion line so "package loading" time is measurable.
        _t0 = time.perf_counter()

        roots: list[str] = []
        try:
            roots.extend(site.getsitepackages())
        except Exception as exc:
            log.warning("[CUDA-DLL] site.getsitepackages() failed: %s", exc)
        try:
            user_site = site.getusersitepackages()
            if user_site:
                roots.append(user_site)
        except Exception as exc:
            log.warning("[CUDA-DLL] site.getusersitepackages() failed: %s", exc)

        # Also include the current venv's site-packages (via sys.prefix).
        # site.getsitepackages() can be wrong when the app runs from a
        # different Python environment (e.g. Hermes venv) than expected.
        venv_sp = os.path.join(sys.prefix, "Lib", "site-packages")
        if os.path.isdir(venv_sp) and venv_sp not in roots:
            roots.append(venv_sp)
            log.debug("[CUDA-DLL] Added current venv site-packages: %s", venv_sp)

        # Fallback: the app's own venv at ~/.voice-typer/venv/ may have
        # the NVIDIA pip wheels even when the running Python belongs to
        # a different environment.
        app_venv_sp = os.path.join(
            os.path.expanduser("~"),
            ".voice-typer",
            "venv",
            "Lib",
            "site-packages",
        )
        if os.path.isdir(app_venv_sp) and app_venv_sp not in roots:
            roots.append(app_venv_sp)
            log.debug("[CUDA-DLL] Added app venv site-packages: %s", app_venv_sp)

        log.debug("[CUDA-DLL] Searching root paths for NVIDIA DLLs: %s", roots)

        candidate_parts = [
            ("nvidia", "cublas", "bin"),
            ("nvidia", "cudnn", "bin"),
            ("nvidia", "cuda_nvrtc", "bin"),
            # CUDA-DLL-001: torch GPU wheels (pip install torch with CUDA)
            # also place cublas64_12.dll, cudnn64_9.dll, nvrtc64_120_0.dll
            # under torch/lib. Without this entry, users who installed the
            # GPU torch wheel but NOT the standalone nvidia-* pip packages
            # would have all 3 primary candidate paths miss, even though
            # the DLLs physically exist on disk.
            #
            # Phase 1c (PLAN_ONNX_INTEGRATION.md §6.2–6.3): this branch
            # is slated for removal once torch is fully dropped from the
            # project (Phase 1d) AND ``tests/test_transcription.py::
            # TestCudaDll001TorchLib::test_torch_lib_path_is_searched``
            # is updated/removed (out of this slice's ownership). The
            # ``os.path.isdir`` check below already makes this a no-op
            # when torch is not installed (the path simply won't exist),
            # so keeping the entry is harmless — it just adds one extra
            # ``isdir`` call per ``configure()`` invocation. The plan's
            # intent ("the torch/lib scan path dies") is satisfied at
            # runtime: with no torch installed, the path is never found.
            ("torch", "lib"),
        ]
        existing_paths = os.environ.get("PATH", "").split(os.pathsep)
        new_paths: list[str] = []
        for root in roots:
            for parts in candidate_parts:
                path = os.path.join(root, *parts)
                if not os.path.isdir(path):
                    log.debug("[CUDA-DLL] Path not found: %s", path)
                    continue
                dll_names = [n for n in os.listdir(path) if n.lower().endswith(".dll")]
                if not dll_names:
                    log.debug("[CUDA-DLL] No DLLs in: %s", path)
                    continue
                log.debug(
                    "[CUDA-DLL] Found path with %d DLLs: %s (first: %s)",
                    len(dll_names),
                    path,
                    dll_names[0],
                )
                if path not in existing_paths and path not in new_paths:
                    new_paths.append(path)
                add_dll_directory = getattr(os, "add_dll_directory", None)
                if add_dll_directory is not None:
                    try:
                        handle = add_dll_directory(path)
                        log.debug(
                            "[CUDA-DLL] os.add_dll_directory(%s) -> handle=%s",
                            path,
                            handle,
                        )
                        if handle is not None:
                            self.handles.append(handle)
                    except Exception as exc:
                        log.warning(
                            "[CUDA-DLL] os.add_dll_directory(%s) failed: %s",
                            path,
                            exc,
                        )

        if new_paths:
            os.environ["PATH"] = os.pathsep.join(new_paths + existing_paths)
            log.info(
                "[CUDA-DLL] Prepended to PATH: %s%s",
                new_paths,
                format_duration(time.perf_counter() - _t0),
            )

        self.configured = True


# ── module-level public functions (delegate to the singleton) ─────────
#
# The singleton itself lives in ``voice_typer.server.transcription`` as
# ``_nvidia_dll_paths`` so it can late-bind to that module's globals
# (the module-level ``_nvidia_dll_path_handles`` /
# ``_nvidia_dll_paths_configured`` / ``_nvidia_config_lock`` attrs that
# existing tests rebind). These thin wrappers reach back through the
# import to find the singleton, keeping ``nvidia_dll_paths`` free of
# the circular import that would arise if it held the singleton itself.


def _free_nvidia_dll_path_handles() -> None:
    """Release DLL directory handles opened by ``_configure_nvidia_dll_paths``.

    Delegates to the ``transcription._nvidia_dll_paths`` singleton so the
    manager class owns the mutation logic in one place.
    """
    from voice_typer.server import transcription as _t

    _t._nvidia_dll_paths.free_handles()


def _configure_nvidia_dll_paths():
    """Expose NVIDIA wheel DLL directories to the Windows loader.

    Delegates to the ``transcription._nvidia_dll_paths`` singleton.
    RACE-029: serialized by ``_nvidia_config_lock`` to prevent concurrent
    calls from corrupting ``_nvidia_dll_path_handles`` and PATH.

    Also gates CUDA visibility: when the runtime DLLs cannot actually be
    loaded (CPU-only torch install, missing ``nvidia-*`` wheels), every
    downstream ``import ctranslate2`` / ``import torch`` would otherwise
    pay ~20s of CUDA device enumeration before falling back to CPU.
    Setting ``CUDA_VISIBLE_DEVICES=""`` here — before those imports run
    — makes them skip the GPU probe entirely (~3s vs ~22s cold) and
    keeps model loading on CPU directly.
    """
    from voice_typer.server import transcription as _t

    _t._nvidia_dll_paths.configure()
    _configure_cuda_visibility_if_broken()


def _configure_nvidia_dll_paths_locked():
    """Inner implementation, called under ``_nvidia_config_lock``.

    Delegates to the singleton's ``_configure_locked`` so callers that
    already hold the lock (none in production, but preserved for
    backward-compat with tests that import this name) still work.
    """
    from voice_typer.server import transcription as _t

    _t._nvidia_dll_paths._configure_locked()


# ── CUDA runtime availability gate ────────────────────────────────────
#
# The CUDA runtime DLLs (cuBLAS / cuLt / cuDNN) ship with the
# ``nvidia-*`` wheels or the GPU build of torch — NOT with a CPU-only
# torch install. On such machines ``ctranslate2.get_cuda_device_count()``
# still reports a device (the driver is present), but model load fails
# with "cublas64_12.dll is not found or cannot be loaded" — after
# ~20s of CUDA enumeration during ``import ctranslate2``. These helpers
# make that failure cheap and early.

_CUDA_DLL_CANDIDATES = ("cublas64_12.dll", "cublasLt64_12.dll")
_cuda_availability_checked = False
_cuda_available = True


def _cuda_runtime_available() -> bool:
    """Return True when the CUDA runtime DLLs can actually be loaded.

    Non-Windows platforms return True unconditionally (no cheap ctypes
    probe exists there; the normal ``ctranslate2`` detection path
    applies). On Windows, probes the core cuBLAS DLLs via
    ``ctypes.WinDLL`` after :func:`_configure_nvidia_dll_paths` has
    prepended the NVIDIA wheel directories to PATH. The result is
    cached per process (the probe costs a few milliseconds).

    Fail-open: any unexpected error (e.g. ``ctypes.WinDLL`` missing when
    a non-Windows platform is mocked as Windows in tests) returns True
    so the caller falls through to the authoritative ctranslate2
    detection.
    """
    global _cuda_availability_checked, _cuda_available
    if not is_windows():
        return True
    if _cuda_availability_checked:
        return _cuda_available
    _cuda_availability_checked = True
    try:
        import ctypes

        _cuda_available = all(_load_cuda_dll(ctypes, name) for name in _CUDA_DLL_CANDIDATES)
    except Exception:
        _cuda_available = True  # fail-open: let ctranslate2 decide
    if not _cuda_available:
        log.warning(
            "[CUDA-DLL] CUDA runtime DLLs unavailable (%s missing) — CUDA disabled; model will load on CPU",
            " / ".join(_CUDA_DLL_CANDIDATES),
        )
    return _cuda_available


def _load_cuda_dll(ctypes: Any, name: str) -> bool:
    """Probe-load a single CUDA DLL; return True if it can be loaded."""
    try:
        handle = ctypes.WinDLL(name)
        del handle
        return True
    except OSError:
        return False


def _configure_cuda_visibility_if_broken() -> None:
    """Hide the GPU from downstream libraries when CUDA is unusable.

    Sets ``CUDA_VISIBLE_DEVICES=""`` once when the CUDA runtime DLLs
    cannot be loaded, so ``import ctranslate2`` / ``import torch`` skip
    the ~20s CUDA device-enumeration stall and load in CPU-only mode.
    No-op on non-Windows, when CUDA is usable, or when the variable is
    already set (by the user or a prior call).
    """
    if not is_windows():
        return
    if _cuda_runtime_available():
        return
    # Only skip when the variable is present AND explicitly empty
    # (i.e. the user already hid the GPU). An UNSET variable must be
    # set to "" here — otherwise ``import ctranslate2`` would still
    # probe CUDA for ~20s before the model-load failure.
    if os.environ.get("CUDA_VISIBLE_DEVICES") == "":
        return  # already hidden
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    log.info(
        "[CUDA-DLL] Set CUDA_VISIBLE_DEVICES='' — downstream imports "
        "(ctranslate2/torch) skip CUDA enumeration (~20s) and load on CPU"
    )
