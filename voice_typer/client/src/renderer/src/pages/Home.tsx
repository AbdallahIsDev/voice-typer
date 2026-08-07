// Home.tsx was a 949-line monolith
// mixing layout, data-fetching, business logic, and 4 inline
// sub-components + 1 inline hook. It is now a thin composition root
// that imports the extracted pieces from `./home/`:
//
//   - `./home/lib/constants.ts`    — cache keys, timing constants, STATUS_COLORS
//   - `./home/lib/status.ts`       — normalizeHotkey, statusLabelFor, statusKeyFor
//   - `./home/lib/cache.ts`        — loadCachedRecent/Stats, persistRecent/Stats
//   - `./home/hooks/useFirstRecordingCelebration.ts` — first-run celebration
//   - `./home/components/RecordingStatusPill.tsx`     — status pill
//   - `./home/components/MicToggleButton.tsx`         — mic toggle button
//   - `./home/components/LastTranscriptionPreview.tsx` — last transcription card
//   - `./home/components/RecordingErrorCard.tsx`       — error card
//
// The `export default function Home` signature is unchanged so App.tsx
// routing and existing tests (Home.test.tsx, pages-improvements.test.tsx
// ) continue to work. Pure structural refactor — no behaviour
// changes.
//
// contract: `debouncedRefreshFromEvent` is declared via
// `useCallback` and passed to BOTH the `transcription_final` and
// `history_changed` `usePythonEvent` subscriptions (single callback
// identity). The regression test greps Home.tsx source for this pattern, so
// it stays here in the composition root rather than moving into a hook.

