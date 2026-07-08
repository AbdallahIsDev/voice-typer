# Voice Typer — Prewarm & Autostart Architecture

**Repository:** `https://github.com/AbdallahIsDev/voice-typer`
**Document Type:** Architecture Specification for Implementation
**Date:** 2026-07-08

---

## Current Architecture Analysis

### Prewarm Pipeline

The prewarm module (`voice_typer/server/prewarm.py`) is a standalone Python script that reads ~7 GB of files into the OS standby cache (free RAM repurposed as a disk cache).

**Pipeline stages:**

```
┌─────────────────────────────────────────────────────────────────┐
│  prewarm.py::run()                                              │
│                                                                 │
│  1. _setup_logging()           → configure rotating file log    │
│  2. _fast_startup_enabled()    → always True (no toggle)        │
│  3. _free_ram_mb()             → check free RAM ≥ 6144 MB       │
│  4. _already_warmed()          → check boot-time sentinel       │
│  5. _lower_io_priority()       → PROCESS_MODE_BACKGROUND_BEGIN  │
│  6. _warm_imports()            → import torch + transformers    │
│  7. _active_model_cache_dirs() → find HF cache dirs to warm     │
│  8. _warm_file() per file      → sequential 4 MB chunked reads  │
│  9. _mark_warmed()             → write boot timestamp sentinel  │
│                                                                 │
│  Exit codes: 0=OK, 10=disabled, 20=low_ram, 30=no_model,        │
│              40=import_failed                                   │
└─────────────────────────────────────────────────────────────────┘
```

> **Plain English:** The prewarm script reads the big AI model files from disk into RAM so the app can read them fast later. It checks if there's enough free RAM, lowers its own priority so it doesn't slow down your real work, then reads ~7 GB of files into the OS file cache.

**File I/O breakdown:**

| Component | Size | Cold Read Time | Warm Read Time |
|-----------|------|----------------|-----------------|
| torch + transformers DLLs/PYDs | ~4.5 GB | ~45s (first import) | ~1s (cache hit) |
| Parakeet `model.safetensors` | 2.4 GB | ~5s (sequential read) | <1s (cache hit) |
| faster_whisper (ctranslate2) | ~200 MB | ~3s | <0.5s |
| **Total** | **~7 GB** | **~50s** | **~2s** |

### Scheduling Layer

The prewarm script is triggered by Windows Task Scheduler via `task_scheduler.py`:

| Trigger | Delay | Mechanism | Notes |
|---------|-------|-----------|-------|
| `LogonTrigger` | `PT0S` (0s) | Task Scheduler XML | Primary trigger, fires at every user logon |
| HKCU Run key | `--delay 0` | Registry fallback | Used when schtasks fails (locked task) |

**Task XML structure** (`_build_task_xml`):
```xml
<Task version="1.4">
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT0S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <Hidden>true</Hidden>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
  </Settings>
  <Actions>
    <Exec>
      <Command>pythonw.exe</Command>
      <Arguments>-m voice_typer.server.prewarm</Arguments>
    </Exec>
  </Actions>
</Task>
```

> **Plain English:** Windows has a built-in scheduler. When you log in, it launches the prewarm script immediately using `pythonw.exe` (Python without a console window, so nothing flashes on screen).

### Application Autostart

The main app (`autostart_launcher.py`) is triggered separately via the HKCU `Run` registry key:

| Component | Trigger | Delay | Command |
|-----------|---------|-------|---------|
| App launcher | HKCU Run key | `--delay 30` | `pythonw.exe autostart_launcher.py --hidden --delay 30` |
| App launcher (fallback) | Task Scheduler | LogonTrigger | Same command, no `--delay` |

The `--delay 30` flag makes the launcher sleep 30 seconds before spawning Electron, giving prewarm a head start on warming the cache.

> **Plain English:** Your actual app also starts when you log in, but it waits 30 seconds before showing up. This wait is a heuristic — it gives the prewarm script time to load the big files into RAM first. Without this wait, the app would try to load files from disk at the same time as prewarm, and they'd fight over the disk.

