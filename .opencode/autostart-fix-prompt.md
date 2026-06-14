You are fixing the autostart-on-login feature in the existing Voice Typer codebase at `C:\Users\11\tools\persistent-voice-typing`.

This is not a greenfield project. Extend what exists. Do not rewrite unrelated architecture.

==================================================
PERSISTENT GOAL MODE
==================================================

Single active goal: Fix autostart-on-login so the app launches reliably at Windows login in Electron mode, without conflicts between the Python tray app and Electron's Python backend.

Work only inside `C:\Users\11\tools\persistent-voice-typing`. Read every required file before editing. Execute end to end — do not stop at a plan. If a command fails, diagnose, fix, and rerun.

==================================================
PROJECT CONTEXT
==================================================

The app has two execution modes:
- **Standalone mode**: `python -m voice_typer` — tray app with hotkeys, no window UI
- **Electron mode** (used now): `npm run dev` in `voice_typer/client/` — Electron window spawns `venv/Scripts/python.exe -m voice_typer.server.ipc_server --port 9876` with `stdio: "inherit"`, communicating via TCP

Current autostart writes `pythonw.exe -m voice_typer` to `HKCU\...\Run\VoiceTyper`. At login this launches the standalone tray app. When Electron starts manually later, its `killStalePython()` only kills `python.exe` processes (not `pythonw.exe`), so the old process survives, causing mutex conflicts and the "Only one instance" error.

The Python autostart code lives in `platform.py` (`enable_autostart`, `disable_autostart`, `is_autostart_enabled`, `_autostart_command`). It's synced via `_sync_autostart()` in `app.py` at startup and via `_set_autostart()` from the Settings toggle.

Electron currently has NO autostart mechanism — no `app.setLoginItemSettings()`.

==================================================
WHAT TO READ FIRST
==================================================

1. `voice_typer/server/platform.py` lines 103-218 (autostart functions, registry read/write)
2. `voice_typer/server/app.py` lines 637-648 (`_sync_autostart`), 1379-1396 (`_set_autostart`), 1967-2000 (`_another_voice_typer_alive`), 1900-1964 (`_ensure_single_instance`)
3. `voice_typer/client/src/main/index.ts` lines 35-65 (`killStalePython`), 67-73 (`pythonArgs`), 360-406 (`startPython`)
4. `voice_typer/server/ipc_server.py` lines 291-319 (`set_config` handler)
5. `voice_typer/client/src/renderer/src/pages/Settings.tsx` lines 225-244 (autostart toggle)
6. `voice_typer/client/src/renderer/src/types/config.ts` (TypeScript config type)

==================================================
EXACT OUTCOME REQUIRED
==================================================

1. At Windows login, Electron mode starts silently (no flash of console window). The app appears in the system tray.
2. `killStalePython()` kills BOTH `python.exe` AND `pythonw.exe` voice-typer processes before spawning a fresh backend when Electron starts manually.
3. `_another_voice_typer_alive()` checks BOTH `python.exe` AND `pythonw.exe`.
4. The Settings "Start on Login" toggle works correctly — ON writes the correct command, OFF removes it, and it takes effect immediately without restart.
5. The Registry entry at `HKCU\...\Run\VoiceTyper` contains a valid command for Electron dev mode.
6. Running `npm run dev` while the autostart instance is running does NOT crash — it either connects to the existing backend or kills the old one and starts fresh.
7. All existing tests pass. No unrelated code is broken.

==================================================
IMPORTANT BOUNDARIES
==================================================

Do NOT:
- Remove or change `enable_autostart()`/`disable_autostart()`/`is_autostart_enabled()` signatures (used by Settings UI and tray toggle)
- Rewrite the Settings UI or add new config fields
- Touch the prewarm/task_scheduler code (separate feature)
- Use Electron's `app.setLoginItemSettings()` — that requires a packaged Electron app, but we're in dev mode
- Add new IPC event types unless absolutely necessary

Do:
- Create a hidden launcher Python script at `voice_typer/server/autostart_launcher.py` that spawns `npm run dev` silently
- Update `_autostart_command()` to point to the launcher
- Fix `killStalePython()` to match both `python.exe` and `pythonw.exe`
- Fix `_another_voice_typer_alive()` to check both process names
- Ensure the set_config IPC handler triggers autostart sync when `autostart` changes
- Ensure `_sync_autostart()` writes the new correct command when called at startup
- Run all existing tests to verify no regressions

==================================================
IMPLEMENTATION PLAN
==================================================

### STEP 1: Create `voice_typer/server/autostart_launcher.py`

A standalone script that launches Electron dev mode hidden:

```python
# autostart_launcher.py
"""Launcher that npm run dev in the client directory, hidden.

Called by the Windows Registry autostart entry at login.
Runs via pythonw.exe so no console window appears.
"""
```

