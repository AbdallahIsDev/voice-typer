import { contextBridge, ipcRenderer } from "electron";

// SEC-026: dedicated preload for the bubble window. The bubble renderer
// only needs the `bubble:` IPC channels (level, show, hide, draggable,
// position, drag). It does NOT need access to `python.call` (which can
// send arbitrary IPC commands to the Python backend) or `window_.*`
// (which can minimize / maximize / close the main window and trigger
// history/vocabulary exports).
//
// Splitting the preload so the bubble renderer can only call the small
// set of bubble-specific channels means that even if the bubble is
// compromised (e.g. an XSS payload in the waveform renderer), the
// attacker cannot invoke `python.call({type:"quit_app"})` or
// `python.call({type:"set_config", data:{...}})` to take over the
// app. Previously both windows loaded `preload/index.ts` which
// exposed all three namespaces.
//
// The `window.bubble` surface is now built by the shared
// `makeBubbleApi` factory in `./_bubble-channels.ts`. The factory is
// called with `includeRestricted: true` because the bubble window is
// the ONLY frame allowed to invoke the restricted channels
// (`onSetState` / `onConfig` / `hideComplete` / `resizeTo` /
// `toggleDictation` / `dismiss`). The main-renderer preload
// (`preload/index.ts`) calls the same factory with
// `includeRestricted: false` so a compromised main renderer cannot
// reach the bubble-only channels (defense-in-depth — the
// main-process handlers also assert the sender's frame label, but the
// preload gate is the first line).
import { makeBubbleApi } from "./_bubble-channels";

contextBridge.exposeInMainWorld(
	"bubble",
	makeBubbleApi(ipcRenderer, { includeRestricted: true }),
);
