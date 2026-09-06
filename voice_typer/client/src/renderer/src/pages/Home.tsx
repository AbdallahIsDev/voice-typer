// Home.tsx was an 888-line monolith (now 744)
// mixing layout, data-fetching, business logic, and 4 inline
// sub-components + 1 inline hook. It is now a thin composition root
// that imports the extracted pieces from `./home/`:
//
//   - `./home/lib/constants.ts`    — cache keys, timing constants, STATUS_COLORS
//   - `./home/lib/status.ts`       — normalizeHotkey, statusLabelFor, statusKeyFor
//   - `./home/lib/cache.ts`        — loadCachedRecent/Stats, persistRecent/Stats
//   - `./home/hooks/useFirstRecordingCelebration.ts` — first-run celebration
//   - `./home/hooks/useLastTranscriptionPreview.ts`  — last-transcription card
//       state (text + quality + auto-clear timer), undo/repaste/discard, and
//       the `recording_started` reset
//   - `./home/hooks/useForceCancel.ts` — "Force cancel" state machine
//       (status_change transitions + reveal delay + store sync + cancel IPC)
//   - `./home/hooks/useDownloadProgressEvent.ts` — download-progress bar
//   - `./home/hooks/useDictationToggle.ts` — consent-gated dictation toggle
//   - `./home/components/MicToggleButton.tsx`         — mic toggle button
//   - `./home/components/RecordingStatusPill.tsx`     — status pill
//   - `./home/components/LastTranscriptionPreview.tsx` — last transcription card
//
// Status is kept minimal: the coloured status pill + a live MM:SS
// timer appear above the mic button, and a single dynamic line below
// it swaps between the default hotkey hint, the "Preparing offline
// engine…" message, and red error text (e.g. "No model selected")
// based on the current state.
//
// The `export default function Home` signature is unchanged so App.tsx
// routing and existing tests (Home.test.tsx, pages-improvements.test.tsx
// ) continue to work. Pure structural refactor — no behaviour
// changes.
//
// Wiring note: the `usePythonEvent` subscriptions stay in this
// composition root (the source-guard regression tests grep Home.tsx for
// them) and delegate their business logic to the hooks above — except
// `recording_started` (owned by useLastTranscriptionPreview) and
// `download_progress` (owned by useDownloadProgressEvent), whose
// subscriptions live inside their hooks.
//
// contract: `debouncedRefreshFromEvent` is declared via
// `useCallback` and passed to BOTH the `transcription_final` and
// `history_changed` `usePythonEvent` subscriptions (single callback
// identity). The regression test greps Home.tsx source for this pattern, so
// it stays here in the composition root rather than moving into a hook.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import ActivityList from "@/components/dashboard/ActivityList";
import { ShareStatsDialog } from "@/components/dashboard/ShareStatsDialog";
import StatCards from "@/components/dashboard/StatCards";
import { StatsShareImage } from "@/components/dashboard/StatsShareImage";
import { Spinner } from "@/components/feedback/Spinner";
import { HotkeyChips } from "@/components/hotkey/HotkeyChips";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { useLatestRef } from "@/hooks/useLatestRef";
import { useNavigation } from "@/hooks/useNavigation";
import { useOfflinePackDownload } from "@/hooks/useOfflinePackDownload";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import {
	canShareStats,
	computeShareStats,
	useStatsShare,
} from "@/hooks/useStatsShare";
import { t } from "@/i18n/i18n";
import { useThemePalette } from "@/lib/theme-palette";
import { formatDevice, formatModel } from "@/lib/utils/configDisplay";
import { HOTKEY_DEFAULT } from "@/pages/onboarding/lib/constants";
import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";
import type { HistoryRecord, TodayStats } from "@/types/ipc";
import { LastTranscriptionPreview } from "./home/components/LastTranscriptionPreview";
import { MicToggleButton } from "./home/components/MicToggleButton";
import { RecordingLevelBar } from "./home/components/RecordingLevelBar";
import { RecordingStatusPill } from "./home/components/RecordingStatusPill";
import { RecordingTimer } from "./home/components/RecordingTimer";
import { useDictationToggle } from "./home/hooks/useDictationToggle";
import { useDownloadProgressEvent } from "./home/hooks/useDownloadProgressEvent";
import { useFirstRecordingCelebration } from "./home/hooks/useFirstRecordingCelebration";
import { useForceCancel } from "./home/hooks/useForceCancel";
import { useLastTranscriptionPreview } from "./home/hooks/useLastTranscriptionPreview";
import {
	loadCachedRecent,
	loadCachedStats,
	persistRecent,
	persistStats,
} from "./home/lib/cache";
import { DEFAULT_STATUS_COLOR, STATUS_COLORS } from "./home/lib/constants";
import {
	normalizeHotkey,
	statusKeyFor,
	statusLabelFor,
} from "./home/lib/status";

