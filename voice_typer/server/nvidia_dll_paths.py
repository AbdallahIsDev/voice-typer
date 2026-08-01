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

State ownership: the module-level state that these functions mutate —
``_nvidia_dll_path_handles`` (the open DLL-directory handles),
``_nvidia_dll_paths_configured`` (one-shot latch), and
``_nvidia_config_lock`` (RACE-029 serializer) — STAYS in
``transcription`` so existing tests and callers that read/write
``transcription._nvidia_dll_path_handles`` continue to work. The
functions here access that state via late binding
(``from voice_typer.server import transcription as _t`` inside each
function body) to avoid a circular import at module load time and to
preserve the test-visible mutation contract.

Thread-safety: ``_configure_nvidia_dll_paths`` is serialized by
``_nvidia_config_lock`` (RACE-029) to prevent concurrent loads from
corrupting ``_nvidia_dll_path_handles`` and ``os.environ["PATH"]``.
"""

from __future__ import annotations

import logging
import os
import site
import sys

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)


def _free_nvidia_dll_path_handles() -> None:
    """Release DLL directory handles opened by ``_configure_nvidia_dll_paths``.

    PERF- ``os.add_dll_directory`` returns a handle that holds
    a reference to the OS-level DLL directory entry. Previously these
    handles were stored in ``_nvidia_dll_path_handles`` and never freed,
    so the process held phantom DLL directory refs even after the model
    was unloaded. We now iterate and call each handle's ``close()``
    method (the documented way to release the directory entry).
    Called from ``TranscriptionEngine.unload()`` on shutdown.
    """
    # Late binding: the state lives in ``transcription`` so existing
    # tests that rebind ``transcription._nvidia_dll_path_handles``
    # before calling us continue to see their replacement list.
    from voice_typer.server import transcription as _t

    for handle in _t._nvidia_dll_path_handles:
        try:
            close = getattr(handle, "close", None)
            if close is not None:
                close()
            else:
                # Some Python versions return a path string instead of
                # a handle object; nothing to close in that case.
                pass
        except Exception as exc:
            log.debug("[CUDA-DLL] Error closing handle %s: %s", handle, exc)
    _t._nvidia_dll_path_handles = []


def _configure_nvidia_dll_paths():
    """Expose NVIDIA wheel DLL directories to the Windows loader.

    RACE-029: serialized by _nvidia_config_lock to prevent concurrent
    calls from corrupting _nvidia_dll_path_handles and PATH.
    """
    from voice_typer.server import transcription as _t

    with _t._nvidia_config_lock:
        _configure_nvidia_dll_paths_locked()


def _configure_nvidia_dll_paths_locked():
    """Inner implementation, called under _nvidia_config_lock."""
    from voice_typer.server import transcription as _t

    if _t._nvidia_dll_paths_configured or not is_windows():
        return

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

    # Fallback: the app's own venv at ~/.voice-typer/venv/ may have the
    # NVIDIA pip wheels even when the running Python belongs to a
    # different environment.
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
            log.debug("[CUDA-DLL] Found path with %d DLLs: %s (first: %s)", len(dll_names), path, dll_names[0])
            if path not in existing_paths and path not in new_paths:
                new_paths.append(path)
            add_dll_directory = getattr(os, "add_dll_directory", None)
            if add_dll_directory is not None:
                try:
                    handle = add_dll_directory(path)
                    log.debug("[CUDA-DLL] os.add_dll_directory(%s) -> handle=%s", path, handle)
                    if handle is not None:
                        _t._nvidia_dll_path_handles.append(handle)
                except Exception as exc:
                    log.warning("[CUDA-DLL] os.add_dll_directory(%s) failed: %s", path, exc)

    if new_paths:
        os.environ["PATH"] = os.pathsep.join(new_paths + existing_paths)
        log.info("[CUDA-DLL] Prepended to PATH: %s", new_paths)

    _t._nvidia_dll_paths_configured = True
