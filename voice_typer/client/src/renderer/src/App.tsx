import { useCallback, useEffect, useRef, useState } from "react";
import { APP_NAME } from "@/branding";
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
import { useConnection } from "@/hooks/useConnection";
import { useConnectionToasts } from "@/hooks/useConnectionToasts";
import { useDeviceLostToast } from "@/hooks/useDeviceLostToast";
import { useGlobalKeyboardShortcuts } from "@/hooks/useGlobalKeyboardShortcuts";
import { useHelpOverlayShortcut } from "@/hooks/useHelpOverlayShortcut";
import { useLastResortUnloadedToast } from "@/hooks/useLastResortUnloadedToast";
import { useLlmPolishFailedToast } from "@/hooks/useLlmPolishFailedToast";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useNavigation } from "@/hooks/useNavigation";
import { useNetworkOnline } from "@/hooks/useNetworkOnline";
import { useOnboardingComplete } from "@/hooks/useOnboardingComplete";
import { usePasteFailedToast } from "@/hooks/usePasteFailedToast";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSoundFeedback } from "@/hooks/useSoundFeedback";
import { useTheme } from "@/hooks/useTheme";
import { useWindowMaximized } from "@/hooks/useWindowMaximized";
import { getLocale, setLocale, useT } from "@/i18n/i18n";
import {
	consentBodyKey,
	isConsentField,
	openConsentGate,
} from "@/lib/consentGate";
import { cn } from "@/lib/utils";
// Route→component mapping + per-route code splitting live in
// router/PageSwitch.tsx (Home eager, the other 9 pages lazy). App
// stays pure wiring: hooks, overlays, layout.
import { PageSwitch } from "@/router/PageSwitch";
import { isKnownPage } from "@/router/routes";
import { useAppStore } from "@/stores/appStore";
import type { Page, WindowBridge } from "@/types/ipc";

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
		const navData = (data ?? {}) as Record<string, unknown>;
		const path = typeof navData.path === "string" ? navData.path : undefined;
		if (path) {
			const page = path.replace(/^\//, "");
			if (isKnownPage(page)) {
				// consent_field — deep-link to a specific Settings consent
				// row (used by CLICKABLE OS notifications: the main
				// process broadcasts navigate {path: "/settings",
				// consent_field} when the user clicks the toast; Settings
				// consumes the ``consentField`` option and scrolls to /
				// highlights the exact toggle).
				//
				// When the legacy "settings" parent literal is sent
				// WITH a consent_field, the deep-link must land on the
				// Privacy sub-page (where the consent toggles live), not
				// the General default. The useNavigation.navigate action
				// redirects bare "settings" to "settingsGeneral" — so we
				// override the target here to "settingsPrivacy" when a
				// consent_field is present (the user's intent is "open the
				// consent row", not "open Settings General"). The
				// pendingConsentField transient field carries the row
				// hint to the Privacy sub-page via the same navigate call.
				const consentField =
					typeof navData.consent_field === "string"
						? navData.consent_field
						: undefined;
				const targetPage: Page =
					consentField && page === "settings"
						? "settingsPrivacy"
						: (page as Page);
				navigate(targetPage, consentField ? { consentField } : undefined);
			} else {
				console.warn(`[renderer:App] ignoring unknown page path: "${page}"`);
			}
		}
		return undefined;
	});

	// paste_failed toast — extracted to `usePasteFailedToast` (EO-28).
	usePasteFailedToast(t);

	// Degradation-event toasts — the typed-but-previously-unsubscribed
	// server push events. Each hook is the SINGLE consumer of its event
	// and shows ONE actionable localized notification naming what
	// degraded + what to do:
	//   - device_lost also flips the shared Microphone-page state
	//     (meter pause + recovery banner) via deviceLostStore.
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
	// Art. 9 etc.). The backend publishes this event when a consent-
	// gated action is refused: dictation start without
	// ``voice_biometric_consent`` (recording_lifecycle.py — the path
	// for entry points the renderer can't gate client-side: F2 hotkey,
	// tray click action, sandboxed bubble window), cloud-provider
	// consents, the LLM-polish consent (enhancement_steps.py), the
	// offline-pack consent (update_check.py), etc. Every consent field
	// opens the SAME in-app dialog — "Allow? [Allow / Cancel]" — with
	// the exact toggle deep-link as the secondary action. Dictation
	// refusals are retried after granting (Allow → toggle_dictation),
	// so the user never leaves the flow to dig through Settings.
	// The HuggingFace ``{provider, model}`` shape (no consent_field)
	// is handled by the model-download flow, not here.
	usePythonEvent("consent_required", (data): (() => void) | undefined => {
		const payload = (data ?? {}) as {
			consent_field?: string;
		};
		const field = payload.consent_field;
		if (!field || !isConsentField(field)) {
			return undefined;
		}
		// Dictation-start refusals can be retried after granting: the
		// dialog's Allow handler re-invokes toggle_dictation (start is
		// the only consent-gated direction). Other consent gates have
		// no re-runnable action from here — granting the consent is
		// enough; the user retries the action themselves.
		const dictationField =
			field === "voice_biometric_consent" ||
			field === "cloud_openai_consent" ||
			field === "cloud_groq_consent" ||
			field === "cloud_deepgram_consent";
		openConsentGate({
			consentField: field,
			bodyKey: consentBodyKey(field),
			onAllow: dictationField ? () => call("toggle_dictation") : undefined,
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

	const { dictationLabel, repasteLabel } = configHotkeyLabels({
		hotkey: hotkeyFromConfig,
		repaste_hotkey: repasteHotkeyFromConfig,
	});

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
							className="flex-1 overflow-y-auto bg-(--bg) focus:outline-none rounded-l-lg border border-border/10"
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
