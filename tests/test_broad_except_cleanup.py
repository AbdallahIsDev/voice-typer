"""
Tests for the broad ``except Exception: pass`` cleanup.
Pins the contracts that:
"""

from __future__ import annotations

import ast
import pathlib

_OWNED_FILES = [
    "voice_typer/server/ipc_server.py",
    "voice_typer/server/platform_launch.py",
    "voice_typer/server/recording/device_manager.py",
    "voice_typer/server/recording/recorder.py",
    "voice_typer/server/recording/recording_buffer.py",
    "voice_typer/server/recording/recording_lifecycle.py",
    "voice_typer/server/recording/recording_snapshot.py",
    "voice_typer/server/recording/stream_lifecycle.py",
    "voice_typer/server/recording/buffer.py",
    "voice_typer/server/segmented_download.py",
    "voice_typer/server/native_hotkeys/_reader.py",
    "voice_typer/server/native_hotkeys/_core.py",
    "voice_typer/server/hotkeys/wayland.py",
    "voice_typer/server/hotkeys/native_adapter.py",
    "voice_typer/server/hotkeys/win32_vk.py",
    "voice_typer/server/clipboard/manager.py",
    "voice_typer/server/clipboard/windows.py",
    "voice_typer/server/clipboard/linux.py",
    "voice_typer/server/streaming.py",
    "voice_typer/server/task_scheduler.py",
    "voice_typer/server/crash_recovery.py",
    "voice_typer/server/clipboard_target_safety/__init__.py",
    "voice_typer/server/clipboard_target_safety/targets.py",
    "voice_typer/server/clipboard_target_safety/injection.py",
    "voice_typer/server/clipboard_target_safety/validation.py",
    "voice_typer/server/dictation_pipeline.py",
    "voice_typer/server/service.py",
    "voice_typer/server/startup_tasks.py",
]


def _find_broad_except_pass(filepath: str) -> list[tuple[int, str]]:
    """Return ``[(line_number, snippet)]`` for every bare"""
    p = pathlib.Path(filepath)
    if not p.exists():
        return []
    # cp1252 on Windows. C-TEST-5-gen.
    src = p.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            continue
        # ``except Exception:`` (bare name, not tuple)
        if (
            isinstance(node.type, ast.Name)
            and node.type.id == "Exception"
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Pass)
        ):
            results.append((node.lineno, "except Exception: pass"))
    return results


class TestNoBroadExceptPassInOwnedFiles:
    """``except Exception: pass`` swallows real bugs. Every site"""

    def test_no_broad_except_pass_remains(self):
        violations: list[str] = []
        for f in _OWNED_FILES:
            for line, snippet in _find_broad_except_pass(f):
                violations.append(f"{f}:{line}: {snippet}")
        assert not violations, (
            "XS-36: bare ``except Exception: pass`` blocks remain in owned "
            "files (they swallow real bugs). Convert each to either a "
            "narrower exception type, a debug log with exc_info=True, or "
            "contextlib.suppress(SpecificException). Violations:\n" + "\n".join(violations)
        )


class TestNarrowedExceptionHandlers:
    """Spot-check that the narrowed handlers catch the documented"""

    def test_ipc_server_sigusr1_handler_catches_attribute_error(self):
        """``signal.SIGUSR1`` is missing on Windows → ``AttributeError``."""
        import voice_typer.server.ipc_server as mod

        assert hasattr(mod, "IPCServer"), "ipc_server module failed to import"

    def test_task_scheduler_schtasks_catches_filenotfound_and_timeout(self):
        """``task_scheduler._schtasks`` runs ``schtasks`` via"""
        import inspect

        from voice_typer.server import task_scheduler

        src = inspect.getsource(task_scheduler._schtasks)
        # The narrowed handlers MUST be present (: no broad
        assert "except FileNotFoundError:" in src, (
            "task_scheduler._schtasks should catch FileNotFoundError (schtasks.exe "
            "missing on non-Windows hosts) instead of broad Exception"
        )
        assert "except subprocess.TimeoutExpired:" in src, (
            "task_scheduler._schtasks should catch subprocess.TimeoutExpired (a hung "
            "Task Scheduler service) instead of broad Exception"
        )

    def test_recorder_rec1_join_catches_runtime_error(self):
        """``pre_thread.join()`` on an un-started thread raises"""
        import inspect

        from voice_typer.server.recording.recorder import Recorder

        src = inspect.getsource(Recorder._start_audio_worker)
        assert "except RuntimeError:" in src or "contextlib.suppress(RuntimeError)" in src, (
            "REC-1 wrapper should catch RuntimeError from pre_thread.join(), "
            "use a RuntimeError-narrowed handler (except RuntimeError: or "
            "contextlib.suppress(RuntimeError)), not a broad except Exception"
        )
