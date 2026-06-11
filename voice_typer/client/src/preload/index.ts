import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("python", {
  call: (msg: Record<string, unknown>) => ipcRenderer.invoke("python-call", msg),
  onEvent: (callback: (msg: Record<string, unknown>) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, msg: unknown) => callback(msg as Record<string, unknown>);
    ipcRenderer.on("python-event", handler);
    return () => { ipcRenderer.removeListener("python-event", handler); };
  },
});
