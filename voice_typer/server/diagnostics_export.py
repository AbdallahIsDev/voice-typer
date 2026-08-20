"""Diagnostic bundle export ( extraction).

Extracted verbatim from :meth:`CrashRecovery.create_diagnostic_bundle`
so the ``crash_recovery.py`` module can focus on its core concern
(storing / flushing / replaying transcription recovery entries). The
diagnostic bundle is a separate concern — it reads from many subsystems
(config, system info, archive) and writes a redacted zip for
support tickets ().

The function takes the owning :class:`CrashRecovery` instance (``recovery``)
as an explicit argument because the bundle body needs three things from
it:

* ``recovery._path.parent`` — fallback config dir if
  ``_config_dir()`` raises.
* ``recovery._lock`` — the per-instance mutex guarding ``_entries``.
* ``recovery._entries`` — the recovery entries list (read under
  ``_lock`` to produce a metadata-only snapshot for
  ``crash_recovery.json``).

The lock is acquired at the SAME point in the zip-write sequence as the
original method body (mid-zip, just before writing
``crash_recovery.json``) so behavior — including the happens-before
ordering between subsystem snapshots — is preserved exactly.

:meth:`CrashRecovery.create_diagnostic_bundle` becomes a thin delegate
that calls :func:`create_diagnostic_bundle` here.
``service.diagnostics.DiagnosticsMixin.export_diagnostics`` also calls
this module directly (no longer through ``CrashRecovery``).
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.crash_recovery import CrashRecovery

log = logging.getLogger(__name__)


def create_diagnostic_bundle(recovery: CrashRecovery) -> str | None:
    """Create a diagnostic bundle zip file.

        Collects:
          - voice-typer.log
          - config.json (redacted — API keys removed)
          - System info (platform, Python version, GPU info)
          - Model info (loaded model, device)
          - Crash recovery entries (metadata only — no transcription text,
    per )
          - Prewarm health + sentinel + PID file contents
          - Archived ``crash_diagnostics_archive/*`` files

        Returns the path to the created zip file, or ``None`` on failure.

        Behavior is preserved verbatim from the original
    meth:`CrashRecovery.create_diagnostic_bundle` body ().
    """
    import zipfile
    from datetime import datetime

    try:
        from voice_typer.server.config import _config_dir

        config_dir = _config_dir()
    except Exception:
        config_dir = recovery._path.parent

    # UE-5-F6 collision: ``%Y%m%d_%H%M%S`` is second-resolution, so
    # two exports in the same second produce the same bundle_path —
    # the second call's ``os.replace`` would clobber the first call's
    # zip. ``%f`` (microseconds) was added next, but on Windows
    # ``datetime.now()`` is quantized to the system timer tick
    # (~15.6 ms), so two rapid back-to-back exports can STILL land on
    # the identical microsecond and produce the same name (observed in
    # the full-suite run: two sequential exports both named
    # ``..._030802_328984.zip``). ``time.time_ns()`` is equally
    # quantized on Windows. The only collision-proof disambiguator is
    # a per-call random suffix — mirrors the ``mkstemp`` strategy used
    # for the tmp file, and the ``uuid4().hex[:8]`` / ``token_hex``
    # patterns used elsewhere in the codebase.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique = uuid.uuid4().hex[:8]
    bundle_name = f"voice-typer-diagnostics-{timestamp}-{unique}.zip"
    bundle_path = config_dir / bundle_name

    # Write the zip to a sibling ``.tmp`` file first, then atomically
    # ``os.replace`` it to the final name. Pre-fix,
    # ``zipfile.ZipFile(str(bundle_path), "w", ...)`` opened the final
    # path directly — if the process crashed mid-write (or the disk
    # filled, or the user Ctrl-C'd the export), a partial zip would
    # be left in the config dir. A user attaching that partial zip to
    # a bug report would confuse support (zip is corrupt, no error
    # visible). The atomic rename ensures the final path only ever
    # exists as a complete, valid zip.
    #
    # the tmp file is created via ``tempfile.mkstemp`` (NOT
    # the pre-fix fixed name ``bundle_path.with_suffix(".zip.tmp")``).
    # The fixed name collided on ``O_EXCL`` if two exports ran
    # concurrently (e.g. a user-triggered export from Settings →
    # Troubleshooting racing with a crash-recovery auto-export); the
    # second caller would clobber the first caller's already-written
    # tmp file. ``mkstemp`` returns a unique per-call name with
    # ``O_EXCL`` semantics on the fd itself, so concurrent callers
    # never collide. The fd is wrapped with ``os.fdopen(fd, "wb")``
    # and passed to ``zipfile.ZipFile`` (which needs a seekable
    # binary writable file object — ``BufferedWriter`` from
    # ``fdopen`` qualifies).
    fd, tmp_name = tempfile.mkstemp(
        dir=str(config_dir),
        prefix=bundle_name + ".",
        suffix=".tmp",
    )
    tmp_bundle_path = Path(tmp_name)
    # ``owned_fd`` tracks ownership of the raw fd. ``-1`` means "fd
    # is now owned by the file object (or already closed); do NOT
    # call ``os.close`` on it again" — mirrors the  pattern in
    # ``secure_file_io._secure_atomic_write`` to avoid a double-close
    # if ``os.fdopen`` itself raises.
    owned_fd = fd
    try:
        tmp_file = os.fdopen(fd, "wb", closefd=True)
        owned_fd = -1  # fd is now owned by tmp_file
        try:
            with zipfile.ZipFile(tmp_file, "w", zipfile.ZIP_DEFLATED) as zf:
                # ─── 1. Live log ─────────────────────────────────────
                # Redact PII + secrets line-by-line before adding to the
                # zip. Previously the log was added verbatim via
                # ``zf.write(str(log_path), ...)`` which meant any PII /
                # API key that slipped past the ``PIIRedactionFilter``
                # (e.g. an exception message logged at DEBUG before the
                # filter was attached, or a ``log.debug("config: %s",
                # cfg_dict)`` that bypassed structured redaction) would
                # ship in the bug-report zip. Now we read the log, run
                # each line through the unified :func:`redact_for_export`
                # pipeline ( — same helper used by the archived
                # crash-dump path and ``ipc_diagnostics``), and write
                # the redacted bytes into the zip. ``redact_for_export``
                # passes ``aggressive=True`` to :func:`redact_secret`
                # () so bare short secrets are caught too.
                log_path = config_dir / "logs" / "voice-typer.log"
                if log_path.exists():
                    try:
                        from voice_typer.server._secrets import redact_for_export

                        redacted_lines: list[str] = []
                        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
                            for line in fh:
                                redacted_lines.append(redact_for_export(line))
                        zf.writestr(
                            "voice-typer.log",
                            "".join(redacted_lines),
                        )
                    except Exception:
                        # pre-fix this branch was wrapped in a
                        # ``contextlib.suppress(Exception)`` that
                        # silently swallowed ALL failures (including
                        # redaction failures) at DEBUG level — making
                        # a "skipped the live log because redaction
                        # blew up" event invisible to operators. The
                        # outer suppress is removed (so unexpected
                        # failures propagate to the bundle-creation
                        # except below) and the inner skip is now
                        # logged at WARNING so the operator can see
                        # *which* subsystem's redaction failed.
                        #
                        # Defense in depth: if redaction fails, skip
                        # the log entirely rather than shipping raw
                        # content — the same policy as the archived
                        # crash-dump path ().
                        log.warning(
                            "[CRASH-RECOVERY] failed to redact "
                            "voice-typer.log for diagnostic bundle; "
                            "skipping (redaction-for-export pipeline "
                            "raised — defense in depth)",
                            exc_info=True,
                        )

                # ─── 2. Config (redacted) ────────────────────────────
                config_path = config_dir / "config.json"
                if config_path.exists():
                    try:
                        import json

                        # Iterate over the canonical ``_SECRET_CONFIG_FIELDS``
                        # set instead of a hardcoded tuple that missed
                        # ``cloud_api_key`` and ``groq_api_key``. Pre-fix,
                        # the diagnostic bundle leaked 2 of the 5 API
                        # keys to the zip file (and thus to any bug
                        # report the user attached it to). The shared
                        # frozenset is the single source of truth — any
                        # future secret field added there is
                        # automatically redacted here too. Import from
                        # the canonical ``config_sanitizer`` module
                        # instead of reaching into IPC-server private
                        # state (``ipc_server`` re-exports the same
                        # object for backwards compat — the two paths
                        # produce identical results — but the
                        # dependency direction should be crash_recovery
                        # → config_sanitizer, not crash_recovery →
                        # ipc_server → config_sanitizer).
                        from voice_typer.server.config_sanitizer import (
                            _SECRET_CONFIG_FIELDS,
                        )

                        raw = config_path.read_text(encoding="utf-8")
                        data = json.loads(raw)
                        # Redact sensitive keys
                        for key in _SECRET_CONFIG_FIELDS:
                            if key in data and data[key]:
                                data[key] = "[REDACTED]"
                        zf.writestr("config.json", json.dumps(data, indent=2))
                    except Exception:
                        log.debug(
                            "[CRASH-RECOVERY] failed to redact+write config.json into diagnostic bundle",
                            exc_info=True,
                        )

                # ─── 3. System info ───────────────────────────────────
                import platform
                import sys

                sys_info = [
                    f"Platform: {platform.platform()}",
                    f"Python: {sys.version}",
                    f"Architecture: {platform.machine()}",
                    f"Processor: {platform.processor()}",
                ]
                # Extend system_info with OS release, distro, display
                # server, audio devices, app version, and a redacted
                # env-var allowlist so support engineers can diagnose
                # platform-specific issues (Wayland stalls, missing
                # audio devices, sidecar mode, etc.) without asking
                # the user to run ``--status`` manually.
                sys_info.append(f"OS release: {platform.release()}")
                # distro.id() — Linux-only, lazy import (not available
                # on macOS/Windows by default; the ``distro`` package
                # is a soft dependency).
                if sys.platform.startswith("linux"):
                    try:
                        import distro

                        sys_info.append(f"Distro ID: {distro.id()}")
                        sys_info.append(f"Distro version: {distro.version()}")
                    except ImportError:
                        sys_info.append("Distro ID: <distro package not installed>")
                    except Exception as exc:
                        sys_info.append(f"Distro ID error: {exc}")
                # Display server — distinguishes X11 vs Wayland
                # sessions (matters for clipboard, hotkeys, and tray
                # quirks).
                sys_info.append(f"XDG_SESSION_TYPE: {os.environ.get('XDG_SESSION_TYPE', '<unset>')}")
                sys_info.append(f"WAYLAND_DISPLAY: {os.environ.get('WAYLAND_DISPLAY', '<unset>')}")
                # Audio devices — hostapi + name + max_input_channels +
                # default_samplerate only. : device names ARE
                # redacted via ``redact_pii`` before being written —
                # device names like "John's AirPods Pro" can carry a
                # user-identifying name, and Bluetooth device names
                # in particular are user-settable and may carry PII
                # (email-shaped strings, phone numbers, etc.). Lazy
                # import because sounddevice pulls in PortAudio.
                try:
                    import sounddevice

                    from voice_typer.server.security import redact_pii as _redact_pii_for_devices

                    devices = sounddevice.query_devices()
                    sys_info.append(f"Audio devices (count): {len(devices)}")
                    for dev in devices:
                        # Each device is a dict; guard against
                        # malformed entries.
                        if not isinstance(dev, dict):
                            continue
                        hostapi = dev.get("hostapi", "?")
                        # redact the device name via
                        # ``redact_pii`` so any PII embedded in it
                        # (emails, phones, SSNs, CCs) is masked. We
                        # use ``redact_pii`` (NOT the aggressive
                        # ``redact_for_export``) so legitimate device
                        # names like "External Microphone Array" (a
                        # 25-char run) aren't false-positive-masked —
                        # only the specific PII patterns fire.
                        name = _redact_pii_for_devices(str(dev.get("name", "?")))
                        max_in = dev.get("max_input_channels", 0)
                        sr = dev.get("default_samplerate", "?")
                        # Only include input-capable devices (mics).
                        # Output-only devices aren't relevant for ASR.
                        if max_in and max_in > 0:
                            sys_info.append(
                                f"  [input] hostapi={hostapi} name={name!r} "
                                f"max_input_channels={max_in} "
                                f"default_samplerate={sr}"
                            )
                except ImportError:
                    sys_info.append("Audio devices: <sounddevice not installed>")
                except Exception as exc:
                    sys_info.append(f"Audio devices error: {exc}")
                # App version from the ``voice_typer`` package
                # (exposed via PEP 562 in ``voice_typer/__init__.py``).
                # We use ``voice_typer.__version__`` directly rather
                # than ``branding.__version__`` to avoid modifying
                # ``branding.py`` (owned by another agent). The
                # version is resolved lazily on first access via
                # ``importlib.metadata.version``.
                try:
                    import voice_typer

                    sys_info.append(f"App version: {voice_typer.__version__}")
                except Exception as exc:
                    sys_info.append(f"App version error: {exc}")
                # TAURI_SIDECAR env flag — distinguishes the bundled
                # sidecar process from a standalone Python invocation.
                sys_info.append(f"TAURI_SIDECAR: {os.environ.get('TAURI_SIDECAR', '<unset>')}")
                # VOICE_TYPER_* env-var values are piped
                # through ``redact_secret(value, aggressive=True)``
                # before being written into the bundle. Pre-fix they
                # were written verbatim under the assumption that the
                # app "controls" them — but a user can set
                # ``VOICE_TYPER_API_KEY=sk-…`` (or any other secret-
                # bearing var) at the shell, and that value would
                # ship in the bundle. ``aggressive=True`` catches
                # short bare secrets too. PATH is included as
                # basenames only so we can see what tool directories
                # are on PATH without leaking the user's home
                # directory path.
                from voice_typer.server._secrets import (
                    _redact_home_path as _redact_home_path_for_env,
                    redact_secret as _redact_secret_for_env,
                )

                for key in sorted(os.environ):
                    if key.startswith("VOICE_TYPER_"):
                        value = os.environ[key]
                        # redact home-directory prefix from
                        # path-bearing env vars (e.g.
                        # ``VOICE_TYPER_CONFIG_DIR=/home/alice/.voice-typer``,
                        # ``VOICE_TYPER_NATIVE_DIR``,
                        # ``VOICE_TYPER_NATIVE_BINARY``,
                        # ``VOICE_TYPER_PREWARM_EXE``). Pre-fix, the
                        # ``redact_secret`` call below only redacted
                        # secret-shaped values, so a path-bearing value
                        # shipped verbatim and leaked the OS username
                        # via the path prefix. ``_redact_home_path``
                        # replaces the home-directory prefix with ``~``
                        # (mirrors the env-var path-redaction applied
                        # just below) — applied BEFORE secret
                        # redaction so a value that is BOTH a path AND
                        # contains a secret token gets both treatments.
                        if value and (os.sep in value or "/" in value):
                            value = _redact_home_path_for_env(value)
                        # Redact any secret-bearing value before
                        # truncation. Order matters: redact first,
                        # then truncate, so a truncated secret is
                        # never partially-shipped (a truncation in
                        # the middle of an ``sk-…`` run would defeat
                        # the pattern matcher).
                        value = _redact_secret_for_env(value, aggressive=True)
                        # Truncate very long values (or very long
                        # redacted values) to keep the bundle
                        # manageable.
                        if len(value) > 200:
                            value = value[:200] + "...(truncated)"
                        sys_info.append(f"env[{key}]={value}")
                path_value = os.environ.get("PATH", "")
                if path_value:
                    # PATH basename only: split by os.pathsep, take
                    # basename of each component. This reveals the
                    # directory names (e.g. ``bin``, ``sbin``)
                    # without leaking the user's home directory path.
                    path_parts = [os.path.basename(p) for p in path_value.split(os.pathsep) if p]
                    sys_info.append(f"env[PATH] (basenames)={os.pathsep.join(path_parts)}")
                # GPU info — Phase 1c (PLAN_ONNX_INTEGRATION.md §3.7):
                # replaced the ``torch.cuda.*`` block with
                # ``onnxruntime.__version__`` / ``get_available_providers()``
                # / ``get_device()`` so the diagnostic bundle no longer
                # imports torch. A ``nvidia-smi`` subprocess probe (when
                # available) provides the GPU name + total VRAM, since
                # ORT's ``get_device()`` returns only "cuda" or "cpu".
                try:
                    import onnxruntime as ort  # type: ignore[import-untyped]

                    sys_info.append(f"onnxruntime version: {ort.__version__}")
                    sys_info.append(f"onnxruntime providers: {ort.get_available_providers()}")
                    sys_info.append(f"onnxruntime device: {ort.get_device()}")
                except ImportError:
                    sys_info.append("onnxruntime not installed")
                except Exception as exc:
                    sys_info.append(f"onnxruntime info error: {exc}")
                # GPU name + total VRAM via ``nvidia-smi`` (best-effort).
                # ``nvidia-smi`` is queried as a subprocess (NOT via
                # ``pynvml``) so the diagnostic bundle works on hosts
                # that have the NVIDIA driver but not the Python wheel.
                try:
                    import subprocess as _sp

                    _smi = _sp.run(
                        [
                            "nvidia-smi",
                            "--query-gpu=name,memory.total",
                            "--format=csv,noheader,nounits",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                    if _smi.returncode == 0 and _smi.stdout.strip():
                        _line = _smi.stdout.strip().splitlines()[0]
                        _parts = [p.strip() for p in _line.split(",")]
                        if len(_parts) >= 1:
                            sys_info.append(f"GPU: {_parts[0]}")
                        if len(_parts) >= 2:
                            try:
                                _mem_mb = int(float(_parts[1]))
                                sys_info.append(f"GPU memory: {_mem_mb} MB")
                            except ValueError:
                                pass
                    else:
                        sys_info.append("GPU: <nvidia-smi unavailable>")
                except Exception as exc:
                    sys_info.append(f"GPU info error: {exc}")
                zf.writestr("system_info.txt", "\n".join(sys_info))

                # ─── 4. Model info ────────────────────────────────────
                try:
                    from voice_typer.server.config import Config

                    # Legitimate fresh-snapshot read — this runs
                    # inside the diagnostic-bundle export path which
                    # is post-crash (or user-triggered from Settings →
                    # Troubleshooting). A stale live ``app.config``
                    # could reflect a half-applied mutation that
                    # caused the crash, so reading the on-disk
                    # snapshot is the safer choice for diagnostic
                    # accuracy. Read-only — no mutation, no
                    # config-mutation lock required.
                    cfg = Config.load()
                    model_info = [
                        f"Model: {cfg.model_size}",
                        f"Device: {cfg.device}",
                    ]
                    zf.writestr("model_info.txt", "\n".join(model_info))
                except Exception:
                    pass

                # ─── 4b. ONNX model file SHA-256 hashes ───────────────
                # Phase 1c (PLAN_ONNX_INTEGRATION.md §3.7): include the
                # SHA-256 of ``silero_vad.onnx`` and any Parakeet ONNX
                # files so support engineers can verify the on-disk model
                # matches the expected pinned hash in
                # ``model_hashes.json``. Files are read in 1 MiB chunks
                # to avoid loading the full ~1.3 GB Parakeet FP16 model
                # into memory. Missing files are reported as
                # ``<not present>`` rather than raising — the bundle is
                # best-effort and a missing ONNX file is itself useful
                # diagnostic information (e.g. a failed/interrupted
                # download).
                try:
                    import hashlib as _hashlib
                    import json as _json

                    onnx_hashes: dict[str, str] = {}

                    def _sha256_of(path: Path) -> str:
                        h = _hashlib.sha256()
                        with path.open("rb") as fh:
                            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                                h.update(chunk)
                        return h.hexdigest()

                    # Silero VAD ONNX — lives next to vad.py.
                    try:
                        silero_path = Path(__file__).resolve().parent / "silero_vad.onnx"
                        if silero_path.is_file():
                            onnx_hashes["silero_vad.onnx"] = _sha256_of(silero_path)
                        else:
                            onnx_hashes["silero_vad.onnx"] = "<not present>"
                    except Exception as exc:
                        onnx_hashes["silero_vad.onnx"] = f"<error: {exc}>"

                    # Parakeet ONNX files — live in the HF cache under
                    # ``models--grikdotnet--parakeet-tdt-0.6b-fp16/``
                    # (the fp16 ONNX export the engine + download path
                    # use; see parakeet_engine.py). We glob for
                    # ``*.onnx`` under the snapshot dir so the hash list
                    # auto-includes any future ONNX variants the user
                    # may have downloaded.
                    try:
                        from voice_typer.server.config import _config_dir

                        _parakeet_cache = (
                            _config_dir()
                            / "huggingface"
                            / "hub"
                            / "models--grikdotnet--parakeet-tdt-0.6b-fp16"
                            / "snapshots"
                        )
                        if _parakeet_cache.is_dir():
                            for onnx_file in sorted(_parakeet_cache.rglob("*.onnx")):
                                try:
                                    rel = onnx_file.relative_to(_parakeet_cache)
                                    onnx_hashes[f"parakeet/{rel}"] = _sha256_of(onnx_file)
                                except Exception as exc:
                                    onnx_hashes[f"parakeet/{onnx_file.name}"] = f"<error: {exc}>"
                        else:
                            onnx_hashes["parakeet/"] = "<not downloaded>"
                    except Exception as exc:
                        onnx_hashes["parakeet/"] = f"<error: {exc}>"

                    zf.writestr(
                        "onnx_model_hashes.json",
                        _json.dumps(onnx_hashes, indent=2, sort_keys=True),
                    )
                except Exception as exc:
                    # Best-effort: never abort the bundle over a hash
                    # computation failure. Write the error so the
                    # support engineer knows why the file is missing.
                    with contextlib.suppress(Exception):
                        zf.writestr(
                            "onnx_model_hashes.json",
                            _json.dumps({"error": str(exc)}),
                        )

                # ─── 5. Crash recovery entries (METADATA ONLY) ───────
                # fix: previously this dumped the full
                # self._entries list (which contains the user's
                # dictated transcribed text) into the diagnostic zip.
                # Users sharing diagnostic bundles for bug reports
                # would leak their last 10 transcriptions (which may
                # contain names, addresses, medical info, passwords
                # dictated via voice) in cleartext. The companion CLI
                # path (scripts/diagnostics.py:74) explicitly
                # documents "Excludes: Transcription text (PIII)" —
                # the IPC handler path now honors the same policy.
                with recovery._lock:
                    redacted_entries = [
                        {
                            "timestamp": e.get("timestamp"),
                            "pasted": e.get("pasted", False),
                            "text_length": len(e.get("text", "")),
                        }
                        for e in recovery._entries
                    ]
                    entries_json = _json.dumps(
                        {"entries": redacted_entries, "count": len(recovery._entries)},
                        indent=2,
                        ensure_ascii=False,
                    )
                zf.writestr("crash_recovery.json", entries_json)

                # ─── 6. Prewarm health check ─────────────────────────
                # Prewarm became a worker startup phase (master plan
                # §6.2 P-1): there is no longer a separate prewarm
                # process, sentinel file, or PID file to bundle. The
                # previous block (which probed
                # ``voice_typer.server.prewarm.get_prewarm_status`` +
                # read the sentinel/PID file contents) was removed
                # along with the deleted prewarm machinery. The
                # ``prewarm.json`` entry is intentionally NOT emitted
                # any more — support engineers investigating cache
                # state should look at the worker's startup log
                # instead.

                # ─── 6b. Permission state ────────────────────
                # Bundle the OS-level keyboard + microphone permission
                # state, the pyobjc availability flag, and on Linux the
                # contents of ``/var/lib/voice-typer/permissions-manifest.json``
                # (written by ``scripts/linux/install_permissions.py``
                # after a successful pkexec install) plus a ground-truth
                # ``os.access("/dev/input/event0", os.R_OK)`` check.
                # Pre-fix, support engineers had to ask the user to run
                # ``--status`` manually to gather this information; the
                # diagnostic bundle now includes it so a bug report
                # contains the full permission picture without a
                # back-and-forth. All probes are best-effort — a probe
                # failure populates an ``error`` key but never aborts
                # the bundle creation.
                try:
                    from voice_typer.server.permissions import (
                        _is_pyobjc_available,
                        check_keyboard_permission,
                        check_microphone_permission,
                    )

                    permissions_data: dict = {
                        "keyboard_permission_state": check_keyboard_permission().value,
                        "microphone_permission_state": check_microphone_permission().value,
                        "pyobjc_available": bool(_is_pyobjc_available()),
                    }
                    # Linux-only: bundle the install-manifest JSON
                    # (written by ``install_permissions.py`` after a
                    # successful pkexec install) and the ground-truth
                    # ``/dev/input/event0`` readability check. On
                    # macOS/Windows these keys are absent (the
                    # renderer / support engineer infers "not
                    # applicable" from the platform field in
                    # ``system_info.txt``).
                    if sys.platform.startswith("linux"):
                        manifest_path = Path("/var/lib/voice-typer/permissions-manifest.json")
                        if manifest_path.is_file():
                            try:
                                permissions_data["install_manifest"] = _json.loads(
                                    manifest_path.read_text(encoding="utf-8", errors="replace")
                                )
                            except Exception as manifest_exc:
                                permissions_data["install_manifest"] = {
                                    "error": f"failed to parse {manifest_path}: {manifest_exc}",
                                }
                        else:
                            permissions_data["install_manifest"] = None
                        # Ground-truth readability check for the first
                        # evdev device. This is the SAME check the
                        # ``_check_linux_input_access`` probe performs
                        # internally, but here we expose the raw boolean
                        # so support engineers can correlate "denied"
                        # (from the permission probe) against the actual
                        # filesystem state without re-running the probe.
                        permissions_data["dev_input_event0_readable"] = os.access("/dev/input/event0", os.R_OK)
                    zf.writestr(
                        "permissions.json",
                        _json.dumps(permissions_data, indent=2, default=str),
                    )
                except Exception as perms_exc:
                    # Defensive: never let a permission probe failure
                    # abort the entire diagnostics export. Include the
                    # error so support engineers know why permission
                    # data is missing.
                    zf.writestr(
                        "permissions.json",
                        _json.dumps(
                            {"error": str(perms_exc)},
                            indent=2,
                            default=str,
                        ),
                    )

                # ─── 7. Crash diagnostics archive ────────────────────
                # ``crash_handler.report_pending_crash`` archives each
                # processed crash_diagnostics / python_crash file to
                # ``<config_dir>/crash_diagnostics_archive/`` instead
                # of unlinking it, so the diagnostic bundle can
                # include it here. Each archived file is added under
                # a ``crash_diagnostics_archive/`` prefix in the zip
                # so support engineers can locate it easily.
                #
                # archived files are REDACTED line-by-line
                # via ``redact_for_export`` (the same pipeline as the
                # live ``voice-typer.log``) before being written into
                # the zip. Pre-fix, ``zf.write(str(archived_file),
                # ...)`` shipped each archived file verbatim — but
                # archived files are Python tracebacks +
                # ``sys.modules`` snapshots + platform headers from
                # PRIOR sessions, any of which can carry API keys
                # (URL query-string ``?key=sk-…``), env-var dumps,
                # bearer tokens, or ``str(exception)`` payloads. A
                # user attaching the bundle to a support ticket would
                # leak every secret-bearing crash traceback from
                # prior sessions in cleartext — the very failure mode
                # the live-log redaction exists to prevent.
                #
                # On redaction failure (e.g. archive file unreadable,
                # redactor raises), the file is SKIPPED — defense in
                # depth, mirroring the live-log skip policy ().
                archive_dir = config_dir / "crash_diagnostics_archive"
                if archive_dir.is_dir():
                    from voice_typer.server._secrets import redact_for_export

                    for archived_file in sorted(archive_dir.glob("*")):
                        if not archived_file.is_file():
                            continue
                        try:
                            redacted_lines: list[str] = []
                            with archived_file.open("r", encoding="utf-8", errors="replace") as af:
                                for line in af:
                                    redacted_lines.append(redact_for_export(line))
                            zf.writestr(
                                f"crash_diagnostics_archive/{archived_file.name}",
                                "".join(redacted_lines),
                            )
                        except Exception:
                            # skip on redaction failure
                            # (defense in depth). Never ship the raw
                            # archived file — a single un-redacted
                            # crash dump can leak every secret-bearing
                            # traceback from prior sessions. The skip
                            # is logged at WARNING (matching the
                            # live-log skip policy from ) so
                            # the operator can see *which* archived
                            # file couldn't be redacted.
                            log.warning(
                                "[CRASH-RECOVERY] failed to redact "
                                "archived crash dump %s for diagnostic "
                                "bundle; skipping (defense in depth)",
                                archived_file.name,
                                exc_info=True,
                            )
        finally:
            # Close the tmp file before the atomic rename — on
            # Windows, ``os.replace`` fails if either endpoint has
            # an open handle. ``closefd=True`` (passed to
            # ``os.fdopen`` above) ensures the underlying fd is
            # closed when ``tmp_file`` is closed, so the rename can
            # proceed.
            tmp_file.close()
        # Atomic rename — only do this if the tmp file was
        # successfully written. If the ``with zipfile.ZipFile`` block
        # above raised, we never get here and the tmp file (if any)
        # is left for the next export to overwrite. Use ``os.replace``
        # for atomicity (POSIX rename(2) is atomic; Windows
        # ReplaceFile is too on NTFS).
        os.replace(str(tmp_bundle_path), str(bundle_path))
        # redact the home-directory prefix from the bundle
        # path in the log message so the OS username doesn't leak
        # via the path (the returned ``str(bundle_path)`` is NOT
        # redacted — callers like the IPC handler display it to the
        # user who already knows their own home dir).
        from voice_typer.server._secrets import _redact_home_path

        log.info(
            "[RECOVERY] Diagnostic bundle created: %s",
            _redact_home_path(str(bundle_path)),
        )
        return str(bundle_path)
    except Exception:
        # Clean up the partial tmp file on failure so it doesn't
        # accumulate across failed exports. Best-effort.
        if owned_fd != -1:
            # ``os.fdopen`` itself raised — the fd is still owned by
            # this function and must be closed. (If ``os.fdopen``
            # succeeded, ``owned_fd`` was flipped to ``-1`` and the
            # fd is owned by ``tmp_file``, which is closed in the
            # ``finally`` above.)
            with contextlib.suppress(OSError):
                os.close(owned_fd)
        try:
            if tmp_bundle_path.exists():
                tmp_bundle_path.unlink()
        except OSError:
            pass
        log.exception("[RECOVERY] Failed to create diagnostic bundle")
        return None


__all__ = ["create_diagnostic_bundle"]
