import React from "react";
import ReactDOM from "react-dom/client";
import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";
import { installGlobalErrorHandlers } from "@/lib/globalErrorHandler";
import App from "./App";
import { ensureTauriBridgeInstalled } from "./lib/tauri-bridge/ensure";
import "./index.css";

// ADR-0020 §6.3 (Phase 3 UI port): install the Tauri bridge BEFORE the
// React app mounts so `window.python` / `window.bubble` / `window.window_`
// are available when `usePython` and other hooks initialize. The runtime
// gate (Tauri-only dynamic import, separate async chunk, never fetched
// under Electron where the preload already installed the namespaces) and
// its full rationale live in `./lib/tauri-bridge/ensure` — the single
// shared copy of a gate this entrypoint previously duplicated from
// `bubble-main.tsx`. Top-level await guarantees ordering — the
// `ReactDOM.createRoot().render()` below does not run until the bridge is
// installed.
await ensureTauriBridgeInstalled();

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
