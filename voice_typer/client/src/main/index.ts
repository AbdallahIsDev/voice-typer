import { app, BrowserWindow, dialog, ipcMain, Menu, screen } from "electron";
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

// When set (autostart at login), the dashboard window is created hidden.
// The process + tray + bubble still work; the window appears on demand
// via the Start Menu (second-instance) or tray "Open app".
const START_HIDDEN = process.env.VT_START_HIDDEN === "1";

/**
 * Single-instance gate.
 *
 * Acquiring the lock is Electron's native, OS-level mechanism for "only one
 * of me may run." It uses a named pipe on Windows / a lockfile on POSIX —
 * no port scanning, no process killing, no Python involvement. When a
 * SECOND Electron process starts:
 *
 *   • the second instance: requestSingleInstanceLock() returns false → quit
 *   • the first instance:  emits "second-instance" → we show+focus the
 *     dashboard window (creating it if it was never created, e.g. after a
 *     hidden autostart).
 *
 * This is how the user "opens" the app from Start Menu / Desktop when it's
 * already running in the background: the shortcut launches a throwaway
 * Electron process whose only job is to fail the lock and wake the real one.
 *
 * MUST run before app.whenReady() — the lock is checked at process start.
 */
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  // We are the duplicate.  The first instance has already received (or is
  // about to receive) the "second-instance" event and will show itself.
  app.quit();
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

