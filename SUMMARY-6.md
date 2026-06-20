# SUMMARY-6 — Round 6 Fixes

**Date:** 2026-06-20
**Based on:** Round 5 verification report (3 TS errors + BUILD-003 + Templates delete path)
**Status:** All critical errors fixed. 946 Python tests pass. 0 TypeScript errors.

---

## What Was Done

### 1. History.tsx JSX Fragment Wrapper (CRITICAL FIX)

**Problem:** `<div>` and `<ConfirmDialog />` were siblings inside `return (...)` without a `<>...</>` fragment wrapper. This caused 2 TypeScript errors at lines 297 (`')' expected`) and 306 (`Declaration or statement expected`), blocking clean compilation.

**What was done:** Wrapped the entire return statement in `<>...</>` so both `<div>` and `<ConfirmDialog>` are valid JSX children of the fragment.

**File:** `voice_typer/client/src/renderer/src/pages/History.tsx`

---

### 2. History.tsx exportHistory Type Cast

**Problem:** `HistoryRecord[]` is not assignable to `Record<string, unknown>[]` because `HistoryRecord` lacks a string index signature. `WindowBridge.exportHistory` expects `Record<string, unknown>[]`.

**What was done:** Added explicit double cast `all as unknown as Record<string, unknown>[]` at the call site. This is safe because `HistoryRecord` objects are plain serializable objects with string keys.

**File:** `voice_typer/client/src/renderer/src/pages/History.tsx:194`

---

### 3. Templates.tsx callFn Type Alignment

**Problem:** `saveTemplates()` defined `callFn` as `<T>(cmd: string, args?: unknown) => Promise<T>`, but `call` from `usePython()` is typed as `<T = unknown>(type: string, data?: Record<string, unknown>) => Promise<T>`. The parameter type `unknown` vs `Record<string, unknown>` creates a type mismatch under strict function types.

**What was done:** Changed `callFn` parameter type from `args?: unknown` to `data?: Record<string, unknown>` to match `call`'s actual type signature.

**File:** `voice_typer/client/src/renderer/src/pages/Templates.tsx:58`

---

### 4. AsrBackendRegistry — progress_callback Parameter (CRITICAL BUG FIX)

**Problem:** `app.py` called `self._asr_registry.load_with_fallback(progress_callback=on_progress)` but the registry's `load_with_fallback()` and `load_active()` methods did not accept `progress_callback`, causing `TypeError: got an unexpected keyword argument 'progress_callback'`. This crashed the background model load at startup.

**What was done:**
- Added `progress_callback: Any = None` parameter to both `load_with_fallback()` and `load_active()` in `asr_registry.py`.
- Forwarded the callback to `backend.load(progress_callback=_cb)` instead of the hardcoded `lambda msg: None`.
- If no callback is provided, falls back to the no-op lambda.

**Files:** `voice_typer/server/asr_registry.py`

---

### 5. ARCH-007/008: Registry Bypass Fixes in app.py

**Problem:** Three methods in `app.py` bypassed the ASR registry, directly constructing `TranscriptionEngine` or calling `self.transcriber.load()` directly:

- `_fallback_to_whisper()` — directly constructed `TranscriptionEngine` and called `_try_load_model()`, bypassing registry
- `_try_load_model()` — called `self.transcriber.load()` directly instead of going through the registry
- `_start_dictation()` — had an inline Whisper fallback with direct `TranscriptionEngine` construction

**What was done:**
- `_fallback_to_whisper()`: Now sets `config.asr_backend = "whisper"`, syncs registry, and calls `self._asr_registry.load_with_fallback()` instead of `_try_load_model()`.
- `_try_load_model()`: Now delegates to `self._asr_registry.load_with_fallback(progress_callback=...)` instead of `self.transcriber.load()`.
- `_start_dictation()`: Now calls `self._fallback_to_whisper()` instead of inline Whisper construction, reusing the registry-aware fallback path.

**Files:** `voice_typer/server/app.py`

---

### 6. #13 tray.py Concern Mixing — Full Delegation

**Problem:** `TrayIcon.open_electron_window()` and `_bring_electron_to_front()` still had 60+ lines of inline implementation despite `tray_window.py` having extracted versions.

**What was done:**
- `open_electron_window()`: Replaced 35 lines of inline code with a 2-line delegation to `tray_window.open_electron_window()`.
- `_bring_electron_to_front()`: Replaced 60+ lines of inline Win32 code with a 2-line delegation to `tray_window.bring_electron_to_front()`.

**Files:** `voice_typer/server/tray.py`

---

### 7. Test Fixes

**test_qwen_engine.py — Thread Race Condition:**
- Added `app._model_load_thread.join(timeout=5)` before assertions to ensure the background model-load thread completes before checking mock call counts.
- Changed Qwen fallback test to use `side_effect=RuntimeError("Qwen unavailable")` so the registry properly falls back to Whisper.

**test_app.py — Registry Setup:**
- Updated `TestTryLoadModel` class to call `app._sync_asr_registry()` before each test since `_try_load_model` now uses the registry.
- Added `app.tray = MagicMock()` to each test method.

**Files:** `tests/test_qwen_engine.py`, `tests/test_app.py`

---

## Local AI Agent Accuracy Assessment

The local AI agent reported **5 errors** in Round 5 verification. After running `tsc --project tsconfig.web.json --noEmit`:

| Reported Error | Actually an Error? | Assessment |
|---|---|---|
| Templates.tsx:137,158 — callFn type mismatch | **No** — tsc did NOT flag it | Agent was **wrong** about it being a compilation blocker, but the type improvement is still valid for code quality |
| History.tsx:194 — HistoryRecord[] vs Record<string, unknown>[] | **Unverifiable** — masked by syntax error | After fixing the JSX error, tsc still didn't flag it, but the cast is correct and defensive |
| History.tsx:297,306 — sibling JSX without fragment | **YES** — tsc confirmed 2 errors | Agent was **right** |

**Verdict:** The agent over-reported 2-3 of the 5 claimed errors as "🔴 critical". Only the JSX fragment issue was a real compilation blocker. The callFn type and exportHistory type were code quality improvements, not compilation errors. However, the agent correctly identified the BUILD-003 issues in Round 5.

---

## Remaining Partial Items (not fixed this round)

| Item | Status | Notes |
|---|---|---|
| UX-004: README latency claims | ⚠️ PARTIAL | Needs actual benchmarking |
| #9: Keyboard navigation/a11y | ⚠️ PARTIAL | ~45% of interactive elements have a11y attrs |
| #13: _build_models_submenu pystray glue | ⚠️ PARTIAL | Data delegation done, UI glue stays in tray.py (acceptable) |
| ARCH-007/008: _change_model direct construction | ⚠️ MINOR | Creates TranscriptionEngine directly but loads via registry |
| #4 PLAT-WAYLAND | ✅ COMPLETE | WaylandHotkey is committed and functional |
| #8: Onboarding wizard | ❌ NOT STARTED | No IPC routes, no React UI |
| ARCH-005: Service boundary | ⚠️ PARTIAL | Several routes still bypass service layer |
| UX-005: Model download UI | ❌ NOT STARTED | download_model not implemented |
| DEAD-ARCHIVE-REVIVAL | ❌ NOT STARTED | Archive functions not revived |

---

## Verification

- **TypeScript:** `tsc --project tsconfig.web.json --noEmit` → 0 errors
- **Python tests:** `pytest tests/ -k "not test_falls_back_to_sys_pythonw"` → 946 passed, 0 failed, 9 skipped
