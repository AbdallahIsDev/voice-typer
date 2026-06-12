import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("python", {
  call: (msg: Record<string, unknown>) => ipcRenderer.invoke("python-call", msg),
  onEvent: (callback: (msg: Record<string, unknown>) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, msg: unknown) => callback(msg as Record<string, unknown>);
    ipcRenderer.on("python-event", handler);
    return () => { ipcRenderer.removeListener("python-event", handler); };
  },
});

contextBridge.exposeInMainWorld("window_", {
  minimize: () => ipcRenderer.invoke("window:minimize"),
  toggleMaximize: () => ipcRenderer.invoke("window:toggle-maximize") as Promise<boolean>,
  close: () => ipcRenderer.invoke("window:close"),
  isMaximized: () => ipcRenderer.invoke("window:is-maximized") as Promise<boolean>,
  onMaximizedChanged: (callback: (maximized: boolean) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, maximized: unknown) => callback(Boolean(maximized));
    ipcRenderer.on("window:maximized-changed", handler);
    return () => { ipcRenderer.removeListener("window:maximized-changed", handler); };
  },
});
