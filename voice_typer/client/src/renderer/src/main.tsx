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
import "./lib/tauri-bridge";

// PVT-009 / G4-CR-10 / PVT-G5-016 (combined): install the global `error`
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
// logs to `console.error` with a `[Renderer]` prefix (forwarded to the
// main-process log via `webContents.on("console-message")`) and shows a
// generic localized toast via `sonner.toast.error` (the toast is a no-op
// if the `<Toaster />` hasn't mounted yet — see lib/globalErrorHandler.ts
// for the defensive guard).
//
// Placement note: ESM static imports are hoisted, so `./lib/tauri-bridge`
// above executes BEFORE this call. We accept that limitation: the primary
// failure surface is React render + async effects, both of which happen
// AFTER this install call. Module-eval failures in tauri-bridge.ts are
// unlikely (it has internal try/catch) and would be visible via a blank
// renderer anyway.
installGlobalErrorHandlers();

// ERR-ERR-005 (fix): explicit null check instead of `!` non-null assertion.
// If the root element is missing, fail loudly with a clear error message
// instead of crashing inside ReactDOM.createRoot.
const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found in index.html");

// ERR-ERR-006: wrap <App /> in <ErrorBoundary> so a render-time
// crash in any component (Settings, Models, History, etc.) shows a
// graceful fallback UI with "Try Again" / "Reload App" buttons instead of
// white-screening the entire renderer. The ErrorBoundary component already
// existed (NEW-UX-015) but was never wired into the render tree — leaving
// the renderer vulnerable to single-component crashes taking down the
// whole app. See components/ErrorBoundary.tsx for the full fallback UI.
ReactDOM.createRoot(rootEl).render(
	<React.StrictMode>
		<ErrorBoundary>
			<App />
		</ErrorBoundary>
	</React.StrictMode>,
);
