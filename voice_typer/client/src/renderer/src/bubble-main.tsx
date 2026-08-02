import React from "react";
import ReactDOM from "react-dom/client";
import { Bubble } from "./Bubble";
import { ErrorBoundary } from "./components/feedback/ErrorBoundary";
import { installGlobalErrorHandlers } from "./lib/globalErrorHandler";
import "./index.css";

// ADR-0020 §6.3 (Phase 3 UI port): install the Tauri bridge BEFORE the
// bubble React app mounts so `window.bubble` is available. In Electron
// mode the bubble preload (`src/preload/index.ts`) already installed
// it; this is a no-op. Must come before the `window.bubble?.signalReady`
// call below.
//
// the side-effect lives in `./lib/tauri-bridge/install` (the sibling
// `install.ts` module). Previously this was a STATIC top-level import
// (`import "./lib/tauri-bridge/install"`) which pulled the entire Tauri
// core API surface (~1.4 MB) into the bubble renderer bundle even under
// Electron, where the preload script (`src/preload/index.ts:19-22`)
// already installs `window.bubble` via `contextBridge.exposeInMainWorld`.
//
// The dynamic `import()` below is gated on `window.__TAURI__?.core?.invoke`
// (the same check `isTauri()` in `lib/tauri-bridge/detect.ts` uses). Under
// Electron the gate is false, so Vite emits `install.ts` (and its
// `@tauri-apps/api` dependency graph) as a SEPARATE async chunk that is
// never fetched. Under Tauri the gate is true, the chunk is fetched, and
// `installTauriBridge()` runs before the bubble React tree mounts
// (top-level await guarantees ordering — the `ReactDOM.createRoot().render()`
// call below does not execute until the await resolves).
//
// The preload script is the source of truth for the Electron `bubble`
// namespace (`preload/index.ts:19-22`). This dynamic import does NOT touch
// it — `installTauriBridge()` is a no-op when `isTauri()` returns false
// (Electron path).
if (
	typeof window !== "undefined" &&
	(window as unknown as { __TAURI__?: { core?: { invoke?: unknown } } })
		.__TAURI__?.core?.invoke
) {
	await import("./lib/tauri-bridge/install");
}

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
