import { useCallback, useEffect } from "react";
import { A11yLiveRegions } from "@/components/common/A11yLiveRegions";
import ConsentGateDialog from "@/components/consent/ConsentGateDialog";
import { HelpOverlay } from "@/components/help/HelpOverlay";
import { configHotkeyLabels } from "@/components/hotkey/hotkey-utils";
import { ConnectionStatusScreen } from "@/components/layout/ConnectionStatusScreen";
import { Sidebar } from "@/components/layout/Sidebar";
import { TitleBar } from "@/components/layout/TitleBar";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useAsrBackendDisabledToast } from "@/hooks/useAsrBackendDisabledToast";
import { useConnectingProgress } from "@/hooks/useConnectingProgress";
import { useConnection } from "@/hooks/useConnection";
import { useConnectionToasts } from "@/hooks/useConnectionToasts";
import { useConsentRequiredEvent } from "@/hooks/useConsentRequiredEvent";
import { useDeviceLostToast } from "@/hooks/useDeviceLostToast";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { useGlobalKeyboardShortcuts } from "@/hooks/useGlobalKeyboardShortcuts";
import { useHelpOverlayShortcut } from "@/hooks/useHelpOverlayShortcut";
import { useLastResortUnloadedToast } from "@/hooks/useLastResortUnloadedToast";
import { useLinuxWindowButtons } from "@/hooks/useLinuxWindowButtons";
import { useLlmPolishFailedToast } from "@/hooks/useLlmPolishFailedToast";
import { useNavigateEvent } from "@/hooks/useNavigateEvent";
import { useNavigation } from "@/hooks/useNavigation";
import { useNetworkOnline } from "@/hooks/useNetworkOnline";
import { useOnboardingComplete } from "@/hooks/useOnboardingComplete";
import { useOnboardingRouteGuard } from "@/hooks/useOnboardingRouteGuard";
import { usePasteFailedToast } from "@/hooks/usePasteFailedToast";
import { usePython } from "@/hooks/usePython";
import { useRouteChangeFocus } from "@/hooks/useRouteChangeFocus";
import { useSidebarAutoCollapse } from "@/hooks/useSidebarAutoCollapse";
import { useSoundFeedback } from "@/hooks/useSoundFeedback";
import { useTheme } from "@/hooks/useTheme";
import { useWindowMaximized } from "@/hooks/useWindowMaximized";
import { getLocale, setLocale, useT } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
// Route→component mapping + per-route code splitting live in
// router/PageSwitch.tsx (Home eager, the other 9 pages lazy). App
// stays pure wiring: hooks, overlays, layout.
import { PageSwitch } from "@/router/PageSwitch";
import { prefetchRouteChunks } from "@/router/prefetch";
import { useAppStore } from "@/stores/appStore";
import type { WindowBridge } from "@/types/ipc";

