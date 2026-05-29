You are implementing fixes in the existing **Voice Typer** codebase — a premium offline background voice-to-text utility for Windows that runs in the system tray.

This is NOT a greenfield project. You must first understand the current architecture and then add each fix in a way that fits what already exists.

**WARNING:** Everything written in `PROBLEMS.md` was written by a weak AI model. **Don't trust any problem.** Investigate first to see if it's a real problem. If you find it's real, mark it as `verified real` in the file. Under each problem there is a **Fix:** section — it might contain the best solution, but it might not because it was written by a weak AI model. Double-check it, brainstorm yourself, and find + apply the best solution.

**CRITICAL: The GitHub repo has been updated since the last commit.** You MUST re-clone the repo fresh before starting any work. The latest commit is `71f5bf2`. Your local clone must match the exact state at that commit before you make any changes. Do not work from a stale clone.

==================================================
PERSISTENT GOAL MODE
==================================================

**Single active goal:** Fix all problems in PROBLEMS.md inside `C:\Users\11\tools\persistent-voice-typing`.

**Rules:**

1. Work only inside the existing repo/workspace.
2. Read this prompt and PROBLEMS.md completely before editing anything.
3. Read every file in the required reading order. Do not skip files because they look unrelated.
4. Do not restart from scratch. Extend the current architecture and reuse completed work.
5. Execute the implementation. Do not stop at a plan.
6. If a command fails, diagnose the cause, fix it, and rerun the command.
7. Do not silently delete user data.
8. Do not claim complete while any required gate fails or any required verification is missing.
9. Clean up every server, process, thread, or helper started by this task.
10. Keep status files truthful. Mark unverified work as Partial or Blocked with evidence.

==================================================
PROJECT CONTEXT
==================================================

Voice Typer is a tray-first Windows desktop app. Architecture:

```
voice_typer/
├── __main__.py       # Entry point (python -m voice_typer)
├── app.py            # Main orchestrator — startup, state machine, callbacks (992 lines)
├── config.py         # Configuration with platform-aware paths
├── recording.py      # Session-based audio recording
├── transcription.py  # faster-whisper engine with GPU fallback
├── qwen_engine.py    # Qwen3-ASR-0.6B optional backend
├── text_cleanup.py   # Post-transcription text cleanup
├── clipboard.py      # Clipboard copy + safe auto-paste
├── focus.py          # Platform-aware text input focus detection (Win32 only)
├── hotkeys.py        # Hotkey backend abstraction (Win32 native + pynput)
├── platform.py       # OS-specific autostart adapters + mic listing
├── settings.py       # Tkinter settings window
├── streaming.py      # Hidden streaming transcription with overlapping audio windows
├── tray.py           # System tray icon with state indication and dynamic menu
├── corrections.json  # User-editable misspelling/phrase corrections
└── __init__.py       # Package marker
```

Key design decisions:
- **Tray-first**: tray icon appears before model loading starts
- **Hidden streaming transcription**: records full session while transcribing overlapping chunks
- **Graceful degradation**: if GPU fails → CPU fallback, if model fails → app stays alive, F2 retries
- **Safe auto-paste**: only sends paste keystrokes when a text input is confirmed focused
- **Platform adapters**: autostart, focus detection, paste behavior isolated behind platform-specific code
- **CUDA-first**: tries CUDA, falls back to CPU if unavailable
- **Qwen3-ASR optional backend**: replaces Whisper when configured, Whisper stays as fallback
- **Timer tracking**: all pending timers are tracked in `_pending_timers` list for cancellation
- **Thread safety**: `_busy_event` (threading.Event) replaces plain bool for busy state, `_lock` for shared state
- **Atomic state**: `threading.Event` for busy flag, `threading.Lock` for shared data
- **Menu caching**: tray menu is cached and only rebuilt when state changes

Config (`config.py`):
```python
class Config:
    hotkey: str = "<f2>"
    microphone: Optional[str] = None
    model_size: str = "small.en"
    device: str = "cuda"
    asr_backend: str = "whisper"   # "whisper" or "qwen"
    qwen_model_path: Optional[str] = None
    text_cleanup_enabled: bool = True
    corrections_path: Optional[str] = None
    unsafe_paste_on_unknown_focus: bool = False
    streaming_transcription: bool = True
    log_transcriptions: bool = False  # default off — prevents logging transcription text
    # ... other fields
```

