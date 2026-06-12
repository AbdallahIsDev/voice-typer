import { app, BrowserWindow, dialog, ipcMain, Menu } from "electron";
import { spawn, ChildProcess } from "child_process";
import path from "path";
import os from "os";

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (reason: unknown) => void;
}

let pythonProcess: ChildProcess | null = null;
let mainWindow: BrowserWindow | null = null;
const pendingRequests = new Map<number, PendingRequest>();
let nextId = 1;
let buffer = "";
let pythonReady = false;
let pythonExitedEarly = false;

function pythonArgs(): [string, string[]] {
  const home = os.homedir();
  const base = path.join(home, ".voice-typer", "venv");
  const exe = process.platform === "win32"
    ? path.join(base, "Scripts", "python.exe")
    : path.join(base, "bin", "python3");
  return [exe, ["-m", "voice_typer.server.ipc_server"]];
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
  } else if (!mainWindow) {
    // No window yet — intercept "ready" to trigger window creation
    if (msg.type === "ready") {
      createMainWindow();
    }
  } else {
    BrowserWindow.getAllWindows().forEach((win) => {
      win.webContents.send("python-event", msg);
    });
  }
}

function sendToPython(msg: Record<string, unknown>): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const proc = pythonProcess;
    if (!proc || !proc.stdin) {
      reject(new Error("Python backend is not connected"));
      return;
    }
    const id = nextId++;
    (msg as Record<string, unknown>).id = id;
    pendingRequests.set(id, { resolve, reject });
    const line = JSON.stringify(msg) + "\n";
    proc.stdin.write(line);
    setTimeout(() => {
      if (pendingRequests.has(id)) {
        pendingRequests.delete(id);
        reject(new Error("Timeout"));
      }
    }, 5000);
  });
}

function createMainWindow() {
  if (mainWindow) return;
  pythonReady = true;
  mainWindow = new BrowserWindow({
    width: 1000,
    height: 700,
    minWidth: 800,
    minHeight: 500,
    icon: path.join(__dirname, "../../resources/icon.png"),
    frame: false,
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
    },
  });

  Menu.setApplicationMenu(null);

  mainWindow.on("maximize", () => broadcastMaximized(true));
  mainWindow.on("unmaximize", () => broadcastMaximized(false));

  if (process.env.ELECTRON_RENDERER_URL) {
    mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL);
  } else {
    mainWindow.loadFile(path.join(__dirname, "../renderer/index.html"));
  }
}

function startPython() {
  const [exe, args] = pythonArgs();
  pythonProcess = spawn(exe, args, {
    stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
  });

  pythonProcess.stdout!.on("data", (chunk: Buffer) => {
    buffer += chunk.toString();
    const lines = buffer.split("\n");
    buffer = lines.pop()!;
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        handleMessage(JSON.parse(line));
      } catch (e) {
        console.error("Invalid JSON from Python:", line);
      }
    }
  });

  pythonProcess.stderr!.on("data", (chunk: Buffer) => {
    console.error("Python stderr:", chunk.toString());
  });

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

  // Fallback: create window even without "ready" (backward compat)
  setTimeout(() => {
    if (!pythonReady && pythonProcess) {
      createMainWindow();
    }
  }, 30000);
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
  startPython();
  // Window is created when Python sends "ready", not here.
});

ipcMain.handle("python-call", async (_event, msg) => {
  if (!pythonProcess) {
    if (pythonExitedEarly) {
      throw new Error("Python backend exited early — another instance is running");
    }
    throw new Error("Python backend is not connected");
  }
  return await sendToPython(msg);
});

// ── Window control IPC (used by the custom title bar) ──────────────

ipcMain.handle("window:minimize", () => {
  mainWindow?.minimize();
});

ipcMain.handle("window:toggle-maximize", () => {
  if (!mainWindow) return false;
  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize();
  } else {
    mainWindow.maximize();
  }
  return mainWindow.isMaximized();
});

ipcMain.handle("window:close", () => {
  mainWindow?.close();
});

ipcMain.handle("window:is-maximized", () => {
  return mainWindow?.isMaximized() ?? false;
});

app.on("before-quit", () => stopPython());
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
