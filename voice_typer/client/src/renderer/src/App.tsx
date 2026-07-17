import { useCallback, useEffect, useRef, useState } from "react";
// NEW-UX-006: toast subscription for paste_failed events published
// by dictation_pipeline._copy_and_paste when the clipboard is
// unavailable. The existing tray.notify still fires for redundancy
// (tray icon tooltip is visible when the user is on another app;
// the toast is visible when the renderer has focus).
import { toast } from "sonner";
import { Modal } from "@/components/common/Modal";
// NEW-UX-015: ErrorBoundary catches render errors so a single bad
// config or component crash doesn't white-screen the entire app.
import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";
import { Spinner } from "@/components/feedback/Spinner";
// NEW-UX-026: Punctuation cheat sheet — embedded in the help overlay.
import { PunctuationCheatSheet } from "@/components/help/PunctuationCheatSheet";
import { Sidebar } from "@/components/layout/Sidebar";
import { TitleBar } from "@/components/layout/TitleBar";
import { Button } from "@/components/ui/button";
import { Toaster } from "@/components/ui/sonner";
import { useConnection } from "@/hooks/useConnection";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSoundFeedback } from "@/hooks/useSoundFeedback";
import { useTheme } from "@/hooks/useTheme";
import { useT } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
// NEW-UX-009: About/Diagnostics page.
import AboutPage from "@/pages/About";
import DashboardPage from "@/pages/Dashboard";
import HistoryPage from "@/pages/History";
import Home from "@/pages/Home";
import MicrophonePage from "@/pages/Microphone";
import ModelsPage from "@/pages/Models";
// #8: Onboarding wizard — was previously dead code (275-line component
// never imported, never rendered). Now wired in via the first-run check
// in the connection lifecycle effect (see useConnection.ts).
import OnboardingPage from "@/pages/Onboarding";
import SettingsPage from "@/pages/Settings";
import TemplatesPage from "@/pages/Templates";
import VocabularyPage from "@/pages/Vocabulary";
import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page, WindowBridge } from "@/types/ipc";
export default function App() {
	// F-3: subscribe to locale changes so the whole UI tree re-renders
	// (cascading) when the user switches languages — no page reload.
	const t = useT();

	// ── Routing (extracted to useNavigation) ──────────────────────
	const { currentPage, navigate, goBack, goForward, canGoBack, canGoForward } =
		useNavigation();

	// ── Route guard: protect onboarding from completed users ─────
	const config = useAppStore((s) => s.config);
	useEffect(() => {
		if (currentPage === "onboarding" && config?.onboarding_completed === true) {
			navigate("home");
		}
	}, [currentPage, config, navigate]);

	// SOUND-FIX-004: App-level sound feedback subscription.  Previously
	// lived in Home.tsx, so cues only played when the user was on Home.
	// Mounting at the App root ensures cues fire on every page and when
	// the window is hidden to the tray.
	useSoundFeedback();

	// NEW-UX-043: "?" key opens a help overlay listing keyboard shortcuts.
	// Also closes on Escape.
	const [showHelpOverlay, setShowHelpOverlay] = useState(false);
	useEffect(() => {
		const handler = (e: KeyboardEvent) => {
			if (e.key === "?" && !e.ctrlKey && !e.metaKey && !e.altKey) {
				const active = document.activeElement;
				const tag = active?.tagName?.toLowerCase();
				if (tag === "input" || tag === "textarea" || tag === "select") return;
				e.preventDefault();
				setShowHelpOverlay(true);
			} else if (e.key === "Escape" && showHelpOverlay) {
				setShowHelpOverlay(false);
			}
		};
		document.addEventListener("keydown", handler);
		return () => document.removeEventListener("keydown", handler);
	}, [showHelpOverlay]);

	// ── Window-chrome state (kept local to App.tsx) ───────────────
	const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
	const [isMaximized, setIsMaximized] = useState(false);

	const { call } = usePython();

	// ── Theme + connection (extracted to useTheme / useConnection) ─
	const {
		themeMode,
		handleThemeChange,
		reloadThemeFromConfig,
		textSize,
		setTextSize,
	} = useTheme(call);
	const { recordingState, connectionStatus, lastError, handleRetryConnection } =
		useConnection({ call, currentPage, navigate });

	// THEME-RESTART-FIX: re-trigger `reloadThemeFromConfig()` when the
	// backend connection is established.  The initial `useEffect` inside
	// `useTheme` fires on mount, but at that point the backend may not yet
	// be connected — `call("get_config")` silently fails (the catch block
	// is empty), so the theme stays at its initial state (system/default/
	// null).  After a full Electron relaunch, the renderer mounts before
	// the new Python backend is ready, causing the theme to appear to
	// reset to default despite the config being correctly persisted to
	// disk.  Re-fetching on every connect transition ensures the theme
	// is always applied from the persisted config.
	// THEME-RESTART-FIX-001: track the previous connection status so we
	// only re-fetch on the *transition* from something-else → "connected",
	// not on every render where connectionStatus == "connected".
	const prevConnectionRef = useRef(connectionStatus);
	useEffect(() => {
		const prev = prevConnectionRef.current;
		prevConnectionRef.current = connectionStatus;
		if (prev !== "connected" && connectionStatus === "connected") {
			reloadThemeFromConfig();
		}
	}, [connectionStatus, reloadThemeFromConfig]);

	// ── Window maximize state (for removing border-radius when maximized) ──

	const bridge =
		typeof window !== "undefined"
			? (window.window_ as WindowBridge)
			: undefined;

	useEffect(() => {
		if (!bridge) return;
		let cancelled = false;
		bridge
			.isMaximized()
			.then((v) => {
				if (!cancelled) {
					setIsMaximized(v);
					// UX-045: toggle a class on the html element so the CSS border-radius
					// on html is also removed when maximized (mirrors the wrapper div's
					// !isMaximized && 'rounded-lg' logic).
					document.documentElement.classList.toggle("is-maximized", v);
				}
			})
			.catch(() => {});
		const unsub = bridge.onMaximizedChanged((v) => {
			if (!cancelled) {
				setIsMaximized(v);
				document.documentElement.classList.toggle("is-maximized", v);
			}
		});
		return () => {
			cancelled = true;
			unsub();
		};
	}, [bridge]);

	// UX-031: Ctrl+B toggles the sidebar — discoverable keyboard shortcut
	// matching VS Code / Chrome's convention. Without this the collapse
	// button is invisible at width 0px in the collapsed state.
	//
	// UX-NAV-001: Ctrl+, (Cmd+, on macOS) jumps to Settings — matches the
	// convention used by VS Code, macOS System Settings, and most Electron
	// apps.  Ctrl+H jumps to Home (matches the "Home" key convention in
	// browsers and the Chrome "History" mnemonic doesn't apply here since
	// Voice Typer's own History page is reached via the sidebar / Ctrl+Alt+H
	// is reserved by some OS IMEs).  Both shortcuts are skipped while typing
	// in an input/textarea so the user can type "b" or "," into a field
	// without navigating away.
	useEffect(() => {
		const keyHandler = (e: KeyboardEvent) => {
			if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
				const target = e.target as HTMLElement | null;
				const tag = target?.tagName?.toLowerCase() ?? "";
				const typing =
					tag === "input" ||
					tag === "textarea" ||
					target?.isContentEditable === true;

				if (e.key === "b" && !typing) {
					e.preventDefault();
					setSidebarCollapsed((c) => !c);
					return;
				}
				// Ctrl+, → Settings.  The comma key is "," so we match
				// e.key rather than e.code (which would be "Comma" and
				// locale-dependent).
				if (e.key === "," && !typing) {
					e.preventDefault();
					navigate("settings");
					return;
				}
				// Ctrl+H → Home.  Skip when typing so IME composition
				// (e.g. Japanese romaji → hiragana "h") isn't interrupted.
				if (e.key === "h" && !typing) {
					e.preventDefault();
					navigate("home");
					return;
				}
			}

			// Ctrl+= / Ctrl+Plus → Increase text size by 1px
			if ((e.ctrlKey || e.metaKey) && (e.key === "=" || e.key === "+")) {
				e.preventDefault();
				const current = textSize ?? 14;
				const next = Math.min(current + 1, 20);
				if (next !== current) {
					setTextSize(next);
					call("set_config", { text_size: next }).catch(() => {});
				}
				return;
			}

			// Ctrl+- / Ctrl+Minus → Decrease text size by 1px
			if ((e.ctrlKey || e.metaKey) && e.key === "-") {
				e.preventDefault();
				const current = textSize ?? 14;
				const next = Math.max(current - 1, 10);
				if (next !== current) {
					setTextSize(next);
					call("set_config", { text_size: next }).catch(() => {});
				}
				return;
			}
		};

		// Ctrl+MouseWheel → Zoom text size by 1px per notch
		const wheelHandler = (e: WheelEvent) => {
			if (!e.ctrlKey && !e.metaKey) return;
			e.preventDefault();
			const current = textSize ?? 14;
			if (e.deltaY < 0) {
				// Scroll up → increase
				const next = Math.min(current + 1, 20);
				if (next !== current) {
					setTextSize(next);
					call("set_config", { text_size: next }).catch(() => {});
				}
			} else if (e.deltaY > 0) {
				// Scroll down → decrease
				const next = Math.max(current - 1, 10);
				if (next !== current) {
					setTextSize(next);
					call("set_config", { text_size: next }).catch(() => {});
				}
			}
		};

		window.addEventListener("keydown", keyHandler);
		window.addEventListener("wheel", wheelHandler, { passive: false });
		return () => {
			window.removeEventListener("keydown", keyHandler);
			window.removeEventListener("wheel", wheelHandler);
		};
	}, [navigate, textSize, call, setTextSize]);

	// ── Listen for navigate events from Python (e.g. tray "More models...") ──
	usePythonEvent("navigate", (data) => {
		const path = (data as Record<string, string>)?.path;
		if (path) {
			// Strip leading slash and convert to page name
			const page = path.replace(/^\//, "");
			// Map URL paths to internal page names
			const pageMap: Record<string, Page> = {
				models: "models",
				home: "home",
				settings: "settings",
				history: "history",
				templates: "templates",
				vocabulary: "vocabulary",
				microphone: "microphone",
				analytics: "analytics",
				about: "about",
			};
			// F-10: only navigate to a known page. The backend can push
			// arbitrary strings; casting unknown paths with `(page as Page)`
			// slipped invalid values into currentPage and silently
			// white-screened the app. Unknown paths are ignored + warned.
			const target = pageMap[page];
			if (target) {
				navigate(target);
			} else {
				console.warn(`[navigate] ignoring unknown page path: "${page}"`);
			}
		}
	});

	// NEW-UX-006: subscribe to ``paste_failed`` events published by
	// ``dictation_pipeline._copy_and_paste`` when clipboard.copy()
	// raises ``ClipboardCopyError``. Previously the failure was only
	// surfaced via the tray icon tooltip (tray.notify) — invisible
	// when the renderer had focus. This handler raises a sonner
	// warning toast with the message + a "Copy path" action button
	// (so the user can paste the recovery file's path into a file
	// manager to retrieve their transcription). When
	// ``recovery_path`` is null (crash recovery disabled), the
	// action button is omitted.
	//
	// i18n: the toast message itself comes from the server (which
	// is the single source of truth for the failure wording — also
	// used by tray.notify). Only the action button label ("Copy
	// path") is rendered here; we keep it inline English to avoid
	// adding new keys to all 8 locale files for a one-off error
	// notification. If a future round adds i18n for this label,
	// swap to ``t("pasteFailed.copyPath")`` and backfill locales.
	usePythonEvent("paste_failed", (data) => {
		const payload = (data ?? {}) as {
			message?: string;
			recovery_path?: string | null;
		};
		const message =
			payload.message ??
			"Transcription complete, but the clipboard was unavailable. Your text was saved to the crash-recovery file so it is not lost.";
		const recoveryPath =
			typeof payload.recovery_path === "string" ? payload.recovery_path : null;
		// Long duration (8s) so the user has time to read the
		// path + click the action button before the toast auto-
		// dismisses. The first line of the message is the title;
		// subsequent lines become the description.
		const lines = message.split("\n");
		const title = lines[0] ?? message;
		const description = lines.slice(1).join("\n") || undefined;
		if (recoveryPath) {
			toast.warning(title, {
				description,
				duration: 8000,
				action: {
					label: "Copy path",
					onClick: () => {
						// Best-effort: copy the recovery file path to
						// the clipboard so the user can paste it into
						// a file manager. navigator.clipboard is
						// available in the Electron renderer (and in
						// Tauri's webview). Silently swallow errors
						// — the toast still conveyed the path in its
						// description text.
						try {
							navigator.clipboard?.writeText(recoveryPath).catch(() => {});
						} catch {
							// clipboard API may be unavailable — non-fatal.
						}
					},
				},
			});
		} else {
			toast.warning(title, { description, duration: 8000 });
		}
	});

	// ── Sidebar toggle handler ──────────────────────────────────────
	const handleToggleSidebar = () => setSidebarCollapsed((c) => !c);

	// ── Page renderer ─────────────────────────────────────────────

	// #8: Called by the Onboarding wizard after the user finishes (apply
	// or skip). Routes the user back to home and reloads the config so
	// the rest of the UI sees the user's onboarding choices.
	const handleOnboardingComplete = useCallback(async () => {
		navigate("home");
		// Reload the config so theme/hotkey/mic/model selections take effect.
		// 17-H-FIX-1: onboarding_apply now emits a config_changed event
		// (parity with set_config), so the hotkey re-registration and model
		// reload happen server-side without restart. We still re-fetch here
		// to refresh theme state in this already-mounted instance, but the
		// bespoke re-fetch is no longer load-bearing.
		try {
			const cfg = await call<VoiceTyperConfig>("get_config");
			if (cfg?.theme_mode) {
				// Trigger a full theme reload via the hook's exposed helper.
				await reloadThemeFromConfig();
			}
		} catch {
			// non-fatal — config will be re-read on next mount
		}
	}, [navigate, call, reloadThemeFromConfig]);

	const renderPage = () => {
		switch (currentPage) {
			case "home":
				return (
					<Home
						recordingState={recordingState}
						lastError={lastError}
						onNavigate={navigate}
					/>
				);
			case "history":
				return <HistoryPage onNavigate={navigate} />;
			case "templates":
				return <TemplatesPage />;
			case "vocabulary":
				return <VocabularyPage />;
			case "models":
				return <ModelsPage />;
			case "microphone":
				return <MicrophonePage />;
			case "analytics":
				return <DashboardPage onNavigate={navigate} />;
			case "settings":
				return (
					<SettingsPage
						themeMode={themeMode}
						onThemeChange={handleThemeChange}
						onNavigate={navigate}
					/>
				);
			case "about":
				// NEW-UX-009: About/Diagnostics page.
				return <AboutPage />;
			case "onboarding":
				return <OnboardingPage onComplete={handleOnboardingComplete} />;
			default:
				// F-10: defensive fallback for any unknown currentPage value
				// (e.g. corrupted state). Prevents a silent blank screen.
				return (
					<div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
						<p className="text-sm font-medium text-(--text-primary)">
							Page not found
						</p>
						<p className="text-xs text-(--text-muted)">
							Unknown page: {String(currentPage)}
						</p>
					</div>
				);
		}
	};

	// ── Render ────────────────────────────────────────────────────

	// NEW-UX-015: wrap the entire app in ErrorBoundary so a render error
	// in any page/component shows a recovery UI instead of white-screening.
	return (
		<ErrorBoundary>
			{/* NEW-A11Y-004: Skip-to-main-content link for keyboard users.
      Visually hidden until focused, then appears as a floating button.
      WCAG 2.1 SC 2.4.1 (Bypass Blocks). */}
			<a
				href="#main-content"
				className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-100 focus:rounded-lg focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground"
			>
				{t("a11y.skipToMain")}
			</a>
			<div
				className={cn(
					"flex h-screen flex-col bg-(--bg-subtle) font-sans text-(--text-primary) overflow-hidden",
					!isMaximized && "rounded-lg border border-border",
				)}
			>
				<TitleBar
					onToggleSidebar={handleToggleSidebar}
					onGoBack={goBack}
					onGoForward={goForward}
					canGoBack={canGoBack}
					canGoForward={canGoForward}
					// NEW-TS-007: pass isMaximized down so TitleBar doesn't need
					// its own subscription to bridge.onMaximizedChanged.
					isMaximized={isMaximized}
					// UX-8: surface the "?" help overlay as a TitleBar button so
					// it's reachable without the keyboard shortcut.
					onOpenHelp={() => setShowHelpOverlay(true)}
				/>

				<div className="flex min-h-0 flex-1">
					<Sidebar
						currentPage={currentPage}
						onNavigate={navigate}
						themeMode={themeMode}
						onThemeChange={handleThemeChange}
						collapsed={sidebarCollapsed}
					/>

					<div className="flex min-w-0 flex-1 flex-col">
						<main
							id="main-content"
							className="flex-1 overflow-y-auto rounded-l-xl border-border border border-r-0 border-b-0 bg-(--bg)"
							style={{ scrollbarGutter: "stable" }}
						>
							{connectionStatus === "connecting" ? (
								<div className="flex h-full flex-col items-center justify-center gap-4 px-8 text-center">
									<Spinner />
									<div className="space-y-2">
										<p className="text-sm font-medium text-(--text-primary)">
											{t("app.startingBackend")}
										</p>
										<p className="text-xs text-(--text-muted) max-w-md">
											{t("app.firstLaunchHint")}
										</p>
									</div>
								</div>
							) : connectionStatus === "restarting" ? (
								// Issue 1E: dedicated restart UI.  Deliberately does NOT
								// reuse the "connecting" branch because that one advertises
								// a 30–60 s model download that doesn't apply here — the
								// model is already cached, only the Python process is being
								// re-spawned.  Showing the download hint here made users
								// think the restart was hung on a 466 MB re-download.
								<div className="flex h-full flex-col items-center justify-center gap-4 px-8 text-center">
									<Spinner />
									<div className="space-y-2">
										<p className="text-sm font-medium text-(--text-primary)">
											{t("app.restartingBackend")}
										</p>
										<p className="text-xs text-(--text-muted) max-w-md">
											{t("app.restartingHint")}
										</p>
									</div>
								</div>
							) : connectionStatus === "disconnected" ? (
								<div className="flex h-full flex-col items-center justify-center gap-4">
									<p className="text-sm text-destructive">
										{t("app.lostConnection")}
									</p>
									<Button
										variant="outline"
										size="sm"
										onClick={handleRetryConnection}
									>
										{t("app.retryConnection")}
									</Button>
								</div>
							) : (
								renderPage()
							)}
						</main>
					</div>
				</div>
				<Toaster />

				{/* NEW-UX-043: "?" help overlay — migrated to shared Modal (F-3) */}
				<Modal
					open={showHelpOverlay}
					onClose={() => setShowHelpOverlay(false)}
					title={t("help.title")}
					size="sm"
					className="w-110"
				>
					<div className="space-y-2 text-sm">
						{[
							{
								keys: t("help.keys.dictation"),
								desc: t("help.dictation"),
							},
							{
								keys: t("help.keys.cancel"),
								desc: t("help.cancel"),
							},
							{ keys: t("help.keys.repaste"), desc: t("help.repaste") },
							{
								keys: t("help.keys.toggleSidebar"),
								desc: t("help.toggleSidebar"),
							},
							{
								keys: t("help.keys.openSettings"),
								desc: t("help.openSettings"),
							},
							{ keys: t("help.keys.goHome"), desc: t("help.goHome") },
							{
								keys: t("help.keys.navigate"),
								desc: t("help.navigate"),
							},
							{ keys: t("help.keys.toggle"), desc: t("help.toggle") },
							{ keys: t("help.keys.activate"), desc: t("help.activate") },
							{
								keys: t("help.keys.zoomTextSize"),
								desc: t("help.zoomTextSize"),
							},
							{ keys: t("help.keys.openHelp"), desc: t("help.openHelp") },
							{
								keys: t("help.keys.navBack"),
								desc: t("help.navBack"),
							},
						].map((shortcut) => (
							<div
								key={shortcut.keys}
								className="flex items-center justify-between gap-4"
							>
								<span className="text-(--text-muted)">{shortcut.desc}</span>
								<kbd className="rounded border border-border bg-(--bg-subtle) px-2 py-0.5 font-mono text-xs text-(--text-primary)">
									{shortcut.keys}
								</kbd>
							</div>
						))}
					</div>
					{/* NEW-UX-026: Punctuation cheat sheet —
                                            say these words during dictation to
                                            insert the matching punctuation. */}
					<PunctuationCheatSheet />
					<p className="text-xs text-(--text-muted)">
						{t("help.closeHint", { key: "Esc" })}
					</p>
				</Modal>

				{/* #9: Screen reader live region for dynamic status updates.
          NEW-A11Y-002: NVDA/JAWS/VoiceOver users press F2, hear nothing,
          don't know if recording started.  This aria-live region announces
          state transitions so screen reader users know what's happening. */}
				<div aria-live="polite" className="sr-only">
					{recordingState === "recording" ? t("a11y.recordingStarted") : ""}
					{recordingState === "transcribing" ? t("a11y.transcribingAudio") : ""}
					{recordingState === "idle" ? t("a11y.ready") : ""}
					{recordingState === "error" ? t("a11y.errorOccurred") : ""}
					{recordingState === "loading" ? t("a11y.loadingModel") : ""}
					{recordingState === "cancelling" ? t("a11y.cancelling") : ""}
				</div>
			</div>
		</ErrorBoundary>
	);
}