### Model Loading (GPU Path)

When the app starts, `parakeet_engine.py::load()` calls:

```python
self._model = self._AutoModelForTDT.from_pretrained(
    _PARAKERT_MODEL_ID,
    dtype=self._torch.float16 if effective_device == "cuda" else self._torch.float32,
    device_map=effective_device,  # "cuda" or "cpu"
    low_cpu_mem_usage=True,
    local_files_only=True,
)
```

> **Plain English:** When the app runs, it loads the AI model from the cached files (fast because prewarm put them in RAM) and moves it to the GPU for fast transcription.

### The Timing Problem

```
T=0s    User logs in
        ├─ Task Scheduler fires prewarm (pythonw.exe)
        ├─ HKCU Run key fires autostart_launcher (--delay 30)
        │
T=0-2s  prewarm: Python interpreter cold start + config load
T=2-47s prewarm: import torch + transformers (~45s cold)
T=47-50s prewarm: read model.safetensors (2.4 GB, ~5s cold)
T=50s   prewarm: writes sentinel, exits
        │
T=30s   autostart_launcher: --delay 30 expires, spawns Electron
T=30-32s Electron: cold start, loads main bundle
T=32-35s Electron: spawns Python IPC server
T=35-37s Python: cold import of app modules
T=37-40s Python: model load from cache (~2s warm) → GPU transfer
T=40s   App ready
```

> **Plain English:** At login, both prewarm and the app launcher start. Prewarm spends ~50 seconds reading files into RAM. The app launcher waits 30 seconds, then starts. By the time the app loads the model (~second 37), prewarm has already put the files in RAM, so loading is fast (~3 seconds). Total: ~40 seconds from login to ready.

**The core inefficiency:** Prewarm runs **after logon**, but the user is present and waiting. If prewarm ran **at boot** (before logon), it would complete while the user types their password, and the app would start instantly at logon.

> **Plain English:** Prewarm starts when you log in, but you're sitting there waiting. If it started when the computer boots (before you type your password), it would finish while you're still logging in.

---

## Issue 1: Prewarm Trigger Timing (Boot, Cross-Platform)

### Problem

The task XML uses `<LogonTrigger>`, which fires only when a user logs in. This means prewarm cannot start before the user types their password. The 30-second app delay exists to compensate, but it's fragile — if the user logs in quickly (5 seconds to type a password), prewarm won't have finished.

### Solution: Dual Trigger + Robust Path Resolution

Register BOTH a boot trigger and a logon trigger on every platform. The boot trigger fires at system startup (before any user logs in); the logon trigger covers Windows Fast Startup (which skips the boot trigger). The existing boot-time sentinel prevents double execution.

**Cross-platform trigger mapping:**

| Platform | Boot Trigger | Logon/Fast-Startup Trigger | Sentinel Mechanism |
|----------|-------------|---------------------------|-------------------|
| Windows | `<BootTrigger>` in Task Scheduler XML | `<LogonTrigger>` in Task Scheduler XML | `~/.voice-typer/.prewarm-sentinel` (boot timestamp) |
| macOS | LaunchAgent with `RunAtLoad=true` fires at login; for boot, use a system LaunchDaemon with `StartOnMount` or a `systemd`-equivalent | LaunchAgent `RunAtLoad=true` (covers all logins) | Same sentinel file |
| Linux | systemd user timer with `OnBootSec=10s` | systemd user timer with `OnUnitActiveSec` is NOT used; instead, the `OnBootSec` timer fires once after boot | Same sentinel file |

> **Plain English:** Use two triggers on every operating system: one that fires when the computer starts, and one that fires when the user logs in. The first handles real boots; the second handles Windows' "fast shutdown" feature. A sentinel file prevents running twice.

**Windows XML implementation:**

```python
# task_scheduler.py::_build_task_xml()

triggers = ET.SubElement(root, "Triggers")

# Trigger 1: Boot — fires at system boot (cold boot + restart)
boot = ET.SubElement(triggers, "BootTrigger")
ET.SubElement(boot, "Enabled").text = "true"

# Trigger 2: Logon — fires at user logon (covers Fast Startup)
logon = ET.SubElement(triggers, "LogonTrigger")
ET.SubElement(logon, "Enabled").text = "true"
ET.SubElement(logon, "Delay").text = "PT0S"
```

