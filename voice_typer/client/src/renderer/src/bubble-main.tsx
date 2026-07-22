import React from "react";
import ReactDOM from "react-dom/client";
import { Bubble } from "./Bubble";
import { ErrorBoundary } from "./components/feedback/ErrorBoundary";
import { installGlobalErrorHandlers } from "./lib/globalErrorHandler";
import "./index.css";

// ADR-0020 §6.3 (Phase 3 UI port): install the Tauri bridge BEFORE the
// bubble React app mounts so `window.bubble` is available. In Electron
// mode the bubble preload (`src/preload/bubble.ts`) already installed
// it; this is a no-op. Must come before the `window.bubble?.signalReady`
// call below.
import "./lib/tauri-bridge";

// PVT-G5-016 / G4-CR-10 (combined): install the global `error` and
// `unhandledrejection` listeners BEFORE `ReactDOM.createRoot().render(...)`
// so async errors that escape React's ErrorBoundary (e.g. unhandled promise
// rejections in `useEffect`) are caught and logged instead of silently
// swallowed. The bubble is an always-on-top transparent overlay — an
// unhandled rejection that React doesn't catch would otherwise leave the
// overlay in an undefined state, and a render-time crash without an
// ErrorBoundary leaves a stuck invisible overlay (see P1-2c comment below).
// The global handler is the safety net that also surfaces async-effect
// rejections via toast + console.error (forwarded to the main-process log).
//
// `installGlobalErrorHandlers()` is idempotent — calling it again from
// bubble-main.tsx is a no-op if main.tsx already installed the handlers
// in the same renderer process (which it doesn't — each BrowserWindow
// has its own JS context). Safe to call before
// `window.bubble?.signalReady?.()` below.
installGlobalErrorHandlers();

// console.warn('[bubble renderer] mounting')

// Signal the main process that we're mounted and ready to receive
// level events.  Used for diagnostics and to mark the window as
// page-ready in the main process.
window.bubble?.signalReady?.();

// ERR-ERR-005 (fix): explicit null check instead of `!` non-null assertion.
const bubbleRootEl = document.getElementById(
	"bubble-root",
) as HTMLElement | null;
if (!bubbleRootEl)
	throw new Error("Bubble root element #bubble-root not found in bubble.html");

// P1-2c (Round 0 forward-port): wrap <Bubble /> in
// <ErrorBoundary fallback={null}>. The bubble window is an
// always-on-top transparent overlay — if its render crashes without an
// error boundary, React unmounts the tree but the BrowserWindow itself
// stays alive, leaving a stuck invisible overlay that intercepts
// clicks. Rendering null on error makes the overlay visually disappear
// (and the ErrorBoundary logs the caught error to the renderer console,
// which Electron surfaces in the diagnostic log).
ReactDOM.createRoot(bubbleRootEl).render(
	<React.StrictMode>
		<ErrorBoundary fallback={null}>
			<Bubble />
		</ErrorBoundary>
	</React.StrictMode>,
);
