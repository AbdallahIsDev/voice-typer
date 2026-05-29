# PROBLEMS

**Last updated:** 2026-05-29 (forensic re-audit after claimed-fix pass)

**Legend:**
- ❌ NOT FIXED = still open.
- ⚠️ PARTIALLY FIXED = some aspects addressed, underlying issue remains.
- 🆕 INTRODUCED = bug or regression introduced by the fix PR itself.
- 🚫 FALSE CLAIM = the fix report claims this was done, code does NOT match.

**Note:** All statuses below are verified against the actual codebase at commit `adc80c3`. The implementer's REPORT.md contained several overstatements and one fabricated claim (see #1).

---

## P0 — Critical

### 1. Console handler orphan guard thread was claimed but never implemented

**Status:** 🚫 FALSE CLAIM — REPORT.md claims a 30-second grace-period thread with `os._exit(0)`. No such code exists anywhere in the codebase. The implemented fix is substantially weaker than claimed.

**Evidence:**
- `app.py:938-971` — `_win32_console_handler` handles `CTRL_CLOSE_EVENT` by calling `FreeConsole()` and redirecting stdout/stderr to devnull. That's it.
- No orphan guard thread, no grace period timeout, no `os._exit(0)` — searched the entire codebase for these terms, zero matches.
- `app.py:953` — `self._devnull = open(os.devnull, 'w')` creates a file handle that is NEVER closed and NEVER tracked in `_devnull_files`.

**Retained risk:** If the tray icon's menu system is already destroyed or the event loop stops pumping, the orphan process is invisible with no way to terminate except Task Manager. The `self._devnull` handle leak is minor (`os.devnull` is a kernel object) but untracked cleanup is sloppy.

**Fix:**
- Add a grace period (e.g. `threading.Timer(30.0, os._exit, [0]).start()`) after `FreeConsole()` succeeds.
- Append `self._devnull` to the module-level `_devnull_files` list so `quit()` closes it.

**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.

---

## P1 — High

### 2. Model change race guard uses fragile double-negative logic

**Status:** ⚠️ PARTIALLY FIXED — notification added; guard logic unchanged.

**Evidence:**
- `app.py:808` — `if self.recorder.recording or not self._busy_event.is_set():  # busy`
- `app.py:140` — `self._busy_event.set()  # SET = not busy`
- `is_set()` returns True when NOT busy. `not is_set()` returns True when busy. The comment at line 808 says `# busy` but the expression is a double-negative.
- Notification was added at `app.py:810-813` ("Model will change to ... after current recording") — that part is correctly fixed.

**Risk:** The double-negative compiles and works, but makes the intent non-obvious to future maintainers. Low probability of introducing bugs during refactoring but easy to misinterpret.

**Remaining work:**
- Replace with positive naming, e.g. `_idle_event = threading.Event(); _idle_event.set()  # SET = idle` and check `if self.recorder.recording or not self._idle_event.is_set()`.

**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.

---

### 3. HWND_MESSAGE dead code paths not fully cleaned up

**Status:** ⚠️ PARTIALLY FIXED — actual `CreateWindowExW`/`DestroyWindow` calls removed; stale references remain.

**Evidence:**
- `hotkeys.py:293-298` — class docstring still says "Creates a hidden message-only window (HWND_MESSAGE) and runs a message loop" — no message-only window is created, no message loop runs. This is misleading to readers.
- `hotkeys.py:310` — `self._success = False  # True only when both CreateWindowExW AND RegisterHotKey succeed` references `CreateWindowExW` which was deleted.
- `hotkeys.py:313` — `self._hwnd = None  # message-only window handle` — field is initialized but never read or used anywhere in the class.
- `hotkeys.py:357` — docstring in `run()` still says "Message loop thread: registers hotkey, runs polling loop." No message loop runs — it uses `GetAsyncKeyState` polling.

**Remaining work:**
- Update class docstring to describe the actual polling-based approach.
- Remove or correct the `_success` comment.
- Remove `self._hwnd` field entirely.
- Update `run()` docstring.