**Robust HF cache path resolution (handles pre-session execution):**

At boot time, the user session may not be fully initialized. `Path.home()` relies on `%USERPROFILE%` (Windows) / `$HOME` (POSIX), which may not be set. Fall back to platform-specific resolution.

```python
# prewarm.py

def _resolve_hf_cache_dir() -> Path:
    """Resolve the HF cache directory, robust to pre-session execution.

    At BootTrigger time, the user session may not be fully initialized.
    Path.home() relies on environment variables that may not be set yet.
    Fall back to platform-specific resolution.
    """
    # Fast path: environment variables are set (LogonTrigger, normal session)
    if is_windows():
        home = os.environ.get("USERPROFILE") or str(Path.home())
    else:
        home = os.environ.get("HOME") or str(Path.home())
    cache = Path(home) / ".voice-typer" / "huggingface"
    if cache.exists():
        return cache

    # Fallback: Windows registry (needed when BootTrigger fires before session init)
    if is_windows():
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Volatile Environment",
                0, winreg.KEY_READ,
            )
            profile = winreg.QueryValueEx(key, "USERPROFILE")[0]
            winreg.CloseKey(key)
            return Path(profile) / ".voice-typer" / "huggingface"
        except OSError:
            pass

    # Fallback: POSIX getpwuid (needed when LaunchDaemon fires before session init)
    if is_linux() or is_macos():
        try:
            import pwd
            import os as _os
            pw = pwd.getpwuid(_os.getuid())
            return Path(pw.pw_dir) / ".voice-typer" / "huggingface"
        except (KeyError, ImportError):
            pass

    return cache  # best-effort fallback
```

> **Plain English:** At boot time, the script might not know which user's files to read because no user is logged in yet. On Windows, we look up the user's home folder from the registry. On macOS/Linux, we look it up from the system password database. This makes the script work even when it runs before anyone logs in.

---

## Issue 2: The Idle/Session Re-Fire Bug

### Problem

The user reports that even after the sentinel fix, prewarm still re-fires during idle periods:

```
13:48:49  [PREWARM] complete (20.4s)           ← first run at logon
14:14:51  [PREWARM] free RAM 5705 MB < 6144 MB budget — skipping  ← 26 min later
```

### Root Cause

The sentinel check (`_already_warmed()`) prevents re-runs within the same boot session. But the re-fire at 14:14:51 is NOT a sentinel miss — it's a **NEW trigger firing**. `LogonTrigger` with `Hidden=true` re-fires when the user unlocks the screen after the display turns off (a known Windows quirk).

Additionally, the current code checks RAM **before** the sentinel, so the log shows the RAM message instead of the sentinel message — even though the sentinel would have caught it.

```python
# prewarm.py::run() — current order (WRONG)
if not force:
    free = _free_ram_mb()
    if free is not None and free < min_ram_mb:     # ← RAM check FIRST
        return EXIT_LOW_RAM                         # ← exits here, shows RAM message
if not force and _already_warmed():                # ← sentinel SECOND (never reached)
    return EXIT_OK
```

> **Plain English:** The sentinel is actually working, but the log shows the wrong message because the RAM check happens first. The real problem is that Windows re-fires the "logon" trigger when you unlock your screen — so prewarm starts a new Python process, checks RAM, and exits. It's wasteful even though it doesn't re-read the files.

### Solution: Event-Based Trigger + Reordered Checks

Replace `<LogonTrigger>` with an event-based trigger that fires ONLY on the actual boot event (Windows Event ID 12: "OS started"), never on screen unlock. Then reorder the checks so the sentinel runs first (cheapest check, correct log message).

**Windows event trigger XML:**

