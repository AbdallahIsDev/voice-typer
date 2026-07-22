import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Modal } from "@/components/common/Modal";
import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";
import { Spinner } from "@/components/feedback/Spinner";
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
import AboutPage from "@/pages/About";
import DashboardPage from "@/pages/Dashboard";
import HistoryPage from "@/pages/History";
import Home from "@/pages/Home";
import MicrophonePage from "@/pages/Microphone";
import ModelsPage from "@/pages/Models";
import OnboardingPage from "@/pages/Onboarding";
import SettingsPage from "@/pages/Settings";
import TemplatesPage from "@/pages/Templates";
import VocabularyPage from "@/pages/Vocabulary";
import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page, WindowBridge } from "@/types/ipc";

export default function App() {
	const t = useT();

	// ── Routing (extracted to useNavigation) ──────────────────────
	// PVT-fix-12: `replace` mirrors `history.replaceState` — it swaps
	// the current history entry without pushing a new one. Used by the
	// onboarding-completed route guard below so the wizard entry is
	// replaced (not stacked on top of) by the home entry, preventing
	// the user from pressing Back to land back in the wizard they
	// just finished.
	const {
		currentPage,
		navigate,
		replace,
		goBack,
		goForward,
		canGoBack,
		canGoForward,
	} = useNavigation();

	// ── Route guard: protect onboarding from completed users ─────
	const config = useAppStore((s) => s.config);
	useEffect(() => {
		if (currentPage === "onboarding" && config?.onboarding_completed === true) {
			// PVT-fix-12: use `replace` instead of `navigate` so the
			// "onboarding" entry is swapped for "home" in the history
			// stack. With `navigate`, the stack would become
			// [..., "onboarding", "home"] and pressing Back would return
			// the user to the wizard they just completed — confusing.
			replace("home");
		}
	}, [currentPage, config, replace]);

	useSoundFeedback();

	// NEW-UX-043: "?" key opens a help overlay listing keyboard shortcuts.
	const [showHelpOverlay, setShowHelpOverlay] = useState(false);
	useEffect(() => {
		const handler = (e: KeyboardEvent) => {
			if (e.key === "?" && !e.ctrlKey && !e.metaKey && !e.altKey) {
				const active = document.activeElement as HTMLElement | null;
				const tag = active?.tagName?.toLowerCase();
				if (
					tag === "input" ||
					tag === "textarea" ||
					tag === "select" ||
					active?.isContentEditable === true
				)
					return;
				// PVT-fix-10: if any Radix Dialog-based modal is currently
				// open (ConfirmDialog, AlertDialog, the help overlay
				// itself, etc.), don't pop the help overlay on top of
				// it. Radix renders dialog content via Portal into
				// document.body with role="dialog" + data-state="open",
				// so a single querySelector covers every Modal/AlertDialog
				// instance in the app.
				if (document.querySelector('[role="dialog"][data-state="open"]'))
					return;
				e.preventDefault();
				setShowHelpOverlay(true);
			} else if (e.key === "Escape" && showHelpOverlay) {
				setShowHelpOverlay(false);
			}
		};
		document.addEventListener("keydown", handler);
		return () => document.removeEventListener("keydown", handler);
	}, [showHelpOverlay]);

	// ── Window-chrome state ───────────────────────────────────────
	const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
	const [isMaximized, setIsMaximized] = useState(false);

	const { call } = usePython();

	// ── Theme + connection ────────────────────────────────────────
	const {
		themeMode,
		handleThemeChange,
		reloadThemeFromConfig,
		textSize,
		setTextSize,
	} = useTheme(call);
	const { recordingState, connectionStatus, lastError, handleRetryConnection } =
		useConnection({ call, currentPage, navigate });

	const prevConnectionRef = useRef(connectionStatus);
	useEffect(() => {
		const prev = prevConnectionRef.current;
		prevConnectionRef.current = connectionStatus;
		if (prev !== "connected" && connectionStatus === "connected") {
			reloadThemeFromConfig();
		}

		// PVT-fix-20: surface connection-state transitions as toasts
		// so the user gets immediate visual feedback when the backend
		// drops out, restarts, or recovers — previously the only
		// feedback was the connecting/disconnected/restarting swap
		// inside the main content area, which a user looking at the
		// Home mic button could easily miss. Toasts reuse existing
		// i18n keys (`app.lostConnection`, `app.restartingBackend`,
		// `about.connected`) so no new translation keys are required.
		//
		// Transitions are tracked via the `prev` ref so each toast
		// fires exactly once per transition (not on every re-render).
		// The initial mount path (prev === connectionStatus ===
		// "connecting") doesn't fire a toast — only state CHANGES do.
		if (prev !== connectionStatus) {
			if (connectionStatus === "disconnected") {
				toast.error(t("app.lostConnection"), {
					description: t("app.lostConnectionHint"),
					duration: 6000,
				});
			} else if (connectionStatus === "restarting") {
				toast.warning(t("app.restartingBackend"), {
					description: t("app.restartingHint"),
					duration: 4000,
				});
			} else if (connectionStatus === "connected" && prev !== "connecting") {
				// Don't toast on the initial connect (prev ===
				// "connecting") — the user just launched the app
				// and doesn't need a "Connected!" toast. Only
				// surface RECOVERIES from a disconnected/restarting
				// state.
				toast.success(t("about.connected"), { duration: 3000 });
			}
		}
	}, [connectionStatus, reloadThemeFromConfig, t]);

	// ── Window maximize state ─────────────────────────────────────
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
					document.documentElement.classList.toggle("is-maximized", v);
				}
			})
			.catch((err) => console.warn("[IPC] window isMaximized failed:", err));
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

	// Keyboard shortcuts: Ctrl+B (sidebar), Ctrl+, (settings), Ctrl+H (home),
	// Ctrl+=/Ctrl+- (text size), Ctrl+Wheel (text size)
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
				if (e.key === "," && !typing) {
					e.preventDefault();
					navigate("settings");
					return;
				}
				if (e.key === "h" && !typing) {
					e.preventDefault();
					navigate("home");
					return;
				}

				// PVT-fix-11: zoom shortcuts (Ctrl+= / Ctrl+-) moved
				// inside the `!typing` guard so Ctrl+=/Ctrl+- pressed
				// while focus is inside an <input>/<textarea>/
				// contentEditable (e.g. the Settings search field) does
				// NOT hijack the keystroke to bump text size. The
				// browser's native zoom remains available via Ctrl++
				// (different key) outside the app's text-size shortcut
				// namespace. Behaviour is otherwise preserved (same
				// min/max bounds, same `set_config` IPC).
				if ((e.key === "=" || e.key === "+") && !typing) {
					e.preventDefault();
					const current = textSize ?? 14;
					const next = Math.min(current + 1, 20);
					if (next !== current) {
						setTextSize(next);
						call("set_config", { text_size: next }).catch((err) => {
							console.warn("[IPC] set_config failed:", err);
							toast.error(t("errorBoundary.unknownError"));
						});
					}
					return;
				}

				if (e.key === "-" && !typing) {
					e.preventDefault();
					const current = textSize ?? 14;
					const next = Math.max(current - 1, 10);
					if (next !== current) {
						setTextSize(next);
						call("set_config", { text_size: next }).catch((err) => {
							console.warn("[IPC] set_config failed:", err);
							toast.error(t("errorBoundary.unknownError"));
						});
					}
					return;
				}
			}
		};

		const wheelHandler = (e: WheelEvent) => {
			if (!e.ctrlKey && !e.metaKey) return;
			e.preventDefault();
			const current = textSize ?? 14;
			if (e.deltaY < 0) {
				const next = Math.min(current + 1, 20);
				if (next !== current) {
					setTextSize(next);
					call("set_config", { text_size: next }).catch(() => {});
				}
			} else if (e.deltaY > 0) {
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
	}, [navigate, textSize, call, setTextSize, t]);

	// ── Listen for navigate events from Python ────────────────────
	usePythonEvent("navigate", (data): (() => void) | undefined => {
		const path = (data as Record<string, string>)?.path;
		if (path) {
			const page = path.replace(/^\//, "");
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
			const target = pageMap[page];
			if (target) {
				navigate(target);
			} else {
				console.warn(`[navigate] ignoring unknown page path: "${page}"`);
			}
		}
		return undefined;
	});

	// NEW-UX-006: paste_failed toast
	// PVT-fix-17: the action button label was a hardcoded English
	// string ("Copy path") which broke i18n for non-English users.
	// Wired through `t("common.copyPath")` so the label resolves to
	// the active locale's translation. The key is defined in
	// translations/en.json ("Copy path") and falls back to English
	// for locales that haven't translated it yet.
	usePythonEvent("paste_failed", (data): (() => void) | undefined => {
		const payload = (data ?? {}) as {
			message?: string;
			recovery_path?: string | null;
		};
		const message =
			payload.message ??
			"Transcription complete, but the clipboard was unavailable. Your text was saved to the crash-recovery file so it is not lost.";
		const recoveryPath =
			typeof payload.recovery_path === "string" ? payload.recovery_path : null;
		const lines = message.split("\n");
		const title = lines[0] ?? message;
		const description = lines.slice(1).join("\n") || undefined;
		if (recoveryPath) {
			toast.warning(title, {
				description,
				duration: 8000,
				action: {
					label: t("common.copyPath"),
					onClick: () => {
						try {
							navigator.clipboard
								?.writeText(recoveryPath)
								.catch((err) =>
									console.warn("[clipboard] writeText failed:", err),
								);
						} catch {
							// clipboard API may be unavailable — non-fatal.
						}
					},
				},
			});
		} else {
			toast.warning(title, { description, duration: 8000 });
		}
		return undefined;
	});

	// NF-R10-5: connecting progress
	const [connectingProgress, setConnectingProgress] = useState<number | null>(
		null,
	);
	usePythonEvent("download_progress", (data): (() => void) | undefined => {
		const progress = (data as Record<string, unknown> | undefined)?.progress;
		if (typeof progress === "number") setConnectingProgress(progress);
		return undefined;
	});

	const handleToggleSidebar = () => setSidebarCollapsed((c) => !c);

	const handleOnboardingComplete = useCallback(async () => {
		navigate("home");
		try {
			const cfg = await call<VoiceTyperConfig>("get_config");
			if (cfg?.theme_mode) {
				await reloadThemeFromConfig();
			}
		} catch {
			// non-fatal
		}
	}, [navigate, call, reloadThemeFromConfig]);

	const formatHotkey = (h: string): string =>
		h === "<caps_lock>" ? "Caps Lock" : h.replace(/[<>]/g, "").toUpperCase();
	const dictationLabel = formatHotkey(config?.hotkey ?? "<f2>");
	const repasteLabel = formatHotkey(config?.repaste_hotkey ?? "<ctrl>+<alt>+v");

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
				return <AboutPage />;
			case "onboarding":
				return <OnboardingPage onComplete={handleOnboardingComplete} />;
			default:
				return (
					<div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
						<p className="text-sm font-medium text-(--text-primary)">
							Page not found
						</p>
						<p className="text-xs text-(--text-muted)">
							Unknown page: {String(currentPage)}
						</p>
						<Button variant="default" onClick={() => navigate("home")}>
							{t("app.goHome")}
						</Button>
					</div>
				);
		}
	};

	// ── Render ────────────────────────────────────────────────────
	return (
		<ErrorBoundary>
			<a
				href="#main-content"
				className="sr-only focus:not-sr-only focus:fixed focus:inset-s-4 focus:top-4 focus:z-100 focus:rounded-lg focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground"
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
					isMaximized={isMaximized}
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
							tabIndex={-1}
							className="flex-1 overflow-y-auto rounded-s-xl border-border border border-s-0 border-b-0 bg-(--bg) focus:outline-none"
							style={{ scrollbarGutter: "stable" }}
						>
							{connectionStatus === "connecting" ? (
								<div className="flex h-full flex-col items-center justify-center gap-4 px-8 text-center">
									<Spinner />
									<div className="space-y-2">
										<p className="text-sm font-medium text-(--text-primary)">
											{t("app.startingBackend")}
										</p>
										{/* NF-R10-5: 3-step progress indicator */}
										<ol className="text-xs text-(--text-muted) max-w-md space-y-1 list-none">
											<li>
												{connectingProgress !== null ? "✓" : "①"}{" "}
												{t("app.connecting.step1StartingPython")}
											</li>
											<li>
												{connectingProgress !== null &&
												connectingProgress >= 100
													? "✓"
													: "②"}{" "}
												{t("app.connecting.step2LoadingModel", {
													percent:
														connectingProgress !== null &&
														connectingProgress < 100
															? ` (${Math.round(connectingProgress)}%)`
															: "",
												})}
											</li>
											<li>
												{"③"} {t("app.connecting.step3Ready")}
											</li>
										</ol>
										{/* PVT-fix-19: wire the existing
                                                                                    `app.firstLaunchHint` key into the
                                                                                    connecting UI. The key has shipped in
                                                                                    translations/en.json since the early
                                                                                    i18n rollout ("First launch can take
                                                                                    30–60 seconds while we download the
                                                                                    speech model (~466 MB for small.en)…")
                                                                                    but was never rendered — the connecting
                                                                                    screen only showed the 3-step progress
                                                                                    list, leaving first-time users wondering
                                                                                    whether the 30–60s wait was normal. The
                                                                                    hint is shown ONLY on the `connecting`
                                                                                    screen (not `restarting` or
                                                                                    `disconnected`) because it specifically
                                                                                    describes the model-download path. */}
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
								<div className="flex h-full flex-col items-center justify-center gap-4 px-8 text-center">
									<div className="space-y-2">
										<p className="text-sm font-medium text-(--text-primary)">
											{t("app.lostConnection")}
										</p>
										{/* NF-R10-5: show the actual error message when
                                                                                        available (e.g. "Python crashed: exit code 137")
                                                                                        so the user can act on it instead of seeing a
                                                                                        generic "lost connection" message. */}
										<p className="text-xs text-(--text-muted) max-w-md">
											{lastError ?? t("app.lostConnectionHint")}
										</p>
										<Button
											variant="outline"
											size="sm"
											onClick={handleRetryConnection}
										>
											{t("app.retryConnection")}
										</Button>
									</div>
								</div>
							) : (
								renderPage()
							)}
						</main>
					</div>
				</div>
				<Toaster />

				{/* Help overlay */}
				<Modal
					open={showHelpOverlay}
					onClose={() => setShowHelpOverlay(false)}
					title={t("help.title")}
					description={t("help.description")}
					size="sm"
					className="w-110"
				>
					<ul className="space-y-2 text-sm">
						{[
							{ keys: dictationLabel, desc: t("help.dictation") },
							{ keys: t("help.keys.cancel"), desc: t("help.cancel") },
							{ keys: repasteLabel, desc: t("help.repaste") },
							{
								keys: t("help.keys.toggleSidebar"),
								desc: t("help.toggleSidebar"),
							},
							{
								keys: t("help.keys.openSettings"),
								desc: t("help.openSettings"),
							},
							{ keys: t("help.keys.goHome"), desc: t("help.goHome") },
							{ keys: t("help.keys.navigate"), desc: t("help.navigate") },
							{ keys: t("help.keys.toggle"), desc: t("help.toggle") },
							{ keys: t("help.keys.activate"), desc: t("help.activate") },
							{
								keys: t("help.keys.zoomTextSize"),
								desc: t("help.zoomTextSize"),
							},
							{ keys: t("help.keys.openHelp"), desc: t("help.openHelp") },
							{ keys: t("help.keys.navBack"), desc: t("help.navBack") },
						].map((shortcut) => (
							<li
								key={shortcut.keys}
								className="flex items-center justify-between gap-4"
							>
								<span className="text-(--text-muted)">{shortcut.desc}</span>
								<kbd className="rounded border border-border bg-(--bg-subtle) px-2 py-0.5 font-mono text-xs text-(--text-primary)">
									{shortcut.keys}
								</kbd>
							</li>
						))}
					</ul>
					<PunctuationCheatSheet />
					<p className="text-xs text-(--text-muted)">
						{t("help.closeHint", { key: "Esc" })}
					</p>
				</Modal>

				{/* Screen reader live region */}
				<div aria-live="polite" aria-atomic="true" className="sr-only">
					{recordingState === "recording" ? t("a11y.recordingStarted") : ""}
					{recordingState === "transcribing" ? t("a11y.transcribingAudio") : ""}
					{recordingState === "idle" ? t("a11y.ready") : ""}
					{recordingState === "error" ? t("a11y.errorOccurred") : ""}
					{recordingState === "loading" ? t("a11y.loadingModel") : ""}
					{recordingState === "cancelling" ? t("a11y.cancelling") : ""}
					{/* PVT-fix-9: announce connection-state transitions so
                                            screen-reader users get the same feedback that
                                            sighted users get from the connecting/disconnected/
                                            restarting UI swap. Reuses existing i18n keys
                                            (`app.lostConnection`, `app.restartingBackend`,
                                            `about.connected`) so no new translation keys are
                                            required. The empty-string fallback for non-matching
                                            states keeps the region silent between transitions
                                            (aria-atomic=true means each change re-announces the
                                            whole region, so a stable empty string is silent). */}
					{connectionStatus === "disconnected" ? t("app.lostConnection") : ""}
					{connectionStatus === "restarting" ? t("app.restartingBackend") : ""}
					{connectionStatus === "connected" &&
					prevConnectionRef.current !== "connected" &&
					prevConnectionRef.current !== "connecting"
						? t("about.connected")
						: ""}
				</div>
			</div>
		</ErrorBoundary>
	);
}
