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
import { HelpOverlay } from "@/components/help/HelpOverlay";
import { formatHotkey } from "@/components/hotkey/hotkey-utils";
import { ConnectionStatusScreen } from "@/components/layout/ConnectionStatusScreen";
import { Sidebar } from "@/components/layout/Sidebar";
import { TitleBar } from "@/components/layout/TitleBar";
import { Button } from "@/components/ui/button";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useConnection } from "@/hooks/useConnection";
import { useConnectionToasts } from "@/hooks/useConnectionToasts";
import { useGlobalKeyboardShortcuts } from "@/hooks/useGlobalKeyboardShortcuts";
import { useHelpOverlayShortcut } from "@/hooks/useHelpOverlayShortcut";
import { useLastResortUnloadedToast } from "@/hooks/useLastResortUnloadedToast";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useNavigation } from "@/hooks/useNavigation";
import { useOnboardingComplete } from "@/hooks/useOnboardingComplete";
import { usePasteFailedToast } from "@/hooks/usePasteFailedToast";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSoundFeedback } from "@/hooks/useSoundFeedback";
import { useTheme } from "@/hooks/useTheme";
import { useWindowMaximized } from "@/hooks/useWindowMaximized";
import { getLocale, setLocale, useT } from "@/i18n/i18n";
import { VOICE_BIOMETRIC_CONSENT_FIELD } from "@/lib/consent";
import { cn } from "@/lib/utils";
// Route-level code splitting. Home is the default landing page
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
import type { WindowBridge } from "@/types/ipc";

