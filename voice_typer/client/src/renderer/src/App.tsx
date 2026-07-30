import {
	lazy,
	Suspense,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";
import { toast } from "sonner";
import { APP_NAME } from "@/branding";
import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";
import { HelpOverlay } from "@/components/help/HelpOverlay";
import { formatHotkey } from "@/components/hotkey/hotkey-utils";
import { ConnectionStatusScreen } from "@/components/layout/ConnectionStatusScreen";
import { Sidebar } from "@/components/layout/Sidebar";
import { TitleBar } from "@/components/layout/TitleBar";
import { Button } from "@/components/ui/button";
import { Toaster } from "@/components/ui/sonner";
import { useConnection } from "@/hooks/useConnection";
import { useConnectionToasts } from "@/hooks/useConnectionToasts";
import { useGlobalKeyboardShortcuts } from "@/hooks/useGlobalKeyboardShortcuts";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSoundFeedback } from "@/hooks/useSoundFeedback";
import { useTheme } from "@/hooks/useTheme";
import { getLocale, setLocale, useT } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
// ER-25: route-level code splitting. Home is the default landing page
// and stays eagerly imported so first paint is fast. The other 9 pages
// (History, Templates, Vocabulary, Models, Microphone, Analytics,
// Settings, About, Onboarding) are loaded on demand via React.lazy so
// Vite emits per-route chunks and the initial JS payload only carries
// the Home page's transitive deps. Each lazy import resolves to the
// page module's default export.
import Home from "@/pages/Home";

const AboutPage = lazy(() => import("@/pages/About"));
const DashboardPage = lazy(() => import("@/pages/Dashboard"));
const HistoryPage = lazy(() => import("@/pages/History"));
const MicrophonePage = lazy(() => import("@/pages/Microphone"));
const ModelsPage = lazy(() => import("@/pages/Models"));
const OnboardingPage = lazy(() => import("@/pages/Onboarding"));
const SettingsPage = lazy(() => import("@/pages/Settings"));
const TemplatesPage = lazy(() => import("@/pages/Templates"));
const VocabularyPage = lazy(() => import("@/pages/Vocabulary"));

import { HOTKEY_DEFAULT } from "@/pages/onboarding/lib/constants";
import { isKnownPage } from "@/router/routes";
import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";
import type { WindowBridge } from "@/types/ipc";

/**
 * ER-25: Suspense fallback for the lazy-loaded secondary routes.
 *
 * Inline (not a separate component file) so we don't introduce a new
 * module outside the refactor scope. The spinner matches the visual
 * style already used by ``DoneStep.tsx``, ``RecordingErrorCard.tsx``,
 * and ``MicToggleButton.tsx`` (``animate-spin rounded-full border-2
 * border-current border-t-transparent``) so the user sees a consistent
 * loading indicator across the app.
 *
 * The fallback is intentionally minimal — a route chunk typically
 * loads in <100ms on a local dev server and <300ms from a packaged
 * build, so a full-screen skeleton would flash too briefly to register.
 */
