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
	const { currentPage, navigate, goBack, goForward, canGoBack, canGoForward } =
		useNavigation();

	// ── Route guard: protect onboarding from completed users ─────
	const config = useAppStore((s) => s.config);
	useEffect(() => {
		if (currentPage === "onboarding" && config?.onboarding_completed === true) {
			navigate("home");
		}
	}, [currentPage, config, navigate]);

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
	}, [connectionStatus, reloadThemeFromConfig]);

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
			}

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
	}, [navigate, textSize, call, setTextSize]);

	// ── Listen for navigate events from Python ────────────────────
	usePythonEvent("navigate", (data) => {
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
	});

	// NEW-UX-006: paste_failed toast
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

	// NF-R10-5: connecting progress
	const [connectingProgress, setConnectingProgress] = useState<number | null>(
		null,
	);
	usePythonEvent("download_progress", (data) => {
		const progress = (data as Record<string, unknown> | undefined)?.progress;
		if (typeof progress === "number") setConnectingProgress(progress);
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
										{/* NF-R10-5: 3-step progress indicator */}
										<ol className="text-xs text-(--text-muted) max-w-md space-y-1 list-none">
											<li>
												{connectingProgress !== null ? "✓" : "①"}{" "}
												{t("app.startingPythonStep")}
											</li>
											<li>
												{connectingProgress !== null &&
												connectingProgress >= 100
													? "✓"
													: "②"}{" "}
												{t("app.loadingModelStep", {
													percent:
														connectingProgress !== null &&
														connectingProgress < 100
															? ` (${Math.round(connectingProgress)}%)`
															: "",
												})}
											</li>
											<li>
												{"③"} {t("app.readyStep")}
											</li>
										</ol>
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
											{t("app.connectionLost")}
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
				</div>
			</div>
		</ErrorBoundary>
	);
}
