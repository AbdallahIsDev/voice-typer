# Voice Typer — Architecture Completion Prompt

Convert this URL: https://github.com/AbdallahIsDev/voice-typer

You are implementing the **launch & lifecycle architecture** described below in the existing **Voice Typer** codebase — a premium offline background voice-to-text utility for Windows that runs as a tray app with an Electron dashboard and bubble overlay.

This is NOT a greenfield project and NOT a full-codebase rewrite. You are completing a partial architecture implementation that was started but never finished. You must first deeply investigate the current state of every file mentioned in the architecture, then implement what's still missing.

**Source of truth:** The design architecture defined in the block-quoted section below (titled "The Design Architecture"). This is the target topology. Everything else in the repo is the starting point — your job is to make the code match this architecture across every file.

**CRITICAL: The GitHub repo has been updated since the last commit.** You MUST re-clone the repo fresh before starting any work.

==================================================
THE DESIGN ARCHITECTURE
==================================================

The following block-quoted text is the target design architecture produced by a previous AI agent after deep analysis of the codebase. It defines exactly how launch, single-instance, window lifecycle, and process ownership should work. Read it completely, then investigate the current code to determine how much of it is already implemented and what remains.

---

> I've now read the full launch topology. Before recommending anything, let me lay out what your code actually does today — because part of your mental model is slightly off, and that matters for the decision.
>
> **What your code actually does today**
>
> You have three different launch paths, and they behave differently:
>
> | Path | Entry command | What starts | Bubble works? |
> |---|---|---|---|
> | Autostart at login | `pythonw autostart_launcher.py` → `npm run dev` → Electron → spawns Python `--port 9876` | Electron + Python together | ✅ Yes |
> | Desktop/Start Menu shortcut (`create_launcher_shortcut`) | `pythonw -m voice_typer` → `ipc_server.main()` stdin/stdout, no `--port` | Python only, no Electron | ❌ No |
> | Manual `npm run dev` | Electron → spawns Python `--port 9876` | Electron + Python | ✅ Yes |
>
> So two corrections to your mental model:
>
> 1. Your autostart (`autostart_launcher.py`) already launches Electron + Python together — it runs `npm run dev`. It does not launch "only the backend." What you're actually noticing is that at login the Electron dashboard window pops up visibly (because `createMainWindow()` makes it visible on TCP connect), which feels intrusive. The fix isn't "don't start Electron," it's "start Electron hidden."
>
> 2. The thing that launches only the backend is your Desktop/Start Menu shortcut — `create_launcher_shortcut()` writes `pythonw -m voice_typer`, which runs the backend in stdin/stdout mode with no `--port`, so no Electron ever connects, and the bubble can never appear. This is the real inconsistency.
>
> **The single root cause**
>
> You have no single source of truth for "what does launching Voice Typer mean." Each entry point was bolted on separately, so they disagree. Everything else you're frustrated by is a symptom:
>
> - `killStalePython()` doing wmic/taskkill scans on every launch → symptom of no clean single-instance coordination (and wmic is deprecated on Win11 — this will break).
> - The mutex error dialog when you double-launch → symptom of second-instance not being activated.
> - Bubble missing when started from shortcut → symptom of the shortcut spawning a backend Electron never meets.
>
> **How premium resident apps actually do this**
>
> Discord, Slack, Teams, Spotify, 1Password, Dropbox — they all converge on the same pattern:
>
> ```
>                     ┌─────────────────────────────────────┐
>    ONE entry point  │   Electron main process            │
>    (autostart AND   │   • requestSingleInstanceLock()    │  ← single-instance gate
>     shortcut AND    │   • spawns Python backend as child │  ← backend lifetime owned here
>     start menu)     │   • owns tray icon                 │
>                     │   • owns the bubble BrowserWindow  │
>                     └─────────────────────────────────────┘
>                               │ second-instance event
>                               ▼
>                     "User launched us again" → focus/show existing window, new process exits immediately
> ```
>
> The key invariants they hold:
>
> 1. **One binary, one entry point.** Autostart, shortcut, and Start Menu all point at the same thing. There is no "backend-only" launcher.
>
> 2. **Electron is the single-instance gate**, via `app.requestSingleInstanceLock()` + the `second-instance` event. No process scanning, no killing. The second launch is told "you're a duplicate" and just signals the real instance to show its window, then exits.
>
> 3. **The backend is a child of Electron.** Electron spawns it, owns its lifetime, restarts it if it crashes. (You already do this — keep it.)
>
> 4. **Close-to-tray.** The X button hides the window (`close` event → `preventDefault()` + `hide()`), removing it from the taskbar. The process + tray icon + backend stay alive. Quit only happens via tray "Quit."
>
> 5. **Start-hidden flag.** Autostart passes `--hidden` (or `--autostart`). Electron creates the window hidden (or lazily, on first open). Manual launch shows it. Same binary, different flag.
>
> 6. **Production build for shipping, not `npm run dev`.** Dev mode boots a Vite server, compiles TS live, opens dev ports. For a background resident app that boots at every login, you want `electron-vite build` + packaged `.exe` (via `electron-builder`). Faster cold start, less RAM.
>
> **My recommendation for your app**
>
> Your Option A is the right call. Option B can't work for your stated goal, because the bubble is an Electron window. If Electron isn't running when the hotkey fires, there's nothing to show the visualizer in. So Electron + backend must always start as one unit — which is exactly Option A's premise. The places where I'd correct/refine your thinking:
>
> 1. **Don't keep a "backend-only" shortcut at all.** Delete the `pythonw -m voice_typer` desktop shortcut. Everything points at the Electron app. This alone eliminates the bubble-missing bug.
>
> 2. **Replace `killStalePython()` with `requestSingleInstanceLock()`.** On `second-instance`, show+focus the existing window (and unhide it if hidden). The Python mutex stays as defense-in-depth but should never fire in normal use. This eliminates the wmic fragility and the "already running" dialog — clicking Start Menu while running just brings your window forward.
>
> 3. **Wire close-to-tray.** `mainWindow.on('close', e => { if (closeToTray) { e.preventDefault(); win.hide(); } })`. Default on, with a setting.
>
> 4. **Add `--hidden` argv for autostart.** When present, `createMainWindow` makes the window with `show: false`; the tray "Open" / Start Menu second-launch shows it.
>
> 5. **Move off `npm run dev` for autostart/prod.** Stage this as a follow-up: first fix the lifecycle architecture (above), then set up `electron-vite build` + `electron-builder` so autostart runs the real `.exe`.
>
> **A couple of things you were right to suspect but should keep as-is:**
>
> - Keeping the backend alive across Electron restarts is not worth it. Let Electron own the backend and restart it together. The model-load is the expensive part; you already mitigate it with the prewarm scheduled task + background loader thread. Decoupling lifetimes (Option B's "backend outlives Electron") adds huge complexity for little gain, and breaks the "one unit" invariant.
>
> - The tray right-click → "Open app" already exists and already focuses the window via `_bring_electron_to_front()`. Good — it just needs to also `show()` the window if it's currently hidden.
>
> **Concrete target topology**
>
> ```
> Login ──► Registry Run: "VoiceTyper.exe --hidden"
>                   │
> Manual ──► Start Menu / Desktop: "VoiceTyper.exe"
>                   │
>                   ▼
>         ┌─────────────────────────────┐
>         │ Electron main (single lock) │
>         │  • --hidden? → window hidden│
>         │  • spawn Python --port 9876 │
>         │  • tray icon                │
>         │  • bubble BrowserWindow     │
>         └─────────────────────────────┘
>                   │ 2nd launch (lock held)
>                   ▼
>         second-instance event → win.show() + focus, new proc exits(0)
> ```
>
> **Keep the tray in Python, not Electron.** Python's pystray tray already has deep integration: state machine (IDLE/RECORDING/TRANSCRIBING), model menu population from config, notifications, and `_bring_electron_to_front()` to focus the Electron window. Moving it to Electron is unnecessary refactor. Just make Electron always run hidden so the bubble overlay works.

---

> Understood — and you're right to push back. Moving the tray to Electron would throw away a working state machine, the config-driven model submenu, and the notification plumbing, all for zero functional gain. The tray stays in pystray in Python. Electron remains a view layer: it owns the bubble BrowserWindow and the dashboard window, nothing more.
>
> Here's the refined design honoring that constraint.
>
> **Architecture (unchanged spawn direction, new invariants)**
>
> ```
>                 ALL entry points (autostart / Desktop / Start Menu)
>                           │  pythonw autostart_launcher.py
>                           ▼
>         ┌──────────────────────────────────────────────┐
>         │ autostart_launcher.py  (universal launcher)   │
>         │  • port 9876 OPEN?  ──► TCP "show_window" ──► │  (instant focus, exit)
>         │  • port CLOSED?     ──► spawn `npm run dev`   │  (fresh start)
>         └──────────────────────────────────────────────┘
>                           │ (fresh start only)
>                           ▼
>         ┌──────────────────────────────────────────────┐
>         │ Electron main  (root process)                 │
>         │  • requestSingleInstanceLock()  ◄── gate      │
>         │  • VT_START_HIDDEN=1 → main window show:false │
>         │  • close → hide (close-to-tray)               │
>         │  • spawn pythonw -m ipc_server --port 9876    │  (unchanged)
>         │  • owns bubble BrowserWindow + dashboard      │
>         └──────────────────────────────────────────────┘
>                           │ TCP 9876
>                           ▼
>         ┌──────────────────────────────────────────────┐
>         │ Python backend  (child of Electron)           │
>         │  • pystray tray  ◄── STAYS HERE               │
>         │  • hotkey, recorder, transcription, state     │
>         │  • pushes bubble_show / show_main_window      │
>         └──────────────────────────────────────────────┘
> ```
>
> Ownership stays exactly as-is: tray, hotkey, state machine, notifications, model menu — all Python. Electron gains only four view-layer things.
>
> **Concrete changes**
>
> | File | Change | Why |
> |---|---|---|
> | `client/src/main/index.ts` | (1) `app.requestSingleInstanceLock()` + `second-instance` → show+focus existing window, new proc quits. (2) Read `VT_START_HIDDEN` → `createMainWindow` with `show:false`. (3) `mainWindow.on('close')` → `preventDefault()` + `hide()`. (4) New IPC handler: on push `show_main_window` → show+focus. | Single-instance gate, hidden autostart, close-to-tray, reliable "open app". |
> | `server/app.py` / `tray.py` | `open_electron_window()` pushes `show_main_window` over TCP instead of Win32 EnumWindows title-guessing (`_bring_electron_to_front` becomes a fallback). | Removes fragile window-title search; works whether window is hidden/minimized/behind. |
> | `server/autostart_launcher.py` | Becomes the universal launcher. If port 9876 is open → send TCP `show_window` request, exit (instant). If closed → spawn `npm run dev` (existing behavior). Autostart sets `VT_START_HIDDEN=1` in spawn env. | One entry point for autostart + Desktop + Start Menu. Fixes "click icon, nothing happens." |
> | `server/platform.py` | `create_launcher_shortcut()` target changes from `pythonw -m voice_typer` (backend-only — the actual bug) → `pythonw autostart_launcher.py`. Also drop a copy in Start Menu Programs dir (not just Desktop) so Start search finds it. | Makes the bubble work from every entry point. Fixes root inconsistency. |
>
> **Behavior after the change**
>
> | User action | Result |
> |---|---|
> | Login (autostart) | Electron starts hidden, tray appears, backend ready, bubble works on hotkey. No intrusive window. |
> | Hotkey while running | Bubble shows. (No change — already works when Electron runs.) |
> | Search "Voice Typer" in Start Menu (already running) | Launcher sees port open → TCP `show_window` → dashboard appears instantly. No second instance. |
> | Search Start Menu (not running) | Launcher spawns Electron fresh → dashboard visible. |
> | Window X button | Hides window + removes from taskbar. Tray + backend + bubble stay alive. |
> | Tray → "Open app" | Python pushes `show_main_window` → dashboard shows. |
> | Tray → "Quit" | Existing `quit_app()` → `before-quit` → `stopPython()`. Full shutdown. |
>
> **What I am NOT touching**
>
> - Tray — stays in Python, zero refactor. ✓ (your constraint)
> - Backend, hotkey, recording, transcription, bubble internals — unchanged.
> - `killStalePython()` — leaving as-is for now (it still works on current Win11). Heads-up: it uses wmic, which Microsoft is removing; once you package to .exe we delete it and rely on `requestSingleInstanceLock()` + the Python mutex. Flagging, not fixing, to keep this change focused.
> - Packaging — deferred per your choice; everything still runs through `npm run dev`.
>
> **One micro-decision**
>
> When autostarted hidden, should the dashboard BrowserWindow be **created-hidden-but-loaded** (React app boots in background → instant when opened; ~150 MB RAM held) or **created lazily** on first open (no RAM until needed; ~1 s load on first open)? Premium resident apps (Discord, Slack) use create-hidden-but-loaded. I recommend the same. If you're RAM-sensitive, lazy is the alternative.
>
> This keeps the tray exactly where it is and makes "Electron always runs hidden" actually true across every launch path.

---

> The Problem TCP "show_window" Solves
> When user clicks Start Menu while app is already running:
>
> 1. `autostart_launcher.py` runs again
> 2. It checks port 9876 → finds it open (Python is running)
> 3. It sends a TCP message "show_window" to Python
> 4. Python forwards it to Electron
> 5. Electron shows the window
>
> That's 3 hops and 2 processes to do one simple thing.
>
> **Why `requestSingleInstanceLock()` is Better**
>
> Electron already has this built in. Here's what happens:
>
> User clicks Start Menu while app is running:
>
> 1. Second Electron starts
> 2. `requestSingleInstanceLock()` returns false (first instance holds the lock)
> 3. Electron emits `second-instance` event on the FIRST instance
> 4. First instance shows its window
> 5. Second instance calls `app.quit()` immediately
>
> That's 0 hops, direct Electron↔Electron communication.
>
> | Aspect | TCP route | `requestSingleInstanceLock` |
> |---|---|---|
> | Protocol | Need new TCP message type, need Python to forward it | Built into Electron, zero code to write |
> | Speed | Python needs to parse + route the message | Direct inter-process signal, instant |
> | Reliability | What if Python is busy transcribing? Message might be delayed | OS-level named pipe, always immediate |
> | Fragility | What if the TCP connection drops for a moment? Message is lost | Can't fail — it's Electron's native mechanism |
> | Complexity | Python needs to understand "show me" UI commands | Electron handles it, Python doesn't care |
>
> **The Even Simpler Version**
>
> Think of it like a radio:
>
> - TCP route: You tell your friend (Python) to tell your other friend (Electron) to open the window. If your friend is busy, the message waits.
> - `requestSingleInstanceLock`: You tap Electron directly on the shoulder. It always hears you instantly.
>
> When the OS already gives you a direct line, don't route through a middleman.

---

## Self-Improving Quality Loop

Apply this loop after every meaningful feature, bug fix, refactor, backend change, test change, documentation change, packaging change, or release step.

Do not treat a task as finished just because the code works once or the build passes.

After each implementation chunk, stop and critically review it. Ask yourself:

1. Did I follow the design architecture above?
2. Did I preserve the existing architecture?
3. Did I avoid creating a parallel system?
4. Did I preserve existing functionality and user data?
5. Is the implementation complete, not only scaffolded?
6. Is it clean, maintainable, and easy to understand?
7. Is it secure by default?
8. Did I follow the best practices for this?
9. Are edge cases and failure states handled?
10. Is the user experience clear for a real user, not only a developer?
11. Is the UI responsive, accessible, and usable in light/dark mode where relevant?
12. Did I avoid unnecessary complexity and overengineering?
13. Did I add or update tests where needed?
14. Am I satisfied with the current results?

If any answer is "no," "not sure," or "not verified," do not move on. Improve the implementation, fix the issue, rerun verification, and repeat this loop.

Before finalizing the chunk, pressure-test it:

- Could this introduce a hidden security risk?
- Could this cause data loss?
- Could this break an existing workflow or persistent state?
- Could this fail on Windows, Linux, macOS, or a clean install?
- Could this confuse a normal user?
- Could this create performance, state-sync, or race-condition problems?

If a real risk is found, fix it and test again. If a risk is valid but not blocking, document it clearly with evidence.

Stop improving only when the chunk is implemented, integrated, tested, manually verified where relevant, and aligned with the project's quality bar.

==================================================
WHAT YOU MUST READ FIRST (Required Reading Order)
==================================================

Read EVERY file in this order before editing anything. Do not skip files because they look unrelated to the architecture.

**Top-level documents:**
1. **ARCHITECTURE_PROMPT.md** (this file) — the design architecture you must implement
2. **README.md** — project overview, architecture, manual verification checklist

**Architecture-relevant source files (read every one completely):**
3. **voice_typer/server/autostart_launcher.py** — universal launcher
4. **voice_typer/client/src/main/index.ts** — Electron main process
5. **voice_typer/server/tray.py** — system tray icon (pystray)
6. **voice_typer/server/platform.py** — autostart adapters + shortcuts
7. **voice_typer/server/app.py** — main orchestrator (check `_ensure_desktop_shortcut`, startup paths)
8. **voice_typer/server/ipc_server.py** — TCP IPC (check `_push_event_now`, message dispatch)

**Supporting files (understand the wider context):**
9. **voice_typer/client/package.json** — Electron scripts and "main" field
10. **voice_typer/client/electron.vite.config.ts** — build configuration
11. **voice_typer/client/tsconfig.node.json** — TypeScript config for main process
12. **voice_typer/__main__.py** — entry point
13. **voice_typer/client/src/main/preload/index.ts** — preload (if it exposes window control IPC)
14. **voice_typer/server/config.py** — config model

**Tests:**
15. **tests/test_platform.py** — shortcut creation tests
16. **tests/test_tray.py** — tray icon tests
17. **tests/test_app.py** — app orchestrator tests
18. **tests/test_server.py** — IPC server tests

**Build / infra:**
19. **scripts/build/** — build configuration files
20. **pyproject.toml** — project metadata + dependencies

==================================================
IMPLEMENTATION APPROACH
==================================================

The design architecture above defines the target. Your job is to:

1. **Investigate** every file in the required reading order.
2. **Compare** each file's current code against the architecture spec and the concrete changes table.
3. **Determine** what's already implemented and what's still missing. Use git diff, git log, and source code inspection to see what exists.
4. **Implement** what's missing. Do not re-implement what already works — complete the partial work.
5. **Test** after every meaningful change.

**Things to look for specifically:**

- Is `requestSingleInstanceLock()` wired in `index.ts`? Does the `second-instance` handler call `showMainWindow()`?
- Is `VT_START_HIDDEN` read in `createMainWindow`? Is `show: false` applied when set?
- Is close-to-tray wired? Does X button call `preventDefault()` + `hide()`?
- Is `app.isQuitting` set correctly in `before-quit`?
- Does `autostart_launcher.py` have both paths: port-open (focus existing) and port-closed (fresh start)?
- Does `create_launcher_shortcut()` point at `autostart_launcher.py` instead of `-m voice_typer`?
- Is there a Start Menu shortcut in addition to Desktop shortcut?
- Does `open_electron_window()` in `tray.py` push `show_window` over TCP as primary path?
- Does `_bring_electron_to_front()` handle hidden windows (SW_SHOW) in addition to minimized (SW_RESTORE)?
- Does `_autostart_command()` pass `--hidden` to the launcher?
- Does `_ensure_desktop_shortcut()` migrate away the legacy backend-only `.bat`?
- Does `handleMessage` in `index.ts` route `show_window` → `showMainWindow()`?
- Is `killStalePython()` appropriate next to `requestSingleInstanceLock()`, or should it be replaced?

**Do NOT assume anything is fully implemented.** Investigate each line in the source code to confirm.

==================================================
IMPORTANT BOUNDARIES
==================================================

Do NOT:
- Restructure or rename the package layout
- Add new dependencies unless absolutely required (document if you do; prefer `>=X,<Y` floating ranges per existing pattern in pyproject.toml)
- Refactor existing architecture patterns (tray stays in Python, backend stays child of Electron, etc.)
- Touch files outside the architecture-relevant set unless a necessary dependency emerges
- Modify `pyproject.toml` UNLESS adding a new dependency or test extra
- Remove existing tests unless they are broken by your changes (update them instead)
- Move the tray to Electron — it stays in pystray in Python (explicit constraint in the architecture)

Do:
- Follow existing code style: no comments in production code (docstrings are OK)
- Use same mocking patterns as existing tests (`MagicMock`, `monkeypatch`, `sys.modules` replacement)
- Keep test classes grouped by feature with clear class names
- Run `python -m pytest tests/ -v` after EACH change before moving to the next
- Add new tests for every new behavior
- If you discover a bug or incomplete implementation in a file not in the architecture list, fix it if it blocks the architecture, otherwise flag it but don't scope-creep

==================================================
TESTING / VERIFICATION
==================================================

Run after EACH fix or feature before moving to the next:
```bash
python -m pytest tests/ -v -x  # stop on first failure
```

Run before final report:
```bash
python -m pytest tests/ -v  # full suite, don't stop on failure
```

Also verify:
- TypeScript compilation: `npx tsc -p tsconfig.node.json --noEmit` (from client directory)
- Electron build: `npx electron-vite build` (from client directory)
- Python syntax: `python -c "import ast; [ast.parse(open(f).read(), f) for f in ['voice_typer/server/autostart_launcher.py', 'voice_typer/server/platform.py', 'voice_typer/server/tray.py', 'voice_typer/server/app.py', 'voice_typer/server/ipc_server.py']]; print('SYNTAX_OK')"`
- Python imports: `python -c "import voice_typer.server.autostart_launcher; import voice_typer.server.platform; import voice_typer.server.tray; print('IMPORT_OK')"`

Expected: all tests pass, all compilations succeed. Count them and report the total.

==================================================
DELIVERABLES
==================================================

You MUST produce TWO deliverables at the end:

**1. Changed files zip** — `build/changes.zip`
   - Create a `build/changes/` directory that mirrors the repo structure.
   - Copy ONLY the files you changed into `build/changes/`, preserving the directory structure.
   - Do NOT include these local-only files:
     - `ARCHITECTURE_PROMPT.md`, `PROMPT.md`, `PROBLEMS.md`, `FEATURES.md`, `CHANGELOG.md`
     - `config.json` (user's local config), `.git/`, `.gitignore`
     - `changes.patch`, `build/` directory itself
   - Zip the `build/changes/` directory into `build/changes.zip`.
   - Copy `build/changes.zip` to the user's Downloads directory.

**2. Final report markdown file** — `build/REPORT.md`
   - Copy to the user's Downloads directory.
   - Concise implementation report with:
     - What you read
     - What files you changed (list every file with summary of changes)
     - How each architectural element works
     - Verification results (test counts, manual checks, TS compilation, builds)
     - Intentional limitations
     - What was already implemented vs what you completed
     - The full behavior table from the architecture spec, with ✅/❌ for each row
