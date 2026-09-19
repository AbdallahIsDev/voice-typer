"""Windows-specific NVIDIA CUDA DLL path setup.

This module exposes NVIDIA wheel DLL directories (cuBLAS / cuDNN / nvRTC
shipped by the ``nvidia-*`` pip packages) to the Windows loader so that
faster-whisper's CTK runtime can locate them at model-load time.

On non-Windows platforms every entry point is a no-op, the inner
implementation early-returns when ``is_windows()`` is false, so callers
on Linux / macOS can invoke ``_configure_nvidia_dll_paths()`` unconditionally.

Extracted from ``voice_typer.server.transcription`` so the DLL-search
logic lives in a single, focused module. The public names are re-exported
from ``transcription`` for backward compatibility with callers and tests
that import them from there.

State ownership: the mutable state that these functions mutate —
``_nvidia_dll_path_handles`` (the open DLL-directory handles),
``_nvidia_dll_paths_configured`` (one-shot latch), and
``_nvidia_config_lock`` (RACE-029 serializer). STAYS declared at
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
    """Encapsulates the mutable NVIDIA DLL-path state."""

    def __init__(self, state_dict: dict[str, Any] | None = None) -> None:
        self._state_dict: dict[str, Any] | None = state_dict

    def _get(self, key: str) -> Any:
        """Read a state value, late-binding to ``transcription`` globals
        when no explicit ``state_dict`` was provided."""
        if self._state_dict is not None:
            return self._state_dict[key]
        # Late binding: read through the ``transcription`` module's
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

    def free_handles(self) -> None:
        """Release DLL directory handles opened by ``configure()``."""
        handles = self.handles
        for handle in list(handles):
            try:
                close = getattr(handle, "close", None)
                if close is not None:
                    close()
                else:
                    # Some Python versions return a path string instead
                    pass
            except Exception as exc:
                log.debug("[CUDA-DLL] Error closing handle %s: %s", handle, exc)
        # Mutate the existing list in place so external references
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
        venv_sp = os.path.join(sys.prefix, "Lib", "site-packages")
        if os.path.isdir(venv_sp) and venv_sp not in roots:
            roots.append(venv_sp)
            log.debug("[CUDA-DLL] Added current venv site-packages: %s", venv_sp)

        # Fallback: the app's own venv at ~/.voice-typer/venv/ may have
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


# The singleton itself lives in ``voice_typer.server.transcription`` as


def _free_nvidia_dll_path_handles() -> None:
    """Release DLL directory handles opened by ``_configure_nvidia_dll_paths``."""
    from voice_typer.server import transcription as _t

    _t._nvidia_dll_paths.free_handles()


def _configure_nvidia_dll_paths():
    """Expose NVIDIA wheel DLL directories to the Windows loader.

     Delegates to the ``transcription._nvidia_dll_paths`` singleton.
     RACE-029: serialized by ``_nvidia_config_lock`` to prevent concurrent
     calls from corrupting ``_nvidia_dll_path_handles`` and PATH.

      Also gates CUDA visibility: when the runtime DLLs cannot actually be
      loaded (CPU-only install, missing ``nvidia-*`` wheels), every
      downstream ``import ctranslate2`` would otherwise
      pay ~20s of CUDA device enumeration before falling back to CPU.
      Setting ``CUDA_VISIBLE_DEVICES=""`` here, before those imports run
    , makes them skip the GPU probe entirely (~3s vs ~22s cold) and
     keeps model loading on CPU directly.

     Also defaults ``CUDA_MODULE_LOADING=LAZY`` (NVIDIA CUDA 11.7+,
     default-on since 12.3 on Windows): defers CUDA kernel loading from
     context init to first use, cutting cold device-enumeration time on
     older drivers. No-op on drivers where lazy is already default.
     ``setdefault`` so an explicit user env value always wins.
    """
    import os

    os.environ.setdefault("CUDA_MODULE_LOADING", "LAZY")
    from voice_typer.server import transcription as _t

    _t._nvidia_dll_paths.configure()
    _configure_cuda_visibility_if_broken()


def _configure_nvidia_dll_paths_locked():
    """Inner implementation, called under ``_nvidia_config_lock``."""
    from voice_typer.server import transcription as _t

    _t._nvidia_dll_paths._configure_locked()


# The CUDA runtime DLLs (cuBLAS / cuLt / cuDNN) ship with the

_CUDA_DLL_CANDIDATES = ("cublas64_12.dll", "cublasLt64_12.dll")
_cuda_availability_checked = False
_cuda_available = True


def _cuda_runtime_available() -> bool:
    """Return True when the CUDA runtime DLLs can actually be loaded."""
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
            "[CUDA-DLL] CUDA runtime DLLs unavailable (%s missing). CUDA disabled; model will load on CPU",
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
    """Hide the GPU from downstream libraries when CUDA is unusable."""
    if not is_windows():
        return
    if _cuda_runtime_available():
        return
    # Only skip when the variable is present AND explicitly empty
    if os.environ.get("CUDA_VISIBLE_DEVICES") == "":
        return  # already hidden
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    log.info(
        "[CUDA-DLL] Set CUDA_VISIBLE_DEVICES='': downstream imports "
        "(ctranslate2) skip CUDA enumeration (~20s) and load on CPU"
    )
