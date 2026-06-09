# Electron + stdio JSON-lines IPC Architecture

## Why This Pattern

The current architecture has two Python processes communicating via file polling (`flet_state.json`) and simulated hotkeys (`keybd_event`). This works but is fragile and indirect.

The stdio JSON-lines pattern is the minimal viable bridge between Electron and a Python backend:

- **No network stack** — no ports, no firewalls, no WebSocket handshake, no reconnection logic
- **No extra dependencies** — no FastAPI, no uvicorn, no websockets in Python
- **Process lifecycle is trivial** — Electron spawns Python, writes to its stdin, reads from its stdout. Kill the process = clean shutdown
- **Zero latency** — pipes are kernel buffers, not network sockets
- **Single-file Python server** — the entire IPC layer fits in ~30 lines

## Architecture

```
┌───────────────────────────────────────┐
│  Electron (Node.js)                   │
│                                       │
│  ┌─────────────────────────────────┐  │
│  │  Main Process                   │  │
│  │  - spawns python.exe            │  │
│  │  - writes JSON to stdin         │  │
│  │  - reads JSON from stdout       │  │
│  │  - kills on app quit            │  │
│  └─────────────────────────────────┘  │
│                    │                   │
│              ipcMain/ipcRenderer       │
│                    │                   │
│  ┌─────────────────────────────────┐  │
│  │  Renderer (React/Vue/Svelte)   │  │
│  │  - HTML/CSS/JS UI              │  │
│  │  - no direct Python access     │  │
│  └─────────────────────────────────┘  │
└──────────────────────┬────────────────┘
                       │
          stdin/stdout (pipe)
          JSON objects separated by \n
                       │
┌──────────────────────┴────────────────┐
│  Python (voice_typer.server)          │
│                                       │
│  ┌─────────────────────────────────┐  │
│  │  IPC Loop                       │  │
│  │  for line in sys.stdin:         │  │
│  │    msg = json.loads(line)       │  │
│  │    result = dispatch(msg)       │  │
│  │    print(json.dumps(result))    │  │
│  │    sys.stdout.flush()           │  │
│  └─────────────────────────────────┘  │
│                                       │
│  ┌─────────────────────────────────┐  │
│  │  VoiceTyperApp (unchanged)      │  │
│  │  - Recorder                     │  │
│  │  - Transcriber                  │  │
│  │  - Hotkeys (Win32)              │  │
│  │  - Config                       │  │
│  │  - History DB                   │  │
│  │  - Clipboard                    │  │
│  │  - Tray icon (pystray)          │  │
│  └─────────────────────────────────┘  │
└───────────────────────────────────────┘
```

## Protocol

Every message is a single JSON object on one line, terminated by `\n`.

### Request (Electron → Python)

```json
{"id": 1, "type": "get_status"}
{"id": 2, "type": "toggle_dictation"}
{"id": 3, "type": "get_config"}
{"id": 4, "type": "set_config", "data": {"hotkey": "F2"}}
{"id": 5, "type": "get_history", "data": {"limit": 50}}
{"id": 6, "type": "get_today_stats"}
```

### Response (Python → Electron)

```json
{"id": 1, "type": "status", "data": {"status": "recording", "model": "base"}}
{"id": 2, "type": "ack"}
{"id": 3, "type": "config", "data": {"hotkey": "F2", "theme": "dark", ...}}
{"id": 4, "type": "ack"}
{"id": 5, "type": "history", "data": [{"text": "...", "timestamp": "..."}]}
```

### Push Events (Python → Electron, no `id`)

Python can push events without a corresponding request:

```json
{"type": "status_change", "data": {"status": "recording"}}
{"type": "transcript", "data": {"text": "...", "is_final": false}}
{"type": "error", "data": {"message": "Model load failed"}}
```

Push messages have no `id` field. Electron treats any message without `id` as a push event.

### Error Response

```json
{"id": 7, "type": "error", "data": {"message": "Unknown command: frobnicate"}}
```

## Implementation

### Python side: `voice_typer/server.py`

