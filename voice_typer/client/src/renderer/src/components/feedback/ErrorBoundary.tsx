// ErrorBoundary — catches render errors so a single bad
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
// The fallback now exposes three recovery affordances in
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
// ``componentDidCatch`` forwards the caught error to the
// main process for explicit persistence in
// ``electron-renderer-errors.log`` (separate from the
// ``console-message`` path so React's ``componentStack`` is
// preserved — ``console.error`` serializes ``errorInfo`` to a string
// and loses the structured component-tree trace).

import { Component, createRef, type ErrorInfo, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
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
	// Loop guard. ``handleReset`` clears React error state
	// but NOT the underlying poisoned state (localStorage, malformed
	// theme token) that caused the render crash. Without this guard,
	// clicking "Try Again" re-mounts the children against the same
	// poisoned state → same crash → same error UI → user clicks "Try
	// Again" again → infinite loop. We cap the count at 1: after the
	// first failed retry, the "Try Again" button is disabled and the
	// user is steered to "Reset settings" (which DOES clear poisoned
	// state via ``localStorage.clear()`` + backend ``get_defaults``).
	// A successful ``handleResetSettings`` resets the counter to 0
	// (because the underlying state is now clean and "Try Again"
	// becomes safe again).
	tryAgainCount: number;
}

export class ErrorBoundary extends Component<
	ErrorBoundaryProps,
	ErrorBoundaryState
