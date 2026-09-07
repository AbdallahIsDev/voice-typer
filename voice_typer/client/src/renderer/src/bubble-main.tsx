import React from "react";
import ReactDOM from "react-dom/client";
import { Bubble } from "./Bubble";
import { ErrorBoundary } from "./components/feedback/ErrorBoundary";
import { installGlobalErrorHandlers } from "./lib/globalErrorHandler";
import { ensureTauriBridgeInstalled } from "./lib/tauri-bridge/ensure";
import "./index.css";

// ADR-0020 §6.3 (Phase 3 UI port): install the Tauri bridge BEFORE the
// bubble React app mounts so `window.bubble` is available. The runtime
// gate (Tauri-only dynamic import, separate async chunk, never fetched
// under Electron where the bubble preload already installed the
// namespace) and its full rationale live in `./lib/tauri-bridge/ensure` —
// the single shared copy of a gate this entrypoint previously duplicated
// from `main.tsx`. In Electron mode the gate is false and this is a
// no-op. Top-level await guarantees ordering — the
// `ReactDOM.createRoot().render()` call below does not run until the
// bridge is installed, so the `window.bubble?.signalReady` call further
// down sees a live bridge under Tauri.
await ensureTauriBridgeInstalled();

// Install the global `error` and `unhandledrejection` listeners BEFORE
// `ReactDOM.createRoot().render(...)` so async errors that escape
// React's ErrorBoundary (e.g. unhandled promise rejections in
// `useEffect`) are caught and logged instead of silently swallowed. The
// bubble is an always-on-top transparent overlay — an unhandled
// rejection that React doesn't catch would otherwise leave the overlay
// in an undefined state, and a render-time crash without an
// ErrorBoundary leaves a stuck invisible overlay (see the
// `<ErrorBoundary fallback={null}>` rationale below). The global
// handler is the safety net that also surfaces async-effect rejections
// via toast + console.error (forwarded to the main-process log).
//
// `installGlobalErrorHandlers()` is idempotent — calling it again from
// bubble-main.tsx is a no-op if main.tsx already installed the handlers
// in the same renderer process (which it doesn't — each BrowserWindow
// has its own JS context). Safe to call before
// `window.bubble?.signalReady?.()` below.
installGlobalErrorHandlers();

// Signal the main process that we're mounted and ready to receive
// level events.  Used for diagnostics and to mark the window as
// page-ready in the main process.
window.bubble?.signalReady?.();

// Explicit null check instead of `!` non-null assertion.
const bubbleRootEl = document.getElementById(
	"bubble-root",
) as HTMLElement | null;
if (!bubbleRootEl)
	throw new Error("Bubble root element #bubble-root not found in bubble.html");

// Wrap <Bubble /> in <ErrorBoundary fallback={null}>. The bubble window
// is an always-on-top transparent overlay — if its render crashes
// without an error boundary, React unmounts the tree but the
// BrowserWindow itself stays alive, leaving a stuck invisible overlay
// that intercepts clicks. Rendering null on error makes the overlay
// visually disappear (and the ErrorBoundary logs the caught error to
// the renderer console, which Electron surfaces in the diagnostic log).
ReactDOM.createRoot(bubbleRootEl).render(
	<React.StrictMode>
		<ErrorBoundary fallback={null}>
			<Bubble />
		</ErrorBoundary>
	</React.StrictMode>,
);