import { Share08Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import ActivityList from "@/components/dashboard/ActivityList";
import StatCards from "@/components/dashboard/StatCards";
import { StatsShareImage } from "@/components/dashboard/StatsShareImage";
import { Spinner } from "@/components/feedback/Spinner";
import { Button } from "@/components/ui/button";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import {
	canShareStats,
	computeShareStats,
	useStatsShare,
} from "@/hooks/useStatsShare";
import { t } from "@/i18n/i18n";
import { VOICE_BIOMETRIC_CONSENT_FIELD } from "@/lib/consent";
import { HOTKEY_DEFAULT } from "@/pages/onboarding/lib/constants";
import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";
import type { HistoryRecord, TodayStats } from "@/types/ipc";
import { LastTranscriptionPreview } from "./home/components/LastTranscriptionPreview";
import { MicToggleButton } from "./home/components/MicToggleButton";
import { RecordingErrorCard } from "./home/components/RecordingErrorCard";
import { RecordingStatusPill } from "./home/components/RecordingStatusPill";
import { useFirstRecordingCelebration } from "./home/hooks/useFirstRecordingCelebration";
import {
	loadCachedRecent,
	loadCachedStats,
	persistRecent,
	persistStats,
} from "./home/lib/cache";
import {
	FORCE_CANCEL_DELAY_MS,
	LAST_TEXT_AUTO_CLEAR_MS,
	STATUS_COLORS,
} from "./home/lib/constants";
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
	const celebrateFirstRecording = useFirstRecordingCelebration(call);

	const [hotkey, setHotkey] = useState("F2");
	const [lastText, setLastText] = useState("");
	// QV-49(a): live MM:SS recording timer — elapsed seconds while
	// recording, reset to 0 when a new recording starts.
	const [elapsedSec, setElapsedSec] = useState(0);
	const isRecording = recordingState === "recording";
	useEffect(() => {
		if (!isRecording) {
			setElapsedSec(0);
			return;
		}
		setElapsedSec(0);
		const id = window.setInterval(() => {
			setElapsedSec((s) => s + 1);
		}, 1000);
		return () => window.clearInterval(id);
	}, [isRecording]);
	const [downloadPct, setDownloadPct] = useState<number | null>(null);
	const [transcribeStartedAt, setTranscribeStartedAt] = useState<number | null>(
		null,
	);
	const [showForceCancel, setShowForceCancel] = useState(false);
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
	const [toggling, setToggling] = useState(false);
	const [cfg, setCfg] = useState<VoiceTyperConfig | null>(null);
	const { agoLabel, markUpdated } = useLastUpdated();
	const [refreshing, setRefreshing] = useState(false);
	const { imageRef, shareAsImage } = useStatsShare();

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

	//timer refs declared BEFORE the usePythonEvent handlers
	// that use them (avoids temporal-dead-zone errors).
	const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	//timer that auto-clears lastText after 5s of idle.
	const lastTextTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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
				const [newRecent, newStats] = await Promise.all([
					call<HistoryRecord[]>("get_history", { limit: 5 }),
					call<TodayStats>("get_today_stats"),
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
					"[Home] event refresh (get_history/get_today_stats) failed:",
					e,
				);
			}
		}, 500);
		return undefined;
	}, [call]);

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
	useEffect(() => {
		let cancelled = false;
		const cfgSettled = call<VoiceTyperConfig>("get_config")
			.then((cfg) => {
				if (cancelled) return;
				setCfg(cfg);
				setHotkey(normalizeHotkey(cfg?.hotkey ?? HOTKEY_DEFAULT));
			})
			.catch((e) => console.warn("[Home] initial get_config failed:", e));
		const statsSettled = call<TodayStats>("get_today_stats")
			.then((s) => {
				if (cancelled) return;
				if (s) {
					persistStats(cachedStatsRef, s);
					setStats(s);
				}
			})
			.catch((e) => console.warn("[Home] initial get_today_stats failed:", e));
		const historySettled = call<HistoryRecord[]>("get_history", { limit: 4 })
			.then((h) => {
				if (cancelled) return;
				const recs = h ?? [];
				persistRecent(cachedRecentRef, recs);
				setRecent(recs);
			})
			.catch((e) => console.warn("[Home] initial get_history failed:", e));
		Promise.allSettled([cfgSettled, statsSettled, historySettled]).then(() => {
			if (cancelled) return;
			setInitialLoading(false);
			markUpdated();
		});
		return () => {
			cancelled = true;
		};
	}, [call, markUpdated]);

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

	//+ : status_change listener — tracks entry into "transcribing" so
	// we can show "Force cancel" after FORCE_CANCEL_DELAY_MS. The
	// hotkey is no longer re-fetched here — see the `config_changed`
	// handler below. The `status_change` event fires on every
	// recording → transcribing → idle transition, so a per-event
	// `get_config` round-trip was wasted work (ER-62).
	usePythonEvent("status_change", (data): (() => void) | undefined => {
		const status = typeof data?.status === "string" ? data.status : "";
		if (status === "transcribing") {
			setTranscribeStartedAt((prev) => prev ?? Date.now());
			setShowForceCancel(false);
		} else {
			setTranscribeStartedAt(null);
			setShowForceCancel(false);
		}
		return undefined;
	});

	//+ : config_changed listener — re-fetches the hotkey when the
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
					"[Home] config_changed reloadHotkey get_config failed:",
					e,
				);
			}
		};
		reloadHotkey();
		return () => {
			cancelled = true;
		};
	});

	//surface "Force cancel" after FORCE_CANCEL_DELAY_MS in "transcribing".
	useEffect(() => {
		if (transcribeStartedAt === null) return;
		const timeout = setTimeout(
			() => setShowForceCancel(true),
			FORCE_CANCEL_DELAY_MS,
		);
		return () => clearTimeout(timeout);
	}, [transcribeStartedAt]);

	// Belt-and-suspenders: sync transcribe tracking with the recordingState
	// prop in case the page mounts mid-transcription.
	useEffect(() => {
		if (recordingState === "transcribing") {
			setTranscribeStartedAt((prev) => prev ?? Date.now());
		} else {
			setTranscribeStartedAt(null);
			setShowForceCancel(false);
		}
	}, [recordingState]);

	//subscribe to download_progress events emitted while a
	// HuggingFace model download is in flight.
	usePythonEvent(
		"download_progress",
		(data: Record<string, unknown> | undefined): (() => void) | undefined => {
			const pct = data?.percent;
			if (typeof pct === "number" && pct >= 0 && pct <= 100) {
				setDownloadPct(pct);
			}
			return undefined;
		},
	);
	useEffect(() => {
		if (recordingState !== "loading") setDownloadPct(null);
	}, [recordingState]);

	// transcription_final: update lastText + auto-clear, refresh
	// recent/stats, celebrate the first ever transcription.
	usePythonEvent("transcription_final", (data): (() => void) | undefined => {
		if (typeof data?.text === "string" && data.text.trim()) {
			setLastText(data.text);
			if (lastTextTimer.current) clearTimeout(lastTextTimer.current);
			lastTextTimer.current = setTimeout(
				() => setLastText(""),
				LAST_TEXT_AUTO_CLEAR_MS,
			);
			celebrateFirstRecording();
		}
		debouncedRefreshFromEvent();
		return undefined;
	});

	usePythonEvent("history_changed", debouncedRefreshFromEvent);

	usePythonEvent("recording_started", (): (() => void) | undefined => {
		setLastText("");
		if (lastTextTimer.current) {
			clearTimeout(lastTextTimer.current);
			lastTextTimer.current = null;
		}
		return undefined;
	});

	// Clean up pending refresh timer on unmount.
	useEffect(() => {
		return () => {
			if (refreshTimer.current) clearTimeout(refreshTimer.current);
			if (lastTextTimer.current) {
				clearTimeout(lastTextTimer.current);
				lastTextTimer.current = null;
			}
		};
	}, []);

	const shareStats = useCallback(() => {
		if (!stats || !cfg) return;
		shareAsImage("voice-typer-stats");
	}, [stats, cfg, shareAsImage]);

	const handleToggle = useCallback(async () => {
		// GDPR Art. 9 gate: the backend refuses to start recording without
		// ``voice_biometric_consent`` — but the refusal is silent over
		// IPC (``toggle_dictation`` returns ``ack`` and only the tray
		// notification fires). Gate client-side so the user gets the
		// in-app consent prompt + Settings → Privacy deep-link instead
		// of a dead button. The backend gate (recording_lifecycle.py)
		// remains the enforcement backstop for hotkey/tray-triggered
		// dictation.
		if (cfg && !cfg.voice_biometric_consent) {
			toast.warning(t("notify.recording_controller.consent_required_body"), {
				duration: 6000,
				action: {
					label: t("microphone.consentRequiredAction"),
					// Deep-link to the EXACT consent toggle — Settings
					// consumes the ``consentField`` navigate option and
					// scrolls to / highlights the Voice Biometric row.
					onClick: () =>
						navigate("settings", {
							consentField: VOICE_BIOMETRIC_CONSENT_FIELD,
						}),
				},
			});
			return;
		}
		setToggling(true);
		try {
			await call("toggle_dictation");
		} catch (err) {
			console.error("Toggle dictation failed:", err);
			toast.error(t("home.toggleFailed"));
		} finally {
			setToggling(false);
		}
		// ``t`` is a stable module-level import — not a render-scoped
		// value, so it must NOT be listed as a dep (biome
		// useExhaustiveDependencies flags it as unnecessary).
	}, [call, cfg, navigate]);

	const handleUndo = useCallback(async () => {
		try {
			await call("undo_last");
		} catch (err) {
			console.error("Undo failed:", err);
			toast.error(t("home.undoFailed"));
		}
		setLastText("");
		if (lastTextTimer.current) {
			clearTimeout(lastTextTimer.current);
			lastTextTimer.current = null;
		}
	}, [call]);

	const handleRepaste = useCallback(async () => {
		try {
			await call("repaste_last");
		} catch (err) {
			console.error("Re-paste failed:", err);
			toast.error(t("home.repasteFailed"));
		}
	}, [call]);

	const handleForceCancel = useCallback(async () => {
		try {
			await call("force_cancel_transcription");
			toast.success(t("home.forceCancel"));
		} catch (err) {
			console.error("Force cancel failed:", err);
			toast.error(t("home.forceCancelFailed"));
		}
	}, [call]);

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
	// (Renamed from `shareStats` to avoid colliding with the
	// `shareStats` click-handler callback declared above.)
	const asrBackend = cfg?.asr_backend;
	const shareImageStats = useMemo(
		() => (stats && asrBackend ? computeShareStats(stats, asrBackend) : null),
		[stats, asrBackend],
	);

	const key = statusKeyFor(recordingState, !!lastError);
	// noUncheckedIndexedAccess: `STATUS_COLORS[key]` is `string | undefined`.
	// `statusKeyFor` always returns a known key, but the index access still
	// widens to `string | undefined` under strict TS; fall back to a literal
	// sentinel that matches `STATUS_COLORS.idle` so we never pass `undefined`
	// to the `RecordingStatusPill` `statusColor` prop.
	const statusColor = STATUS_COLORS[key] ?? "#787878";
	const statusLabel = statusLabelFor(key) ?? t("home.ready");

	// Inline status text shown between the mic button and the hotkey
	// hint. The mic button is disabled during `transcribing` and
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

	return (
		<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col items-center justify-center gap-5 px-6 py-4">
			<div className="flex items-center gap-3">
				<RecordingStatusPill
					statusColor={statusColor}
					statusLabel={statusLabel}
					isRecording={isRecording}
				/>
				{isRecording && (
					<span
						className="font-mono text-sm tabular-nums text-(--text-muted)"
						// QV-49(a): accessible live timer while recording. role="timer"
						// gives the span a role that supports aria-label (plain
						// spans don't accept aria-label) and is the semantically
						// correct ARIA role for a live count-up timer.
						role="timer"
						aria-label={t("home.timerAria", {
							duration: `${String(Math.floor(elapsedSec / 60)).padStart(2, "0")}:${String(elapsedSec % 60).padStart(2, "0")}`,
						})}
					>
						{String(Math.floor(elapsedSec / 60)).padStart(2, "0")}:
						{String(elapsedSec % 60).padStart(2, "0")}
					</span>
				)}
			</div>

			{recordingState === "error" && lastError && (
				<RecordingErrorCard
					message={lastError}
					onRetry={handleToggle}
					retrying={toggling}
					onOpenMicSettings={() => navigate("microphone")}
				/>
			)}

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
						className="h-0.5 rounded-full bg-amber-500"
					/>
				</div>
			)}

			{showForceCancel && recordingState === "transcribing" && (
				<button
					type="button"
					onClick={handleForceCancel}
					className="text-xs text-amber-700 hover:text-amber-800 hover:underline dark:text-amber-500 dark:hover:text-amber-400 transition-colors"
					aria-label={t("home.forceCancelHint")}
				>
					{t("home.forceCancelHint")}
				</button>
			)}

			<MicToggleButton
				isRecording={isRecording}
				toggling={toggling}
				disabled={micDisabled}
				onClick={handleToggle}
				label={isRecording ? t("home.stopDictation") : t("home.startDictation")}
				disabledReason={micDisabledReason ?? undefined}
			/>

			{inlineStatus && (
				// The <output> element maps to the implicit `status` role,
				// so screen readers treat this as a live region (announced
				// on change) without forcing a duplicate `aria-live` that
				// would compete with App.tsx's sr-only live region.
				<output
					aria-live="polite"
					className="block text-[13px] text-(--text-muted) animate-fade-in"
				>
					{inlineStatus}
				</output>
			)}

			<p className="flex items-center gap-2 text-[13px] text-(--text-muted)">
				<span>{t("home.press")}</span>
				<span className="inline-flex items-center justify-center rounded-md border border-border bg-(--bg-subtle) px-1.75 py-0.75 font-mono text-[11px] font-medium text-(--text-primary) shadow-[0_1px_3px_rgba(0,0,0,0.06),inset_0_1px_0_rgba(255,255,255,0.4)] dark:shadow-[0_1px_3px_rgba(0,0,0,0.15),inset_0_1px_0_rgba(255,255,255,0.06)] leading-none tracking-tight">
					{hotkey}
				</span>
				<span>{t("home.pressOrClick")}</span>
			</p>

			{lastText && (
				<output
					aria-live="polite"
					// QV-9: the transcription preview text is wrapped in the
					// semantic HTML5 live region element (<output>) so screen
					// readers announce freshly arrived transcriptions.
					className="block"
				>
					<LastTranscriptionPreview
						text={lastText}
						onUndo={handleUndo}
						onRepaste={handleRepaste}
					/>
				</output>
			)}

			{stats && (
				<div className="mt-4 w-full">
					<div className="mb-3 flex items-center justify-between">
						<span className="text-xs font-medium text-(--text-muted) capitalize tracking-wide">
							{t("home.todayStats")}
						</span>
						<div className="flex items-center gap-2">
							<LastUpdatedIndicator
								agoLabel={agoLabel}
								onRefresh={handleManualRefresh}
								refreshing={refreshing}
							/>
							<Button
								variant="outline"
								size="sm"
								onClick={shareStats}
								disabled={
									!cfg ||
									!canShareStats({
										todayCount: stats.count,
										totalCount: recent.length > 0 ? 1 : 0,
									})
								}
								className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
							>
								<HugeiconsIcon
									icon={Share08Icon}
									strokeWidth={2}
									className="h-4 w-4"
								/>
								{t("home.shareStats")}
							</Button>
						</div>
					</div>
					<StatCards stats={stats} />
				</div>
			)}

			{!stats && initialLoading && (
				<section
					className="mt-4 w-full flex items-center justify-center py-6"
					aria-label={t("home.loadingTodayStatsAria")}
				>
					<Spinner />
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
				{shareImageStats && <StatsShareImage stats={shareImageStats} />}
			</div>

			{initialLoading && recent.length === 0 ? (
				<section
					className="mt-4 w-full flex items-center justify-center py-6"
					aria-label={t("home.loadingRecentAria")}
				>
					<Spinner />
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