**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.

---

### 4. macOS/Linux autostart paths still unescaped on Linux, zero test coverage

**Status:** ⚠️ PARTIALLY FIXED — macOS escaping added; Linux still has same bug pattern.

**Evidence:**
- `platform.py:209` — macOS path now uses `escape(sys.executable)` ✅.
- `platform.py:243-251` — Linux `.desktop` file uses `Exec={sys.executable} -m voice_typer` — unescaped `sys.executable` means a path like `/home/user/my python/bin` will break. Same class of bug as the original problem #5.
- Zero test coverage for any non-Windows autostart path (`test_platform.py` only covers Windows).

**Risk:** On Linux, Python installed to a path with spaces will silently fail to autostart.

**Fix:**
- Escape `sys.executable` with `shlex.quote()` for the Linux `.desktop` file.
- Add mock-based tests for macOS and Linux autostart paths.

**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.

---

## P2 — Medium

### 5. medium.en latency and no UI guidance

**Status:** ❌ NOT FIXED — no action taken despite PROBLEMS.md explicitly requesting changes.

**Evidence:**
- `config.py:14` — `ALLOWED_USER_MODELS = {"small.en", "medium.en"}` — medium.en is still offered as a tray-accessible option.
- No speed/size note, no latency indication, no warning in the settings UI or tray menu.
- `settings.py` — grep for "speed", "latency", "size", "recommendation" returns zero matches.
- REPORT.md claims this is "informational only, no code change needed" but PROBLEMS.md explicitly says "Add a speed/size note in the settings UI next to the model selector" and "Consider removing medium.en as a tray-accessible option."

**Risk:** Non-technical users who select medium.en on CPU will experience ~12x slowdown with no warning and no explanation.

**Fix:**
- Add a latency/size indication next to the model label in the tray menu or settings window (e.g. "small.en (fast)", "medium.en (~3GB, slower)").
- Alternatively, remove medium.en from the tray menu and make it config-file-only.

**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.

---

### 6. Composite hotkey fallback ignores modifiers

**Status:** ⚠️ PARTIALLY FIXED — no longer crashes, but modifier matching is not implemented.

**Evidence:**
- `hotkeys.py:162-208` — `_parse_hotkey_to_pynput` correctly parses composite hotkeys like `<ctrl>+1` into a tuple `(modifiers_tuple, target_key)`.
- `hotkeys.py:110-111` — `_start_fallback` extracts only `target[1]` (the target key) and **ignores** the modifiers entirely:
  ```python
  match_key = target[1] if isinstance(target, tuple) else target
  ```
- The `on_press` handler at line 113-116 only checks `if key == match_key` — no modifier check.
- The REPORT acknowledges this in its "Remaining Limitations" section: "matches only the target key (not modifiers), which is the same behavior as before but no longer crashes."

**Remaining work:**
- Track pressed modifier keys in the fallback `Listener` and require them to match.
- Or document that the fallback mode only matches the primary key.

**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.

---

### 7. Missing return type annotations — most still missing

**Status:** ⚠️ PARTIALLY FIXED — 12 methods annotated; ~29 remain.

**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.

**Evidence from `app.py` (methods without `->` hint):**
- `filter(self, record)` (line 44), `_setup_logging()` (52), `__init__(self)` (105), `_init_qwen_engine()` (153), `_get_active_transcriber()` (173), `_cancel_pending_timers()` (197), `_get_streaming_session()` (206), `_set_streaming_session()` (211), `start()` (218), `_do_startup()` (253), `_try_load_model()` (317), `_register_hotkey()` (346), `toggle_dictation()` (369), `_start_dictation()` (386), `_stop_dictation()` (448), `_start_streaming_session_if_enabled()` (636), `_cancel_streaming_session()` (656), `_force_recover_from_stuck_transcription()` (666), `_toggle_autostart()` (698), `_set_autostart()` (702), `_set_notifications()` (717), `_select_microphone()` (724), `show_settings()` (739), `_open_config_file()` (768), `_restart_hotkey()` (790), `_change_model()` (804), `quit()` (863), `_install_win32_console_handler()` (905), `_win32_console_handler()` (938), `main()` (974).

