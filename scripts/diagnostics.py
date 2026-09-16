#!/usr/bin/env python3
"""Consolidated diagnostic script for Voice Typer.

Previously 5 separate scripts in scripts/diagnostics/.
Consolidated into a single entry point with subcommands.
The old scripts are kept as thin wrappers for backward compatibility.

Subcommands:
    f2          Trace the full F2 -> recording -> transcription path
    cublas      Verify cuBLAS DLL failure path handling
    runtime     End-to-end runtime verification
    test-runner Run the interactive test suite
export      : Export a diagnostic bundle (logs, config, system info)

Usage:
    python scripts/diagnostics.py f2
    python scripts/diagnostics.py cublas
    python scripts/diagnostics.py runtime
    python scripts/diagnostics.py test-runner
    python scripts/diagnostics.py export
"""

import contextlib
import shutil
import sys
from pathlib import Path

# The diagnostic entry-point modules were moved from
# ``scripts/diagnostics/`` to ``tests/manual/`` (). Add the repo
# root to ``sys.path`` so ``tests`` (and ``tests.manual``) are importable
# as packages when this script is invoked directly via
# ``python scripts/diagnostics.py`` (where ``scripts/`` is on the path
# but the repo root is not).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def run_f2():
    """Delegate to tests/manual/diagnose_f2.py.

    Updated import path, the module was moved from
    ``scripts/diagnostics/`` to ``tests/manual/`` and exposes its
    entry point as ``run`` (per tests/manual/README.md).
    """
    from tests.manual.diagnose_f2 import run as main

    main()


def run_cublas():
    """Delegate to tests/manual/cublas_fallback.py.

    Updated import path: see ``run_f2`` docstring.
    """
    from tests.manual.cublas_fallback import run as main

    main()


def run_runtime():
    """Delegate to tests/manual/runtime_proof.py.

    Updated import path: see ``run_f2`` docstring.
    """
    from tests.manual.runtime_proof import run as main

    main()


def run_test_runner():
    """Delegate to tests/manual/runtime_test_runner.py.

    Updated import path: see ``run_f2`` docstring.
    """
    from tests.manual.runtime_test_runner import main

    main()


def run_export():
    """Export a diagnostic bundle for bug reports."""
    export_diagnostics()


COMMANDS = {
    "f2": ("Trace F2 -> recording -> transcription path", run_f2),
    "cublas": ("Verify cuBLAS DLL failure path handling", run_cublas),
    "runtime": ("End-to-end runtime verification", run_runtime),
    "test-runner": ("Run the interactive test suite", run_test_runner),
    "export": ("Export diagnostic bundle for bug reports", run_export),
}


def _collect_log_tail(
    src: Path,
    dest_dir: Path,
    dest_name: str,
    max_bytes: int = 1024 * 1024,
) -> None:
    """Copy ``src`` into ``dest_dir/dest_name``, keeping only the last ``max_bytes``.

    Silently skips when ``src`` doesn't exist (callers pass paths that are
    only sometimes present, e.g. rotated logs). On read error, writes a
    placeholder file so the diagnostic bundle still records that the log
    was present but unreadable (matches the previous single-log behavior).
    """
    if not src.exists():
        return
    dest = dest_dir / dest_name
    try:
        log_size = src.stat().st_size
        if log_size > max_bytes:
            with open(src, encoding="utf-8", errors="replace") as f:
                f.seek(log_size - max_bytes)
                f.readline()  # skip partial first line
                dest.write_text(f.read(), encoding="utf-8")
        else:
            shutil.copy2(src, dest)
    except Exception as exc:
        dest.write_text(f"Error reading log {src}: {exc}", encoding="utf-8")


def _unique_zip_name(name: str, taken: set[str]) -> str:
    """Return ``name``, suffixed when a previously collected log already claimed it.

    Defensive only: with the current runtime log set the only reachable
    collision is the legacy root ``<config>/voice-typer.log`` against the
    current Python log ``<config>/logs/voice-typer.log`` (same basename,
    different directories). Collection is directory-driven, so an
    unexpected file must never silently overwrite another file's
    contents in a support bundle (the whole point of the bundle is that
    nothing is missing).
    """
    if name not in taken:
        return name
    stem, _, suffix = name.partition(".log")
    index = 2
    while f"{stem}.log-{index}{suffix}" in taken:
        index += 1
    return f"{stem}.log-{index}{suffix}"


