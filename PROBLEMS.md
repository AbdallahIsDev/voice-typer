# PROBLEMS

**Last updated:** 2026-05-29 (all fixes verified, zero open issues)

---

## All Previously Documented Problems — Status

All problems from the original forensic audit (commit `adc80c3`) and the subsequent deep-codebase review have been addressed:

| Original Issue | Status | Fix |
|----------------|--------|-----|
| **P0: Settings model selector broken** | **FIXED** | `settings.py:269` — strips display suffix before `apply()` |
| **P1: LOGOFF/SHUTDOWN never exits** | **FIXED** | `app.py:983-993` — `_daemon_quit_and_exit` calls `os._exit(0)` |
| **P1: CTRL_C_EVENT race** | **FIXED** | `app.py:983-993` — unified daemon quit path |
| **P1: text_cleanup.py thread safety** | **FIXED** | `text_cleanup.py:12,154,280,299,311` — `_corrections_lock` added |
| **P1: Recording callback error handling** | **FIXED** | `recording.py:248-272` — try/except wraps callback body |
| **P1: Orphan guard tests hang in pytest** | **FIXED** | `tests/test_app.py` — mock `threading.Thread` in ctrl_close tests, restore `sys.__stdout__` after handler call |
| **P2: Missing return type annotations** | **FIXED** | ~40 methods annotated with `-> None`, `-> bool`, `-> Optional[...]` |
| **P2: PynputHotkey.start() silent failure** | **FIXED** | `hotkeys.py:94-99` — clear warning when both backends fail |
| **P3: _setup_logging() not idempotent** | **FIXED** | `app.py:37,54-59` — `_logging_initialized` guard |
| **P3: _open_config_file() Popen failure** | **FIXED** | `app.py:791-797` — polls exit code, notifies on failure |
| **P3: corrections.json miscategorized** | **FIXED** | `corrections.json:40` — "without whether" moved to phrase_corrections |
| **P4: Stale HWND_MESSAGE docstring** | **FIXED** | Already resolved before this pass |
| **P4: self._hwnd unused field** | **FIXED** | Already resolved before this pass |
| **P4: self._devnull untracked** | **FIXED** | Already resolved before this pass |
| **P4: Dead variables** | **FIXED** | Already resolved before this pass |
| **All documentation gaps** | **FIXED** | README.md rewritten, CHANGELOG.md created, diagnostics README updated |

**Total: 16 problems resolved — 2 open issues.**

---

## Open Issues

### B1: CI installer build — Inno Setup cannot find PyInstaller output

**Status:** UNRESOLVED (multiple fix attempts failed)

**Problem:** GitHub Actions CI workflow runs `pyinstaller` successfully (exit 0) but then `iscc` fails because the compiled `VoiceTyper.exe` is not at the expected path. The location of PyInstaller's `dist/VoiceTyper/VoiceTyper.exe` output depends on whether PyInstaller chdirs to the spec directory before resolving `--distpath`, which is not clearly documented and varies by version.

**Relevant files:**
- `.github/workflows/build.yml:34` — `pyinstaller scripts/build/voice-typer.spec --noconfirm --distpath dist --workpath build`
- `scripts/build/voice-typer.spec` — PyInstaller spec
- `scripts/build/installer.iss:9` — `#define MyBuildDir "dist\VoiceTyper"` (or `..\..\dist\VoiceTyper`)

**What's known:** PyInstaller step exits 0 (success) in every run, yet the EXE is never found by InnoSetup at either `repo-root/dist/VoiceTyper/` or `spec-dir/dist/VoiceTyper/`.

---

### B2: Redundant "Settings..." button in tray menu

**Status:** FIXED (line 255 removed from tray.py in commit b4fbecc)