Also in **`tray.py`**: `__init__`, `run()`, `invalidate_menu_cache()`, `_build_menu()`, `_build_hotkey_menu_items()`, `_build_model_menu_items()`, `_build_advanced_menu_items()`, `_build_mic_menu_items()`, `_wrap()`, `wrapper()`.

**Risk:** Pyrefly is configured for type checking (`pyproject.toml`) but large gaps in annotations weaken coverage. Not a runtime bug but a maintenance concern.

---

### 8. Audio buffer cap lacks tray indication and documentation

**Status:** ⚠️ PARTIALLY FIXED — O(n) cap implemented; no user-facing feedback.

**Evidence:**
- `recording.py:249-255` — hard cap at 30,000 chunks (~30 min) using `self._buffer.pop(0)` (O(n) per drop — acknowledged in REPORT.md as a known limitation).
- `recording.py:258-262` — log warning at 5,000 chunks (~5 min).
- `recording.py:263-268` — buffer-size telemetry every 1,000 chunks.
- `app.py` — no code reads the buffer size to show tray status. No indication to the user that their recording is unusually long.
- README.md — no mention of the practical upper bound.

**Remaining work:**
- Show buffer state in tray status when it exceeds e.g. 5,000 chunks (e.g. "Recording (long)").
- Replace `list.pop(0)` with `collections.deque.popleft()` for O(1) removal.
- Document the ~30-min upper bound in README.

**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.

---

## P4 — Low

### 9. Stale HWND_MESSAGE comment in WindowsNativeHotkey class docstring

**Status:** 🆕 INTRODUCED — the fix removed the code but left the documentation misleading.

**Evidence:**
- `hotkeys.py:293-298` — class docstring describes HWND_MESSAGE window and message-loop approach that no longer exists.

**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.

---

### 10. `self._hwnd` unused field

**Status:** 🆕 INTRODUCED — `hotkeys.py:313` initializes `self._hwnd = None` but no method reads or writes it.

**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.

---

### 11. `self._devnull` untracked in console handler

**Status:** 🆕 INTRODUCED — `app.py:953` opens a devnull file handle that is never closed or tracked in `_devnull_files`.

**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.

---

### 12. `_WHISPER_MISSPELLINGS` dead variables

**Status:** 🆕 INTRODUCED — `text_cleanup.py:35-37` declares three module-level variables that are initialized to empty and never referenced by any code. They are dead code.

**Evidence:**
- The three variables are only defined at lines 35-37. A grep across the entire `voice_typer/` package shows zero reads of these names.
- The actual active pipelines read from `_active_misspellings`, `_active_phrases`, `_active_extra_words`.

**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.

---

## Remediation Priority Summary

| Priority | Item | Effort | Risk |
|----------|------|--------|------|
| P0 | #1 Implement orphan guard thread (force-exit after 30s) | ~30 min | Process leak on console close |
| P1 | #2 Fix double-negative guard | ~5 min | Future maintainer confusion |
| P1 | #3 Clean up HWND_MESSAGE stale refs | ~10 min | Misleading documentation |
| P1 | #4 Escape Linux `Exec` path + add tests | ~15 min | Linux autostart breaks with spaces |
| P2 | #5 medium.en UI guidance or removal | ~30 min | Silent UX degradation |
| P2 | #6 Implement modifier matching in fallback | ~1 hr | Composite hotkeys wrong in fallback |
| P2 | #7 Add remaining return type annotations | ~1 hr | Weaker pyrefly coverage |
| P2 | #8 Tray status for large buffer + deque | ~30 min | No user feedback |
| P4 | #9 Clean stale docstring | ~2 min | Cosmetic |
| P4 | #10 Remove `self._hwnd` | ~2 min | Cosmetic |
| P4 | #11 Track `self._devnull` | ~5 min | Micro-handle-leak |
| P4 | #12 Remove dead variables | ~2 min | Cosmetic |