```python
"""JSON-lines IPC server over stdin/stdout."""
import json
import sys
import threading
from voice_typer.app import VoiceTyperApp


class IPCServer:
    """Reads JSON commands from stdin, writes JSON responses to stdout."""

    def __init__(self, app: VoiceTyperApp):
        self.app = app
        self._running = False

    def start(self):
        self._running = True
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def _run(self):
        for line in sys.stdin:
            if not self._running:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                result = self._dispatch(msg)
                self._send(result)
            except json.JSONDecodeError:
                self._send({"type": "error", "data": {"message": "invalid JSON"}})

    def _dispatch(self, msg):
        cmd = msg.get("type")
        data = msg.get("data")
        resp = {"id": msg.get("id")}

        if cmd == "get_status":
            resp["type"] = "status"
            resp["data"] = {"status": self.app.status}
        elif cmd == "toggle_dictation":
            self.app.toggle_dictation()
            resp["type"] = "ack"
        elif cmd == "get_config":
            resp["type"] = "config"
            resp["data"] = self.app.config.__dict__
        elif cmd == "set_config":
            for k, v in (data or {}).items():
                setattr(self.app.config, k, v)
            self.app.config.save()
            resp["type"] = "ack"
        elif cmd == "get_history":
            resp["type"] = "history"
            resp["data"] = self.app.history.get_recent(data.get("limit", 50))
        elif cmd == "get_today_stats":
            resp["type"] = "today_stats"
            resp["data"] = self.app.history.get_today_stats()
        else:
            resp["type"] = "error"
            resp["data"] = {"message": f"Unknown command: {cmd}"}

        return resp

    def push(self, msg: dict):
        """Send an unsolicited event to Electron."""
        self._send(msg)

    def _send(self, msg: dict):
        line = json.dumps(msg)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


# ── Entry point for Electron subprocess ─────────────────────────────────
def main():
    app = VoiceTyperApp()
    server = IPCServer(app)
    server.start()
    # Push initial status
    server.push({"type": "status_change", "data": {"status": app.status}})
    # Block main thread (tray loop, etc.)
    app.run()


if __name__ == "__main__":
    main()
```

### Electron side: `main.js`

```javascript
const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");

let pythonProcess = null;
let pendingRequests = new Map();
let nextId = 1;
let buffer = "";

function startPython() {
  const pythonPath = path.join(process.resourcesPath, "python", "python.exe");
  const scriptPath = path.join(__dirname, "..", "voice_typer", "server.py");

  pythonProcess = spawn(pythonPath, [scriptPath], {
    stdio: ["pipe", "pipe", "pipe"],
  });

  // Read stdout line by line
  pythonProcess.stdout.on("data", (chunk) => {
    buffer += chunk.toString();
    const lines = buffer.split("\n");
    buffer = lines.pop(); // keep incomplete line
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

  pythonProcess.stderr.on("data", (chunk) => {
    console.error("Python stderr:", chunk.toString());
  });

  pythonProcess.on("exit", (code) => {
    console.log("Python process exited:", code);
    pythonProcess = null;
  });
}

function handleMessage(msg) {
  if (msg.id != null) {
    // Response to a pending request
    const resolve = pendingRequests.get(msg.id);
    if (resolve) {
      pendingRequests.delete(msg.id);
      resolve(msg);
    }
  } else {
    // Push event — forward to renderer
    BrowserWindow.getAllWindows().forEach((win) => {
      win.webContents.send("python-event", msg);
    });
  }
}

function sendToPython(msg) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    msg.id = id;
    pendingRequests.set(id, resolve);
    const line = JSON.stringify(msg) + "\n";
    pythonProcess.stdin.write(line);
    setTimeout(() => {
      if (pendingRequests.has(id)) {
        pendingRequests.delete(id);
        reject(new Error("Timeout"));
      }
    }, 5000);
  });
}

// ── App lifecycle ───────────────────────────────────────────────────────

app.whenReady().then(() => {
  startPython();

  const win = new BrowserWindow({
    width: 1000,
    height: 700,
    frame: false, // custom title bar
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
    },
  });

  win.loadFile("index.html");
});

app.on("before-quit", () => {
  if (pythonProcess) {
    pythonProcess.kill();
    pythonProcess = null;
  }
});

// ── IPC handlers for renderer ───────────────────────────────────────────

ipcMain.handle("python-call", async (event, msg) => {
  return await sendToPython(msg);
});
```

