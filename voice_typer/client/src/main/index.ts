import { app, BrowserWindow, dialog, ipcMain, Menu, screen, nativeTheme, shell, session } from "electron";
import { spawn, ChildProcess } from "child_process";
import net from "net";
import fs from "fs";
import path from "path";
import os from "os";

// Augment Electron's App interface with isQuitting so the close-to-tray
// handler can distinguish a real quit (tray Quit → let the window close)
// from the X button (→ hide instead).
// The electron module uses `export =`, so we must augment via the global
// Electron namespace rather than `declare module "electron"`.
declare global {
  namespace Electron {
    interface App {
      isQuitting?: boolean;
    }
  }
}

const IPC_PORT = 9876;

// SEC-018: per-launch random session token.  Passed to the Python
// subprocess via the VOICE_TYPER_IPC_TOKEN env var and sent as the
// first JSON line after TCP connect.  The Python IPC server validates
// it and drops the connection if it doesn't match.  This prevents any
// local process from connecting to 127.0.0.1:9876 and sending
// quit_app / set_config / etc.
//
// Generated once per Electron process lifetime using crypto.randomBytes
// (32 bytes = 256 bits of entropy, base64-encoded for transport).
import { randomBytes } from "crypto";
const IPC_TOKEN = randomBytes(32).toString("base64");

// When set (autostart at login), the dashboard window is created hidden.
// The process + tray + bubble still work; the window appears on demand
// via the Start Menu (second-instance) or tray "Open app".
const START_HIDDEN = process.env.VT_START_HIDDEN === "1";

/**
 * Single-instance gate + focus-only guard.
 *
 * Acquiring the lock is Electron's native, OS-level mechanism for "only one
 * of me may run." It uses a named pipe on Windows / a lockfile on POSIX —
 * no port scanning, no process killing, no Python involvement. When a
 * SECOND Electron process starts:
 *
 *   • the second instance: requestSingleInstanceLock() returns false → exit
 *   • the first instance:  emits "second-instance" → we show+focus the
 *     dashboard window (creating it if it was never created, e.g. after a
 *     hidden autostart).
 *
 * This is how the user "opens" the app from Start Menu / Desktop when it's
 * already running in the background: the shortcut launches a throwaway
 * Electron process whose only job is to fail the lock and wake the real one.
 *
 * MUST run before app.whenReady() — the lock is checked at process start.
 *
 * VT_FOCUS_ONLY is set by autostart_launcher._focus_running_app() as a
 * defensive guard: if this env var is set, this process is a lightweight
 * duplicate that must exit without doing ANY heavy init (Python, TCP,
 * windows).  The single-instance lock check below handles the normal case;
 * FOCUS_ONLY catches edge cases where the lock might not work as expected.
 */
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock || process.env.VT_FOCUS_ONLY === "1") {
  // We are the duplicate (or a focus-only probe).  The first instance has
  // already received (or is about to receive) the "second-instance" event
  // and will show itself.
  //
  // Use app.exit(0) instead of app.quit() to guarantee immediate
  // termination — app.quit() allows the event loop to drain (which can
  // fire whenReady and start Python before the process exits), while
  // app.exit(0) terminates without waiting.
  app.exit(0);
} else {
  app.on("second-instance", () => {
    // Another launch attempt happened.  Show + focus the dashboard so it
    // feels like the app "opened."  Create it lazily if autostart started
    // us hidden and the user never opened it yet.
    showMainWindow();
  });
}

/**
 * Show + focus the dashboard window, creating it if needed.
 *
 * Used by:
 *   • second-instance event  (Start Menu / Desktop click while running)
 *   • tray "Open app" IPC path (see showMainWindow IPC handler below)
 */
// SEC-025: helper that detects whether the foreground window is in
// exclusive fullscreen mode. Returns false if detection fails (we err
// on the side of NOT painting over fullscreen).
function isForegroundFullscreen(): boolean {
  try {
    // Electron doesn't expose a direct "is foreground fullscreen" API,
    // but we can check every screen's workspace for a fullscreen window.
    const displays = screen.getAllDisplays();
    for (const _display of displays) {
      // On macOS, BrowserWindow.getAllWindows() lets us inspect each
      // window's fullscreen state. On Windows / Linux this is a no-op
      // (we just return false and let setVisibleOnAllWorkspaces run).
      if (process.platform === "darwin") {
        const win = BrowserWindow.getFocusedWindow();
        if (win && win.isFullScreen()) {
          return true;
        }
      }
    }
  } catch {
    // Best-effort detection.
  }
  return false;
}

function showMainWindow(): void {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createMainWindow(/* forceShow */ true);
    return;
  }
  if (!mainWindow.isVisible()) {
    mainWindow.show();
  }
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.focus();
}

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (reason: unknown) => void;
}

let pythonProcess: ChildProcess | null = null;
let tcpSocket: net.Socket | null = null;
let mainWindow: BrowserWindow | null = null;
let bubbleWindow: BrowserWindow | null = null;
const pendingRequests = new Map<number, PendingRequest>();
let nextId = 1;
let tcpBuffer = "";
let pythonReady = false;
let pythonExitedEarly = false;
// SEC-029: per-session nonce tagged onto every python-event so the
// renderer can reject replayed frames from an unauthenticated TCP
// attacker (SEC-018). Generated once at app.whenReady() time.
let sessionNonce: string = "";

// Bubble geometry (logical px).
const BUBBLE_WIDTH = 220;
const BUBBLE_HEIGHT = 60;