Test conventions:
- `tests/__init__.py` exists (package marker)
- 15 test files covering all modules (total ~4500 lines)
- Tests use `unittest.mock.MagicMock, patch` and `pytest.monkeypatch`
- Heavy imports mocked at module level via `@pytest.fixture(autouse=True)` replacing `sys.modules`
- Tests run headless: no GPU, no microphone, no display needed
- Qwen tests are in `tests/test_qwen_engine.py`
- Settings tests use `SettingsController` (the controller, not the tkinter window)
- Test classes are grouped by feature with clear names
- All tests pass on any platform without hardware

Verification commands:
```bash
pip install -e ".[test]"
python -m pytest tests/ -v
```

==================================================
WHAT YOU MUST READ FIRST (Required Reading Order)
==================================================

Read EVERY file in this order before editing anything:

1. **PROBLEMS.md** — source of truth for all problems, their evidence, and suggested fixes
2. **README.md** — project overview, architecture, manual verification checklist
3. **voice_typer/app.py** — main orchestrator (startup, dictation cycle, settings window)
4. **voice_typer/config.py** — config model with save/load, path validation
5. **voice_typer/clipboard.py** — clipboard copy + safe auto-paste with focus-aware guard
6. **voice_typer/focus.py** — Win32 text input focus detection
7. **voice_typer/recording.py** — session-based recording with buffer telemetry
8. **voice_typer/transcription.py** — Whisper engine with GPU fallback, download, unload
9. **voice_typer/qwen_engine.py** — Qwen3-ASR engine (load, transcribe, unload)
10. **voice_typer/text_cleanup.py** — text cleanup pipeline with corrections
11. **voice_typer/corrections.json** — bundled corrections data
12. **voice_typer/streaming.py** — hidden streaming transcription session
13. **voice_typer/hotkeys.py** — hotkey backend abstraction (Win32 + pynput)
14. **voice_typer/platform.py** — autostart adapters (Win32/macOS/Linux)
15. **voice_typer/settings.py** — tkinter settings window
16. **voice_typer/tray.py** — system tray icon with dynamic menu
17. **tests/test_app.py** — existing test patterns for app state, protocol compliance
18. **tests/test_config.py** — config load/save and path validation tests
19. **tests/test_text_cleanup.py** — text cleanup pipeline tests
20. **tests/test_streaming.py** — streaming session tests
21. **tests/test_clipboard.py** — clipboard manager tests
22. **tests/test_focus.py** — focus detection tests
23. **tests/test_settings.py** — headless settings controller tests
24. **tests/test_qwen_engine.py** — Qwen engine tests
25. **tests/test_platform.py** — platform adapter tests
26. **tests/test_tray.py** — tray icon tests
27. **tests/test_recording.py** — recording tests
28. **tests/test_hotkeys.py** — hotkey backend tests
29. **tests/test_transcription.py** — transcription engine tests

==================================================
GOAL
==================================================

Fix every problem listed in PROBLEMS.md. The file has 12 remaining entries across P0–P4:

**P0 — Critical (1 problem):**
1. Console handler orphan guard thread claimed but never implemented — no grace period, no forced exit, untracked `self._devnull` handle leak

**P1 — High (3 problems):**
2. Model change race guard uses fragile double-negative logic — notification added, but `not self._busy_event.is_set()` is confusing
3. HWND_MESSAGE dead code paths not fully cleaned up — stale docstring, stale `_success` comment, unused `self._hwnd` field, stale `run()` docstring
4. macOS/Linux autostart paths still unescaped on Linux, zero test coverage — Linux `Exec={sys.executable}` unescaped, same bug class as original #5

**P2 — Medium (4 problems):**
5. medium.en latency and no UI guidance — no speed/size note in settings or tray, medium.en still offered with no warning
6. Composite hotkey fallback ignores modifiers — no longer crashes, but `<ctrl>+1` matches any `1` press without modifier check
7. Missing return type annotations — most still missing (~29 methods in `app.py` alone)
8. Audio buffer cap lacks tray indication and documentation — O(n) `pop(0)`, no user-facing status for long recordings, no README docs

**P4 — Low (4 introduced issues):**
9. Stale HWND_MESSAGE comment in `WindowsNativeHotkey` class docstring
10. `self._hwnd` unused field in `WindowsNativeHotkey`
11. `self._devnull` untracked in console handler — `_win32_console_handler` opens devnull handle never closed
12. `_WHISPER_MISSPELLINGS` dead variables in `text_cleanup.py` — empty var declarations never referenced

Each fix must be implemented exactly as described in PROBLEMS.md unless you find a better approach. If you find a better approach, document why your approach is better in the final report.

