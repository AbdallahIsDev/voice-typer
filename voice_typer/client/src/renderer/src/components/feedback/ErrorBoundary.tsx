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
//
// PVT-fix #11: the fallback now exposes three recovery affordances in
// addition to the existing "Try Again" / "Reload App" buttons:
//
//   - "Copy error" — copies the error name, message, and component
//     stack to the clipboard so users can paste it into a bug report
//     without having to dig through log files.  The button label
//     briefly flips to "Copied!" for affirmative feedback (the
//     toast system can't be relied on here — the same render crash
//     that triggered the boundary may have broken the toaster).
//
//   - "Open logs" — invokes the main process's ``window:open-logs``
//     IPC handler so the user can attach the full log file to a
//     support request.  Same path as Settings → Troubleshooting →
//     Open Log Folder.
//
//   - "Reset settings" — escape hatch for the common case where a
//     bad config value (e.g. a malformed theme token, an out-of-range
//     number field) is what crashed the renderer.  Asks the Python
//     backend to re-publish its default Config dataclass, applies it
//     via ``set_config``, clears any renderer-side localStorage that
//     might also be poisoned, and reloads.  This mirrors the
//     Settings → Reset to Defaults flow but is callable from the
//     error UI without needing the Settings page to render.
//
// G4-M-69: ``componentDidCatch`` forwards the caught error to the
// main process for explicit persistence in
// ``electron-renderer-errors.log`` (separate from the
// ``console-message`` path so React's ``componentStack`` is
// preserved — ``console.error`` serializes ``errorInfo`` to a string
// and loses the structured component-tree trace).

import { Component, type ErrorInfo, type ReactNode } from "react";
import { t } from "@/i18n/i18n";

interface ErrorBoundaryProps {
	children: ReactNode;
	fallback?: ReactNode;
}

interface ErrorBoundaryState {
	hasError: boolean;
	error: Error | null;
	errorInfo: ErrorInfo | null;
	copied: boolean;
	resetting: boolean;
	resetFailed: boolean;
}

export class ErrorBoundary extends Component<
	ErrorBoundaryProps,
	ErrorBoundaryState
