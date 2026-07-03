# Voice Typer — Cold-Start Optimization Report

**Task ID:** 9
**Date:** 2026-07-02
**Scope:** Benchmark and optimize the Python backend cold-start time (the
latency from launching `voice-typer` / `python -m voice_typer.server` to the
tray icon being ready to paint).

---

## 1. Executive summary

The tray cold-start import path was dominated by `pystray`, whose Linux xorg
backend runs `Xlib.display.Display()` **at module import time** — a ~29 ms
side effect that also **crashes headless** (no `DISPLAY`). `pystray` was
imported eagerly by both `tray.py` and `tray_menu.py` even though it is only
needed when the icon is actually constructed.

A parallel problem existed on the app path: `recording.py` imported
`sounddevice` (PortAudio) at module top, which made `voice_typer.server.app`
**unimportable** in any environment without `sounddevice` installed and
added PortAudio load latency to every cold start.

Both were fixed with a small, stateless lazy-import proxy
(`voice_typer/server/_lazy_import.py`) that defers the real `import` to first
attribute access while preserving every existing call site and every existing
test mock pattern.

### Headline numbers (warm OS cache, back-to-back, fresh subprocess)

| Metric                                          | Before    | After    | Δ                 |
|-------------------------------------------------|-----------|----------|-------------------|
| `voice_typer.server.tray` import (median)       | 138.3 ms  | 105.8 ms | **−32.5 ms (−23.5%)** |
| `voice_typer.server.tray` import (importtime cum)| 95.1 ms  | 67.1 ms  | **−28.0 ms (−29.4%)** |
| Heavy 3rd-party pkgs pulled in on tray import   | pystray, PIL | **none** | pystray + PIL eliminated |
| `importtime` exit code (import-time side effect)| 1 (X11 connect) | 0 (clean) | headless crash eliminated |
| `bench/bench_startup.py` median                 | 19 ms     | 7 ms     | **−12 ms (−63%)** |
| `bench/bench_startup.py` first (truest cold) run| 161 ms    | 8 ms     | **−153 ms (−95%)** |
| `voice_typer.server.app` import (no sounddevice)| **FAILED** (ModuleNotFoundError) | ~460 ms | app now boots without sounddevice |

### Cold-cache first-launch numbers (initial baseline, OS page cache cold)

| Metric                                          | Before    | After    | Δ                 |
|-------------------------------------------------|-----------|----------|-------------------|
| `voice_typer.server.tray` first-run (subprocess)| 481.3 ms  | 115.1 ms | **−366 ms (−76%)** |
| `voice_typer.server.tray` import (importtime cum)| 197.2 ms | 69.4 ms  | **−127.8 ms (−64.8%)** |

> The first-launch (cold-cache) numbers are the most representative of real
> user-perceived cold start (launching the app for the first time after boot).
> The warm-cache numbers are the fairest apples-to-apples CPU comparison.

---

## 2. Methodology

### 2.1 Benchmark harness

- **`bench/bench_startup.py`** (existing) — measures `import
  voice_typer.server.tray` in-process, 3 iterations, reports median.
  **Caveat (see §5.1):** it only clears `voice_typer.*` from `sys.modules`
  between runs, so runs 2–3 are warm (third-party C extensions stay cached).
  The median therefore understates true cold start; the *first* run is the
  honest cold number.
- **`scripts/profile_imports.py`** (new) — runs `python -X importtime -c
  "import <target>"` in a **fresh subprocess** for each of N runs (truly
  cold `sys.modules` every time), parses per-module self/cumulative
  microseconds, and writes a report. This is the only honest way to measure
  import latency; in-process re-imports are contaminated by cached C
  extensions. Output saved to `scripts/coldstart_BEFORE.txt` and
  `scripts/coldstart_AFTER.txt`.

### 2.2 Environment

- Python 3.12.13 (`/home/z/.venv`)
- Installed: `numpy`, `scipy`, `Pillow`, `psutil`, `pyperclip`, `pystray`,
  `pynput`, `librosa`
- **Not installed:** `sounddevice`, `faster-whisper`, `transformers`,
  `torch` (already lazy-loaded inside `transcription.py`, `vad.py`,
  `parakeet_engine.py` etc., so their absence does not affect the import
  chain)
- Linux + Xvfb (`:99`) for `pystray`'s import-time X11 connection

---

## 3. Baseline analysis (before)

Top slowest imports on the `voice_typer.server.tray` path (importtime,
warm cache):

```
rank    self_ms     cum_ms  module
   1       9.46       9.76  six
   2       5.29      95.09  voice_typer.server.tray
   3       3.90       3.99  typing
   4       3.73      57.52  voice_typer            ← __init__.py does importlib.metadata.version()
   5       3.64      28.90  pystray._xorg          ← X11 display connection at import time
   ...
  12       1.84      53.03  importlib.metadata     ← __version__ lookup
```