**Standard protocol for each fix:**
- Read the problem's Evidence section in PROBLEMS.md
- Read the corresponding source code
- Read the existing tests for that module
- Implement the fix
- Update tests (add new tests or update existing)
- Run `python -m pytest tests/ -v` — all must pass
- Mark the problem as `✅ FIXED` in PROBLEMS.md

==================================================
IMPORTANT BOUNDARIES
==================================================

Do NOT:
- Restructure or rename the package layout
- Add new dependencies unless absolutely required (document if you do)
- Refactor existing architecture patterns (timer tracking, threading, etc.)
- Touch files outside `voice_typer/`, `tests/`, and project root configs unless necessary
- Modify `pyproject.toml` unless adding a new dependency
- Remove existing tests unless they are broken by your changes (update them instead)

Do:
- Follow existing code style: no comments in production code (docstrings are OK)
- Use same mocking patterns as existing tests (`MagicMock`, `monkeypatch`, `sys.modules` replacement)
- Keep test classes grouped by feature with clear class names
- Run `python -m pytest tests/ -v` after EACH fix before moving to the next
- Run the FULL suite before the final report

==================================================
EXACT OUTCOMES REQUIRED
==================================================

Each fix in PROBLEMS.md has a specific expected outcome defined in its Fix section. After implementing a fix, verify:

- The fix works correctly (manual or automated verification)
- No existing tests break
- If the fix introduces a new behavior, at least one test covers it
- The fix follows existing code patterns

==================================================
TESTING / VERIFICATION
==================================================

Run after EACH fix before moving to the next:
```bash
python -m pytest tests/ -v -x  # stop on first failure
```

Run before final report:
```bash
python -m pytest tests/ -v  # full suite, don't stop on failure
```

Expected: all tests pass. Count them and report the total.

==================================================
PRODUCT-LEVEL VERIFICATION
==================================================

After all automated tests pass, run the manual verification checklist from README.md:

```bash
python -m voice_typer
```

Verify:
- Tray icon appears
- F2 starts recording and tray shows recording state
- F2 stops recording and tray shows transcribing, then idle
- Short phrase copies to clipboard and pastes into focused text input
- Settings opens and saves changes
- Quit exits cleanly with no orphan process
- After console close, tray continues running (CTRL_CLOSE_EVENT survival)

If you are not on a Windows machine with a microphone, skip this section and note it in the final report.

==================================================
SELF-REVIEW IMPROVEMENT LOOP
==================================================

After each fix, run this loop before moving to the next:

1. Does the implementation satisfy the requested behavior from PROBLEMS.md?
2. Does it fit the existing architecture?
3. Are there hidden risks: race conditions, data loss, exception handling gaps, process leaks, flaky tests?
4. If any answer is not backed by evidence, improve and rerun the relevant checks.
5. Continue until no known release-blocking issue remains.

Before final handoff:
1. Code-review your own changes for bugs, regressions, unused code, weak tests.
2. Security-review any new platform APIs, subprocess calls, file reads.
3. Fix every issue found.
4. Run the full test suite one final time.

==================================================
STRICT COMPLETION AUDIT
==================================================

In the final response, include:

- **Objective**: "Fix all problems in PROBLEMS.md"
- For each problem (1-12): Complete / Partial / Blocked — with evidence (test names, file diffs)
- **Test suite results**: total passed, skipped, failed
- **Product verification**: pass/skip with reason
- **Remaining limitations**: any intentional limitations or untested paths

Do not use vague language like "should work" or "looks good" without evidence.

==================================================
DELIVERABLES
==================================================

You MUST produce TWO deliverables at the end:

**1. `.patch` file** — `build/changes-v3.patch`
   - Run `git add -A && git diff --cached > build/changes-v3.patch` (or `git diff HEAD` if no staged changes) to capture every change made.
   - If the repo has uncommitted changes that should be excluded, stage only the intended files first.
   - The patch file must apply cleanly with `git apply build/changes-v3.patch` on the user's machine.
   - Create the `build/` directory if it doesn't exist.

**2. Final report markdown file** — `build/REPORT.md`

**After both files are generated, copy them to the user's Downloads directory:**
   - Verify both files exist at the destination before finishing.
   - Concise implementation report with:
     - What you read
     - What files you changed (list every file with summary of changes)
     - How each fix works
     - Verification results (test counts, manual checks)
     - Intentional limitations (e.g., "Problem X path validation untested on real filesystem paths — mocked tests only")

==================================================
WHEN YOU FINISH
==================================================

Return a concise summary of what was accomplished. copy `build/changes-v3.patch` and `build/REPORT.md` To the user downloads directory.
