"""Diagnostic bundle export (DR-27 extraction).

Extracted verbatim from :meth:`CrashRecovery.create_diagnostic_bundle`
so the ``crash_recovery.py`` module can focus on its core concern
(storing / flushing / replaying transcription recovery entries). The
diagnostic bundle is a separate concern — it reads from many subsystems
(config, prewarm, system info, archive) and writes a redacted zip for
support tickets (PROD-010).

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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.crash_recovery import CrashRecovery

log = logging.getLogger(__name__)


def create_diagnostic_bundle(recovery: CrashRecovery) -> str | None:
    """PROD-010: Create a diagnostic bundle zip file.

    Collects:
      - voice-typer.log
      - config.json (redacted — API keys removed)
      - System info (platform, Python version, GPU info)
      - Model info (loaded model, device)
      - Crash recovery entries (metadata only — no transcription text,
        per CR-39)
      - Prewarm health + sentinel + PID file contents
      - Archived ``crash_diagnostics_archive/*`` files

    Returns the path to the created zip file, or ``None`` on failure.

    Behavior is preserved verbatim from the original
    :meth:`CrashRecovery.create_diagnostic_bundle` body (DR-27).
    """
    import zipfile
    from datetime import datetime

    try:
        from voice_typer.server.config import _config_dir

        config_dir = _config_dir()
    except Exception:
        config_dir = recovery._path.parent

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle_name = f"voice-typer-diagnostics-{timestamp}.zip"
    bundle_path = config_dir / bundle_name

    # Write the zip to a sibling .tmp file first, then atomically
    # ``os.replace`` it to the final name.  Pre-fix,
    # ``zipfile.ZipFile(str(bundle_path), "w", ...)`` opened the
    # final path directly — if the process crashed mid-write (or
    # the disk filled, or the user Ctrl-C'd the export), a partial
    # zip would be left in the config dir. A user attaching that
    # partial zip to a bug report would confuse support (zip is
    # corrupt, no error visible). The atomic rename ensures the
    # final path only ever exists as a complete, valid zip.
    tmp_bundle_path = bundle_path.with_suffix(".zip.tmp")

    try:
        with zipfile.ZipFile(str(tmp_bundle_path), "w", zipfile.ZIP_DEFLATED) as zf:
            # 1. Log file — redact PII + secrets line-by-line
            # before adding to the zip.  Previously the log was added
            # verbatim via ``zf.write(str(log_path), ...)`` which meant
            # any PII / API key that slipped past the
            # ``PIIRedactionFilter`` (e.g. an exception message logged
            # at DEBUG before the filter was attached, or a
            # ``log.debug("config: %s", cfg_dict)`` that bypassed
            # structured redaction) would ship in the bug-report zip.
            # Now we read the log, run each line through the same
            # ``redact_secret(redact_pii(line))`` pipeline used by the
            # excepthook, and write the redacted bytes into the zip.
            log_path = config_dir / "voice-typer.log"
            if log_path.exists():
                with contextlib.suppress(Exception):
                    try:
                        from voice_typer.server._secrets import redact_secret
                        from voice_typer.server.security import redact_pii

                        redacted_lines: list[str] = []
                        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
                            for line in fh:
                                # ``redact_pii`` + ``redact_secret`` both
                                # operate on str → str; chaining them
                                # catches both PII patterns (email,
                                # phone, IBAN, SSN, CC) and secret
                                # patterns (Bearer tokens, long
                                # alphanumeric keys, ``token=abc``
                                # key=value forms).
                                redacted_lines.append(redact_secret(redact_pii(line)))
                        zf.writestr(
                            "voice-typer.log",
                            "".join(redacted_lines),
                        )
                    except Exception:
                        # If redaction fails (e.g. security
                        # module unavailable), fall back to skipping
                        # the log entirely rather than shipping raw
                        # content — defense in depth.
                        log.debug(
                            "[CRASH-RECOVERY] failed to redact voice-typer.log; skipping",
                            exc_info=True,
                        )

            # 2. Config (redacted)
            config_path = config_dir / "config.json"
            if config_path.exists():
                try:
                    import json

                    # Iterate over the canonical ``_SECRET_CONFIG_FIELDS``
                    # set instead of a hardcoded tuple that missed
                    # ``cloud_api_key`` and ``groq_api_key``. Pre-fix,
                    # the diagnostic bundle leaked 2 of the 5 API keys
                    # to the zip file (and thus to any bug report the
                    # user attached it to). The shared frozenset is the
                    # single source of truth — any future secret field
                    # added there is automatically redacted here too.
                    # Import from the canonical ``config_sanitizer``
                    # module instead of reaching into IPC-server
                    # private state (``ipc_server`` re-exports the
                    # same object for backwards compat — the two
                    # paths produce identical results — but the
                    # dependency direction should be crash_recovery →
                    # config_sanitizer, not crash_recovery →
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

            # 3. System info
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
            # platform-specific issues (Wayland stalls, missing audio
            # devices, sidecar mode, etc.) without asking the user to
            # run ``--status`` manually.
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
            # Display server — distinguishes X11 vs Wayland sessions
            # (matters for clipboard, hotkeys, and tray quirks).
            sys_info.append(f"XDG_SESSION_TYPE: {os.environ.get('XDG_SESSION_TYPE', '<unset>')}")
            sys_info.append(f"WAYLAND_DISPLAY: {os.environ.get('WAYLAND_DISPLAY', '<unset>')}")
            # Audio devices — hostapi + name + max_input_channels +
            # default_samplerate only (no PII: device names are
            # hardware identifiers, not user data).  Lazy import
            # because sounddevice pulls in PortAudio.
            try:
                import sounddevice

                devices = sounddevice.query_devices()
                sys_info.append(f"Audio devices (count): {len(devices)}")
                for dev in devices:
                    # Each device is a dict; guard against malformed entries.
                    if not isinstance(dev, dict):
                        continue
                    hostapi = dev.get("hostapi", "?")
                    name = dev.get("name", "?")
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
            # App version from the ``voice_typer`` package (exposed
            # via PEP 562 in ``voice_typer/__init__.py``). We use
            # ``voice_typer.__version__`` directly rather than
            # ``branding.__version__`` to avoid modifying
            # ``branding.py`` (owned by another agent).  The version
            # is resolved lazily on first access via
            # ``importlib.metadata.version``.
            try:
                import voice_typer

                sys_info.append(f"App version: {voice_typer.__version__}")
            except Exception as exc:
                sys_info.append(f"App version error: {exc}")
            # TAURI_SIDECAR env flag — distinguishes the bundled
            # sidecar process from a standalone Python invocation.
            sys_info.append(f"TAURI_SIDECAR: {os.environ.get('TAURI_SIDECAR', '<unset>')}")
            # Redacted env-var allowlist: VOICE_TYPER_* values are
            # included verbatim (they're app-controlled, no PII);
            # PATH is included as basenames only so we can see what
            # tool directories are on PATH without leaking the
            # user's home directory path.
            for key in sorted(os.environ):
                if key.startswith("VOICE_TYPER_"):
                    value = os.environ[key]
                    # Truncate very long values to keep the bundle
                    # manageable (e.g. VOICE_TYPER_NATIVE_DIR is short,
                    # but a hypothetical future var could be long).
                    if len(value) > 200:
                        value = value[:200] + "...(truncated)"
                    sys_info.append(f"env[{key}]={value}")
            path_value = os.environ.get("PATH", "")
            if path_value:
                # PATH basename only: split by os.pathsep, take
                # basename of each component.  This reveals the
                # directory names (e.g. ``bin``, ``sbin``) without
                # leaking the user's home directory path.
                path_parts = [os.path.basename(p) for p in path_value.split(os.pathsep) if p]
                sys_info.append(f"env[PATH] (basenames)={os.pathsep.join(path_parts)}")
            # GPU info
            try:
                import torch

                sys_info.append(f"CUDA available: {torch.cuda.is_available()}")
                if torch.cuda.is_available():
                    sys_info.append(f"CUDA version: {torch.version.cuda}")
                    sys_info.append(f"GPU: {torch.cuda.get_device_name(0)}")
                    _gpu_props = torch.cuda.get_device_properties(0)
                    # TASK-14: ``_CudaDeviceProperties`` is created
                    # dynamically by torch (``_dummy_type`` when CUDA
                    # is not compiled in), so its attribute surface
                    # is invisible to pyrefly.  Use ``getattr`` to
                    # read ``total_mem`` (bytes) without a static
                    # ``missing-attribute`` error.
                    _total_mem = getattr(_gpu_props, "total_mem", 0)
                    _gpu_mem = _total_mem // 1048576
                    sys_info.append(f"GPU memory: {_gpu_mem} MB")
            except ImportError:
                sys_info.append("PyTorch not installed")
            except Exception as exc:
                sys_info.append(f"GPU info error: {exc}")
            zf.writestr("system_info.txt", "\n".join(sys_info))

            # 4. Model info
            try:
                from voice_typer.server.config import Config

                # Legitimate fresh-snapshot read — this runs inside
                # the diagnostic-bundle export path which is
                # post-crash (or user-triggered from Settings →
                # Troubleshooting). A stale live ``app.config`` could
                # reflect a half-applied mutation that caused the
                # crash, so reading the on-disk snapshot is the safer
                # choice for diagnostic accuracy. Read-only — no
                # mutation, no config-mutation lock required.
                cfg = Config.load()
                model_info = [
                    f"Model: {cfg.model_size}",
                    f"Device: {cfg.device}",
                ]
                zf.writestr("model_info.txt", "\n".join(model_info))
            except Exception:
                pass

            # 5. Crash recovery entries — METADATA ONLY (no transcription text).
            # CR-39 fix: previously this dumped the full self._entries list
            # (which contains the user's dictated transcribed text) into the
            # diagnostic zip. Users sharing diagnostic bundles for bug
            # reports would leak their last 10 transcriptions (which may
            # contain names, addresses, medical info, passwords dictated
            # via voice) in cleartext. The companion CLI path
            # (scripts/diagnostics.py:74) explicitly documents "Excludes:
            # Transcription text (PIII)" — the IPC handler path now
            # honors the same policy.
            import json as _json

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

            # 6. Prewarm health check (Task 4)
            # Bundles the full prewarm status + sentinel/PID file
            # contents so support engineers can diagnose prewarm
            # issues without asking the user to run --status manually.
            try:
                from voice_typer.server.prewarm import (
                    _pid_file_path,
                    _sentinel_path,
                    get_prewarm_status,
                )

                prewarm_data = get_prewarm_status()
                # Add the raw sentinel + PID file contents + paths
                # for full diagnostics.
                prewarm_data["sentinel_path"] = str(_sentinel_path())
                prewarm_data["pid_file_path"] = str(_pid_file_path())
                # Read sentinel file raw contents (if it exists).
                sentinel = _sentinel_path()
                if sentinel.exists():
                    try:
                        prewarm_data["sentinel_contents"] = sentinel.read_text()
                    except OSError as e:
                        prewarm_data["sentinel_contents"] = f"<read error: {e}>"
                else:
                    prewarm_data["sentinel_contents"] = None
                # Read PID file raw contents (if it exists).
                pid_file = _pid_file_path()
                if pid_file.exists():
                    try:
                        prewarm_data["pid_file_contents"] = pid_file.read_text()
                    except OSError as e:
                        prewarm_data["pid_file_contents"] = f"<read error: {e}>"
                else:
                    prewarm_data["pid_file_contents"] = None
                zf.writestr(
                    "prewarm.json",
                    _json.dumps(prewarm_data, indent=2, default=str),
                )
            except Exception as prewarm_exc:
                # Defensive: never let a prewarm probe failure abort
                # the entire diagnostics export. Include the error
                # so support engineers know why prewarm data is missing.
                zf.writestr(
                    "prewarm.json",
                    _json.dumps(
                        {"error": str(prewarm_exc)},
                        indent=2,
                        default=str,
                    ),
                )

            # 7. Crash diagnostics archive
            # ``crash_handler.report_pending_crash`` archives each
            # processed crash_diagnostics / python_crash file to
            # ``<config_dir>/crash_diagnostics_archive/`` instead of
            # unlinking it, so the diagnostic bundle can include it
            # here.  Each archived file is added under a
            # ``crash_diagnostics_archive/`` prefix in the zip so
            # support engineers can locate it easily.
            archive_dir = config_dir / "crash_diagnostics_archive"
            if archive_dir.is_dir():
                for archived_file in sorted(archive_dir.glob("*")):
                    if not archived_file.is_file():
                        continue
                    with contextlib.suppress(Exception):
                        zf.write(
                            str(archived_file),
                            f"crash_diagnostics_archive/{archived_file.name}",
                        )

        # Atomic rename — only do this if the tmp file was
        # successfully written. If the ``with zipfile.ZipFile`` block
        # above raised, we never get here and the tmp file (if any)
        # is left for the next export to overwrite. Use ``os.replace``
        # for atomicity (POSIX rename(2) is atomic; Windows
        # ReplaceFile is too on NTFS).
        os.replace(str(tmp_bundle_path), str(bundle_path))
        log.info("[RECOVERY] Diagnostic bundle created: %s", bundle_path)
        return str(bundle_path)
    except Exception:
        # Clean up the partial tmp file on failure so it doesn't
        # accumulate across failed exports.  Best-effort.
        try:
            if tmp_bundle_path.exists():
                tmp_bundle_path.unlink()
        except OSError:
            pass
        log.exception("[RECOVERY] Failed to create diagnostic bundle")
        return None


__all__ = ["create_diagnostic_bundle"]