```xml
<Triggers>
  <!-- Event-based: fires on Event ID 12 (OS started) from Microsoft-Windows-Kernel-General -->
  <!-- This fires once per boot, NEVER on screen unlock or session reconnect. -->
  <EventTrigger>
    <Enabled>true</Enabled>
    <Subscription>
      &lt;QueryList&gt;
        &lt;Query Id="0" Path="System"&gt;
          &lt;Select Path="System"&gt;
            *[System[Provider[@Name='Microsoft-Windows-Kernel-General']
            and (EventID=12)]]
          &lt;/Select&gt;
        &lt;/Query&gt;
      &lt;/QueryList&gt;
    </Subscription>
  </EventTrigger>
</Triggers>
```

**Cross-platform equivalent:**
- **Windows:** EventTrigger (Event ID 12) — fires once per boot, never on unlock.
- **macOS:** LaunchAgent with `RunAtLoad=true` already fires only at login. To make it boot-only, use a system LaunchDaemon with `StartOnMount=false` and a `WatchPaths` on `/var/run/system_boot_complete`. However, since macOS doesn't have the Windows unlock quirk, the existing LaunchAgent is sufficient.
- **Linux:** systemd user timer with `OnBootSec=10s` already fires once per boot. No unlock quirk exists on Linux.

> **Plain English:** On Windows, use a different trigger that fires once when the operating system starts — this event never fires on screen unlock. On macOS and Linux, the existing triggers already work correctly because those systems don't have the unlock quirk.

**Reordered checks (sentinel before RAM):**

```python
# prewarm.py::run() — corrected order
def run(min_ram_mb, force=False, delay=0.0) -> int:
    _setup_logging()
    if delay > 0:
        time.sleep(delay)
    log.info("[PREWARM] starting (force=%s, min_ram_mb=%d)", force, min_ram_mb)
    t_start = time.perf_counter()

    if not force and not _fast_startup_enabled():
        return EXIT_DISABLED

    # SENTINEL FIRST — cheapest check, prevents all redundant work.
    # This also produces the correct log message when the trigger
    # re-fires (e.g., on Windows session unlock).
    if not force and _already_warmed():
        log.info("[PREWARM] already ran this boot session — skipping")
        return EXIT_OK

    # RAM GUARD SECOND — only check if we're actually going to run.
    if not force:
        free = _free_ram_mb()
        if free is not None and free < min_ram_mb:
            log.info(
                "[PREWARM] free RAM %d MB < %d MB budget — skipping to avoid "
                "evicting the user's working set", free, min_ram_mb,
            )
            return EXIT_LOW_RAM

    _lower_io_priority()
    # ... rest of pipeline (imports, file warming, mark_warmed)
```

> **Plain English:** First check "did I already run?" (cheap — just reads a small file). Only if not, check "is there enough RAM?" This way, if the trigger fires again, the script exits in milliseconds with the correct "already ran" message, without starting the RAM check.

---

## Issue 3: Cache State Visibility

### Problem

After prewarm completes, there is no way to know whether the cached data is still in RAM (the OS may have evicted it under memory pressure), what percentage remains cached, or when it was last refreshed. The user measured model file read speed at 1884 MB/s (partial cache) vs 501 MB/s (cold disk) but had no way to see this from the app.

### Solution: Random 4K Page Probe + IPC Endpoint + UI Indicator

Read small random 4K pages from the model file and measure latency. Pages in the OS standby cache return in <10 μs; pages on disk return in >100 μs. Sample 20 pages to estimate cache percentage. Expose this via a new IPC endpoint and display it in the Settings > About page.

**Cache ratio probe:**

```python
import os, time, random

def _cache_ratio(path: Path, samples: int = 20) -> float:
    """Estimate what fraction of `path` is in the OS standby cache.

    Returns 0.0 (cold) to 1.0 (fully cached).

    Reads `samples` random 4K pages and measures latency:
    - <50μs → page is in OS standby cache (RAM)
    - >50μs → page is on disk (cache miss)

    The slight cache-warming side effect (reading a cold page pulls it
    into cache) is acceptable and actually beneficial — it re-warms
    evicted pages.
    """
    size = path.stat().st_size
    if size == 0:
        return 0.0

    hot = 0
    with open(path, "rb") as f:
        for _ in range(samples):
            offset = random.randint(0, size - 4096)
            f.seek(offset)
            t0 = time.perf_counter_ns()
            f.read(4096)
            elapsed_us = (time.perf_counter_ns() - t0) / 1000
            # 50μs threshold: SSD cold read ~100-500μs, RAM cache hit <10μs
            if elapsed_us < 50:
                hot += 1
    return hot / samples
```