function RouteSuspenseFallback() {
	return (
		<output
			aria-live="polite"
			className="flex h-full w-full items-center justify-center p-8"
		>
			<span className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-(--text-muted) border-t-transparent" />
		</output>
	);
}

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

	// BG-25 (a11y / WCAG 2.4.2 Page Titled): keep `document.title` in sync
	// with the active route so screen-reader users (who announce the window
	// title to orient) and OS taskbar users can tell which page is active
	// without reading into main content. The title is composed as
	// `t("nav.<page>") — APP_NAME` so it localises with the rest of the UI.
	// Runs on mount AND whenever `currentPage` or `t` (i.e. the active
	// locale) changes — a locale switch re-titles the window.
	useEffect(() => {
		document.title = `${t(`nav.${currentPage}`)} — ${APP_NAME}`;
	}, [currentPage, t]);

	// One-time startup hook to propagate the restored locale (read
	// from localStorage at i18n module-init time) to BOTH the
	// Electron main process (so native dialogs render in the user's
	// language) AND the Python backend (so tray menu, tray tooltip,
	// and OS notifications render in the user's language).
	// Previously this propagation only happened on an explicit Settings
	// change — so after every app restart with a saved non-English
	// locale, the renderer showed the right language but native surfaces
	// stayed English. `setLocale()` is now the single entry point that
	// pushes to both processes (see i18n.ts). Calling it with the
	// already-restored locale is idempotent on the renderer side and
	// fires the IPC pushes on the backend side. Runs ONCE on mount.
	useEffect(() => {
		setLocale(getLocale());
	}, []);

	// BG-26 (a11y / focus management on route change): move keyboard focus
	// to `<main id="main-content">` whenever `currentPage` changes so screen
	// reader + keyboard users aren't stranded on the previously-focused
	// nav item after a route transition. The skip link + `tabIndex={-1}`
	// plumbing was already in place; this is the missing focus call.
	// `skipFirstRun` suppresses the focus call on the initial mount (the
	// user hasn't navigated yet, so stealing focus from whatever they were
	// doing would be rude — e.g. if they opened the app and immediately
	// focused the URL bar or a bookmark).
	const skipFirstRun = useRef(true);
	useEffect(() => {
		if (skipFirstRun.current) {
			skipFirstRun.current = false;
			return;
		}
		document.getElementById("main-content")?.focus();
	}, []);

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

	// BG-64 (partial): auto-collapse the sidebar when the window narrows
	// below the `640px` breakpoint. Only the wide→narrow TRANSITION (and
	// the initial narrow mount) forces a collapse — once collapsed, the
	// user's manual expand (Ctrl+B or TitleBar toggle) is respected until
	// the next wide→narrow transition. Narrow→wide transitions do NOT
	// auto-expand (the user may have intentionally collapsed the sidebar
	// on a wide window).
	const isNarrowViewport = useMediaQuery("(max-width: 640px)");
	const prevNarrowRef = useRef<boolean | null>(null);
	useEffect(() => {
		const prev = prevNarrowRef.current;
		// `prev !== true` covers BOTH the initial mount (prev === null)
		// and the wide→narrow transition (prev === false). On the
		// narrow→wide transition and on re-renders while narrow, prev
		// === true and we no-op so the user's manual toggle wins.
		if (isNarrowViewport && prev !== true) {
			setSidebarCollapsed(true);
		}
		prevNarrowRef.current = isNarrowViewport;
	}, [isNarrowViewport]);

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

	// BG-27: connection-state toasts + theme-reload-on-recover extracted
	// to `useConnectionToasts`. Returns the prev-connection ref so the
	// aria-live region below can announce RECOVERIES only (not the
	// initial connecting → connected transition).
	const prevConnectionRef = useConnectionToasts({
		connectionStatus,
		reloadThemeFromConfig,
		t,
	});

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

	// BG-27: app-wide keyboard shortcuts (Ctrl+B/,/H/=/-/wheel) extracted
	// to `useGlobalKeyboardShortcuts`. Behaviour byte-identical to the
	// original inline effect.
	useGlobalKeyboardShortcuts({
		navigate,
		textSize,
		setTextSize,
		call,
		t,
		setSidebarCollapsed,
	});

	// ── Listen for navigate events from Python ────────────────────
	// EC-FIX-13: page validation uses the single route table in
	// `router/routes.ts` via `isKnownPage`. Previously this had a
	// hand-maintained `pageMap` that had drifted out of sync with the
	// `Page` union — it was missing `onboarding`, so a backend
	// `navigate` event with `path: "onboarding"` (e.g. from the tray
	// menu's "Open onboarding" item) hit the else branch and was
	// silently dropped with a spurious console warning. Routing through
	// `isKnownPage` makes the route table the single source of truth:
	// any page in the `Page` union that has a `ROUTES` entry is
	// reachable here automatically.
	usePythonEvent("navigate", (data): (() => void) | undefined => {
		const path = (data as Record<string, string>)?.path;
		if (path) {
			const page = path.replace(/^\//, "");
			if (isKnownPage(page)) {
				navigate(page);
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
		const message = payload.message ?? t("home.pasteFailedMessage");
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
						} catch (e) {
							// clipboard API may be unavailable — non-fatal.
							console.warn(
								"[App] clipboard writeText (recovery path) failed:",
								e,
							);
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

	// DJ-94: stable callbacks so React.memo on <TitleBar>/<HelpOverlay> can
	// short-circuit when their other props haven't changed.
	const handleToggleSidebar = useCallback(
		() => setSidebarCollapsed((c) => !c),
		[],
	);
	const handleOpenHelp = useCallback(() => setShowHelpOverlay(true), []);
	const handleCloseHelp = useCallback(() => setShowHelpOverlay(false), []);

	const handleOnboardingComplete = useCallback(async () => {
		navigate("home");
		try {
			const cfg = await call<VoiceTyperConfig>("get_config");
			if (cfg?.theme_mode) {
				await reloadThemeFromConfig();
			}
		} catch (e) {
			// non-fatal — the user already finished onboarding;
			// theme will be re-applied on the next config_changed
			// event or the next app launch.
			console.warn(
				"[App] handleOnboardingComplete get_config/reload failed:",
				e,
			);
		}
	}, [navigate, call, reloadThemeFromConfig]);

	const dictationLabel = formatHotkey(config?.hotkey ?? HOTKEY_DEFAULT);
	const repasteLabel = formatHotkey(config?.repaste_hotkey ?? "<ctrl>+<alt>+v");

	const renderPage = () => {
		// Route table: see router/routes.ts for the single source of page names.
		// This switch maps each `Page` literal to its component — legitimate
		// routing logic (which component renders for which page), not a
		// duplicate of the page registry. The set of valid page names lives
		// in `ROUTES` (router/routes.ts); this switch only chooses the view.
		switch (currentPage) {
			case "home":
				return <Home />;
			case "history":
				return <HistoryPage />;
			case "templates":
				return <TemplatesPage />;
			case "vocabulary":
				return <VocabularyPage />;
			case "models":
				return <ModelsPage />;
			case "microphone":
				return <MicrophonePage />;
			case "analytics":
				return <DashboardPage />;
			case "settings":
				return <SettingsPage />;
			case "about":
				return <AboutPage />;
			case "onboarding":
				return <OnboardingPage onComplete={handleOnboardingComplete} />;
			default:
				// BG-24: page-not-found fallback now resolves via i18n
				// (`app.pageNotFoundTitle` / `app.pageNotFoundDescription`)
				// so non-English users see the fallback in their locale.
				// Both keys ship translated across all 8 locales.
				return (
					<div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
						<p className="text-sm font-medium text-(--text-primary)">
							{t("app.pageNotFoundTitle")}
						</p>
						<p className="text-xs text-(--text-muted)">
							{t("app.pageNotFoundDescription", {
								page: String(currentPage),
							})}
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
					onOpenHelp={handleOpenHelp}
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
							{connectionStatus === "connected" ? (
								<Suspense fallback={<RouteSuspenseFallback />}>
									{renderPage()}
								</Suspense>
							) : (
								<ConnectionStatusScreen
									status={connectionStatus}
									lastError={lastError}
									onRetry={handleRetryConnection}
									connectingProgress={connectingProgress}
								/>
							)}
						</main>
					</div>
				</div>
				<Toaster />

				{/* BG-27: help overlay extracted to <HelpOverlay /> */}
				<HelpOverlay
					open={showHelpOverlay}
					onClose={handleCloseHelp}
					dictationLabel={dictationLabel}
					repasteLabel={repasteLabel}
				/>

				{/* Split the screen-reader live region
                                    into TWO regions — one for recording state, one for
                                    connection status. Previously a single aria-atomic region
                                    concatenated both streams, so any change in either
                                    re-announced the ENTIRE combined text — meaning a brief
                                    `connectionStatus` flicker caused "Recording started." to
                                    be re-announced even though recording state hadn't
                                    changed. Two regions isolate the two streams so each only
                                    re-announces when ITS OWN content changes. */}
				<div aria-live="polite" aria-atomic="true" className="sr-only">
					{recordingState === "recording" ? t("a11y.recordingStarted") : ""}
					{recordingState === "transcribing" ? t("a11y.transcribingAudio") : ""}
					{recordingState === "idle" ? t("a11y.ready") : ""}
					{recordingState === "error" ? t("a11y.errorOccurred") : ""}
					{recordingState === "loading" ? t("a11y.loadingModel") : ""}
					{recordingState === "cancelling" ? t("a11y.cancelling") : ""}
				</div>
				{/* PVT-fix-9: announce connection-state transitions so
                                    screen-reader users get the same feedback that sighted
                                    users get from the connecting/disconnected/restarting UI
                                    swap. Reuses existing i18n keys (`app.lostConnection`,
                                    `app.restartingBackend`, `about.connected`) so no new
                                    translation keys are required. */}
				<div aria-live="polite" aria-atomic="true" className="sr-only">
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