export default function Home() {
	// Subscribe to the store directly instead of receiving
	// recordingState / lastError as props from App.tsx.
	const recordingState = useAppStore((s) => s.recordingState);
	const lastError = useAppStore((s) => s.lastError);
	//obtain `navigate` directly from the navigation hook
	// instead of receiving it as an `onNavigate` prop from App.tsx.
	const { navigate } = useNavigation();
	const { call } = usePython();

	// Ref mirrors of `call` / `markUpdated` so the mount-load effect
	// keeps `[]` deps. Both are useCallback-stable in production, but
	// test mocks return FRESH functions per render — depending on them
	// re-fires the initial load (get_config/get_today_stats/get_history
	// → setState → re-render → new call → loop → worker OOM). Same
	// pattern as useVocabulary.ts.
	const callRef = useLatestRef(call);
	const celebrateFirstRecording = useFirstRecordingCelebration(call);

	const [hotkey, setHotkey] = useState("F2");
	// The live MM:SS recording timer (elapsed seconds + its 1s interval)
	// is owned by <RecordingTimer /> (./home/components) — keeping the
	// per-second tick state here re-rendered the whole Home tree every
	// second. See RecordingTimer.tsx.
	const isRecording = recordingState === "recording";
	// Per-instance cache refs (replaced the prior module-level
	// `let _cachedRecent` / `let _cachedStats` mutable bindings).
	const cachedRecentRef = useRef<HistoryRecord[]>([]);
	const cachedStatsRef = useRef<TodayStats | null>(null);
	const [stats, setStats] = useState<TodayStats | null>(() =>
		loadCachedStats(cachedStatsRef),
	);
	const [recent, setRecent] = useState<HistoryRecord[]>(() =>
		loadCachedRecent(cachedRecentRef),
	);
	// Only show a loading spinner when we have NO cached data to render.
	const [initialLoading, setInitialLoading] = useState(
		() =>
			loadCachedStats(cachedStatsRef) === null &&
			loadCachedRecent(cachedRecentRef).length === 0,
	);
	const [cfg, setCfg] = useState<VoiceTyperConfig | null>(null);

	// ── Extracted event-concern hooks (./home/hooks/) ──
	// Each call owns one concern that used to live inline in this
	// root: the download-progress bar, the ephemeral last-
	// transcription preview (text + quality + auto-clear timer +
	// card actions), the force-cancel state machine, and the
	// consent-gated dictation toggle. `useDictationToggle` needs
	// `cfg` (the gate reads `voice_biometric_consent`), so the
	// wiring sits after the config state above.
	const downloadPct = useDownloadProgressEvent(recordingState);
	const {
		lastText,
		lastQuality,
		applyTranscriptionFinal,
		handleUndo,
		handleRepaste,
		handleDiscard,
	} = useLastTranscriptionPreview(call, celebrateFirstRecording);
	const forceCancel = useForceCancel(call);
	const { handleToggle, toggling, hasAttemptedDictation } = useDictationToggle(
		call,
		cfg,
	);
	const { agoLabel, markUpdated } = useLastUpdated();
	// Ref mirror of `markUpdated` (declared above via useLastUpdated) so
	// the mount-load effect keeps `[]` deps — see the callRef comment.
	const markUpdatedRef = useRef(markUpdated);
	useEffect(() => {
		markUpdatedRef.current = markUpdated;
	}, [markUpdated]);
	const [refreshing, setRefreshing] = useState(false);

	// Runtime-pack readiness — drives the "Preparing offline engine…"
	// banner. Local whisper / Parakeet transcription degrades silently
	// to "silent download starts, 'Preparing…' line, then works" when
	// the pack isn't ready (§4.9). Cloud transcription (Groq/OpenAI/
	// Deepgram) never needs the pack, so we suppress the banner when
	// the active ASR backend is a cloud one (§4.9: "works — cloud
	// never needs the pack").
	const { isReady: packReady } = useOfflinePackDownload();
	const {
		imageRef,
		downloadImage,
		saveImageAs,
		copyImageToClipboard,
		revealInFolder,
	} = useStatsShare();
	// Live theme palette for the share image — re-reads when the theme
	// changes so the exported PNG always matches the active preset.
	const themePalette = useThemePalette();

	// Track mount state so async callbacks that
	// outlive the component (notably `handleManualRefresh` below, which is
	// a useCallback and therefore cannot use the local-`cancelled`-plus-
	// cleanup-return pattern that the mount-time load effect above uses)
	// can short-circuit their setState calls after unmount. `useRef` avoids
	// the extra render a `useState` flip would cause.
	const mountedRef = useRef(true);
	useEffect(() => {
		mountedRef.current = true;
		return () => {
			mountedRef.current = false;
		};
	}, []);

	//timer ref declared BEFORE the usePythonEvent handlers
	// that use it (avoids temporal-dead-zone errors).
	const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

	// stale-data flag. Set to `true` when a `transcription_final`
	// or `history_changed` event arrives while the window is hidden
	// (document.visibilityState !== "visible"). The visibilitychange
	// listener below checks this flag on focus and triggers a single
	// debounced refresh — so background events don't fire 2 IPC calls
	// each (get_history + get_today_stats) while the user isn't looking
	// at the page. The next focus collapses the backlog into ONE fetch.
	const staleRef = useRef(false);

	// Shared refresh routine — used by both `transcription_final` and
	// `history_changed` handlers (refresh consolidation).
	//
	// declared via `useCallback` and passed to BOTH usePythonEvent
	// subscriptions below so they share a single callback identity (the
	// test greps Home.tsx for this declaration).
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract — .current must NOT become a dep
	const debouncedRefreshFromEvent = useCallback(():
		| (() => void)
		| undefined => {
		// skip the IPC round-trips when the window is hidden.
		// The visibilitychange listener below will trigger a single
		// refresh when the user returns to the page.
		if (
			typeof document !== "undefined" &&
			document.visibilityState !== "visible"
		) {
			staleRef.current = true;
			return undefined;
		}
		if (refreshTimer.current) clearTimeout(refreshTimer.current);
		refreshTimer.current = setTimeout(async () => {
			try {
				// Read the freshest bridge via the callRef mirror above —
				// keeps this callback's identity STABLE ([] deps) so the
				// visibilitychange effect below (deps
				// [debouncedRefreshFromEvent]) doesn't re-attach its
				// listener on every render under unstable test mocks.
				const [newRecent, newStats] = await Promise.all([
					callRef.current<HistoryRecord[]>("get_history", { limit: 5 }),
					callRef.current<TodayStats>("get_today_stats"),
				]);
				if (newRecent) {
					persistRecent(cachedRecentRef, newRecent);
					setRecent(newRecent);
				}
				if (newStats) {
					persistStats(cachedStatsRef, newStats);
					setStats(newStats);
				}
			} catch (e) {
				// Silently ignore — next manual load picks up fresh data.
				console.warn(
					"[renderer:Home] event refresh (get_history/get_today_stats) failed:",
					e,
				);
			}
		}, 500);
		return undefined;
	}, []);

	// refresh on focus when stale. When the window regains
	// visibility AND a stale flag was set by a background event, fire
	// a single debounced refresh. This collapses the "triple
	// subscription per dictation" pattern (Home + History + Dashboard
	// all subscribed to transcription_final) into at most ONE active
	// refresh — only the page the user is actually looking at refreshes.
	useEffect(() => {
		const onVisibility = () => {
			if (document.visibilityState === "visible" && staleRef.current) {
				staleRef.current = false;
				debouncedRefreshFromEvent();
			}
		};
		document.addEventListener("visibilitychange", onVisibility);
		return () => {
			document.removeEventListener("visibilitychange", onVisibility);
		};
	}, [debouncedRefreshFromEvent]);

	// ── Initial data load (config + today stats + recent history) ──
	// Parallelized — the three IPC calls are independent, so running
	// them concurrently cuts initial-load wall time from 3 sequential
	// round-trips (~15-150ms) to one (~5-50ms). Each call updates its
	// own state as soon as it settles (so e.g. `cfg`/`hotkey` aren't
	// blocked on a slow `get_history`), and `Promise.allSettled` is
	// used to mark the load complete once all three have settled —
	// mirroring the parallel-fetch pattern in `handleManualRefresh`.
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract — .current must NOT become a dep
	useEffect(() => {
		let cancelled = false;
		const cfgSettled = callRef
			.current<VoiceTyperConfig>("get_config")
			.then((cfg) => {
				if (cancelled) return;
				setCfg(cfg);
				setHotkey(normalizeHotkey(cfg?.hotkey ?? HOTKEY_DEFAULT));
			})
			.catch((e) =>
				console.warn("[renderer:Home] initial get_config failed:", e),
			);
		const statsSettled = callRef
			.current<TodayStats>("get_today_stats")
			.then((s) => {
				if (cancelled) return;
				if (s) {
					persistStats(cachedStatsRef, s);
					setStats(s);
				}
			})
			.catch((e) =>
				console.warn("[renderer:Home] initial get_today_stats failed:", e),
			);
		const historySettled = callRef
			.current<HistoryRecord[]>("get_history", {
				limit: 4,
			})
			.then((h) => {
				if (cancelled) return;
				const recs = h ?? [];
				persistRecent(cachedRecentRef, recs);
				setRecent(recs);
			})
			.catch((e) =>
				console.warn("[renderer:Home] initial get_history failed:", e),
			);
		Promise.allSettled([cfgSettled, statsSettled, historySettled]).then(() => {
			if (cancelled) return;
			setInitialLoading(false);
			markUpdatedRef.current();
		});
		return () => {
			cancelled = true;
		};
	}, []);

	// F4: manual refresh handler for the LastUpdatedIndicator button.
	const handleManualRefresh = useCallback(async () => {
		setRefreshing(true);
		try {
			const [cfgTry, sTry, hTry] = await Promise.allSettled([
				call<VoiceTyperConfig>("get_config"),
				call<TodayStats>("get_today_stats"),
				call<HistoryRecord[]>("get_history", { limit: 5 }),
			]);
			// Guard against setState-after-unmount.
			if (!mountedRef.current) return;
			if (cfgTry.status === "fulfilled") {
				setCfg(cfgTry.value);
				setHotkey(normalizeHotkey(cfgTry.value?.hotkey ?? HOTKEY_DEFAULT));
			}
			if (sTry.status === "fulfilled" && sTry.value) {
				persistStats(cachedStatsRef, sTry.value);
				setStats(sTry.value);
			}
			if (hTry.status === "fulfilled") {
				const recs = hTry.value ?? [];
				persistRecent(cachedRecentRef, recs);
				setRecent(recs);
			}
			markUpdated();
		} finally {
			if (mountedRef.current) setRefreshing(false);
		}
	}, [call, markUpdated]);

	// status_change listener — delegates to the force-cancel state
	// machine (tracks entry into "transcribing" so the "Force cancel"
	// affordance can reveal after FORCE_CANCEL_DELAY_MS). The hotkey
	// is NOT re-fetched here — see the `config_changed` handler
	// below: `status_change` fires on every recording → transcribing
	// → idle transition, so a per-event `get_config` round-trip
	// would be wasted work.
	usePythonEvent("status_change", (data): (() => void) | undefined => {
		forceCancel.applyStatusChange(data);
		return undefined;
	});

	// config_changed listener — re-fetches the hotkey when the
	// backend reports that Settings saved a new config (the
	// `config_changed` event is published by `apply_config` in
	// `config_handlers.py`). This replaces the per-status_change
	// `get_config` fetch: the hotkey only changes when Settings
	// saves, not on every recording-state transition. The initial
	// hotkey is loaded by the mount-time effect above.
	//
	// Also refreshes ``cfg`` (the full config snapshot) so the
	// GDPR ``voice_biometric_consent`` gate in ``handleToggle`` can
	// never go stale — e.g. consent granted in Settings must unblock
	// dictation immediately even if Home stays mounted.
	usePythonEvent("config_changed", (): (() => void) | undefined => {
		let cancelled = false;
		const reloadHotkey = async () => {
			try {
				const cfg = await call<VoiceTyperConfig>("get_config");
				if (cancelled) return;
				setHotkey(normalizeHotkey(cfg?.hotkey ?? HOTKEY_DEFAULT));
				setCfg(cfg);
			} catch (e) {
				console.warn(
					"[renderer:Home] config_changed reloadHotkey get_config failed:",
					e,
				);
			}
		};
		reloadHotkey();
		return () => {
			cancelled = true;
		};
	});

	// transcription_final: the text/quality half (preview state +
	// auto-clear timer + first-run celebration) is owned by
	// useLastTranscriptionPreview; the refresh half stays here so
	// the shared `debouncedRefreshFromEvent` identity contract is
	// preserved (the source-guard tests grep this root for it).
	usePythonEvent("transcription_final", (data): (() => void) | undefined => {
		applyTranscriptionFinal(data);
		debouncedRefreshFromEvent();
		return undefined;
	});

	usePythonEvent("history_changed", debouncedRefreshFromEvent);

	// Clean up the pending refresh timer on unmount. (The preview
	// auto-clear timer's unmount cleanup lives inside
	// useLastTranscriptionPreview.)
	useEffect(() => {
		return () => {
			if (refreshTimer.current) clearTimeout(refreshTimer.current);
		};
	}, []);

	const shareActions = {
		downloadImage,
		saveImageAs,
		copyImageToClipboard,
		revealInFolder,
	};

	// Stable callback for ActivityList's `onViewAll` so the
	// memo'd ActivityList (default export, React.memo) doesn't
	// re-render on every parent re-render. Previously the inline
	// `onViewAll={() => navigate("history")}` allocated a fresh
	// closure per render, defeating ActivityList's memo.
	const handleViewAllHistory = useCallback(() => {
		navigate("history");
	}, [navigate]);

	// Memoise the ShareStats object so its identity is stable
	// across unrelated re-renders (e.g. recordingState transitions,
	// hotkey changes). Without this, every Home re-render produced
	// a fresh `computeShareStats(...)` return value, defeating the
	// React.memo wrapper on StatsShareImage. Keyed on `stats` and
	// `cfg?.asr_backend` — the only inputs `computeShareStats` reads.
	// Home's share image derives from the today-stats cache + config
	// (no lifetime aggregates on this page) — `computeShareStats`
	// defaults the lifetime fields to today's values, and the model /
	// device come from the config snapshot.
	const asrBackend = cfg?.asr_backend;
	const shareImageStats = useMemo(
		() =>
			stats && asrBackend
				? computeShareStats(stats, asrBackend, {
						// Pre-formatted display values ("Tiny", "GPU") — the
						// share image renders them as-is.
						model: cfg?.model_size ? formatModel(cfg.model_size) : "",
						device: cfg?.device ? formatDevice(cfg.device) : "",
					})
				: null,
		[stats, asrBackend, cfg?.model_size, cfg?.device],
	);

	// Inline status text shown in the dynamic status line below the mic
	// button. The mic button is disabled during `transcribing` and
	// `loading`, so without an inline hint the user has no visual
	// explanation for why the button is unresponsive. The hint also
	// doubles as the `disabledReason` passed to MicToggleButton so
	// screen readers announce the same explanation on focus.
	//
	// - `transcribing` → "Transcribing… please wait"
	// - `loading` + no download percentage yet → "Downloading model…"
	//   (once `downloadPct` arrives, the progressbar below takes over
	//   and the inline hint is suppressed to avoid duplication).
	let inlineStatus: string | null = null;
	if (recordingState === "transcribing") {
		inlineStatus = t("home.transcribingHint");
	} else if (recordingState === "loading" && downloadPct === null) {
		inlineStatus = t("home.downloadingModel");
	}
	const micDisabled =
		toggling ||
		recordingState === "loading" ||
		recordingState === "transcribing";
	// `toggling` already shows the spinner overlay inside the button,
	// so only `loading` / `transcribing` need a textual reason.
	const micDisabledReason = micDisabled && !toggling ? inlineStatus : undefined;

	// Single dynamic status line under the mic button — ONE element that
	// swaps its content (and color) based on the current state, instead
	// of separate hotkey-hint / "Preparing offline engine…" / inline
	// status lines (or a status pill above the button):
	//   1. No model selected (the backend's `NO_MODEL_SIZE` sentinel,
	//      `model_size === ""`) → red "No model selected…" error.
	//   2. Recording error with a message → red error text.
	//   3. Transcribing / downloading model → the inline status hint.
	//   4. Offline engine still preparing (pack not ready AND the user
	//      attempted dictation) → "Preparing offline engine…".
	//   5. Otherwise → "Press <hotkey> or click to dictate".
	const noModelSelected = cfg?.model_size === "";
	const preparingOffline = !packReady && hasAttemptedDictation;
	let hint:
		| { variant: "error"; text: string }
		| { variant: "status"; text: string }
		| null = null;
	if (noModelSelected) {
		hint = { variant: "error", text: t("home.noModelSelectedHint") };
	} else if (recordingState === "error" && lastError) {
		hint = { variant: "error", text: lastError };
	} else if (inlineStatus) {
		hint = { variant: "status", text: inlineStatus };
	} else if (preparingOffline) {
		hint = { variant: "status", text: t("pack.preparingOfflineEngine") };
	}

	// Status pill — computed AFTER the dynamic line so the pill always
	// agrees with it: when the line below the button is showing an error
	// (no model selected, or a recording error with a message), the pill
	// flips to the `error` state instead of staying in the underlying
	// state (e.g. "Ready" while the page is actually broken).
	//
	// PILL/DESCRIPTION INVARIANT: the pill (`key`) and the description
	// line (`hint`) MUST be derived from the same underlying state pair
	// (`recordingState` + `lastError`, hydrated atomically by
	// `applyStatusWithReason` in useConnection.ts). Deriving either one
	// from an independent source re-opens the intermittent bug where the
	// pill shows ERROR while the description still shows the normal
	// dictate hint (or vice versa). `statusKeyFor` mirrors this contract:
	// an error state without a message falls back to the ready key so the
	// pill never advertises an error the description doesn't explain.
	const baseKey = statusKeyFor(recordingState, !!lastError);
	const key = hint?.variant === "error" ? "error" : baseKey;
	// noUncheckedIndexedAccess: `STATUS_COLORS[key]` is `string | undefined`.
	// `statusKeyFor` always returns a known key, but the index access still
	// widens to `string | undefined` under strict TS; fall back to the
	// shared idle sentinel so we never pass `undefined` to the
	// `RecordingStatusPill` `statusColor` prop.
	const statusColor = STATUS_COLORS[key] ?? DEFAULT_STATUS_COLOR;
	const statusLabel = statusLabelFor(key) ?? t("home.ready");

	return (
		<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col items-center justify-center gap-4 px-16 py-4">
			{downloadPct !== null && (
				<div
					className="h-0.5 w-32 rounded-full bg-(--bg-subtle)"
					role="progressbar"
					aria-valuemin={0}
					aria-valuemax={100}
					aria-valuenow={Math.round(downloadPct)}
					aria-label={t("home.downloadingModel")}
				>
					<div
						style={{ width: `${downloadPct}%` }}
						className="h-0.5 rounded-full bg-warning"
					/>
				</div>
			)}

			{forceCancel.showForceCancel && recordingState === "transcribing" && (
				<button
					type="button"
					onClick={forceCancel.handleForceCancel}
					className="text-xs text-warning hover:text-warning/80 hover:underline transition-colors"
					aria-label={t("home.forceCancelHint")}
				>
					{t("home.forceCancelHint")}
				</button>
			)}

			<div className="flex items-center gap-3">
				<RecordingStatusPill
					statusColor={statusColor}
					statusLabel={statusLabel}
					isRecording={isRecording}
				/>
				<RecordingTimer isRecording={isRecording} />
				{isRecording && <RecordingLevelBar />}
			</div>

			<MicToggleButton
				isRecording={isRecording}
				toggling={toggling}
				disabled={micDisabled}
				onClick={handleToggle}
				label={isRecording ? t("home.stopDictation") : t("home.startDictation")}
				disabledReason={micDisabledReason ?? undefined}
				error={recordingState === "error" && !!lastError}
			/>

			{/* Single dynamic status line under the mic button — see the
                                `hint` computation above for the state → content mapping.
                                This is Home's ONE status live region: the pill above is a
                                plain <div> (no implicit `status` role) and the recording
                                timer is role="timer" with explicit aria-live="off", so a
                                state change announces exactly once here — no double
                                announcements. Coarse transitions ("Recording started." /
                                "Ready." / …) are additionally covered by App.tsx's sr-only
                                region (app-level, visible on every page). Errors switch to
                                `role="alert"` so they're announced as alerts. */}
			<output
				aria-live="polite"
				role={hint?.variant === "error" ? "alert" : undefined}
				className={`flex items-center gap-2 text-[0.8125rem] animate-fade-in ${
					hint?.variant === "error" ? "text-destructive" : "text-(--text-muted)"
				}`}
			>
				{hint ? (
					noModelSelected && hint.variant === "error" ? (
						<button
							type="button"
							onClick={() => navigate("models")}
							className="inline-flex items-center gap-1 text-destructive underline decoration-destructive/50 underline-offset-4 transition-colors hover:decoration-destructive cursor-pointer"
						>
							{hint.text}
						</button>
					) : (
						hint.text
					)
				) : (
					<>
						<span>{t("home.press")}</span>
						<HotkeyChips keys={hotkey} />
						<span>{t("home.pressOrClick")}</span>
					</>
				)}
			</output>

			{lastText && (
				<output
					aria-live="polite"
					// the transcription preview text is wrapped in the
					// semantic HTML5 live region element (<output>) so screen
					// readers announce freshly arrived transcriptions.
					className="block"
				>
					<LastTranscriptionPreview
						text={lastText}
						onUndo={handleUndo}
						onRepaste={handleRepaste}
						onDiscard={handleDiscard}
						quality={lastQuality}
						onRedictate={
							isRecording || micDisabled
								? undefined
								: () => {
										void handleToggle();
									}
						}
					/>
				</output>
			)}

			{stats && (
				<div className="flex w-full flex-col gap-3">
					<div className="flex items-center justify-between">
						<span className="text-xs font-medium text-(--text-muted) capitalize tracking-wide">
							{t("home.todayStats")}
						</span>
						<div className="flex items-center gap-2">
							<LastUpdatedIndicator
								agoLabel={agoLabel}
								onRefresh={handleManualRefresh}
								refreshing={refreshing}
							/>
							<ShareStatsDialog
								actions={shareActions}
								stats={shareImageStats}
								palette={themePalette}
								disabled={
									!cfg ||
									!canShareStats({
										todayCount: stats.count,
										totalCount: recent.length > 0 ? 1 : 0,
									})
								}
							/>
						</div>
					</div>
					<StatCards stats={stats} />
				</div>
			)}

			{!stats && initialLoading && (
				<section
					className="w-full flex items-center justify-center py-6"
					aria-label={t("home.loadingTodayStatsAria")}
				>
					{/* Decorative — the wrapping <section aria-label>
                                            already supplies the accessible name; the bare
                                            Spinner's own role="img" aria-label="Loading" would
                                            compete with it. */}
					<Spinner decorative />
				</section>
			)}

			<div
				ref={imageRef}
				aria-hidden
				style={{
					position: "absolute",
					top: 0,
					left: 0,
					zIndex: -100,
					pointerEvents: "none",
					clipPath: "inset(50% 50% 50% 50%)",
				}}
			>
				{shareImageStats && (
					<StatsShareImage stats={shareImageStats} palette={themePalette} />
				)}
			</div>

			{initialLoading && recent.length === 0 ? (
				<section
					className="w-full flex items-center justify-center py-6"
					aria-label={t("home.loadingRecentAria")}
				>
					{/* Decorative — same reasoning as the
                                            today-stats section above. */}
					<Spinner decorative />
				</section>
			) : (
				<ActivityList
					items={recent}
					lineClamp={2}
					title={t("home.recentActivity")}
					showViewAll
					onViewAll={handleViewAllHistory}
				/>
			)}
		</div>
	);
}