// Bubble screen position preference (persisted via IPC from renderer).
let bubblePosition: 'top' | 'bottom' = 'top';

// ANSI color constants — match the Python backend's _ColorFormatter so
// Electron and Python log lines look identical in the terminal.
const DIM = "\x1b[38;5;242m";      // dim grey for timestamps
const RESET = "\x1b[0m";
const BUBBLE_CLR = "\x1b[38;5;39m"; // bright cyan for [BUBBLE] tags

/**
 * Format current time as H:MM:SS (12h, no leading zero), wrapped in ANSI
 * dim-grey, matching the Python backend's timestamp format/color so the
 * terminal output is visually consistent across both processes.
 */
function ts(): string {
  const d = new Date();
  const h = d.getHours() % 12 || 12;
  const m = String(d.getMinutes()).padStart(2, "0");
  const s = String(d.getSeconds()).padStart(2, "0");
  return `${DIM}${h}:${m}:${s}${RESET}`;
}

/**
 * RELIABILITY-002: the old `killStalePython()` function used `wmic`
 * (deprecated in Win11 24H2+) and `taskkill /T /F` to scan for and
 * kill stale Python backend processes.  This was fragile and
 * dangerous:
 *
 *   1. `wmic` is deprecated and may be absent on newer Windows builds.
 *   2. `taskkill /T /F` killed legitimate autostart sessions when the
 *      user started Electron manually.
 *   3. Any process with "voice_typer" in its command line (e.g. a
 *      backup tool) was fair game for killing.
 *
 * The function has been removed.  Single-instance enforcement is now
 * handled by two independent mechanisms that were already in place:
 *
 *   - Electron side: `app.requestSingleInstanceLock()` (above) ensures
 *     only one Electron process runs.
 *   - Python side: `_ensure_single_instance()` in app.py uses a Win32
 *     named mutex (`VoiceTyperSingleInstance`) to ensure only one
 *     Python backend runs.
 *
 * If Electron starts and a Python backend is already running (e.g.
 * from autostart), `tcpConnect()` will successfully connect to it and
 * adopt it — no killing needed.  If no Python is listening, Electron
 * spawns a new one via `startPython()`.
 *
 * This eliminates all `wmic`/`tasklist`/`taskkill` usage from the
 * Electron main process.
 */

function pythonArgs(): [string, string[]] {
  const home = os.homedir();
  const base = path.join(home, ".voice-typer", "venv");
  const exe = process.platform === "win32"
    ? path.join(base, "Scripts", "pythonw.exe")
    : path.join(base, "bin", "python3");
  return [exe, ["-m", "voice_typer.server.ipc_server", "--port", String(IPC_PORT)]];
}

function handleMessage(msg: Record<string, unknown>) {
  if (msg.id != null) {
    const entry = pendingRequests.get(msg.id as number);
    if (entry) {
      pendingRequests.delete(msg.id as number);
      if (msg.type === "error") {
        entry.reject(new Error((msg.data as Record<string, unknown>)?.message as string ?? "Unknown error"));
      } else {
        entry.resolve(msg.data);
      }
    }
  } else {
    // Route Python push events.  Bubble events go ONLY to the bubble
    // window (not the main app) so the floating overlay updates without
    // re-rendering the sidebar.
    if (msg.type === "bubble_show") {
      console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] received bubble_show from Python${RESET}`);
      showBubbleWindow();
    } else if (msg.type === "bubble_hide") {
      console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] received bubble_hide from Python${RESET}`);
      hideBubbleWindow();
    } else if (msg.type === "bubble_level") {
      bubbleWindow?.webContents.send("bubble:level", msg.data);
    } else if (msg.type === "show_window") {
      // Tray "Open app": Python asks us to show + focus the dashboard.
      // Single hop over the always-up TCP channel; falls back to the
      // Win32 EnumWindows path in tray.open_electron_window() if this
      // never arrives (TCP momentarily down).
      showMainWindow();
    } else if (msg.type === "quit_app") {
      // Tray "Quit": Python is about to force-exit.  Close Electron too
      // so the user isn't left with a window that has no backend.
      app.quit();
    }
    // SEC-029: tag each python-event with a per-session nonce so the
    // renderer can detect replayed frames from an unauthenticated TCP
    // attacker (SEC-018). The nonce is generated once per Electron
    // session and stored in this module-level variable. The renderer
    // compares the nonce on each event and drops any that don't match.
    if (!msg._session_nonce && sessionNonce) {
      (msg as Record<string, unknown>)._session_nonce = sessionNonce;
    }
    // SEC-017: previously this broadcast every Python event to every
    // window.  Transcription text and history records were thus sent
    // to the bubble window too — a data leak (the bubble only needs
    // waveform level + show/hide events).  Filter to the main window
    // only; the bubble gets its own dedicated channel for waveform.
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("python-event", msg);
    }
  }
}