> {
	// Ref to the primary recovery button ("Reset settings") in the
	// fallback UI. We programmatically focus it when the boundary
	// triggers so keyboard / SR users land on the recommended
	// recovery affordance instead of being stranded at the top of
	// a long alert region. ``componentDidUpdate`` performs the focus
	// when ``hasError`` transitions from false → true (focusing in
	// ``componentDidCatch`` directly would be too early — the
	// fallback render hasn't committed yet so the button ref is
	// still null).
	resetButtonRef = createRef<HTMLButtonElement>();

	constructor(props: ErrorBoundaryProps) {
		super(props);
		this.state = {
			hasError: false,
			error: null,
			errorInfo: null,
			copied: false,
			resetting: false,
			resetFailed: false,
			tryAgainCount: 0,
		};
	}

	static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
		return { hasError: true, error };
	}

	componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
		// Log to console for debugging. The main-window
		// `console-message` handler persists level>=3
		// (ERROR) console output to `electron-renderer-errors.log`
		// so this `console.error` automatically lands in the file
		// — the previous comment claiming "the renderer process's
		// console is captured by Electron's main process and
		// written to the log file" was misleading because the
		// console-message handler only re-emitted to the terminal
		// (lost when the terminal closed); it did NOT persist to
		// disk before the console-message persistence path landed.
		console.error(
			"[renderer:ErrorBoundary] Caught render error:",
			error,
			errorInfo,
		);

		// Forward the caught error to the main process
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
					console.warn("[renderer:ErrorBoundary] logError IPC failed:", err);
				});
		} catch (err) {
			// Synchronous throw (e.g. `window.window_` is
			// undefined and `?.` short-circuit didn't fire
			// for some reason). Same swallow rationale.
			console.warn("[renderer:ErrorBoundary] logError call failed:", err);
		}

		// Persist errorInfo in state so the "Copy error" button
		// can include the React component stack in
		// the pasted bug-report blob.
		this.setState({ errorInfo });
	}

	componentDidMount(): void {
		// If the boundary triggered during the initial render (a child
		// threw on first mount), the fallback UI has just committed and
		// we need to move focus to the Reset button here —
		// ``componentDidUpdate`` is NOT called for the initial mount,
		// so without this branch the focus-management logic in
		// ``focusResetButton`` would never fire for the "crashed on
		// first paint" case (which is the most common ErrorBoundary
		// scenario in practice).
		this.focusResetButton();
	}

	componentDidUpdate(
		_prevProps: ErrorBoundaryProps,
		prevState: ErrorBoundaryState,
	): void {
		// When the boundary just triggered (hasError false → true),
		// move focus to the primary recovery button. Without this,
		// keyboard / SR users are stranded at the top of the alert
		// region and must Tab through the whole description + <pre>
		// before reaching any actionable control. Focusing the
		// recommended "Reset settings" button mirrors the ARIA
		// Authoring Practices guidance for error dialogs: surface
		// the primary recovery affordance first.
		if (!prevState.hasError && this.state.hasError) {
			this.focusResetButton();
		}
	}

	/**
	 * Move focus to the primary recovery button (Reset settings) in the
	 * fallback UI. We query the DOM directly via a stable aria attribute
	 * instead of relying on a React ref forwarded through the <Button>
	 * wrapper (Button spreads ``{...props}`` to its underlying
	 * ``<button>`` host element, but React's special handling of ``ref``
	 * makes ref-forwarding through function components unreliable
	 * without an explicit forwardRef / ref-as-prop destructure — which
	 * Button doesn't do). ``querySelector`` is safe here because the
	 * fallback UI has just committed, so the button is in the DOM by
	 * the time componentDidMount / componentDidUpdate fires.
	 */
	private focusResetButton(): void {
		if (!this.state.hasError) return;
		const btn = document.querySelector<HTMLButtonElement>(
			'button[aria-describedby="error-boundary-reset-hint"]',
		);
		if (btn && typeof btn.focus === "function") {
			btn.focus();
		}
	}

	handleReset = (): void => {
		// Increment the loop-guard counter so the
		// "Try Again" button is disabled after the first failed
		// retry (see ``tryAgainCount`` field docstring). The user
		// must then use "Reset settings" (which clears poisoned
		// localStorage state) before "Try Again" becomes safe to
		// retry. The counter is reset to 0 by ``handleResetSettings``
		// on a successful backend reset.
		this.setState((prev) => ({
			hasError: false,
			error: null,
			errorInfo: null,
			copied: false,
			resetting: false,
			resetFailed: false,
			tryAgainCount: prev.tryAgainCount + 1,
		}));
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
			console.warn(
				"[renderer:ErrorBoundary] clipboard fallback copy failed:",
				e,
			);
		}
	};

	// Tracks the "copied" flash timer so a second flashCopied clears the
	// previous timer before arming a new one, and componentWillUnmount
	// can cancel a pending timer (no setState-after-unmount warnings +
	// no leaked timer keeping the crash screen's lifecycle alive).
	copiedTimer: number | null = null;

	flashCopied = (): void => {
		this.setState({ copied: true });
		if (this.copiedTimer !== null) {
			window.clearTimeout(this.copiedTimer);
		}
		this.copiedTimer = window.setTimeout(() => {
			this.copiedTimer = null;
			this.setState({ copied: false });
		}, 2000);
	};

	componentWillUnmount(): void {
		if (this.copiedTimer !== null) {
			window.clearTimeout(this.copiedTimer);
			this.copiedTimer = null;
		}
	}

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
			console.error("[renderer:ErrorBoundary] Failed to open logs:", err);
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
			let backendResetOk = false;
			try {
				const defaults = (await window.python?.call({
					type: "get_defaults",
				})) as Record<string, unknown> | undefined;
				if (defaults && typeof defaults === "object") {
					const REDACTED_RE = /^<redacted.*>$/i;
					const safe: Record<string, unknown> = {};
					for (const [k, v] of Object.entries(defaults)) {
						if (typeof v === "string" && REDACTED_RE.test(v)) continue;
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
				backendResetOk = true;
			} catch (err) {
				console.error(
					"[renderer:ErrorBoundary] Backend reset failed, falling back to localStorage clear + reload:",
					err,
				);
				this.setState({ resetFailed: true });
			}
			if (backendResetOk) {
				this.setState({ tryAgainCount: 0 });
			}
			try {
				localStorage.clear();
			} catch (e) {
				// Ignore — some sandboxed contexts disable localStorage.
				console.warn("[renderer:ErrorBoundary] localStorage.clear failed:", e);
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
					<pre className="max-w-2xl overflow-auto rounded-lg border border-border/10 bg-(--bg-subtle) p-4 text-left text-xs text-(--text-muted)">
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
						<Button
							type="button"
							variant="destructive"
							onClick={this.handleResetSettings}
							disabled={this.state.resetting}
							title={t("errorBoundary.resetSettingsHint")}
							aria-describedby="error-boundary-reset-hint"
							// Attach the focus-target ref so
							// ``componentDidUpdate`` can move focus
							// here when the boundary triggers. This
							// is the recommended recovery action so
							// it gets the initial focus.
							ref={this.resetButtonRef}
						>
							{this.state.resetting
								? t("errorBoundary.resetting")
								: t("errorBoundary.resetSettings")}
						</Button>
						<Button
							type="button"
							variant="default"
							onClick={this.handleReset}
							// Disable after the first failed retry so the
							// user cannot loop on "Try Again" against poisoned state.
							// The hint routes them to "Reset settings" instead.
							// Re-enabled when ``handleResetSettings`` clears the
							// counter on a successful backend reset.
							disabled={this.state.tryAgainCount >= 1}
							title={
								this.state.tryAgainCount >= 1
									? t("errorBoundary.resetSettingsHint")
									: undefined
							}
						>
							{t("errorBoundary.tryAgain")}
						</Button>
						<Button type="button" variant="outline" onClick={this.handleReload}>
							{t("errorBoundary.reloadApp")}
						</Button>
						<Button
							type="button"
							variant="outline"
							onClick={this.handleCopyError}
						>
							{this.state.copied
								? t("errorBoundary.copied")
								: t("errorBoundary.copyError")}
						</Button>
						<Button
							type="button"
							variant="outline"
							onClick={this.handleOpenLogs}
						>
							{t("errorBoundary.openLogs")}
						</Button>
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