> {
	constructor(props: ErrorBoundaryProps) {
		super(props);
		this.state = {
			hasError: false,
			error: null,
			errorInfo: null,
			copied: false,
			resetting: false,
			resetFailed: false,
		};
	}

	static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
		return { hasError: true, error };
	}

	componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
		// G4-M-69: log to console for debugging. The main-window
		// `console-message` handler (G4-M-67) persists level>=3
		// (ERROR) console output to `electron-renderer-errors.log`
		// so this `console.error` automatically lands in the file
		// — the previous comment claiming "the renderer process's
		// console is captured by Electron's main process and
		// written to the log file" was misleading because the
		// console-message handler only re-emitted to the terminal
		// (lost when the terminal closed); it did NOT persist to
		// disk before G4-M-67.
		console.error("[ErrorBoundary] Caught render error:", error, errorInfo);

		// G4-M-69: forward the caught error to the main process
		// for explicit persistence in `electron-renderer-errors.log`
		// (separate from the console-message path so React's
		// `componentStack` is preserved — the console.error above
		// serializes `errorInfo` to a string, losing the structured
		// component tree trace). The IPC call is fire-and-forget:
		// if the preload doesn't expose `logError` (Tauri mode) or
		// the main process is unreachable, the `.catch` swallow is
		// acceptable because the console.error above already
		// surfaced the error to the dev-tools + main-process
		// console forwarding path.
		try {
			window.window_
				?.logError?.({
					kind: "react-render",
					message: error.message,
					stack: error.stack,
					componentStack: errorInfo.componentStack ?? undefined,
				})
				.catch((err: unknown) => {
					// Best-effort: never let the persistence
					// path crash the ErrorBoundary itself.
					console.warn("[ErrorBoundary] logError IPC failed:", err);
				});
		} catch (err) {
			// Synchronous throw (e.g. `window.window_` is
			// undefined and `?.` short-circuit didn't fire
			// for some reason). Same swallow rationale.
			console.warn("[ErrorBoundary] logError call failed:", err);
		}

		// Persist errorInfo in state so the "Copy error" button
		// (PVT-fix #11) can include the React component stack in
		// the pasted bug-report blob.
		this.setState({ errorInfo });
	}

	handleReset = (): void => {
		this.setState({
			hasError: false,
			error: null,
			errorInfo: null,
			copied: false,
			resetting: false,
			resetFailed: false,
		});
	};

	handleReload = (): void => {
		window.location.reload();
	};

	handleCopyError = (): void => {
		const { error, errorInfo } = this.state;
		// Build a structured report so the pasted blob is useful to
		// maintainers without the user having to assemble anything.
		const lines: string[] = [];
		if (error) {
			lines.push(`${error.name}: ${error.message}`);
			if (error.stack) lines.push(error.stack);
		} else {
			lines.push(t("errorBoundary.unknownError"));
		}
		if (errorInfo?.componentStack) {
			// route the label through the i18n catalog so it adapts to
			// the user's UI locale (previously hardcoded as
			// `"\nComponent stack:"`). Preserve the leading newline so the
			// pasted bug-report blob keeps the same visual separation between
			// the JS stack and the React component tree.
			lines.push(`\n${t("errorBoundary.componentStackLabel")}`);
			lines.push(String(errorInfo.componentStack));
		}
		const payload = lines.join("\n");
		try {
			// ``navigator.clipboard.writeText`` is available in all
			// modern Chromium builds (Electron included).  Fall back
			// to a textarea-select hack if it throws (e.g. clipboard
			// API denied by a sandboxed context).
			if (navigator?.clipboard?.writeText) {
				navigator.clipboard
					.writeText(payload)
					.then(() => this.flashCopied())
					.catch(() => this.copyViaTextarea(payload));
			} else {
				this.copyViaTextarea(payload);
			}
		} catch {
			this.copyViaTextarea(payload);
		}
	};

	copyViaTextarea = (payload: string): void => {
		try {
			const ta = document.createElement("textarea");
			ta.value = payload;
			ta.setAttribute("readonly", "");
			ta.style.position = "absolute";
			ta.style.left = "-9999px";
			document.body.appendChild(ta);
			ta.select();
			document.execCommand("copy");
			document.body.removeChild(ta);
			this.flashCopied();
		} catch (e) {
			// Last-resort: leave the error text on screen so the
			// user can manually select + copy from the <pre>.
			console.warn("[ErrorBoundary] clipboard fallback copy failed:", e);
		}
	};

	flashCopied = (): void => {
		this.setState({ copied: true });
		window.setTimeout(() => {
			this.setState({ copied: false });
		}, 2000);
	};

	handleOpenLogs = (): void => {
		// Best-effort: invoke the main process's ``window:open-logs``
		// IPC handler (same path as Settings → Troubleshooting).  The
		// main process opens the OS file manager at the log folder.
		// Errors are swallowed because there's no UI to surface them
		// from inside a crashed renderer — the user can still copy
		// the error text manually as a fallback.
		try {
			void window.window_?.openLogs?.();
		} catch (err) {
			console.error("[ErrorBoundary] Failed to open logs:", err);
		}
	};

	handleResetSettings = (): void => {
		// Escape hatch: try to reset the backend Config to defaults
		// (which often clears whatever bad value crashed the render),
		// then unconditionally clear renderer-side localStorage (which
		// may also hold poisoned state like a malformed custom theme),
		// then reload the window so the renderer re-mounts against
		// the freshly-defaulted config.
		this.setState({ resetting: true, resetFailed: false });
		const tryReset = async (): Promise<void> => {
			try {
				const defaults = (await window.python?.call({
					type: "get_defaults",
				})) as Record<string, unknown> | undefined;
				if (defaults && typeof defaults === "object") {
					// Filter out redacted API-key sentinels so we
					// don't overwrite the user's real keys with
					// "<redacted>" placeholders (mirrors the
					// Settings → Reset to Defaults flow).
					const safe: Record<string, unknown> = {};
					for (const [k, v] of Object.entries(defaults)) {
						if (v === "<redacted>") continue;
						if (
							[
								"schema_version",
								"wayland_warned",
								"onboarding_completed",
							].includes(k)
						)
							continue;
						safe[k] = v;
					}
					await window.python?.call({
						type: "set_config",
						data: safe,
					});
				}
			} catch (err) {
				console.error(
					"[ErrorBoundary] Backend reset failed, falling back to localStorage clear + reload:",
					err,
				);
				// Don't bail — the localStorage clear + reload
				// below is still a useful escape hatch even if
				// the Python round-trip failed.
				this.setState({ resetFailed: true });
			}
			try {
				localStorage.clear();
			} catch (e) {
				// Ignore — some sandboxed contexts disable localStorage.
				console.warn("[ErrorBoundary] localStorage.clear failed:", e);
			}
			window.location.reload();
		};
		void tryReset();
	};

	render(): ReactNode {
		if (this.state.hasError) {
			if (this.props.fallback) {
				return this.props.fallback;
			}

			const errorMessage =
				this.state.error?.message ?? t("errorBoundary.unknownError");

			return (
				<div
					className="flex min-h-screen flex-col items-center justify-center gap-4 bg-(--bg-subtle) p-8 text-center"
					role="alert"
					aria-live="assertive"
				>
					<div className="space-y-2">
						<h1 className="text-2xl font-bold text-(--text-primary)">
							{t("errorBoundary.title")}
						</h1>
						<p className="text-sm text-(--text-muted)">
							{t("errorBoundary.description")}
						</p>
					</div>
					{/* user-friendly summary placed ABOVE the technical <pre>
					    so non-developer users see the recommended recovery path
					    before the raw error message. The raw stack trace below is
					    preserved for bug-report copy-paste but is no longer the
					    first thing the user reads. */}
					<p className="max-w-2xl text-sm text-(--text-muted)">
						{t("errorBoundary.configCrashHint")}
					</p>
					<pre className="max-w-2xl overflow-auto rounded-lg border border-border bg-(--bg-subtle) p-4 text-left text-xs text-(--text-muted)">
						{errorMessage}
					</pre>
					{/* sr-only hint wired to the Reset settings button via
					    aria-describedby so screen-reader / keyboard users hear
					    the rationale when the button receives focus. The `title`
					    attribute alone is not reliably announced by all SRs. */}
					<p id="error-boundary-reset-hint" className="sr-only">
						{t("errorBoundary.resetSettingsHint")}
					</p>
					<div className="flex flex-wrap items-center justify-center gap-2">
						{/* "Reset settings" is rendered FIRST and visually
						    highlighted as the recommended recovery action — most
						    render crashes stem from a bad config value, so this
						    affordance has the highest expected payoff. The
						    destructive tint + soft background visually separate
						    it from the secondary Try Again / Reload App actions. */}
						<button
							type="button"
							onClick={this.handleResetSettings}
							disabled={this.state.resetting}
							className="rounded-lg border border-destructive/60 bg-destructive/10 px-4 py-2 text-sm font-medium text-destructive hover:bg-destructive/20 disabled:cursor-not-allowed disabled:opacity-50"
							title={t("errorBoundary.resetSettingsHint")}
							aria-describedby="error-boundary-reset-hint"
						>
							{this.state.resetting
								? t("errorBoundary.resetting")
								: t("errorBoundary.resetSettings")}
						</button>
						<button
							type="button"
							onClick={this.handleReset}
							className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
						>
							{t("errorBoundary.tryAgain")}
						</button>
						<button
							type="button"
							onClick={this.handleReload}
							className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-(--text-primary) hover:bg-(--bg-subtle)"
						>
							{t("errorBoundary.reloadApp")}
						</button>
						<button
							type="button"
							onClick={this.handleCopyError}
							className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-(--text-primary) hover:bg-(--bg-subtle)"
						>
							{this.state.copied
								? t("errorBoundary.copied")
								: t("errorBoundary.copyError")}
						</button>
						<button
							type="button"
							onClick={this.handleOpenLogs}
							className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-(--text-primary) hover:bg-(--bg-subtle)"
						>
							{t("errorBoundary.openLogs")}
						</button>
					</div>
					{this.state.resetFailed && (
						<p className="text-xs text-destructive">
							{t("errorBoundary.resetFailedNotice")}
						</p>
					)}
				</div>
			);
		}

		return this.props.children;
	}
}
