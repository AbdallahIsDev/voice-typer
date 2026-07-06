import React from "react";
import ReactDOM from "react-dom/client";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import App from "./App";
import "./index.css";

// ERR-ERR-005 (fix): explicit null check instead of `!` non-null assertion.
// If the root element is missing, fail loudly with a clear error message
// instead of crashing inside ReactDOM.createRoot.
const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found in index.html");

// ERR-ERR-006 (Round 0): wrap <App /> in <ErrorBoundary> so a render-time
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