export default function App() {
	const t = useT();

	// Auto-update feature (docs/auto-update-feature.md §10.1):
	// network-is-back trigger — calls the `check_offline_pack_update` IPC
	// command on the false → true `online` transition so the slim
	// core re-fetches the pack manifest (and, consent-gated,
	// restarts a background download). C-DATA-1 category-2 allowed
	// (silent update check against the GitHub API); the download is
	// gated on `config.offline_pack_consent`.
	useNetworkOnline();

	// ── Routing (extracted to useNavigation) ──────────────────────
	const {
		currentPage,
		navigate,
		replace,
		goBack,
		goForward,
		canGoBack,
		canGoForward,
	} = useNavigation();

	// Route guard: protect onboarding from completed users —
	// extracted to `useOnboardingRouteGuard` (the `replace`
	// history-swap semantics and the field-level `config`
	// selector live there).
	useOnboardingRouteGuard({ currentPage, replace });

	const hotkeyFromConfig = useAppStore((s) => s.config?.hotkey);
	const repasteHotkeyFromConfig = useAppStore((s) => s.config?.repaste_hotkey);

	// Privacy: when the app starts hidden in the background (autostart
	// with VT_START_HIDDEN=1), the renderer still boots and restores the
	// last persisted page from localStorage (vt_nav_state). If that page
	// is "microphone", the Microphone page's live level monitor would
	// start immediately and activate the OS mic indicator while the
	// window is still hidden. Redirect to "home" in that specific case.
	// The check is deferred by ~900ms so a normal foreground launch
	// (which briefly starts hidden before ready-to-show) is not
	// misclassified as background — it becomes visible within the grace
	// period and the redirect is cancelled via visibilitychange.
	useEffect(() => {
		if (typeof document === "undefined") return;
		if (currentPage !== "microphone") return;
		if (document.visibilityState === "visible") return;
		let cancelled = false;
		const timer = setTimeout(() => {
			if (
				!cancelled &&
				typeof document !== "undefined" &&
				document.visibilityState !== "visible"
			) {
				// Still hidden after grace period — genuine background
				// autostart with persisted microphone page. Use replace
				// to avoid polluting back/forward history.
				replace("home");
			}
		}, 900);
		const onVisible = () => {
			if (document.visibilityState === "visible") {
				cancelled = true;
				clearTimeout(timer);
				document.removeEventListener("visibilitychange", onVisible);
			}
		};
		document.addEventListener("visibilitychange", onVisible);
		return () => {
			cancelled = true;
			clearTimeout(timer);
			document.removeEventListener("visibilitychange", onVisible);
		};
	}, [currentPage, replace]);

	// a11y / WCAG 2.4.2 Page Titled: keep `document.title` in sync
	// with the active route — extracted to `useDocumentTitle` (the
	// settings-section registry keys and the locale re-title live
	// there).
	useDocumentTitle({ currentPage, t });

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

	// Route-chunk prefetch (router/prefetch.ts): warm every lazy page
	// chunk during idle time so the first navigation to each page
	// renders from React.lazy's module cache instead of waiting on a
	// dynamic import. Runs ONCE on mount; hover/focus on sidebar items
	// (prefetchPage) covers the pre-idle window.
	useEffect(() => {
		prefetchRouteChunks();
	}, []);

	// a11y / focus management on route change: move keyboard focus
	// to `<main id="main-content">` whenever `currentPage` changes so screen
	// reader + keyboard users aren't stranded on the previously-focused
	// nav item after a route transition. Extracted to
	// `useRouteChangeFocus` (the skip link + `tabIndex={-1}` plumbing
	// stays in the App shell; the skip-first-run guard lives there).
	useRouteChangeFocus(currentPage);

	useSoundFeedback();

	// "?" key opens a help overlay listing keyboard shortcuts.
	// Extracted to `useHelpOverlayShortcut` — returns the
	// open flag + stable open/close callbacks.
	const { showHelpOverlay, openHelp, closeHelp } = useHelpOverlayShortcut();

	// ── Window-chrome state ───────────────────────────────────────
	// Sidebar collapse state + the narrow-viewport auto-collapse rule
	// (only the wide→narrow transition and the initial narrow mount
	// force a collapse; the user's manual toggle wins otherwise) are
	// extracted to `useSidebarAutoCollapse`.
	const { sidebarCollapsed, setSidebarCollapsed } = useSidebarAutoCollapse();

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
	// Extracted to `useWindowMaximized` — queries the native
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
	// Page validation (route-table `isKnownPage`), the consent-field
	// Settings deep-link, and the legacy-literal → Privacy override
	// are extracted to `useNavigateEvent` — the entry file stays
	// wiring-only.
	useNavigateEvent({ navigate });

	// paste_failed toast — extracted to `usePasteFailedToast`.
	usePasteFailedToast(t);

	// Degradation-event toasts — the typed-but-previously-unsubscribed
	// server push events. Each hook is the SINGLE consumer of its event
	// and shows ONE actionable localized notification naming what
	// degraded + what to do:
	//   - device_lost also flips the shared Microphone-page state
	//     (meter pause) via deviceLostStore.
	//   - llm_polish_failed covers the silent "transcription delivered
	//     raw" path of the optional AI-polish step.
	//   - asr_backend_disabled covers the recoverable engine-fallback
	//     case (asr_last_resort_unloaded — the TERMINAL case — has its
	//     own hook above).
	useDeviceLostToast(t, () => navigate("microphone"));
	useLlmPolishFailedToast(t);
	useAsrBackendDisabledToast(t, () => navigate("models"));

	// asr_last_resort_unloaded toast — surfaces the Models-page pointer
	// as an IN-APP toast so the user still sees it when OS tray
	// notifications are disabled (the tray path is gated behind the
	// "Show Notifications" toggle). The toast's "Open Models" action
	// mirrors the host notification's ``click_path: "/models"``.
	useLastResortUnloadedToast(t, () => navigate("models"));

	// consent_required — unified point-of-use consent gate (GDPR
	// Art. 9 etc.): the backend publishes this event when a
	// consent-gated action is refused (dictation start, cloud
	// providers, LLM polish, offline pack). Every consent field
	// opens the SAME in-app dialog — "Allow? [Allow / Cancel]" —
	// with the exact toggle deep-link as the secondary action;
	// dictation refusals are retried after granting (Allow →
	// toggle_dictation) so the user never leaves the flow.
	// Extracted to `useConsentRequiredEvent` (the dictation-retry
	// field set comes from `lib/consentGate`'s registry-derived
	// `DICTATION_RETRY_CONSENT_FIELDS`).
	useConsentRequiredEvent({ call });

	// Connecting progress — backend `download_progress` events,
	// ref-gated so the update is skipped while connected (the screen
	// that reads the value is not rendered then). Extracted to
	// `useConnectingProgress`, which also clears the value on any
	// transition away from "connecting".
	const connectingProgress = useConnectingProgress(connectionStatus);

	// Stable callbacks so React.memo on <TitleBar>/<HelpOverlay> can
	// short-circuit when their other props haven't changed. The
	// `setSidebarCollapsed` dep is the useState setter returned by
	// `useSidebarAutoCollapse` — referentially stable, so the callback
	// identity is stable too.
	const handleToggleSidebar = useCallback(
		() => setSidebarCollapsed((c) => !c),
		[setSidebarCollapsed],
	);
	// open/close callbacks come from `useHelpOverlayShortcut` —
	// they are already stable (memoized with empty deps).

	// Onboarding-complete handler extracted to `useOnboardingComplete`
	// navigate home + re-apply the theme from the saved config.
	const handleOnboardingComplete = useOnboardingComplete({
		navigate,
		call,
		reloadThemeFromConfig,
	});

	const { dictationLabel, repasteLabel } = configHotkeyLabels({
		hotkey: hotkeyFromConfig,
		repaste_hotkey: repasteHotkeyFromConfig,
	});

	// Linux window-button layout — resolved by `useLinuxWindowButtons`
	// (field-level config/system selectors + a single memo) and passed
	// to the (memoized) TitleBar as one stable prop. No-op on
	// Windows/macOS (TitleBar ignores the prop there).
	const linuxWindowButtons = useLinuxWindowButtons();

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
					// The icon-only theme control lives in the title bar's
					// window-control cluster (the sidebar no longer carries
					// it). Same store-backed mode + change handler as
					// before — single source of theme state.
					themeMode={themeMode}
					onThemeChange={handleThemeChange}
					linuxWindowButtons={linuxWindowButtons}
					currentPage={currentPage}
				/>

				<div className="flex min-h-0 flex-1">
					<Sidebar
						currentPage={currentPage}
						onNavigate={navigate}
						collapsed={sidebarCollapsed}
					/>

					<div className="flex min-w-0 flex-1 flex-col">
						<main
							id="main-content"
							tabIndex={-1}
							// Focus is moved programmatically to this landmark after
							// navigation (see the useEffect above) so screen readers and
							// keyboard users land at the top of the new page. The element
							// carries no visible focus decoration: the old focus ring
							// framed the whole page window whenever it was focused (e.g.
							// after any click inside the content area) and was reported
							// as an annoying border around the page — focus is moved
							// silently instead.
							// Clean-window: no left/top panel border around the
							// content area. The bg contrast against the
							// --bg-subtle wrapper still separates content from
							// chrome without a hard frame line.
							// 1px frame around the page window, drawn with the theme's
							// own --border token at 10% opacity so it reads as a faint
							// separation line and blends with every theme (light, dark,
							// and custom palettes all define --border).
							className="flex-1 overflow-y-auto bg-(--bg) focus:outline-none rounded-l-lg border border-border/5"
							style={{ scrollbarGutter: "stable" }}
						>
							{connectionStatus === "connected" ? (
								<PageSwitch
									page={currentPage}
									navigate={navigate}
									onOnboardingComplete={handleOnboardingComplete}
								/>
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

				{/* Unified point-of-use consent dialog — mounted once;
                                    opened by any consent-gated flow via openConsentGate() */}
				<ConsentGateDialog />

				{/* Help overlay extracted to <HelpOverlay /> */}
				<HelpOverlay
					open={showHelpOverlay}
					onClose={closeHelp}
					dictationLabel={dictationLabel}
					repasteLabel={repasteLabel}
				/>

				{/* Split the screen-reader live region
                                    into THREE regions (recording / connection-error /
                                    connection-recovery) — see A11yLiveRegions. */}
				<A11yLiveRegions
					recordingState={recordingState}
					currentPage={currentPage}
					connectionStatus={connectionStatus}
					prevConnectionRef={prevConnectionRef}
				/>
			</div>
		</TooltipProvider>
	);
}
