"""Worker exe package — runtime-pack process for offline transcription.

Phase 2 / master plan §4.4: a separate Nuitka onefile (parallel to the
slim-core sidecar) that bundles ``onnxruntime`` + ``ctranslate2`` +
``numpy/scipy`` + ``av`` + ``pyrnnoise`` + the Silero VAD ONNX model +
the Parakeet tokenizer. The worker is launched by the Tauri host AFTER
the runtime-pack download completes + is verified (see plan §7.3), and
stays running for the app's lifetime (long-lived worker model — §7.3).

The worker's job:

1. **Prewarm** (master plan §6.2 P-1): page the runtime-pack libraries'
   files into the OS standby cache via
   :func:`voice_typer.server.prewarm.warm_imports_for_worker`. Runs
   ONCE at startup, BEFORE the first transcription request.
2. **WebSocket server**: bind on ``127.0.0.1:0`` (loopback-only,
   ADR-0020 §1), report the OS-assigned port to the host via a single
   ``{"event":"server_started","port":N}`` line on stdout, and accept
   authenticated WS connections from the slim-core sidecar (which acts
   as the WS client — see plan §7.1 "1-host ↔ 2-processes pattern").
3. **Auth**: bearer-token handshake via ``hmac.compare_digest`` (pattern
   from :mod:`voice_typer.server.ipc.auth`). The token comes from the
   ``VOICE_TYPER_IPC_TOKEN`` env var set by the Tauri host at spawn.
4. **Single-instance**: a lock file prevents parallel worker spawns
   (pattern from :mod:`voice_typer.server.single_instance`).
5. **Shutdown**: graceful via WS close (the slim-core sidecar shut down
   → WS connection drops → worker exits) or forceful via SIGTERM
   (POSIX) / ``taskkill`` (Windows) from the host's kill-children
   backstop (mirroring the slim-core sidecar's shutdown model).
"""

from __future__ import annotations

__all__: list[str] = []