### Electron side: `preload.js`

```javascript
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("python", {
  call: (msg) => ipcRenderer.invoke("python-call", msg),
  onEvent: (callback) => {
    ipcRenderer.on("python-event", (event, msg) => callback(msg));
  },
});
```

### Electron side: renderer usage

```javascript
// Get initial status
const status = await window.python.call({ type: "get_status" });
console.log(status.data); // { status: "idle", model: "base" }

// Listen for push events
window.python.onEvent((msg) => {
  if (msg.type === "status_change") {
    updateUI(msg.data.status);
  }
  if (msg.type === "transcript") {
    appendTranscript(msg.data.text);
  }
});

// Toggle dictation
document.getElementById("mic-btn").onclick = async () => {
  await window.python.call({ type: "toggle_dictation" });
};
```

## Packaging

### Option A: Sidecar (recommended)

Bundle Python as a PyInstaller single `.exe` sidecar:

```
# Build the Python backend
pyinstaller --onefile --name voice-typer-server voice_typer/server.py
```

Place the `.exe` in Electron's `resources/`:

```
electron-app/
├── package.json
├── main.js
├── preload.js
├── renderer/
│   ├── index.html
│   ├── styles.css
│   └── app.js
└── resources/
    └── voice-typer-server.exe   ← PyInstaller output
```

### Option B: Bundled Python

Ship a minimal `python/` folder with the embedded Python distribution + only the needed packages. This avoids PyInstaller complications.

## Porting the Flet Screens

Your 13 Flet screens each correspond to an Electron route:

| Flet screen | Electron route | Backend IPC |
|---|---|---|
| home | `/` | `get_status`, `get_today_stats` |
| history | `/history` | `get_history` |
| templates | `/templates` | `get_templates` |
| vocabulary | `/vocabulary` | `get_vocabulary` |
| models | `/models` | `get_models`, `set_model` |
| microphone | `/microphone` | `get_microphones`, `set_microphone` |
| privacy | `/privacy` | `get_privacy_config` |
| settings | `/settings` | `get_config`, `set_config` |

Each Electron screen is a standalone HTML/CSS/JS component that calls `window.python.call()` for data and `window.python.onEvent()` for live updates.

## What Stays in Python

- Audio recording (sounddevice callback thread)
- ASR inference (faster-whisper)
- Win32 global hotkeys
- System tray icon (pystray)
- Clipboard operations
- History database (SQLite)
- Config management

## What Moves to Electron

- All UI rendering
- Window management (title bar, maximize, minimize, close, resize)
- Dark/light theme detection and switching
- Animation, transitions, CSS variables
- State management for UI
- Routing between screens

## Migration Strategy

1. **Build the Electron shell** — window, title bar, Python subprocess management, IPC bridge
2. **Port one screen at a time** — start with Settings (simplest, mostly config read/write)
3. **Run Flet alongside** — keep launching Flet for screens not yet ported
4. **Drop Flet** when all screens are ported

This lets you ship incrementally instead of a big-bang rewrite.

## Comparison Summary

| Concern | Current (Flet + JSON file) | Stdio JSON-lines (Electron) |
|---|---|---|
| IPC mechanism | File polling every 1s | In-process pipe, sub-ms |
| UI tech | Flet (Python) | HTML/CSS/JS |
| Bundle size | ~50 MB (Python venv) | ~80 MB (Electron + Python sidecar) |
| Dev languages | Python only | Python (backend) + JS/HTML/CSS (UI) |
| UI control | Limited by Flet widgets | Full (CSS, any JS framework) |
| Subprocess mgmt | Custom (3 kill strategies) | `child_process.spawn` + `kill()` |
| Reliability | Fragile (orphan processes, stale JSON) | Reliable (pipe lifecycle tied to Electron) |