The script should:
- Locate the client directory relative to its own location: `../../client/` from `voice_typer/server/`
- Check if port 9876 is already listening (an existing backend is running) — if so, exit(0) silently (no double launch)
- Use `subprocess.Popen` with `creationflags=subprocess.CREATE_NO_WINDOW` (Windows) or `subprocess.DETACHED_PROCESS`
- Run `npm run dev` in the client directory with `shell=True`
- Redirect stdout/stderr to `subprocess.DEVNULL` (log to `~/.voice-typer/voice-typer.log` via the Python backend's logging)
- Write its own PID to `~/.voice-typer/autostart.pid` so Electron's `killStalePython()` can find it
- Handle `FileNotFoundError` for `npm.cmd` gracefully (log to stderr)

Use this exact path for the client directory:
```python
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # voice_typer/server -> voice_typer -> root
CLIENT_DIR = BASE_DIR / "client"
```

Check port 9876 by attempting a socket connection:
```python
def _is_port_open(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0
```

### STEP 2: Update `_autostart_command()` in `platform.py`

Change the return value from `pythonw.exe -m voice_typer` to `pythonw.exe path/to/autostart_launcher.py`:

```python
def _autostart_command() -> str:
    """Build the command the autostart entry should run.
    
    In Electron mode, runs the autostart_launcher script which starts
    npm run dev hidden. Falls back to -m voice_typer for standalone mode.
    """
    if sys.platform == "win32":
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        if pythonw.exists():
            launcher = Path(__file__).resolve().parent / "autostart_launcher.py"
            return f'"{pythonw}" "{launcher}"'
        return f'"{sys.executable}" -m voice_typer'
    return f'"{sys.executable}" -m voice_typer'
```

### STEP 3: Fix `killStalePython()` in `index.ts`

Currently scans only `python.exe`. Add a second scan for `pythonw.exe`:

Before (around line 39):
```typescript
const tasklist = require("child_process").execSync(
  'tasklist /FI "IMAGENAME eq python.exe" /FO CSV /NH',
  { encoding: "utf8", timeout: 5000, windowsHide: true },
) as string;
```

Change to also search for `pythonw.exe`. Simplest approach: run `tasklist` twice (once for each image name) and merge results. Or use a single `tasklist` without the image filter and filter in JS. Use whichever is cleaner.

Also read the PID from `~/.voice-typer/autostart.pid` (if it exists) and skip killing that PID if it's the autostart launcher that started the current session (to avoid kill loops). Actually, simplicity is better — just kill all matching processes.

### STEP 4: Fix `_another_voice_typer_alive()` in `app.py`

Change the WMIC query at line 1977 to search for both Python executables:
```python
# Instead of: Name="python.exe"
# Use: Name="python.exe" or Name="pythonw.exe"
```
On Windows, WMIC doesn't support `OR` natively in the where clause. Instead, run two separate `check_output` calls (one for python.exe, one for pythonw.exe) and merge the results, or modify the regex matching to also find pythonw.exe.

### STEP 5: Wire the autostart toggle

In `ipc_server.py` `set_config` handler, add explicit autostart sync after the existing generic loop:

```python
if isinstance(data, dict):
    for k, v in data.items():
        if hasattr(self.app.config, k):
            setattr(self.app.config, k, v)
    self.app.config.save()
    # Sync autostart immediately so registry is updated live.
    if "autostart" in data:
        try:
            self.app._sync_autostart()
        except Exception as e:
            log.warning("[IPC] autostart sync failed: %s", e)
```

This ensures toggling "Start on Login" in Settings writes the correct registry entry immediately.

### STEP 6: Verify `_sync_autostart()` at startup

Read `_do_startup()` Step 1 (around line 510) to confirm `_sync_autostart()` is still called. It should be — the AI's previous changes moved Step 4 to background but didn't touch Step 1. If it's still there, no change needed. If missing, add it back.

### STEP 7: Run tests and verify

Run all existing tests:
```
cd C:\Users\11\tools\persistent-voice-typing
C:\Users\11\.voice-typer\venv\Scripts\python.exe -m pytest tests/ -x -q --no-header -p no:cacheprovider
```

All tests must pass. If any fail, fix them.

==================================================
TESTING / VERIFICATION
==================================================

Run:
- `python -m pytest tests/ -x -q --no-header -p no:cacheprovider`

After tests pass, verify manually:
1. Check that `enable_autostart()` writes the new autostart_launcher command (not `-m voice_typer`)
2. Verify `_autostart_command()` returns the correct launcher path
3. Verify `killStalePython()` logic handles both python.exe and pythonw.exe (by unit testing or code review)
4. Verify `_another_voice_typer_alive()` handles both process names

==================================================
SELF-REVIEW IMPROVEMENT LOOP
==================================================

After implementing, run this loop:
1. Does the implementation satisfy the requested behavior?
2. Does it fit the existing architecture (extending, not rewriting)?
3. Are there hidden risks: race conditions, orphan processes, registry corruption?
4. If any issue found, fix and rerun tests.

==================================================
STRICT COMPLETION AUDIT
==================================================

In your final response, include:
- Objective restated
- Each requirement marked Complete/Partial/Blocked with evidence
- Test results (all passing)
- Any remaining limitations