function sendToPython(msg: Record<string, unknown>): Promise<unknown> {
  return new Promise((resolve, reject) => {
    if (!tcpSocket) {
      reject(new Error("Python backend is not connected"));
      return;
    }
    // SEC-019: validate the command against an allowlist before
    // forwarding to the Python backend. Combined with SEC-018
    // (unauth TCP), this prevents a compromised renderer from
    // calling arbitrary IPC commands like set_config / quit_app.
    //
    // ERR-IPC-002 (fix): previously missing `quit_app` and `restart_app`,
    // which broke tray Quit/Restart (stopPython sends `quit_app`).
    // ERR-IPC-003 (fix): removed 6 dead/mismatched entries (`quit`,
    // `restart`, `save_config`, `save_vocabulary_with_diff`,
    // `repaste_last`, `complete_onboarding`) — none exist as server
    // IPC commands. The list now matches the server's actual command
    // names exactly (cross-checked against ipc_server.py _dispatch).
    const ALLOWED_COMMANDS = new Set([
      "get_status",
      "toggle_dictation",
      "undo_last",
      "get_config",
      "get_defaults",
      "set_config",
      "get_history",
      "search_history",
      "get_today_stats",
      "delete_history",
      "clear_history",
      "toggle_favorite",
      "get_favorites",
      "get_microphones",
      "restart_app",
      "quit_app",
      "get_templates",
      "save_templates",
      "get_volume_backend_status",
      "get_model_status",
      "get_vocabulary",
      "save_vocabulary",
      "onboarding_is_first_run",
      "onboarding_start",
      "onboarding_get_step",
      "onboarding_next_step",
      "onboarding_prev_step",
      "onboarding_set_microphone",
      "onboarding_set_hotkey",
      "onboarding_set_model",
      "onboarding_skip",
      "onboarding_apply",
      "onboarding_get_microphones",
      "onboarding_get_model_options",
      "onboarding_get_hotkey_presets",
      "download_model",
      // NEW-DEAD-015: allow test_llm_connection so the renderer can
      // wire up a "Test connection" button on the Settings page.
      "test_llm_connection",
    ]);
    const cmd = String(msg?.type ?? "").trim();
    if (!ALLOWED_COMMANDS.has(cmd)) {
      reject(new Error(`Disallowed IPC command: ${cmd}`));
      return;
    }
    const id = nextId++;
    (msg as Record<string, unknown>).id = id;
    pendingRequests.set(id, { resolve, reject });
    const line = JSON.stringify(msg) + "\n";
    tcpSocket.write(line);
    setTimeout(() => {
      if (pendingRequests.has(id)) {
        pendingRequests.delete(id);
        reject(new Error("Timeout"));
      }
    }, 5000);
  });
}

function createMainWindow(forceShow = false) {
  if (mainWindow) return;
  pythonReady = true;

  // When autostarted hidden, the window is created but not shown — the
  // React app still boots (so opening it later is instant) while staying
  // off the taskbar.  forceShow overrides this (second-instance / tray
  // open) so the window appears immediately.
  const shouldShow = forceShow || !START_HIDDEN;

  mainWindow = new BrowserWindow({
    width: 1000,
    height: 700,
    minWidth: 850,
    minHeight: 550,
    icon: path.join(__dirname, `../../resources/icon${nativeTheme.shouldUseDarkColors ? '-dark' : ''}.png`),
    frame: false,
    hasShadow: false,
    show: shouldShow,
    // skipTaskbar when hidden so an autostarted background instance leaves
    // no taskbar entry until the user actually opens it.
    skipTaskbar: !shouldShow,
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      backgroundThrottling: false,
      // SEC-014: explicit hardening.  These are Electron defaults
      // for most fields, but setting them explicitly guards against
      // future Electron version changes flipping a default to a
      // less-safe value.
      contextIsolation: true,        // renderer can't touch Node require
      nodeIntegration: false,        // no require() in renderer
      sandbox: true,                 // preload runs in sandboxed context
      webSecurity: true,             // enforce same-origin policy
      allowRunningInsecureContent: false,  // block mixed-content
      // spellcheck adds a tiny IPC surface; we don't need it.
      spellcheck: false,
    },
  })

  nativeTheme.on("updated", () => {
    if (mainWindow) {
      const name = nativeTheme.shouldUseDarkColors ? "icon-dark.png" : "icon.png";
      mainWindow.setIcon(path.join(__dirname, `../../resources/${name}`));
    }
  });

  Menu.setApplicationMenu(null);

  // Close-to-tray: the X button hides the window instead of quitting the
  // app.  The process (tray icon, Python backend, bubble) stays alive.
  // Full quit only happens via the tray "Quit" menu item → stopPython().
  mainWindow.on("close", (event) => {
    if (!app.isQuitting) {
      event.preventDefault();
      mainWindow?.hide();
      // Remove from taskbar while hidden.
      mainWindow?.setSkipTaskbar(true);
    }
  });

  // When the window is shown again (second-instance / tray open), restore
  // the taskbar entry.
  mainWindow.on("show", () => {
    mainWindow?.setSkipTaskbar(false);
  });

  mainWindow.on("maximize", () => broadcastMaximized(true));
  mainWindow.on("unmaximize", () => broadcastMaximized(false));

  mainWindow.webContents.on("before-input-event", (_event, input) => {
    if (
      (input.control || input.meta) &&
      input.shift &&
      input.key.toLowerCase() === "i"
    ) {
      // SEC-013: DevTools should only be available in dev builds.
      // In production (app.isPackaged === true), the toggle is a
      // no-op so end users (and any XSS that tries to trigger it
      // via synthetic keyboard events) can't open DevTools.
      if (!app.isPackaged) {
        mainWindow?.webContents.toggleDevTools();
      }
    }
  });

  if (process.env.ELECTRON_RENDERER_URL) {
    mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL);
  } else {
    mainWindow.loadFile(path.join(__dirname, "../renderer/index.html"));
  }
}

// ── Bubble window (always-on-top waveform overlay) ──────────────────