def _collect_logs_into(config_dir: Path, dest_dir: Path) -> list[str]:
    """Copy the tail of every runtime log into ``dest_dir``.

    Directory-driven (review.md MO-108): every regular file under
    ``<config_dir>/logs/`` except the inter-process ``*.lock`` files,
    each shipped under its **on-disk basename** (no rename), plus a
    legacy pre-migration ``<config_dir>/voice-typer.log`` when it still
    exists. On-disk names are already distinct after the Rust host
    rename (Python current: ``logs/voice-typer.log``; Rust host:
    ``logs/voice-typer-rust.log``, ``init.rs:149``), so the pre-rename
    ``rust-`` zip alias is gone — a support bundle now labels every file
    the same way the runtime does. :func:`_unique_zip_name` remains as
    the never-overwrite backstop for the legacy-root vs current-Python
    basename pair. This mirrors the three other log-coverage mechanisms,
    which are all directory-scoped (Python sweep in ``log/setup.py``,
    Rust sweep in ``platform/logging/init.rs``, open-logs target in
    ``commands/system_cmds/dialogs.rs``), so a newly added log file
    (e.g. the MO-104 ``sidecar.log`` child tee) ships in support bundles
    automatically instead of being silently dropped by a stale name
    list.

    Pure filesystem work (no app imports) so the coverage contract is
    unit-testable. Returns the collected destination names, sorted.
    """
    collected: list[str] = []
    taken: set[str] = set()
    legacy_python_log = config_dir / "voice-typer.log"
    if legacy_python_log.is_file():
        _collect_log_tail(legacy_python_log, dest_dir, "voice-typer.log")
        collected.append("voice-typer.log")
        taken.add("voice-typer.log")
    logs_dir = config_dir / "logs"
    if logs_dir.is_dir():
        for entry in sorted(logs_dir.iterdir()):
            if not entry.is_file() or entry.name.endswith(".lock"):
                continue
            dest_name = _unique_zip_name(entry.name, taken)
            _collect_log_tail(entry, dest_dir, dest_name)
            collected.append(dest_name)
            taken.add(dest_name)
    return collected


