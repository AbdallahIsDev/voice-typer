// NEW-UX-015: ErrorBoundary — catches render errors so a single bad
// config or component crash doesn't white-screen the entire app.
//
// Previously, any uncaught exception in a React render (e.g. a config
// field with an unexpected type that causes a TypeError when the
// component tries to render it) would crash the entire renderer
// process, leaving the user with a blank white window and no way to
// recover short of killing the app.
//
// Usage: wrap the top-level <App /> in <ErrorBoundary> in main.tsx.

import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
	children: ReactNode;
	fallback?: ReactNode;
}

interface ErrorBoundaryState {
	hasError: boolean;
	error: Error | null;
}

export class ErrorBoundary extends Component<
	ErrorBoundaryProps,
	ErrorBoundaryState
> {
	constructor(props: ErrorBoundaryProps) {
		super(props);
		this.state = { hasError: false, error: null };
	}

	static getDerivedStateFromError(error: Error): ErrorBoundaryState {
		return { hasError: true, error };
	}

	componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
		// Log to console for debugging — the renderer process's console
		// is captured by Electron's main process and written to the log file.
		console.error("[ErrorBoundary] Caught render error:", error, errorInfo);
	}

	handleReset = (): void => {
		this.setState({ hasError: false, error: null });
	};

	render(): ReactNode {
		if (this.state.hasError) {
			if (this.props.fallback) {
				return this.props.fallback;
			}

			return (
				<div
					className="flex min-h-screen flex-col items-center justify-center gap-4 bg-(--bg-subtle) p-8 text-center"
					role="alert"
					aria-live="assertive"
				>
					<div className="space-y-2">
						<h1 className="text-2xl font-bold text-(--text-primary)">
							Something went wrong
						</h1>
						<p className="text-sm text-(--text-muted)">
							The app encountered an unexpected error. Your data is safe.
						</p>
					</div>
					<pre className="max-w-2xl overflow-auto rounded-lg border border-border bg-(--bg-card) p-4 text-left text-xs text-(--text-muted)">
						{this.state.error?.message ?? "Unknown error"}
					</pre>
					<div className="flex gap-2">
						<button
							type="button"
							onClick={this.handleReset}
							className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
						>
							Try Again
						</button>
						<button
							type="button"
							onClick={() => window.location.reload()}
							className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-(--text-primary) hover:bg-(--bg-card)"
						>
							Reload App
						</button>
					</div>
				</div>
			);
		}

		return this.props.children;
	}
}