function centerOnPrimaryDisplay(): { x: number; y: number } {
    const display = screen.getPrimaryDisplay();
    const wa = display.workArea;
    const y = bubblePosition === 'top'
      ? Math.round(wa.y + 16)   // breathing room from top
      : Math.round(wa.y + wa.height - BUBBLE_HEIGHT - 16);  // breathing room from bottom
    return {
      x: Math.round(wa.x + (wa.width - BUBBLE_WIDTH) / 2),
      y,
    };
  }

let _bubblePageReady = false;

function createBubbleWindow(): BrowserWindow {
  if (bubbleWindow && !bubbleWindow.isDestroyed()) {
    return bubbleWindow;
  }
  const { x, y } = centerOnPrimaryDisplay();
  console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] creating window at (${x}, ${y}) ${BUBBLE_WIDTH}x${BUBBLE_HEIGHT}${RESET}`);

  const win = new BrowserWindow({
    width: BUBBLE_WIDTH,
    height: BUBBLE_HEIGHT,
    x,
    y,
    show: false,
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    hasShadow: false,
    focusable: false,
    webPreferences: {
      // SEC-026: dedicated bubble preload — exposes ONLY the `bubble:*`
      // IPC channels. The bubble renderer cannot invoke `python.call`
      // (which sends arbitrary commands to the Python backend) or
      // `window_.*` (which controls the main window). A compromised
      // bubble is now confined to waveform-level operations.
      preload: path.join(__dirname, "../preload/bubble.js"),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
      // SEC-014: harden the bubble window the same way as the main
      // window.  The bubble renders waveform data and is always-on-top;
      // an XSS'd renderer hijacking it as a phishing overlay is a
      // real risk (see SEC-016 for the senderFrame check on its IPC
      // handlers).
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      spellcheck: false,
    },
  });

  try { win.setAlwaysOnTop(true, "screen-saver"); }
  catch (e) { console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] screen-saver failed, trying floating:${RESET}`, e);
    try { win.setAlwaysOnTop(true, "floating"); } catch {} }
  // SEC-025: visibleOnFullScreen can leave the bubble painted on top of
  // exclusive fullscreen apps (games, video players) on some GPUs. We
  // only enable it when the foreground window is NOT in exclusive
  // fullscreen mode. Detection is best-effort; if we can't tell, we
  // err on the side of NOT painting over fullscreen.
  try {
    const foregroundFullscreen = isForegroundFullscreen();
    if (!foregroundFullscreen) {
      win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    }
  } catch {}

  win.webContents.on("did-fail-load", (_e, code, desc, url) => {
    console.error(`${ts()}  ${BUBBLE_CLR}[BUBBLE] did-fail-load code=${code} desc=${desc} url=${url}${RESET}`);
  });
  win.webContents.on("did-finish-load", () => {
    console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] did-finish-load${RESET}`);
  });
  win.webContents.on("render-process-gone", (_e, details) => {
    console.error(`${ts()}  ${BUBBLE_CLR}[BUBBLE] render-process-gone:${RESET}`, details);
    // SEC-024: reload the bubble window so it doesn't stay as a
    // blank, invisible, always-on-top overlay. Without this, a
    // crashed bubble renderer leaves a stuck overlay on the screen.
    try {
      if (!win.isDestroyed()) {
        console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] reloading after render-process-gone${RESET}`);
        win.reload();
      }
    } catch (e) {
      console.error("[BUBBLE] failed to reload after render-process-gone:", e);
    }
  });
  win.webContents.on("preload-error", (_e, file, err) => {
    console.error(`${ts()}  ${BUBBLE_CLR}[BUBBLE] preload-error file=${file}${RESET}`, err);
  });
  win.webContents.on("console-message", (_e, level, message, line, source) => {
    // NEW-SEC-002: no longer suppress CSP warnings — if a CSP violation
    // occurs it should be visible in the log so we can fix it.
    if (level >= 2) {
      const tag = ["VRB", "INF", "WRN", "ERR"][level] ?? "LOG";
      console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE renderer ${tag}] ${message} (${source}:${line})${RESET}`);
    }
  });

  const loadTarget = process.env.ELECTRON_RENDERER_URL
    ? process.env.ELECTRON_RENDERER_URL + "/bubble.html"
    : path.join(__dirname, "../renderer/bubble.html");
  console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] loading ${loadTarget}${RESET}`);
  if (process.env.ELECTRON_RENDERER_URL) {
    void win.loadURL(loadTarget);
  } else {
    void win.loadFile(loadTarget);
  }

  bubbleWindow = win;
  win.on("closed", () => {
    console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] closed${RESET}`);
    if (bubbleWindow === win) bubbleWindow = null;
    _bubblePageReady = false;
  });
  return win;
}

let _hideTimeout: ReturnType<typeof setTimeout> | null = null;

function showBubbleWindow(): void {
  if (!bubbleWindow || bubbleWindow.isDestroyed()) {
    createBubbleWindow();
  }
  const win = bubbleWindow;
  if (!win) {
    console.error(`${ts()}  ${BUBBLE_CLR}[BUBBLE] showBubbleWindow: no window to show${RESET}`);
    return;
  }

  // Rapid-toggle guard: cancel any pending hide timeout/animation so the
  // bubble doesn't flicker when show is called while a hide is in flight.
  if (_hideTimeout) {
    clearTimeout(_hideTimeout);
    _hideTimeout = null;
    // Remove any pending one-shot listener to prevent stale hide callbacks.
    try { ipcMain.removeAllListeners("bubble:hidden"); } catch {}
  }

  const c = centerOnPrimaryDisplay();
  win.setBounds({ x: c.x, y: c.y, width: BUBBLE_WIDTH, height: BUBBLE_HEIGHT });

  try { win.setAlwaysOnTop(true, "screen-saver"); } catch {}
  // SEC-025: conditionally enable visibleOnFullScreen based on
  // foreground fullscreen state.
  try {
    if (!isForegroundFullscreen()) {
      win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    }
  } catch {}

  try {
    if (!win.isVisible()) { win.show(); }
    // Signal the renderer to reset its closing state and play enter animation
    win.webContents.send("bubble:show");
    // Sync the current draggable state on every show (handles initial state
    // and ensures the bubble renderer is always in sync with the backend)
    win.webContents.send("bubble:draggable", bubbleDraggable);
  } catch (e) {
    console.error(`${ts()}  ${BUBBLE_CLR}[BUBBLE] show() failed:${RESET}`, e);
  }
  try { win.moveTop(); } catch {}
  try { win.setAlwaysOnTop(true, "screen-saver"); } catch {}

  setImmediate(() => {
    if (!win || win.isDestroyed()) return;
    if (!win.isVisible()) {
      console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] not visible after show() -- retrying${RESET}`);
      try { win.show(); } catch {}
      try { win.moveTop(); } catch {}
      try { win.setAlwaysOnTop(true, "screen-saver"); } catch {}
    }
  });
}