def export_diagnostics() -> str:
    """Collect diagnostic info and save as a timestamped zip file.

    Collects:
      - every log file under ``<config_dir>/logs/`` (Python current log
        ``voice-typer.log`` + rotations, Rust host ``voice-typer-rust.log``,
        the ``sidecar.log`` child tee, ``worker.log``, native listener
        logs, crash buffer, …, whatever the runtime actually wrote),
        each shipped under its on-disk basename and tailed to 1 MiB;
        plus a legacy pre-migration ``<config_dir>/voice-typer.log``
        when it still exists
      - config.json (with API keys redacted)
      - System info (OS, GPU, CUDA version, Python version)
      - Model info (which models are downloaded)

    Excludes:
      - Transcription text (PII)
      - API keys and secrets
      - Crash recovery buffer contents

    Returns
    -------
    str
        Path to the created zip file.
    """
    import json
    import os
    import platform
    import tempfile
    from datetime import datetime, timezone
    from zipfile import ZIP_DEFLATED, ZipFile

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    zip_filename = f"voice-typer-diagnostics-{timestamp}.zip"

    # Find the config directory
    try:
        from voice_typer.server.config import _config_dir

        config_dir = _config_dir()
    except Exception:
        config_dir = Path(os.path.expanduser("~")) / ".voice-typer"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # 1. System info
        sys_info = {
            "timestamp": timestamp,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "processor": platform.processor(),
            "python_implementation": platform.python_implementation(),
        }

        # GPU / CUDA info. Phase 1c (PLAN_ONNX_INTEGRATION.md §3.7):
        # replaced the ``torch.cuda.*`` block with
        # ``onnxruntime.__version__`` / ``get_available_providers()``
        # / ``get_device()`` so the CLI diagnostic producer no longer
        # imports torch. ``nvidia-smi`` subprocess provides the GPU name
        # + total VRAM (ORT's ``get_device()`` returns only "cuda" or
        # "cpu"). ctranslate2 info is preserved (faster-whisper still
        # uses ctranslate2 in Phase 1c).
        try:
            import onnxruntime as ort

            sys_info["onnxruntime_version"] = ort.__version__
            sys_info["onnxruntime_providers"] = ort.get_available_providers()
            sys_info["onnxruntime_device"] = ort.get_device()
        except ImportError:
            sys_info["onnxruntime_version"] = "not installed"
        except Exception as exc:
            sys_info["onnxruntime_error"] = str(exc)

        # GPU name + total VRAM via ``nvidia-smi`` (best-effort).
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
                    sys_info["gpu_name"] = _parts[0]
                if len(_parts) >= 2:
                    with contextlib.suppress(ValueError):
                        sys_info["gpu_memory_total_mb"] = int(float(_parts[1]))
            else:
                sys_info["gpu_name"] = "nvidia-smi unavailable"
        except Exception as exc:
            sys_info["gpu_error"] = str(exc)

        # ctranslate2 info (preserved, faster-whisper still uses
        # ctranslate2 in Phase 1c; the Qwen engine also uses it
        # transitively).
        try:
            import ctranslate2

            sys_info["ctranslate2_version"] = ctranslate2.__version__
            sys_info["cuda_device_count"] = ctranslate2.get_cuda_device_count()
        except ImportError:
            sys_info["ctranslate2_version"] = "not installed"
        except Exception as exc:
            sys_info["ctranslate2_error"] = str(exc)

        # App version
        try:
            from importlib.metadata import version as get_version

            sys_info["app_version"] = get_version("voice-typer")
        except Exception:
            sys_info["app_version"] = "unknown"

        (tmpdir_path / "system_info.json").write_text(json.dumps(sys_info, indent=2), encoding="utf-8")

        # 2. Config (redacted)
        config_file = config_dir / "config.json"
        if config_file.exists():
            try:
                config_text = config_file.read_text(encoding="utf-8")
                config_data = json.loads(config_text)
                # use the canonical _SECRET_CONFIG_FIELDS
                # frozenset from ipc_server.py (single source of truth).
                # Previously this was a hardcoded set that had drifted
                # and omitted ``cloud_api_key`` and ``groq_api_key`` —
                # users running ``python scripts/diagnostics.py export``
                # with those keys set would leak them in cleartext into
                # the zip's config_redacted.json.
                try:
                    from voice_typer.server.ipc_server import (
                        _SECRET_CONFIG_FIELDS as _redact_keys,  # noqa: N811
                    )
                except Exception:
                    # Fallback: copy of the canonical frozenset. Keep in
                    # sync with voice_typer/server/ipc_server.py, that
                    # module is the canonical source.
                    _redact_keys = frozenset(
                        {
                            "cloud_api_key",
                            "openai_api_key",
                            "groq_api_key",
                            "deepgram_api_key",
                            "llm_api_key",
                        }
                    )
                for key in _redact_keys:
                    if key in config_data and config_data[key]:
                        config_data[key] = "***REDACTED***"
                (tmpdir_path / "config_redacted.json").write_text(json.dumps(config_data, indent=2), encoding="utf-8")
            except Exception as exc:
                (tmpdir_path / "config_redacted.json").write_text(f"Error reading config: {exc}", encoding="utf-8")

        # 3. Log files: EVERY log in ``<config_dir>/logs/`` (plus a legacy
        # root-level ``voice-typer.log`` for pre-migration profiles).
        #
        # Directory-driven on purpose (see review.md MO-108): the three
        # log-coverage lists (the Python sweep in ``log/setup.py``, the
        # Rust sweep in ``platform/logging/init.rs``, and the open-logs
        # target in ``commands/system_cmds/dialogs.rs``) are all
        # DIRECTORY-scoped, so any new log file is automatically rotated
        # and visible in the log folder. A hardcoded name list HERE was
        # the odd one out: it collected only ``voice-typer.log*`` and
        # therefore silently dropped ``voice-typer-rust.log`` (the Rust
        # host's log — the glob never matched it), the MO-104
        # ``sidecar.log`` child tee, ``worker.log``, ``startup-error.log``,
        # the crash buffer and ``native-*.log``. Reading the directory
        # keeps the bundle in lockstep with whatever the runtime actually
        # writes, with no list to rot.
        _collect_logs_into(config_dir, tmpdir_path)

        # 4. Model info
        model_info: dict = {}
        try:
            from huggingface_hub import scan_cache_dir

            cache = scan_cache_dir()
            model_info["cached_repos"] = [
                {
                    "repo_id": repo.repo_id,
                    "size_mb": repo.size_on_disk // (1024 * 1024),
                }
                for repo in cache.repos
            ]
        except ImportError:
            model_info["huggingface_hub"] = "not installed"
        except Exception as exc:
            model_info["cache_error"] = str(exc)
        (tmpdir_path / "model_info.json").write_text(json.dumps(model_info, indent=2), encoding="utf-8")

        # 5. Create zip
        zip_path = Path.cwd() / zip_filename
        with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
            for file_path in tmpdir_path.rglob("*"):
                if file_path.is_file():
                    zf.write(file_path, file_path.relative_to(tmpdir_path))

    print(f"Diagnostic bundle exported to: {zip_path}")
    print("Note: This file does NOT contain transcription text or API keys.")
    return str(zip_path)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Usage: python scripts/diagnostics.py <subcommand>")
        print()
        print("Available subcommands:")
        for name, (desc, _) in COMMANDS.items():
            print(f"  {name:15s} {desc}")
        sys.exit(1)

    _, func = COMMANDS[sys.argv[1]]
    func()


if __name__ == "__main__":
    main()