/**
 * Suspense fallback for the lazy-loaded secondary routes.
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
	const t = useT();
	return (
		<output
			aria-live="polite"
			aria-label={t("a11y.loading")}
			className="flex h-full w-full items-center justify-center p-8"
		>
			<span className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-(--text-muted) border-t-transparent" />
		</output>
	);
}

export default function App() {
	const t = useT();

	// ── Routing (extracted to useNavigation) ──────────────────────
	// `replace` mirrors `history.replaceState` — it swaps
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
	// Field-level selectors (vs subscribing to the whole `config` object)
	// so a settings change to ANY other config field (theme_mode, hotkey,
	// audio preset, etc.) doesn't re-render <App /> and re-fire this
	// effect. `mergeConfig` always allocates a new top-level config
	// object reference, so a single-field selector is the only way to
	// avoid re-render storms on every keystroke in Settings.
	const onboardingCompleted = useAppStore(
		(s) => s.config?.onboarding_completed === true,
	);
	const hotkeyFromConfig = useAppStore((s) => s.config?.hotkey);
	const repasteHotkeyFromConfig = useAppStore((s) => s.config?.repaste_hotkey);
	useEffect(() => {
		if (currentPage === "onboarding" && onboardingCompleted) {
			// Use `replace` instead of `navigate` so the
			// "onboarding" entry is swapped for "home" in the history
			// stack. With `navigate`, the stack would become
			// [..., "onboarding", "home"] and pressing Back would return
			// the user to the wizard they just completed — confusing.
			replace("home");
		}
	}, [currentPage, onboardingCompleted, replace]);

	// a11y / WCAG 2.4.2 Page Titled: keep `document.title` in sync
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

	// a11y / focus management on route change: move keyboard focus
	// to `<main id="main-content">` whenever `currentPage` changes so screen
	// reader + keyboard users aren't stranded on the previously-focused
	// nav item after a route transition. The skip link + `tabIndex={-1}`
	// plumbing was already in place; this is the missing focus call.
	// `skipFirstRun` suppresses the focus call on the initial mount (the
	// user hasn't navigated yet, so stealing focus from whatever they were
	// doing would be rude — e.g. if they opened the app and immediately
	// focused the URL bar or a bookmark).
	const skipFirstRun = useRef(true);
	// The effect must re-run on every route change to move focus to the
	// main landmark — `currentPage` is the intentional reactive trigger
	// and is deliberately NOT read in the body.
	// biome-ignore lint/correctness/useExhaustiveDependencies: currentPage is the reactive trigger, not a body value
	useEffect(() => {
		if (skipFirstRun.current) {
			skipFirstRun.current = false;
			return;
		}
		document.getElementById("main-content")?.focus();
	}, [currentPage]);

	useSoundFeedback();

	// "?" key opens a help overlay listing keyboard shortcuts.
	// Extracted to `useHelpOverlayShortcut` (EO-28) — returns the
	// open flag + stable open/close callbacks.
	const { showHelpOverlay, openHelp, closeHelp } = useHelpOverlayShortcut();

	// ── Window-chrome state ───────────────────────────────────────
	const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

	// Auto-collapse the sidebar when the window narrows
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

	// Connection-state toasts + theme-reload-on-recover extracted
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
	// Extracted to `useWindowMaximized` (EO-28) — queries the native
	// bridge on mount, mirrors `is-maximized` onto <html>, returns the
	// boolean for the caller's own chrome styling.
	const isMaximized = useWindowMaximized(bridge);

	// App-wide keyboard shortcuts (Ctrl+B/,/H/=/-/wheel) extracted
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
	// Page validation uses the single route table in
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
				console.warn(`[renderer:App] ignoring unknown page path: "${page}"`);
			}
		}
		return undefined;
	});

	// paste_failed toast — extracted to `usePasteFailedToast` (EO-28).
	usePasteFailedToast(t);

	// asr_last_resort_unloaded toast — surfaces the Models-page pointer
	// as an IN-APP toast so the user still sees it when OS tray
	// notifications are disabled (the tray path is gated behind the
	// "Show Notifications" toggle). The toast's "Open Models" action
	// mirrors the host notification's ``click_path: "/models"``.
	useLastResortUnloadedToast(t, () => navigate("models"));

	// consent_required toast — GDPR Art. 9 dictation gate. The backend
	// publishes this event (recording_lifecycle.py) when dictation
	// start is refused for missing ``voice_biometric_consent`` — the
	// path for entry points the renderer can't gate client-side (F2
	// hotkey, tray click action, sandboxed bubble window). Surface an
	// in-app prompt with a Settings → Privacy deep-link instead of the
	// silent tray-only refusal (the ``toggle_dictation`` IPC resolves
	// ``ack`` with no feedback). Only the voice-biometric field is
	// handled here — the HuggingFace ``{provider, model}`` shape is
	// handled by the model-download flow.
	usePythonEvent("consent_required", (data): (() => void) | undefined => {
		const payload = (data ?? {}) as {
			consent_field?: string;
		};
		if (payload.consent_field !== VOICE_BIOMETRIC_CONSENT_FIELD) {
			return undefined;
		}
		toast.warning(t("notify.recording_controller.consent_required_body"), {
			duration: 6000,
			action: {
				label: t("microphone.consentRequiredAction"),
				// Deep-link to the EXACT consent toggle — Settings
				// consumes the ``consentField`` navigate option and
				// scrolls to / highlights the Voice Biometric row.
				onClick: () =>
					navigate("settings", {
						consentField: payload.consent_field,
					}),
			},
		});
		return undefined;
	});

	// Connecting progress
	const [connectingProgress, setConnectingProgress] = useState<number | null>(
		null,
	);
	// `connectingProgress` is ONLY consumed by
	// `<ConnectionStatusScreen>`, which App renders exclusively when
	// `connectionStatus !== "connected"`. Updating `connectingProgress`
	// while connected is therefore wasted work — it triggers an App
	// re-render for a state value nobody reads. We mirror
	// `connectionStatus` into a ref and short-circuit the handler when
	// connected. (We can't conditionally call `usePythonEvent` — that
	// would violate the rules of hooks — so the dispatcher-level
	// subscriber stays registered, but the actual `setConnectingProgress`
	// call is gated. The dispatcher fan-out for an unmatched type is a
	// single Map lookup + early return, so the residual cost is
	// negligible.)
	const connectionStatusRef = useRef(connectionStatus);
	connectionStatusRef.current = connectionStatus;
	usePythonEvent("download_progress", (data): (() => void) | undefined => {
		// Skip the state update while connected —
		// ConnectionStatusScreen isn't rendered, so the value would
		// never be read and the re-render would be wasted.
		if (connectionStatusRef.current === "connected") return undefined;
		const progress = (data as Record<string, unknown> | undefined)?.progress;
		if (typeof progress === "number") setConnectingProgress(progress);
		return undefined;
	});

	// Clear the connecting progress value whenever we leave the
	// "connecting" state. Without this, a stale progress percentage
	// (e.g. 73%) would persist across a brief disconnect/reconnect
	// flap and mislead the user into thinking the download was still
	// ongoing after the backend had already reconnected. The next
	// "connecting" phase re-seeds the value via the download_progress
	// handler above.
	useEffect(() => {
		if (connectionStatus !== "connecting") {
			setConnectingProgress(null);
		}
	}, [connectionStatus]);

	// Stable callbacks so React.memo on <TitleBar>/<HelpOverlay> can
	// short-circuit when their other props haven't changed.
	const handleToggleSidebar = useCallback(
		() => setSidebarCollapsed((c) => !c),
		[],
	);
	// open/close callbacks come from `useHelpOverlayShortcut` (EO-28) —
	// they are already stable (memoized with empty deps).

	// Onboarding-complete handler extracted to `useOnboardingComplete`
	// (EO-28): navigate home + re-apply the theme from the saved config.
	const handleOnboardingComplete = useOnboardingComplete({
		navigate,
		call,
		reloadThemeFromConfig,
	});

	const dictationLabel = formatHotkey(hotkeyFromConfig ?? HOTKEY_DEFAULT);
	const repasteLabel = formatHotkey(
		repasteHotkeyFromConfig ?? "<ctrl>+<alt>+v",
	);

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
				// Page-not-found fallback now resolves via i18n
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
	// ErrorBoundary wrap was removed from here — `main.tsx` already
	// wraps `<App />` in the same `<ErrorBoundary>` with the same
	// fallback. The inner wrap was dead-code redundancy: a render
	// crash anywhere inside `<App />` propagated to the parent
	// boundary regardless, and the inner boundary's fallback was
	// identical to the outer one (no `fallback` prop supplied).
	// Keeping a single boundary in `main.tsx` simplifies the tree
	// and removes one layer of catch noise from stack traces.
	return (
		<TooltipProvider delayDuration={200} skipDelayDuration={500}>
			<a
				href="#main-content"
				className="sr-only focus:not-sr-only focus:fixed focus:inset-s-4 focus:top-4 focus:z-100 focus:rounded-lg focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground"
			>
				{t("a11y.skipToMain")}
			</a>
			<div
				className={cn(
					// Clean-window: no outer frame border. The window keeps its
					// rounded corners (the `html` element carries the radius so
					// it persists across React re-renders) but the 1px hard
					// outline around the whole app is removed — the content
					// background alone separates the window from the desktop.
					"flex h-screen flex-col bg-(--bg-subtle) font-sans text-(--text-primary) overflow-hidden",
					!isMaximized && "rounded-lg",
				)}
			>
				<TitleBar
					onToggleSidebar={handleToggleSidebar}
					onGoBack={goBack}
					onGoForward={goForward}
					canGoBack={canGoBack}
					canGoForward={canGoForward}
					isMaximized={isMaximized}
					onOpenHelp={openHelp}
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
							// Visible focus ring so sighted keyboard users see that focus
							// moved into the main landmark after a navigation event. Pre-fix
							// this was `focus:outline-none` only — focus was moved
							// programmatically (see the useEffect above) but the user had no
							// visual confirmation, leading to confusion about whether the
							// shortcut had any effect. The ring uses the same `--ring` token
							// as every other focusable element so it visually matches the
							// rest of the app's focus indicators. We use `focus:` (not
							// `focus-visible:`) because the focus move is programmatic —
							// `focus-visible` only fires for keyboard-initiated focus, so a
							// mouse-click nav followed by the programmatic focus() call
							// would NOT show the ring under `focus-visible:`.
							// Clean-window: no left/top panel border around the
							// content area. The bg contrast against the
							// --bg-subtle wrapper still separates content from
							// chrome without a hard frame line.
							className="flex-1 overflow-y-auto bg-(--bg) focus:outline-none focus:ring-2 focus:ring-ring/30 focus:ring-offset-2"
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

				{/* Help overlay extracted to <HelpOverlay /> */}
				<HelpOverlay
					open={showHelpOverlay}
					onClose={closeHelp}
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
				{/* Assertive region for connection ERRORS (disconnected,
				    restarting) — these interrupt the user since they indicate
				    a problem requiring attention. Split from the recovery
				    region below so the recovery announcement stays polite
				    (non-interrupting) and doesn't yank the user out of what
				    they were doing. Reuses existing i18n keys
				    (`app.lostConnection`, `app.restartingBackend`) so no new
				    translation keys are required. */}
				<div aria-live="assertive" aria-atomic="true" className="sr-only">
					{connectionStatus === "disconnected" ? t("app.lostConnection") : ""}
					{connectionStatus === "restarting" ? t("app.restartingBackend") : ""}
				</div>
				{/* Polite region for connection RECOVERY (re-connected after
				    an outage) — non-interrupting so the user hears it but
				    isn't pulled out of what they were doing. Reuses existing
				    i18n key (`about.connected`) so no new translation keys
				    are required. */}
				<div aria-live="polite" aria-atomic="true" className="sr-only">
					{connectionStatus === "connected" &&
					prevConnectionRef.current !== "connected" &&
					prevConnectionRef.current !== "connecting"
						? t("about.connected")
						: ""}
				</div>
			</div>
		</TooltipProvider>
	);
}