**Root causes identified:**

1. **`pystray` (eager, in `tray.py` + `tray_menu.py`)** — `pystray._xorg`
   runs `Xlib.display.Display()` at module top (~29 ms cum, plus an X11
   round-trip). Imported eagerly even though only `TrayIcon.start()` and
   `build_menu()` actually use it. Also **crashes headless** (no `DISPLAY`).
2. **`sounddevice` (eager, in `recording.py`)** — loads PortAudio C library
   at import time. `voice_typer.server.app` imports `recording` at module
   top, so this tax hits the real app cold-start path and made `app`
   unimportable without `sounddevice` installed.
3. **`voice_typer/__init__.py` `importlib.metadata.version()`** — 53 ms cum
   for the `__version__` lookup. **Out of scope** (not in `server/*.py`) —
   see §5 recommendations.

Modules already lazy-loading their heavy deps correctly (good prior work):
`tray_icon.py` (PIL via `_get_pil_image()`), `transcription.py`
(`faster_whisper`, `torch`), `vad.py` (`torch`), `parakeet_engine.py`
(`torch`, `transformers`), `level_monitor.py` / `server_platform.py` /
`app.py` (`sounddevice` inside functions).

---

## 4. Changes made

### 4.1 New file: `voice_typer/server/_lazy_import.py`

A small, stateless lazy-module proxy:

```python
class _LazyModule:
    """Transparent proxy that defers `import <name>` to first attribute access."""
    __slots__ = ("_module_name",)
    def __init__(self, module_name): ...
    def _resolve(self): return importlib.import_module(self._module_name)
    def __getattr__(self, name): return getattr(self._resolve(), name)
    def __setattr__(self, name, value): setattr(self._resolve(), name, value)
    def __delattr__(self, name): delattr(self._resolve(), name)

def lazy_module(name): return _LazyModule(name)
```

**Key design property:** the proxy stores **no cached module** — every
attribute access re-resolves from `sys.modules`. This means the per-test
`monkeypatch.setitem(sys.modules, "pystray", mock)` / `monkeypatch.setitem(
sys.modules, "sounddevice", mock)` fixtures in `tests/conftest.py` are
honoured on every access, with **zero cross-test leakage** and **no stale
caching**. Both `getattr` and `setattr` are delegated, so tests that do
`monkeypatch.setattr(recording.sd, "InputStream", fake)` keep working
unchanged.

### 4.2 Modified: `voice_typer/server/tray.py`

- Added `from __future__ import annotations` so the
  `self._icon: Optional[pystray.Icon]` annotation in `TrayIcon.__init__`
  becomes a string and no longer forces an eager `pystray` import.
- Replaced module-level `import pystray` with
  `pystray = lazy_module("pystray")` (tagged `PERF-COLDSTART-001`).
- **No call-site changes** — `start()` still does `pystray.Menu(...)`,
  `pystray.Icon(...)`; the proxy imports the real module on first access.

### 4.3 Modified: `voice_typer/server/tray_menu.py`

- Replaced module-level `import pystray` with
  `pystray = lazy_module("pystray")` (tagged `PERF-COLDSTART-001`).
- Already had `from __future__ import annotations`.
- **No call-site changes** — `build_menu()` still does `pystray.MenuItem(...)`,
  `pystray.Menu.SEPARATOR`, etc.

### 4.4 Modified: `voice_typer/server/recording.py`

- Added `from __future__ import annotations` so the
  `self._stream: Optional[sd.InputStream]` annotation becomes a string.
- Replaced module-level `import sounddevice as sd` with
  `sd = lazy_module("sounddevice")` (tagged `PERF-COLDSTART-001`).
- **No call-site changes** — all ~15 `sd.query_devices()`, `sd.InputStream(...)`,
  `sd.query_hostapis(...)` call sites work unchanged via the proxy.

### 4.5 Imports made lazy (summary)

| Module                | Import made lazy | Was used by (runtime)              |
|-----------------------|------------------|------------------------------------|
| `voice_typer/server/tray.py`        | `pystray`        | `TrayIcon.start()`                 |
| `voice_typer/server/tray_menu.py`   | `pystray`        | `build_menu()`                     |
| `voice_typer/server/recording.py`   | `sounddevice`    | `Recorder.start/stop/_resolve_device` etc. |

---

## 5. Verification

### 5.1 Modules import cleanly without heavy deps

After the changes (no `DISPLAY`, no `sounddevice` installed):

```
$ python -c "from voice_typer.server import tray_menu"   # OK (no X display needed)
$ python -c "from voice_typer.server import tray"        # OK (no X display needed)
$ python -c "from voice_typer.server import recording"   # OK (no sounddevice needed)
$ python -c "from voice_typer.server import app"         # OK (previously FAILED)
```