// Bubble geometry (logical px).
const BUBBLE_WIDTH = 400;
const BUBBLE_HEIGHT = 160;

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
 * Kill any leftover `voice_typer` Python processes from previous
 * sessions (console-script runs, prior Electron launches, restart
 * attempts, autostart launcher, etc.).
 *
 * Scans BOTH `python.exe` and `pythonw.exe` — the autostart launcher
 * runs as `pythonw.exe`, so scanning only `python.exe` would let a
 * stale autostarted instance survive and cause mutex conflicts.
 *
 * Additionally tree-kills any orphaned Electron / npm / node process
 * whose command line mentions voice-typer.  Without this, killing only
 * the Python backend leaves the OLD Electron window orphaned (Windows
 * doesn't cascade child kills without a Job Object or /T), resulting in
 * two visible windows after a manual `npm run dev`.  We use `taskkill
 * /T /F` (the documented Windows process-tree kill) so the whole
 * npm→electron→python chain dies atomically.
 */
function killStalePython(): void {
  if (process.platform !== "win32") return;
  const myPyPid = (globalThis as { __myPyPid?: number }).__myPyPid;

  /**
   * Read a process's ParentProcessId via WMIC (best-effort).
   * Returns 0 on failure.
   */
  const parentPidOf = (pid: number): number => {
    try {
      const out = require("child_process").execSync(
        `wmic process where "ProcessId=${pid}" get ParentProcessId /format:list`,
        { encoding: "utf8", timeout: 5000, windowsHide: true },
      ) as string;
      const m = out.match(/ParentProcessId=(\d+)/);
      return m ? parseInt(m[1], 10) : 0;
    } catch { return 0; }
  };

  /**
   * Read a process's command line via WMIC (best-effort).
   */
  const commandLineOf = (pid: number): string => {
    try {
      return require("child_process").execSync(
        `wmic process where "ProcessId=${pid}" get CommandLine /format:list`,
        { encoding: "utf8", timeout: 5000, windowsHide: true },
      ) as string;
    } catch { return ""; }
  };

  /**
   * Tree-kill a PID and ALL its descendants via taskkill /T /F — the
   * documented way to atomically kill a Windows process tree.
   */
  const treeKill = (pid: number): void => {
    try {
      require("child_process").execSync(
        `taskkill /PID ${pid} /T /F`,
        { encoding: "utf8", timeout: 8000, windowsHide: true, stdio: "ignore" },
      );
    } catch { /* process may already be gone */ }
  };

  /**
   * Kill a stale Python PID.  If it was spawned by an autostart npm/
   * electron chain, walk UP to the electron.exe/node.exe ancestor and
   * tree-kill from there so the orphaned Electron window dies too.
   */
  const reapPythonPid = (pid: number): void => {
    if (pid === process.pid) return;
    if (myPyPid && pid === myPyPid) return;
    const cmd = commandLineOf(pid);
    if (!/voice[_-]?typer/i.test(cmd)) return;
    // Don't kill the Flet UI subprocess or the wmic query itself.
    if (/ui\.app/i.test(cmd)) return;
    if (/imt\.exe|wmic\.exe/i.test(cmd)) return;

    // Walk up the process tree looking for an electron.exe / node.exe
    // ancestor that also belongs to voice-typer.  If found, tree-kill
    // from that ancestor (kills npm + electron + python together).
    // Otherwise fall back to SIGTERM on just the Python PID.
    let cursor = pid;
    for (let hop = 0; hop < 6; hop++) {  // bound the walk
      const parent = parentPidOf(cursor);
      if (!parent || parent === process.pid) break;
      const parentCmd = commandLineOf(parent);
      if (
        /electron\.exe/i.test(parentCmd) ||
        (/node\.exe/i.test(parentCmd) && /voice[_-]?typer/i.test(parentCmd))
      ) {
        console.log(`[VT] reaper: tree-killing ancestor (PID=${parent}) of stale python (PID=${pid})`);
        treeKill(parent);
        return;
      }
      cursor = parent;
    }
    console.log(`[VT] reaper: KILLING stale voice_typer python (PID=${pid})`);
    try { process.kill(pid, "SIGTERM"); } catch {}
  };

  // Scan both image names.  We run tasklist once per name because
  // tasklist's /FI filter only accepts a single IMAGENAME.
  for (const image of ["python.exe", "pythonw.exe"]) {
    try {
      const tasklist = require("child_process").execSync(
        `tasklist /FI "IMAGENAME eq ${image}" /FO CSV /NH`,
        { encoding: "utf8", timeout: 5000, windowsHide: true },
      ) as string;
      const lines = tasklist.split(/\r?\n/);
      // The CSV line looks like: "python.exe","1234",...  — match the
      // image name + PID, tolerant of pythonw.exe.
      const pidRe = new RegExp(`^"${image.replace(/\./g, "\\.")}","(\\d+)"`, "i");
      for (const line of lines) {
        const m = line.trim().match(pidRe);
        if (!m) continue;
        reapPythonPid(parseInt(m[1], 10));
      }
    } catch (e) {
      console.warn(`[VT] killStalePython (${image}) failed:`, e);
    }
  }
}

function pythonArgs(): [string, string[]] {
  const home = os.homedir();
  const base = path.join(home, ".voice-typer", "venv");
  const exe = process.platform === "win32"
    ? path.join(base, "Scripts", "python.exe")
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
      console.log(`${ts()}  ${BUBBLE_CLR}[BUBBLE] received bubble_show from Python${RESET}`);
      showBubbleWindow();
    } else if (msg.type === "bubble_hide") {
      console.log(`${ts()}  ${BUBBLE_CLR}[BUBBLE] received bubble_hide from Python${RESET}`);
      hideBubbleWindow();
    } else if (msg.type === "bubble_level") {
      bubbleWindow?.webContents.send("bubble:level", msg.data);
    } else if (msg.type === "show_window") {
      // Tray "Open app": Python asks us to show + focus the dashboard.
      // Single hop over the always-up TCP channel; falls back to the
      // Win32 EnumWindows path in tray.open_electron_window() if this
      // never arrives (TCP momentarily down).
      showMainWindow();
    }
    BrowserWindow.getAllWindows().forEach((win) => {
      win.webContents.send("python-event", msg);
    });
  }
}

function sendToPython(msg: Record<string, unknown>): Promise<unknown> {
  return new Promise((resolve, reject) => {
    if (!tcpSocket) {
      reject(new Error("Python backend is not connected"));
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
    icon: path.join(__dirname, "../../resources/icon.png"),
    frame: false,
    transparent: true,
    hasShadow: false,
    show: shouldShow,
    // skipTaskbar when hidden so an autostarted background instance leaves
    // no taskbar entry until the user actually opens it.
    skipTaskbar: !shouldShow,
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
    },
  })

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
      mainWindow?.webContents.toggleDevTools();
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
  return {
    x: Math.round(wa.x + (wa.width - BUBBLE_WIDTH) / 2),
    y: Math.round(wa.y + (wa.height - BUBBLE_HEIGHT) / 2),
  };
}

let bubblePageReady = false;