> **Plain English:** To check how much of the file is still in RAM, read 20 tiny pieces (4 KB each) from random spots in the file and time each read. If a read takes less than 50 microseconds, that piece is in RAM (fast). If it takes longer, it's on disk (slow). Count how many out of 20 were fast — that's your cache percentage.

**IPC endpoint:**

```python
# New handler: get_prewarm_status
def _handle_get_prewarm_status(app, data):
    sentinel = Path.home() / ".voice-typer" / ".prewarm-sentinel"
    if not sentinel.exists():
        return {"last_run": None, "elapsed_s": None, "cache_ratio": 0.0,
                "cache_label": "unknown"}

    # Read sentinel: "boot_timestamp\nelapsed_seconds"
    content = sentinel.read_text().strip().split("\n")
    boot_ts = int(content[0])
    elapsed = float(content[1]) if len(content) > 1 else None

    # Probe cache ratio for the active model
    cache_ratio = 0.0
    active_dirs = _active_model_cache_dirs()
    if active_dirs:
        ratios = []
        for d in active_dirs:
            for snapshot in (d / "snapshots").iterdir():
                weights = snapshot / "model.safetensors"
                if weights.exists():
                    ratios.append(_cache_ratio(weights))
        if ratios:
            cache_ratio = sum(ratios) / len(ratios)

    label = ("hot" if cache_ratio >= 0.9
             else "partial" if cache_ratio >= 0.1
             else "cold")

    return {
        "last_run": datetime.fromtimestamp(boot_ts).isoformat(),
        "elapsed_s": elapsed,
        "cache_ratio": round(cache_ratio, 2),
        "cache_label": label,
    }
```

**UI: Settings > About page — cache status card:**

| Field | Example | Description |
|-------|---------|-------------|
| Prewarm status | "Hot" (green) / "Partial" (yellow) / "Cold" (red) | Based on cache_ratio |
| Last run | "3 hours ago" | From sentinel timestamp |
| Cache health | "73% cached (1.7 GB of 2.4 GB in RAM)" | cache_ratio × file size |
| Elapsed | "20.4s" | Time prewarm took (from sentinel) |

> **Plain English:** Add a new command that the app's settings page can call to get the cache status. Display it as "Hot" (green, ≥90% cached), "Partial" (yellow, 10-90%), or "Cold" (red, <10%) with a percentage and the last run time.

**Sentinel update (store elapsed time):**

```python
# prewarm.py::_mark_warmed() — updated to store elapsed time

def _mark_warmed(elapsed_s: float) -> None:
    """Record successful prewarm for this boot session.

    Stores boot timestamp AND elapsed seconds so the UI can show
    "Last run: 20.4s" without re-probing.
    """
    try:
        bt = _boot_time()
        if bt is None:
            return
        _PREWARM_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
        _PREWARM_SENTINEL.write_text(f"{bt}\n{elapsed_s:.1f}")
    except Exception:
        pass
```

---

## Issue 4: Autostart Architecture (App Delay Coordination)

### Problem

The app launcher currently uses a hardcoded `--delay 30` to give prewarm a head start. This has two flaws:

1. **If the user logs in quickly (5 seconds to type a password), prewarm (which takes ~50 seconds cold) won't have finished by the time the app starts.** The app will then try to load the model from disk while prewarm is still reading those same files into RAM — they fight over the disk, and both slow down.

2. **If prewarm finishes early (e.g., warm cache, ~20 seconds), the 30-second delay is wasted time.**

### Verification: No Coordination Exists

Investigation of the codebase confirms there is **NO coordination** between the app and the prewarm process:

- `autostart_launcher.py` only checks if the IPC port (9876) is open — it does NOT check if prewarm is running or has completed.
- `app.py::_do_startup()` calls `_sync_prewarm_task()` which only registers the scheduled task — it does NOT wait for prewarm to finish.
- `model_manager.py::try_load()` calls `from_pretrained()` immediately — it does NOT check prewarm status.
- The `_PREWARM_SENTINEL` file is only read by `prewarm.py` itself, never by the app.

The `--delay 30` is the ONLY "coordination," and it's a fragile heuristic.

> **Plain English:** I checked the code — the app has NO way to know if prewarm is running or done. The 30-second wait is just a guess. If you log in fast, prewarm is still running when the app starts, and they both try to read the same files at the same time, fighting over the disk. This is a real problem.

### Solution: Reduce Delay to 15s + App Waits for Prewarm Completion

Reduce the hardcoded delay from 30s to 15s (a reasonable middle ground), AND add explicit coordination: the app's model loader checks if prewarm is running and waits for it to finish before loading the model.

**Part 1: Reduce delay to 15 seconds**

```python
# server_platform.py::_autostart_command()
# Change --delay 30 to --delay 15

def _autostart_command() -> str:
    launcher = Path(__file__).resolve().parent / "autostart_launcher.py"
    if sys.platform == "win32":
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        python_bin = str(pythonw) if pythonw.exists() else sys.executable
        # ISSUE-4: reduced from 30s to 15s. Combined with the prewarm-wait
        # logic in model_manager.py, this gives prewarm a head start
        # without wasting 15s when prewarm finishes early.
        args = [python_bin, str(launcher), "--hidden", "--delay", "15"]
    else:
        args = [sys.executable, str(launcher), "--hidden", "--delay", "15"]
    # ... rest unchanged
```

> **Plain English:** Cut the wait from 30 seconds to 15 seconds. This is a middle ground — short enough that you don't wait too long if prewarm finishes early, long enough that prewarm gets a head start if you log in fast.

**Part 2: App waits for prewarm before loading the model**

Add a `_wait_for_prewarm()` function that checks if prewarm is running. If it is, wait for it to finish (with a timeout). If prewarm already finished, proceed immediately. If prewarm never ran (no sentinel, no running process), proceed with cold load.

```python
# prewarm.py — new public functions

def is_prewarm_running() -> bool:
    """Return True if a prewarm process is currently running.

    Checks for a prewarm PID file written by the prewarm process at
    startup. If the PID file exists and the process is alive, prewarm
    is running.
    """
    pid_file = Path.home() / ".voice-typer" / ".prewarm.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        # Check if the process is alive (cross-platform)
        if is_windows():
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                exit_code = ctypes.c_ulong()
                kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                kernel32.CloseHandle(handle)
                return exit_code.value == STILL_ACTIVE
            return False
        else:
            # POSIX: os.kill(pid, 0) raises OSError if the process is dead
            import os as _os
            try:
                _os.kill(pid, 0)
                return True
            except OSError:
                return False
    except (ValueError, OSError):
        return False


def wait_for_prewarm(timeout_s: float = 60.0) -> bool:
    """Wait for prewarm to finish if it's running.

    Returns True if prewarm completed (or wasn't running), False if
    the timeout was reached. Polls every 500ms.
    """
    if not is_prewarm_running():
        return True  # nothing to wait for

    log.info("[PREWARM] waiting for prewarm to finish (timeout=%.0fs)", timeout_s)
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if not is_prewarm_running():
            log.info("[PREWARM] prewarm finished — proceeding with warm cache")
            return True
        time.sleep(0.5)

    log.warning("[PREWARM] prewarm still running after %.0fs — proceeding anyway", timeout_s)
    return False
```

