import React from "react";
import ReactDOM from "react-dom/client";
import { Bubble } from "./Bubble";
import { ErrorBoundary } from "./components/feedback/ErrorBoundary";
import "./index.css";

// ADR-0020 §6.3 (Phase 3 UI port): install the Tauri bridge BEFORE the
// bubble React app mounts so `window.bubble` is available. In Electron
// mode the bubble preload (`src/preload/bubble.ts`) already installed
// it; this is a no-op. Must come before the `window.bubble?.signalReady`
// call below.
import "./lib/tauri-bridge";

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