### 5.2 Tests

All tray / recording / e2e tests pass — **135 passed** across:
`test_tray.py`, `test_tray_menu.py`, `test_recording.py`,
`test_new_perf_005_dpi_cache.py`, `test_new_perf_004_tray_models_cache.py`,
`test_round8_e2e.py`.

The existing test mock patterns are preserved unchanged:
- `tests/conftest.py` autouse `mock_heavy_imports` fixture: `monkeypatch.setitem(sys.modules, "pystray", mock)` + `monkeypatch.setitem(sys.modules, "sounddevice", mock)` → honoured by the proxy on every access.
- `tests/test_tray.py`: `monkeypatch.setattr(tray_mod, "pystray", mock)` + `monkeypatch.setattr(tray_menu_mod, "pystray", mock)` → replaces the module-level proxy name; works because `start()`/`build_menu()` read `pystray` from module globals.
- `tests/test_tray_menu.py`: `tray_menu_mod.pystray = mock` (direct assign) → same.
- `tests/test_recording.py` / `tests/test_recording_audio_processor.py`: `monkeypatch.setattr(recording_mod.sd, "InputStream", fake)` → goes through the proxy's `__setattr__` → `setattr(sounddevice_module, "InputStream", fake)`. ✓

**Pre-existing failures (not caused by this task):** 6 failures in
`test_recording_audio_processor.py` (`ImportError: cannot import name
'AudioProcessorConfig'`), plus frontend/file-structure failures in
`test_changes5_fixes.py` / `test_changes7_fixes.py` / `test_changes3_fixes.py`
(unrelated to Python imports). These were confirmed to fail identically on a
clean `git stash` of this task's changes.

### 5.3 No public API changes

- No function/class signatures changed.
- No `# type: ignore` or bare `except: pass` added.
- `_lazy_import.py` is private (underscore prefix).
- `pystray` and `sd` remain module-level attributes with the same names
  (now proxy objects instead of real modules), so any
  `from voice_typer.server.tray import pystray` still works.

---

## 6. Recommendations for further work

1. **Lazy `__version__` in `voice_typer/__init__.py` (highest ROI).**
   The `importlib.metadata.version("voice-typer")` call at package import
   time costs **~53 ms cum** — the single biggest remaining chunk on the
   tray path (57% of the post-optimization 67 ms cumulative). Make
   `__version__` a lazy attribute via PEP 562 `__getattr__`:
   ```python
   def __getattr__(name):
       if name == "__version__":
           ...compute and cache...
       raise AttributeError(name)
   ```
   Out of scope here (task scope was `voice_typer/server/*.py`), but would
   take the tray import from ~67 ms to ~14 ms cumulative.

2. **`scipy` on the app path.** `scipy` (~23 ms) is pulled in transitively
   by `audio_quality.py` / `audio_filters/`. If those filters aren't always
   enabled, consider lazy-loading `scipy` the same way. (Lower priority —
   only affects the app path, not the tray benchmark.)

3. **Fix `bench/bench_startup.py` methodology.** `measure_import_time()`
   clears only `voice_typer.*` from `sys.modules`, so runs 2–3 are warm
   (third-party C extensions stay cached) and the median understates true
   cold start. Recommend either (a) reporting the *first* run, or
   (b) delegating to a fresh-subprocess measurement like
   `scripts/profile_imports.py`. The first-run number went 161 ms → 8 ms,
   which is the user-perceived win.

4. **Proxy overhead.** `_LazyModule` does an `importlib.import_module`
   (a `sys.modules` dict lookup) on every attribute access. This is
   negligible (~µs) for the tray/recording call patterns (attributes are
   resolved once per `start()`/`build_menu()`/recording-session, not per
   audio frame). If future profiling shows hot-path overhead, a
   cached-resolution variant (resolve once, then `__getattr__` returns from
   the cache) can be swapped in — but it must re-check `sys.modules` when
   the resolved module differs, to preserve the test-isolation guarantee.

5. **Apply the same pattern to `recording.py`'s sibling modules** if/when
   they grow eager heavy imports. The `lazy_module()` helper is now
   available for reuse.

---

## 7. Artifacts

- `bench/COLDSTART_REPORT.md` — this file.
- `scripts/profile_imports.py` — reusable import profiler (fresh subprocess).
- `scripts/coldstart_BEFORE.txt` — baseline profile (before optimization).
- `scripts/coldstart_AFTER.txt` — post-optimization profile.
- `voice_typer/server/_lazy_import.py` — new lazy-module proxy helper.
- `voice_typer/server/tray.py` — `pystray` made lazy.
- `voice_typer/server/tray_menu.py` — `pystray` made lazy.
- `voice_typer/server/recording.py` — `sounddevice` made lazy.