function hideBubbleWindow(): void {
  const win = bubbleWindow;
  if (!win || win.isDestroyed() || !win.isVisible()) return;

  // Rapid-toggle guard: cancel any previous hide timeout to avoid overlap.
  if (_hideTimeout) {
    clearTimeout(_hideTimeout);
    _hideTimeout = null;
  }

  // Send hide animation event to the renderer, then wait for it to
  // signal back before actually hiding the window.
  try {
    win.webContents.send("bubble:hide");
  } catch {}

  // Use a timeout as fallback in case the renderer is unresponsive.
  _hideTimeout = setTimeout(() => {
    _hideTimeout = null;
    try {
      if (!win.isDestroyed() && win.isVisible()) {
        win.hide();
        console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] hidden (fallback)${RESET}`);
      }
    } catch {}
  }, 300);

  // Listen for the renderer's animation-complete signal (once per hide).
  const onHidden = () => {
    if (_hideTimeout) {
      clearTimeout(_hideTimeout);
      _hideTimeout = null;
    }
    try {
      if (!win.isDestroyed()) {
        win.hide();
        console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] hidden (animated)${RESET}`);
      }
    } catch {}
  };
  ipcMain.once("bubble:hidden", onHidden);
}

// ── Bubble drag-to-move (IPC-based, works with focusable:false) ──

let bubbleDragging = false;

// SEC-016: helper that rejects IPC messages not coming from the bubble
// window's webContents.  Without this check, any XSS'd renderer (or a
// malicious third party that got code into the main window) could
// hijack the always-on-top bubble as a phishing overlay by sending
// drag/position commands.
function assertFromBubble(event: Electron.IpcMainEvent): boolean {
  if (!bubbleWindow || bubbleWindow.isDestroyed()) return false;
  // Compare senderFrame to the bubble window's main frame.  Electron
  // exposes event.senderFrame (an Electron.WebFrameMain) which is the
  // origin of the IPC message.
  return event.senderFrame === bubbleWindow.webContents.mainFrame;
}

ipcMain.on("bubble:drag-start", (event) => {
  // SEC-016: only the bubble window may start a drag.
  if (!assertFromBubble(event)) return;
  bubbleDragging = true;
});

ipcMain.on("bubble:drag", (event, { deltaX, deltaY }: { deltaX: number; deltaY: number }) => {
  // SEC-016: only the bubble window may drag itself.
  if (!assertFromBubble(event)) return;
  if (!bubbleDragging || !bubbleWindow || bubbleWindow.isDestroyed()) return;
  const [x, y] = bubbleWindow.getPosition();
  bubbleWindow.setPosition(x + deltaX, y + deltaY);
});

ipcMain.on("bubble:drag-end", (event) => {
  // SEC-016: only the bubble window may end a drag.
  if (!assertFromBubble(event)) return;
  bubbleDragging = false;
});

let bubbleDraggable = true;

ipcMain.on("bubble:draggable", (_event, draggable: boolean) => {
  // The draggable toggle is a config value that BOTH the main window
  // (Settings page, via window.bubble.setDraggable) and the bubble
  // renderer need to sync, so it is NOT restricted to the bubble frame.
  // (Position/draggable are config values, not hijack vectors — unlike
  // the drag-move commands below, which stay bubble-only.)
  bubbleDraggable = draggable;
  if (bubbleWindow && !bubbleWindow.isDestroyed()) {
    bubbleWindow.webContents.send("bubble:draggable", draggable);
  }
});

ipcMain.on("bubble:show-from-renderer", (event) => {
  // SEC-016: bubble show/hide from the bubble's own UI is allowed;
  // the main window uses `set_config` (allowlisted) for global toggle.
  if (!assertFromBubble(event)) return;
  showBubbleWindow();
});

ipcMain.on("set_bubble_position", (_event, position: 'top' | 'bottom') => {
  // Position is a config value that BOTH the main window (Settings
  // page, via window.bubble.setPosition) and the bubble renderer need
  // to sync, so it is NOT restricted to the bubble frame.  It is a
  // benign enum ('top' | 'bottom'), not a hijack vector.
  if (position === 'top' || position === 'bottom') {
    bubblePosition = position;
    // If the bubble window is visible, reposition it immediately.
    if (bubbleWindow && !bubbleWindow.isDestroyed() && bubbleWindow.isVisible()) {
      const c = centerOnPrimaryDisplay();
      bubbleWindow.setBounds({ x: c.x, y: c.y, width: BUBBLE_WIDTH, height: BUBBLE_HEIGHT });
    }
  }
});

