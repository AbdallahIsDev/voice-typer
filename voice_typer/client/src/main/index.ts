import { app, BrowserWindow, ipcMain } from "electron";
import { spawn, ChildProcess } from "child_process";
import path from "path";
import os from "os";

let pythonProcess: ChildProcess | null = null;
const pendingRequests = new Map<number, (value: unknown) => void>();
let nextId = 1;
let buffer = "";

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
    const resolve = pendingRequests.get(msg.id as number);
    if (resolve) {
      pendingRequests.delete(msg.id as number);
      resolve(msg);
    }
  } else {
    BrowserWindow.getAllWindows().forEach((win) => {
      win.webContents.send("python-event", msg);
    });
  }
}

function sendToPython(msg: Record<string, unknown>): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    (msg as Record<string, unknown>).id = id;
    pendingRequests.set(id, resolve);
    const line = JSON.stringify(msg) + "\n";
    pythonProcess!.stdin!.write(line);
    setTimeout(() => {
      if (pendingRequests.has(id)) {
        pendingRequests.delete(id);
        reject(new Error("Timeout"));
      }
    }, 5000);
  });
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
    pythonProcess = null;
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

app.whenReady().then(() => {
  startPython();

  const win = new BrowserWindow({
    width: 1000,
    height: 700,
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
    },
  });

  if (process.env.ELECTRON_RENDERER_URL) {
    win.loadURL(process.env.ELECTRON_RENDERER_URL);
  } else {
    win.loadFile(path.join(__dirname, "../renderer/index.html"));
  }
});

ipcMain.handle("python-call", async (_event, msg) => {
  return await sendToPython(msg);
});

app.on("before-quit", () => stopPython());
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
