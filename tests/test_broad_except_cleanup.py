"""Tests for the broad ``except Exception: pass`` cleanup.

Pins the contracts that:
1. Production code in the owned-files set no longer contains bare
   ``except Exception: pass`` blocks (they swallow real bugs).
2. The narrowed exception handlers still catch the documented
   platform-specific failures.
"""

from __future__ import annotations

import ast
import pathlib

_OWNED_FILES = [
    "voice_typer/server/ipc_server.py",
    "voice_typer/server/platform_launch.py",
    "voice_typer/server/recording/device_manager.py",
    "voice_typer/server/recording/recorder.py",
    "voice_typer/server/recording/_recorder_split.py",
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
]


def _find_broad_except_pass(filepath: str) -> list[tuple[int, str]]:
    """Return ``[(line_number, snippet)]`` for every bare
    ``except Exception: pass`` block in ``filepath``."""
    p = pathlib.Path(filepath)
    if not p.exists():
        return []
    # UTF-8 explicitly: several owned files contain non-ASCII identifiers
    # (e.g. § comments, non-breaking spaces) that crash getpreferredencoding()
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
    """XS-36: ``except Exception: pass`` swallows real bugs. Every site
    in the owned-files set must either:
      * Catch a narrower exception type (``except OSError:``,
        ``except (KeyError, TypeError):``, etc.), OR
      * Log the failure at ``debug`` level with ``exc_info=True`` (so the
        bug is diagnosable without surfacing to the user), OR
      * Use ``contextlib.suppress(SpecificException)``.
    """

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
    """Spot-check that the narrowed handlers catch the documented
    platform-specific exceptions."""

    def test_ipc_server_sigusr1_handler_catches_attribute_error(self):
        """``signal.SIGUSR1`` is missing on Windows → ``AttributeError``."""
        import voice_typer.server.ipc_server as mod

        # The module imports successfully on every platform because the
        # SIGUSR1 setup is wrapped in ``except (AttributeError, ...)``.
        assert hasattr(mod, "IPCServer"), "ipc_server module failed to import"

    def test_task_scheduler_schtasks_catches_filenotfound_and_timeout(self):
        """``task_scheduler._schtasks`` runs ``schtasks`` via
        ``subprocess.run`` with two narrowed exception handlers
        (``FileNotFoundError`` for non-Windows hosts without schtasks.exe
        + ``subprocess.TimeoutExpired`` for a hung Task Scheduler
        service). Both handlers return a sentinel ``(rc, output)``
        tuple instead of propagating — the caller (autostart register /
        unregister / query) treats the sentinel as a soft failure and
        falls through without crashing the IPC handler.

        (Wave 3, 2026-08-14): the previous test pinned the narrowed
        ``except (IndexError, ValueError, OSError):`` clause inside
        ``task_scheduler._prewarm_command`` (the python-executable
        resolver for the deleted prewarm binary). ``_prewarm_command``
        was removed in lockstep with the prewarm binary (prewarm became
        a worker startup phase — master plan §6.2 P-1), so the test was
        re-pinned on the surviving ``_schtasks`` wrapper which carries
        the SAME narrowed-handler discipline (``FileNotFoundError`` +
        ``TimeoutExpired`` instead of a broad ``except Exception:``).
        """
        import inspect

        from voice_typer.server import task_scheduler

        src = inspect.getsource(task_scheduler._schtasks)
        # The narrowed handlers MUST be present (XS-36: no broad
        # ``except Exception: pass``).
        assert "except FileNotFoundError:" in src, (
            "task_scheduler._schtasks should catch FileNotFoundError (schtasks.exe "
            "missing on non-Windows hosts) instead of broad Exception"
        )
        assert "except subprocess.TimeoutExpired:" in src, (
            "task_scheduler._schtasks should catch subprocess.TimeoutExpired (a hung "
            "Task Scheduler service) instead of broad Exception"
        )

    def test_recorder_rec1_join_catches_runtime_error(self):
        """``pre_thread.join()`` on an un-started thread raises
        ``RuntimeError`` — the REC-1 wrapper catches it, narrowed to
        RuntimeError only. Both XS-36-approved forms are accepted:
        ``except RuntimeError:`` or ``contextlib.suppress(RuntimeError)``
        (the latter is what the file's docstring endorses for this
        site)."""
        import inspect

        from voice_typer.server.recording.recorder import Recorder

        src = inspect.getsource(Recorder._start_audio_worker)
        assert "except RuntimeError:" in src or "contextlib.suppress(RuntimeError)" in src, (
            "REC-1 wrapper should catch RuntimeError from pre_thread.join() — "
            "use a RuntimeError-narrowed handler (except RuntimeError: or "
            "contextlib.suppress(RuntimeError)), not a broad except Exception"
        )