function createBubbleWindow(): BrowserWindow {
  if (bubbleWindow && !bubbleWindow.isDestroyed()) {
    return bubbleWindow;
  }
  const { x, y } = centerOnPrimaryDisplay();
  console.log(`${ts()}  ${BUBBLE_CLR}[BUBBLE] creating window at (${x}, ${y}) ${BUBBLE_WIDTH}x${BUBBLE_HEIGHT}${RESET}`);

  const win = new BrowserWindow({
    width: BUBBLE_WIDTH,
    height: BUBBLE_HEIGHT,
    x,
    y,
    show: false,
    frame: false,
    transparent: false,
    backgroundColor: "#0a0a0c",
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    hasShadow: true,
    focusable: false,
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });

  try { win.setAlwaysOnTop(true, "screen-saver"); }
  catch (e) { console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] screen-saver failed, trying floating:${RESET}`, e);
    try { win.setAlwaysOnTop(true, "floating"); } catch {} }
  try { win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true }); } catch {}
  if (process.platform === "win32") {
    try { (win as any).setFocusable?.(false); } catch {}
  }

  win.webContents.on("did-fail-load", (_e, code, desc, url) => {
    console.error(`${ts()}  ${BUBBLE_CLR}[BUBBLE] did-fail-load code=${code} desc=${desc} url=${url}${RESET}`);
  });
  win.webContents.on("did-finish-load", () => {
    console.log(`${ts()}  ${BUBBLE_CLR}[BUBBLE] did-finish-load${RESET}`);
  });
  win.webContents.on("render-process-gone", (_e, details) => {
    console.error(`${ts()}  ${BUBBLE_CLR}[BUBBLE] render-process-gone:${RESET}`, details);
  });
  win.webContents.on("preload-error", (_e, file, err) => {
    console.error(`${ts()}  ${BUBBLE_CLR}[BUBBLE] preload-error file=${file}${RESET}`, err);
  });
  win.webContents.on("console-message", (_e, level, message, line, source) => {
    if (message.includes("Content-Security-Policy")) return; // suppress dev warning
    if (level >= 2) {
      const tag = ["VRB", "INF", "WRN", "ERR"][level] ?? "LOG";
      console.log(`${ts()}  ${BUBBLE_CLR}[BUBBLE renderer ${tag}] ${message} (${source}:${line})${RESET}`);
    }
  });

  const loadTarget = process.env.ELECTRON_RENDERER_URL
    ? process.env.ELECTRON_RENDERER_URL + "/bubble.html"
    : path.join(__dirname, "../renderer/bubble.html");
  console.log(`${ts()}  ${BUBBLE_CLR}[BUBBLE] loading ${loadTarget}${RESET}`);
  if (process.env.ELECTRON_RENDERER_URL) {
    void win.loadURL(loadTarget);
  } else {
    void win.loadFile(loadTarget);
  }

  bubbleWindow = win;
  win.on("closed", () => {
    console.log(`${ts()}  ${BUBBLE_CLR}[BUBBLE] closed${RESET}`);
    if (bubbleWindow === win) bubbleWindow = null;
    bubblePageReady = false;
  });
  return win;
}

function showBubbleWindow(): void {
  if (!bubbleWindow || bubbleWindow.isDestroyed()) {
    createBubbleWindow();
  }
  const win = bubbleWindow;
  if (!win) {
    console.error(`${ts()}  ${BUBBLE_CLR}[BUBBLE] showBubbleWindow: no window to show${RESET}`);
    return;
  }

  const c = centerOnPrimaryDisplay();
  win.setBounds({ x: c.x, y: c.y, width: BUBBLE_WIDTH, height: BUBBLE_HEIGHT });

  try { win.setAlwaysOnTop(true, "screen-saver"); } catch {}
  try { win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true }); } catch {}

  try {
    if (!win.isVisible()) { win.show(); }
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
  if (win && !win.isDestroyed() && win.isVisible()) {
    win.hide();
    console.log(`${ts()}  ${BUBBLE_CLR}[BUBBLE] hidden${RESET}`);
  }
}

ipcMain.on("bubble:ready", () => {
  console.log(`${ts()}  ${BUBBLE_CLR}[BUBBLE] renderer reports ready${RESET}`);
  bubblePageReady = true;
});

function tcpConnect(port: number) {
  function tryConnect() {
    const client = new net.Socket();
    tcpSocket = client;

    client.connect(port, "127.0.0.1", () => {
      // nothing — Python logs will follow, no need for a banner
      // Python is running and its TCP server accepted us.
      // Create the main window immediately.
      createMainWindow();
    });

    client.on("data", (chunk: Buffer) => {
      tcpBuffer += chunk.toString();
      const lines = tcpBuffer.split("\n");
      tcpBuffer = lines.pop()!;
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const msg = JSON.parse(line);
          handleMessage(msg);
        } catch (e) {
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
      if (pythonReady) {
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
    },
  });

  // Record the spawned Python PID so the stale-killer doesn't kill it.
  (globalThis as { __myPyPid?: number }).__myPyPid = pythonProcess.pid;
  console.log(`spawned Python backend (PID=${pythonProcess.pid})`);

  // Connect via TCP (will retry until Python's TCP server is ready).
  tcpConnect(IPC_PORT);

  pythonProcess.on("exit", (code) => {
    console.log("Python process exited:", code);
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
      pythonProcess = null;
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

  process.on("uncaughtException", (err) => {
    console.error("[VT] uncaughtException:", err);
  });
  process.on("unhandledRejection", (err) => {
    console.error("[VT] unhandledRejection:", err);
  });

  killStalePython();

  if (process.env.VT_BUBBLE_TEST === "1") {
    console.log(`${ts()}  ${BUBBLE_CLR}[BUBBLE] VT_BUBBLE_TEST=1 -- showing bubble for diagnostics${RESET}`);
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

ipcMain.handle("window:move", (_event, { x, y }: { x: number; y: number }) => {
  cancelAnim()
  mainWindow?.setBounds({ x, y })
})

// ── Window animation helpers ──────────────────────────────────────

let animTimer: ReturnType<typeof setTimeout> | null = null
let preMaximizeBounds: Electron.Rectangle | null = null

function cancelAnim() {
  if (animTimer) { clearTimeout(animTimer); animTimer = null }
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t
}

function animateWindow(
  win: BrowserWindow,
  from: Electron.Rectangle,
  to: Electron.Rectangle,
  duration: number,
): Promise<void> {
  return new Promise((resolve) => {
    cancelAnim()
    const start = performance.now()
    const tick = () => {
      const t = Math.min((performance.now() - start) / duration, 1)
      const e = 1 - (1 - t) ** 3
      try {
        win.setBounds({
          x: Math.round(lerp(from.x, to.x, e)),
          y: Math.round(lerp(from.y, to.y, e)),
          width: Math.round(lerp(from.width, to.width, e)),
          height: Math.round(lerp(from.height, to.height, e)),
        })
      } catch {}
      if (t < 1) {
        animTimer = setTimeout(tick, 16)
      } else {
        resolve()
      }
    }
    tick()
  })
}

// ── Window control IPC (used by the custom title bar) ──────────────

ipcMain.handle("window:minimize", async () => {
  const win = mainWindow
  if (!win) return
  const b = win.getBounds()
  const display = screen.getPrimaryDisplay()
  await animateWindow(win, b, {
    x: Math.round(b.x + b.width * 0.05),
    y: display.workArea.y + display.workArea.height,
    width: Math.round(b.width * 0.9),
    height: Math.round(b.height * 0.05),
  }, 180)
  win.minimize()
});

ipcMain.handle("window:toggle-maximize", async () => {
  const win = mainWindow
  if (!win) return false

  if (win.isMaximized()) {
    const target = preMaximizeBounds ?? { x: 100, y: 100, width: 1000, height: 700 }
    await animateWindow(win, win.getBounds(), target, 200)
    win.unmaximize()
    if (preMaximizeBounds) {
      win.setBounds(preMaximizeBounds)
      preMaximizeBounds = null
    }
  } else {
    preMaximizeBounds = win.getBounds()
    const display = screen.getPrimaryDisplay()
    await animateWindow(win, preMaximizeBounds, display.workArea, 200)
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
      const header = Object.keys(data[0] ?? {}).join(',')
      const rows = data.map(r => Object.values(r).map(v => JSON.stringify(v ?? '')).join(','))
      fs.writeFileSync(filePath, [header, ...rows].join('\n'), 'utf-8')
    } else {
      fs.writeFileSync(filePath, JSON.stringify(data, null, 2), 'utf-8')
    }
    return { success: true, path: filePath }
  } catch (e: unknown) {
    return { success: false, error: (e as Error).message }
  }
})

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