```python
# prewarm.py — write PID file at startup, remove on exit

def _write_pid_file() -> None:
    """Write the current PID to the prewarm PID file."""
    try:
        pid_file = Path.home() / ".voice-typer" / ".prewarm.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()))
    except OSError:
        pass

def _remove_pid_file() -> None:
    """Remove the prewarm PID file on exit."""
    try:
        pid_file = Path.home() / ".voice-typer" / ".prewarm.pid"
        pid_file.unlink(missing_ok=True)
    except OSError:
        pass

# In run():
def run(min_ram_mb, force=False, delay=0.0) -> int:
    _setup_logging()
    # ... checks ...
    _write_pid_file()
    try:
        # ... prewarm pipeline ...
        _mark_warmed(elapsed)
        return EXIT_OK
    finally:
        _remove_pid_file()
```

```python
# model_manager.py::try_load() — wait for prewarm before loading

def try_load(self, notify_on_failure: bool = False) -> None:
    self._model_load_attempted = True
    try:
        # ISSUE-4: wait for prewarm to finish before loading the model.
        # This prevents the app and prewarm from fighting over disk I/O
        # when the user logs in quickly (before prewarm completes).
        from voice_typer.server.prewarm import wait_for_prewarm
        wait_for_prewarm(timeout_s=60.0)

        log.info(
            "[MODEL] Loading model (backend=%s, size=%s, device=%s)...",
            self._app.config.asr_backend,
            self._app.config.model_size,
            self._app.config.device,
        )
        # ... rest of try_load unchanged ...
```

> **Plain English:** The app's model loader now asks: "Is prewarm still running?" If yes, it waits for prewarm to finish (up to 60 seconds), then loads the model from the warm cache. If prewarm already finished, it loads immediately. If prewarm never ran, it loads from disk as before. This eliminates the disk fight.

**Decision flow:**

```
App starts model load
    │
    ├─ Is prewarm running? (check PID file + process alive)
    │   ├─ YES → wait for prewarm to finish (poll every 500ms, timeout 60s)
    │   │        → prewarm finished? → load model from warm cache (~3s)
    │   │        → timeout reached? → load model anyway (cold, ~50s)
    │   │
    │   ├─ NO, but sentinel exists → prewarm already finished this boot
    │   │   → load model from warm cache (~3s)
    │   │
    │   └─ NO, no sentinel → prewarm never ran this boot
    │       → load model from disk (cold, ~50s)
    │       → (optionally: kick off prewarm in the background for next time)
```

> **Plain English (decision flow):** When the app loads the model, it checks: "Is prewarm running right now?" If yes, wait for it. If prewarm already finished (sentinel file exists), load from RAM. If prewarm never ran, load from disk the slow way.

---

## Future Work: Rust Startup Scripts (Do NOT Implement Now)

**Status: Documented for future consideration. Do NOT implement in the current phase.**

The startup scripts (`autostart_launcher.py` and `prewarm.py`) are pure Python, which means they pay the Python interpreter cold-start cost (~200-500ms) on every launch. Rewriting these two scripts in Rust would eliminate this overhead.

**Expected benefit:** 200-500ms faster startup (the Python interpreter cold-start time).

**Why it's deferred:**
- The 200-500ms saving is small compared to the ~15-50s prewarm time.
- Adding a Rust toolchain to the build process increases CI complexity.
- The scripts are simple (file I/O + process spawn), so the rewrite is low-risk but low-reward relative to the other issues.

**Scope if implemented later:**
- Rewrite `autostart_launcher.py` in Rust (handles port check, process spawn, PID file).
- Rewrite `prewarm.py` in Rust (handles file I/O, priority lowering, sentinel).
- Add Rust toolchain to CI; cross-compile for Windows/macOS/Linux.
- The Rust binary would be a single ~5 MB executable that starts instantly.

**What NOT to rewrite (ever):**
- The transcription engine — it's GPU-bound (the actual computation runs on the GPU via CUDA; Python is just glue calling PyTorch's C++ backend).
- The model loading — it's disk-bound (and prewarm already fixes the disk bottleneck).
- The audio recording — PortAudio is already C.
- The hotkey listener — already uses a native C listener.

> **Plain English (future work):** Someday, the startup scripts could be rewritten in Rust to save 200-500 milliseconds. But this is a small gain compared to the other fixes, so do it later, not now. Never rewrite the transcription engine or model loading in Rust — those are limited by the GPU and disk, not by Python.

---

*End of document.*
