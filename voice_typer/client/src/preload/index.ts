import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("python", {
  call: (msg: Record<string, unknown>) => ipcRenderer.invoke("python-call", msg),
  onEvent: (callback: (msg: Record<string, unknown>) => void) => {
    ipcRenderer.on("python-event", (_event, msg) => callback(msg as Record<string, unknown>));
  },
});
