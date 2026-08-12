import React from "react";
import ReactDOM from "react-dom/client";
import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";
import { installGlobalErrorHandlers } from "@/lib/globalErrorHandler";
import App from "./App";
import "./index.css";

// ADR-0020 §6.3 (Phase 3 UI port): install the Tauri bridge BEFORE the
// React app mounts so `window.python` / `window.bubble` / `window.window_`
// are available when `usePython` and other hooks initialize. In Electron
// mode this is a no-op (the preload already installed the namespaces).
// Must come before any code that reads `window.python`.
//
// the side-effect lives in `./lib/tauri-bridge/install` (the sibling
// `install.ts` module). Previously this was a STATIC top-level import
// (`import "./lib/tauri-bridge/install"`) which pulled the entire Tauri
// core API surface (~1.4 MB) into the renderer bundle even under
// Electron, where the preload script (`src/preload/index.ts:19-117`)
// already installs `window.python` / `window.bubble` / `window.window_`
// via `contextBridge.exposeInMainWorld`.
//
// The dynamic `import()` below is gated on `window.__TAURI__?.core?.invoke`
// (the same check `isTauri()` in `lib/tauri-bridge/detect.ts` uses). Under
// Electron the gate is false, so Vite emits `install.ts` (and its
// `@tauri-apps/api` dependency graph) as a SEPARATE async chunk that is
// never fetched. Under Tauri the gate is true, the chunk is fetched, and
// `installTauriBridge()` runs before React mounts (top-level await
// guarantees ordering — `ReactDOM.createRoot().render()` below does not
// execute until the await resolves).
//
// The preload script is the source of truth for the Electron namespaces:
// it exposes `python` (`preload/index.ts:25`), `bubble` (`:19`), and
// `window_` (`:52`) via `contextBridge.exposeInMainWorld`. This dynamic
// import does NOT touch those — `installTauriBridge()` is a no-op when
// `isTauri()` returns false (Electron path).
//
// Top-level await is supported by Vite in ESM modules; the `<script
// type="module">` tag in `index.html` defers subsequent module scripts
// until this one resolves, so `main.tsx`'s `ReactDOM.createRoot().render()`
// does not run until the bridge is installed.
if (
	typeof window !== "undefined" &&
	(window as unknown as { __TAURI__?: { core?: { invoke?: unknown } } })
		.__TAURI__?.core?.invoke
) {
	await import("./lib/tauri-bridge/install");
}

//(combined): install the global `error`
// and `unhandledrejection` listeners BEFORE `ReactDOM.createRoot().render()`
// so the listeners are in place before any React render or effect runs.
// This catches:
//   - sync errors in module-level code that fire before React mounts
//   - unhandled promise rejections in `useEffect` that escape React's
//     ErrorBoundary (ErrorBoundary only catches render-phase errors; the
//     global handler catches the async ones)
//   - errors that would otherwise silently vanish — the user sees no toast
//     and the only trace is a dev-tools console message that disappears on
//     refresh.
//
// The function is idempotent — calling it twice is a no-op. The handler
// logs to `console.error` with a `[renderer:globalErrorHandler]` prefix (forwarded to the
// main-process log via `webContents.on("console-message")`) and shows a
// generic localized toast via `sonner.toast.error` (the toast is a no-op
// if the `<Toaster />` hasn't mounted yet — see lib/globalErrorHandler.ts
// for the defensive guard).
installGlobalErrorHandlers();

//(fix): explicit null check instead of `!` non-null assertion.
// If the root element is missing, fail loudly with a clear error message
// instead of crashing inside ReactDOM.createRoot.
const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found in index.html");

//wrap <App /> in <ErrorBoundary> so a render-time
// crash in any component (Settings, Models, History, etc.) shows a
// graceful fallback UI with "Try Again" / "Reload App" buttons instead of
// white-screening the entire renderer. The ErrorBoundary component already
//existed () but was never wired into the render tree — leaving
// the renderer vulnerable to single-component crashes taking down the
// whole app. See components/ErrorBoundary.tsx for the full fallback UI.
ReactDOM.createRoot(rootEl).render(
	<React.StrictMode>
		<ErrorBoundary>
			<App />
		</ErrorBoundary>
	</React.StrictMode>,
);