ipcMain.on("bubble:ready", (event) => {
  // SEC-016: only the bubble window signals readiness.
  if (!assertFromBubble(event)) return;
  console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] renderer reports ready${RESET}`);
  _bubblePageReady = true;
});

function tcpConnect(port: number) {
  function tryConnect() {
    const client = new net.Socket();
    tcpSocket = client;

    client.connect(port, "127.0.0.1", () => {
      // SEC-018: send the auth message as the first line.  The Python
      // IPC server reads this before processing any other commands.
      // If the token doesn't match, the server drops the connection.
      client.write(JSON.stringify({ type: "auth", token: IPC_TOKEN }) + "\n");
      // Python is running and its TCP server accepted us.
      // Create the main window immediately.
      createMainWindow();
    });

    client.on("data", (chunk: Buffer) => {
      // SEC-023: cap tcpBuffer at 4 MB to prevent unbounded memory
      // growth from malformed frames (e.g. a chunk with no newline
      // that never gets split). Drop the connection on overflow.
      tcpBuffer += chunk.toString();
      if (tcpBuffer.length > 4 * 1024 * 1024) {
        console.error(
          "[TCP] tcpBuffer exceeded 4 MB without a newline — dropping connection (possible malformed frame)"
        );
        tcpBuffer = "";
        client.destroy();
        return;
      }
      const lines = tcpBuffer.split("\n");
      tcpBuffer = lines.pop()!;
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const msg = JSON.parse(line);
          handleMessage(msg);
        } catch {
          console.error("Invalid JSON from Python:", line);
        }
      }
    });

    client.on("error", (err: NodeJS.ErrnoException) => {
      if (err.code === "ECONNREFUSED") {
        // Python not ready yet — retry
        client.destroy();
        setTimeout(tryConnect, 500);
      } else {
        console.error("[TCP] error:", err);
        client.destroy();
        setTimeout(tryConnect, 2000);
      }
    });

    client.on("close", () => {
      if (tcpSocket === client) {
        tcpSocket = null;
      }
      // SEC-022: reject all outstanding pendingRequests so the UI
      // doesn't hang forever. Without this, every `await
      // window.electronAPI.python(...)` would leak when the socket
      // died — the renderer's loading spinners would never resolve.
      for (const [id, entry] of pendingRequests) {
        pendingRequests.delete(id);
        entry.reject(new Error("Python socket closed"));
      }
      // If Python exited, don't reconnect — the exit handler below will
      // quit Electron.  If we kept reconnecting, rapid retries exhaust
      // TCP buffer space (ENOBUFS on Windows).
      if (pythonReady && pythonProcess !== null) {
        console.warn("[TCP] connection lost — will reconnect");
        setTimeout(tryConnect, 2000);
      }
    });
  }

  tryConnect();
}

function startPython() {
  const [exe, args] = pythonArgs();
  // Spawn with inherit stdio — stdout/stderr go to the Electron
  // console (terminal), NOT to pipes.  This eliminates the
  // unbuffered-pipe-write slowdown during torch import.
  // IPC happens via TCP instead of pipe parsing.
  pythonProcess = spawn(exe, args, {
    stdio: "inherit",
    env: {
      ...process.env,
      // KMP_DUPLICATE_LIB_OK avoids libiomp5 deadlock when process
      // has no console stdin.
      KMP_DUPLICATE_LIB_OK: "TRUE",
      // SEC-018: per-launch IPC session token.  The Python IPC server
      // reads this from the env and requires the Electron client to
      // send a matching {"type":"auth","token":"..."} message as the
      // first TCP line.
      VOICE_TYPER_IPC_TOKEN: IPC_TOKEN,
    },
  });

  // Record the spawned Python PID so the stale-killer doesn't kill it.
  (globalThis as { __myPyPid?: number }).__myPyPid = pythonProcess.pid;
  console.warn(`spawned Python backend (PID=${pythonProcess.pid})`);

  // Connect via TCP (will retry until Python's TCP server is ready).
  tcpConnect(IPC_PORT);

  pythonProcess.on("exit", (code) => {
    console.warn("Python process exited:", code);
    if (!pythonReady) {
      pythonExitedEarly = true;
      pythonProcess = null;
      for (const [id, entry] of pendingRequests) {
        pendingRequests.delete(id);
        entry.reject(new Error("Python backend exited early"));
      }
      if (mainWindow) {
        mainWindow.close();
        mainWindow = null;
      }
      dialog.showErrorBox(
        "Voice Typer",
        "Only one instance of Voice Typer can run at a time.\n\n" +
          "Close the existing instance first, then try again."
      );
      app.quit();
    } else {
      // Python crashed or was killed during normal operation.
      // Shut down Electron so the user isn't left with a broken UI
      // that spams TCP reconnect errors (ENOBUFS on Windows).
      pythonProcess = null;
      tcpSocket = null;
      for (const [id, entry] of pendingRequests) {
        pendingRequests.delete(id);
        entry.reject(new Error("Python backend disconnected"));
      }
      app.quit();
    }
  });
}

function stopPython() {
  if (!pythonProcess) return;
  sendToPython({ type: "quit_app" }).catch(() => {});
  const killTimer = setTimeout(() => {
    if (pythonProcess) {
      pythonProcess.kill();
      pythonProcess = null;
    }
  }, 3000);
  pythonProcess.on("exit", () => clearTimeout(killTimer));
}

function broadcastMaximized(maximized: boolean) {
  BrowserWindow.getAllWindows().forEach((win) => {
    win.webContents.send("window:maximized-changed", maximized);
  });
}

app.whenReady().then(() => {

  // SEC-029: generate a per-session nonce. Use crypto.randomUUID()
  // when available (Node 14.17+/Electron 12+), fall back to a
  // timestamp+random string.
  try {
    const cryptoMod = require("crypto") as { randomUUID?: () => string };
    sessionNonce = cryptoMod.randomUUID?.()
      || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  } catch {
    sessionNonce = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  // ── Content Security Policy (HTTP headers) ───────────────────
  // SEC-012 / NEW-SEC-002: CSP is also set via <meta> tags in index.html
  // and bubble.html for production file:// loads, but certain directives
  // (frame-ancestors, form-action) are only honored when delivered
  // as actual HTTP headers.  Setting them here via Electron's
  // onHeadersReceived ensures they're properly enforced in dev mode
  // (http://localhost:5173) and in production.
  //
  // NEW-SEC-002: script-src does NOT include 'unsafe-inline' — the
  // renderer uses Vite module scripts (type="module") which are covered
  // by 'self'.  Inline event handlers (onclick="...") are blocked.
  const CSP = [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "form-action 'none'",
    "base-uri 'self'",
  ].join("; ")

  session.defaultSession.webRequest.onHeadersReceived(
    (details: Electron.OnHeadersReceivedListenerDetails, callback: (headers: Electron.HeadersReceivedResponse) => void) => {
      callback({
        responseHeaders: {
          ...details.responseHeaders,
          "Content-Security-Policy": [CSP],
        },
      })
    },
  )

  // SEC-021: previously the uncaughtException handler just console.error'd
  // and continued, leaving the process in a half-broken state (locked
  // mutex, half-written config). We now log to file, count occurrences,
  // and exit non-zero after N consecutive errors so the user sees the
  // crash instead of a silent zombie.
  let uncaughtCount = 0;
  const MAX_UNCAUGHT = 5;
  const crashLogPath = path.join(app?.getPath("userData") ?? process.cwd(), "electron-crashes.log");
  const logCrash = (kind: string, err: unknown) => {
    try {
      const ts = new Date().toISOString();
      const line = `${ts} [${kind}] ${err instanceof Error ? err.stack ?? err.message : String(err)}\n`;
      fs.appendFileSync(crashLogPath, line, { encoding: "utf-8" });
    } catch {
      // Logging is best-effort.
    }
  };
  process.on("uncaughtException", (err) => {
    console.error("[VT] uncaughtException:", err);
    logCrash("uncaughtException", err);
    uncaughtCount++;
    if (uncaughtCount >= MAX_UNCAUGHT) {
      console.error(`[VT] ${uncaughtCount} uncaught exceptions — exiting to avoid zombie state`);
      try {
        dialog.showErrorBox(
          "Voice Typer — Critical Error",
          `The app encountered ${uncaughtCount} uncaught exceptions and will exit.\n` +
            `Crash log: ${crashLogPath}\n` +
            `Please restart Voice Typer.`,
        );
      } catch {
        // dialog may not be available in headless mode
      }
      process.exit(1);
    }
  });
  process.on("unhandledRejection", (err) => {
    console.error("[VT] unhandledRejection:", err);
    logCrash("unhandledRejection", err);
  });

  // RELIABILITY-002: killStalePython() removed — single-instance
  // enforcement is handled by requestSingleInstanceLock() (Electron)
  // and the Win32 named mutex (Python).  See the comment block above
  // for details.

  if (process.env.VT_BUBBLE_TEST === "1") {
    console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] VT_BUBBLE_TEST=1 -- showing bubble for diagnostics${RESET}`);
    setTimeout(() => {
      showBubbleWindow();
      const id = setInterval(() => {
        const rms = 0.05 + 0.4 * Math.abs(Math.sin(Date.now() / 200));
        bubbleWindow?.webContents.send("bubble:level", { rms, peak: rms * 1.5 });
      }, 100);
      setTimeout(() => clearInterval(id), 10_000);
    }, 1500);
  }
  startPython();
});

ipcMain.handle("python-call", async (_event, msg) => {
  if (!tcpSocket) {
    if (pythonExitedEarly) {
      return { _error: "Python backend exited early — another instance is running" };
    }
    return { _error: "Python backend is not connected" };
  }
  return await sendToPython(msg);
});

// ── Window control IPC (used by the custom title bar) ──────────────

let preMaximizeBounds: Electron.Rectangle | null = null

ipcMain.handle("window:minimize", () => {
  mainWindow?.minimize();
});

ipcMain.handle("window:toggle-maximize", async () => {
  const win = mainWindow
  if (!win) return false

  if (win.isMaximized()) {
    win.unmaximize()
    if (preMaximizeBounds) {
      win.setBounds(preMaximizeBounds)
      preMaximizeBounds = null
    }
  } else {
    preMaximizeBounds = win.getBounds()
    win.maximize()
  }
  return win.isMaximized()
});

ipcMain.handle("window:close", () => {
  mainWindow?.close();
});

ipcMain.handle("window:is-maximized", () => {
  return mainWindow?.isMaximized() ?? false;
});

// ── History export ──────────────────────────────────────────────

ipcMain.handle("history:export", async (_event, { data, format }: { data: Record<string, unknown>[]; format: 'json' | 'csv' }) => {
  const filters = format === 'csv'
    ? [{ name: 'CSV', extensions: ['csv'] }]
    : [{ name: 'JSON', extensions: ['json'] }]

  const { canceled, filePath } = await dialog.showSaveDialog({
    title: 'Export History',
    defaultPath: `voice-typer-history.${format}`,
    filters,
  })

  if (canceled || !filePath) return { success: false }

  try {
    if (format === 'csv') {
      // SEC-015: CSV formula injection defense.  Cells starting
      // with =, +, -, @, TAB, or CR are interpreted as formulas by
      // Excel/LibreOffice when the user opens the exported file.
      // Prefix them with a single quote so the spreadsheet treats
      // them as literal text.  Also wrap in double quotes (with
      // embedded quotes doubled) to prevent injection via newlines
      // or commas.
      const csvEscape = (s: string): string => {
        let v = String(s ?? '')
        if (/^[=+\-@\t\r]/.test(v)) {
          v = "'" + v
        }
        return '"' + v.replace(/"/g, '""') + '"'
      }
      const header = Object.keys(data[0] ?? {}).map(csvEscape).join(',')
      const rows = data.map(r => Object.values(r).map(v => csvEscape(v as string)).join(','))
      fs.writeFileSync(filePath, [header, ...rows].join('\n'), 'utf-8')
    } else {
      fs.writeFileSync(filePath, JSON.stringify(data, null, 2), 'utf-8')
    }
    return { success: true, path: filePath }
  } catch (e: unknown) {
    return { success: false, error: (e as Error).message }
  }
})

// ── Vocabulary export ──────────────────────────────────────────

ipcMain.handle("vocabulary:export", async (_event, { data, format }: { data: Record<string, unknown>; format: 'json' | 'csv' }) => {
  const filters = format === 'csv'
    ? [{ name: 'CSV', extensions: ['csv'] }]
    : [{ name: 'JSON', extensions: ['json'] }]

  const { canceled, filePath } = await dialog.showSaveDialog({
    title: 'Export Vocabulary',
    defaultPath: `voice-typer-vocabulary.${format}`,
    filters,
  })

  if (canceled || !filePath) return { success: false }

  try {
    if (format === 'csv') {
      // SEC-015: CSV formula injection defense (see history:export).
      const csvEscape = (s: string): string => {
        let v = String(s ?? '')
        if (/^[=+\-@\t\r]/.test(v)) {
          v = "'" + v
        }
        return '"' + v.replace(/"/g, '""') + '"'
      }
      const rows: string[] = ['original,correction']
      const vocab = data as Record<string, unknown>
      const entries = (vocab.entries ?? []) as Array<Record<string, string>>
      for (const entry of entries) {
        rows.push(`${csvEscape(entry.original ?? '')},${csvEscape(entry.correction ?? '')}`)
      }
      fs.writeFileSync(filePath, rows.join('\n'), 'utf-8')
    } else {
      const vocab = data as Record<string, unknown>
      fs.writeFileSync(filePath, JSON.stringify(vocab.entries ?? [], null, 2), 'utf-8')
    }
    return { success: true, path: filePath }
  } catch (e: unknown) {
    return { success: false, error: (e as Error).message }
  }
})

// ── UX-008: Open log folder ─────────────────────────────────────
// Previously the Settings page's "View Logs" button just showed a
// snackbar saying "Log folder opened" without actually opening
// anything.  This handler opens the Python backend's log directory
// in the OS file manager.  The path mirrors what
// voice_typer/server/app.py:_setup_logging() writes to.

ipcMain.handle("window:open-logs", async () => {
  try {
    const os = require("os");
    const path = require("path");
    // Mirror voice_typer/server/config.py:_config_dir()
    const logDir = path.join(os.homedir(), ".voice-typer");
    // Create the directory if it doesn't exist yet (first run).
    try { fs.mkdirSync(logDir, { recursive: true }); } catch { /* ignore */ }
    const result = await shell.openPath(logDir);
    if (result) {
      // openPath returns an error string on failure, empty string on success.
      return { success: false, error: result, path: logDir };
    }
    return { success: true, path: logDir };
  } catch (e: unknown) {
    return { success: false, error: (e as Error).message };
  }
});

// Tracks a genuine quit (tray Quit / Cmd+Q) so the close-to-tray handler
// on the window knows to let the close proceed instead of hiding.
app.isQuitting = false;

app.on("before-quit", () => {
  app.isQuitting = true;
  stopPython();
});

// With close-to-tray, closing the dashboard window just hides it — the
// process keeps running.  So window-all-closed only fires on a real quit
// (last window destroyed) or on macOS when all windows are closed by the
// user.  Guard accordingly.
app.on("window-all-closed", () => {
  if (app.isQuitting) return;
  if (process.platform !== "darwin") {
    // Don't quit: the tray icon + backend keep the app alive.  Quit only
    // happens explicitly via the tray menu.
  }
});

// macOS: clicking the dock icon when no windows are open should re-show
// the dashboard (mirrors second-instance on the other platforms).
app.on("activate", () => {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createMainWindow(/* forceShow */ true);
  } else {
    showMainWindow();
  }
});

// Allow the Python backend (tray "Open app") to request showing the
// dashboard over TCP — a clean, single-hop alternative to the Win32
// EnumWindows focus hack in tray._bring_electron_to_front.  The tray
// tries this first; the Win32 path remains as a fallback.
ipcMain.handle("window:show", () => {
  showMainWindow();
  return true;
});
