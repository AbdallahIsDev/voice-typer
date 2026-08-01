//(): Home.tsx was a 949-line monolith
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
// R7-F13) continue to work. Pure structural refactor — no behaviour
// changes.
//
// R7-F13 contract: `debouncedRefreshFromEvent` is declared via
// `useCallback` and passed to BOTH the `transcription_final` and
// `history_changed` `usePythonEvent` subscriptions (single callback
// identity). The R7-F13 test greps Home.tsx source for this pattern, so
// it stays here in the composition root rather than moving into a hook.

import { Share08Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
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
	//(BACKLOG-004): subscribe to the store directly instead
	// of receiving recordingState / lastError as props from App.tsx.
	const recordingState = useAppStore((s) => s.recordingState);
	const lastError = useAppStore((s) => s.lastError);
	//obtain `navigate` directly from the navigation hook
	// instead of receiving it as an `onNavigate` prop from App.tsx.
	const { navigate } = useNavigation();
	const { call } = usePython();
	const celebrateFirstRecording = useFirstRecordingCelebration(call);

	const [hotkey, setHotkey] = useState("F2");
	const [lastText, setLastText] = useState("");
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

	// Shared refresh routine — used by both `transcription_final` and
	// `history_changed` handlers (F11-FIX + R7-F13 consolidation).
	//
	// R7-F13: declared via `useCallback` and passed to BOTH usePythonEvent
	// subscriptions below so they share a single callback identity (the
	// test greps Home.tsx for this declaration).
	const debouncedRefreshFromEvent = useCallback(():
		| (() => void)
		| undefined => {
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

	// ── Initial data load (config + today stats + recent history) ──
	useEffect(() => {
		let cancelled = false;
		const load = async () => {
			try {
				const cfg = await call<VoiceTyperConfig>("get_config");
				if (!cancelled) setCfg(cfg);
				if (cancelled) return;
				setHotkey(normalizeHotkey(cfg?.hotkey ?? HOTKEY_DEFAULT));
			} catch (e) {
				console.warn("[Home] initial get_config failed:", e);
			}
			try {
				const s = await call<TodayStats>("get_today_stats");
				if (cancelled) return;
				if (s) {
					persistStats(cachedStatsRef, s);
					setStats(s);
				}
			} catch (e) {
				console.warn("[Home] initial get_today_stats failed:", e);
			}
			try {
				const h = await call<HistoryRecord[]>("get_history", { limit: 4 });
				if (cancelled) return;
				const recs = h ?? [];
				persistRecent(cachedRecentRef, recs);
				setRecent(recs);
			} catch (e) {
				console.warn("[Home] initial get_history failed:", e);
			}
			if (!cancelled) {
				setInitialLoading(false);
				markUpdated();
			}
		};
		load();
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

	//+ : status_change listener — re-fetches the hotkey
	// (so the chip stays in sync after Settings changes) and tracks
	// entry into "transcribing" so we can show "Force cancel" after
	// FORCE_CANCEL_DELAY_MS.
	usePythonEvent("status_change", (data): (() => void) | undefined => {
		const status = typeof data?.status === "string" ? data.status : "";
		if (status === "transcribing") {
			setTranscribeStartedAt((prev) => prev ?? Date.now());
			setShowForceCancel(false);
		} else {
			setTranscribeStartedAt(null);
			setShowForceCancel(false);
		}
		let cancelled = false;
		const reloadHotkey = async () => {
			try {
				const cfg = await call<VoiceTyperConfig>("get_config");
				if (cancelled) return;
				setHotkey(normalizeHotkey(cfg?.hotkey ?? HOTKEY_DEFAULT));
			} catch (e) {
				console.warn("[Home] status_change reloadHotkey get_config failed:", e);
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
		setToggling(true);
		try {
			await call("toggle_dictation");
		} catch (err) {
			console.error("Toggle dictation failed:", err);
			toast.error(t("home.toggleFailed"));
		} finally {
			setToggling(false);
		}
	}, [call]);

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

	const isRecording = recordingState === "recording";
	const key = statusKeyFor(recordingState, !!lastError);
	const statusColor = STATUS_COLORS[key] ?? STATUS_COLORS.idle;
	const statusLabel = statusLabelFor(key);

	return (
		<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col items-center justify-center gap-5 px-6 py-4">
			<RecordingStatusPill
				statusColor={statusColor}
				statusLabel={statusLabel}
				isRecording={isRecording}
			/>

			{recordingState === "error" && lastError && (
				<RecordingErrorCard
					message={lastError}
					onRetry={handleToggle}
					retrying={toggling}
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
				disabled={
					toggling ||
					recordingState === "loading" ||
					recordingState === "transcribing"
				}
				onClick={handleToggle}
				label={isRecording ? t("home.stopDictation") : t("home.startDictation")}
			/>

			<p className="flex items-center gap-2 text-[13px] text-(--text-muted)">
				<span>{t("home.press")}</span>
				<span className="inline-flex items-center justify-center rounded-md border border-border bg-(--bg-subtle) px-1.75 py-0.75 font-mono text-[11px] font-medium text-(--text-primary) shadow-[0_1px_3px_rgba(0,0,0,0.06),inset_0_1px_0_rgba(255,255,255,0.4)] dark:shadow-[0_1px_3px_rgba(0,0,0,0.15),inset_0_1px_0_rgba(255,255,255,0.06)] leading-none tracking-tight">
					{hotkey}
				</span>
				<span>{t("home.pressOrClick")}</span>
			</p>

			{lastText && (
				<div aria-live="polite">
					<LastTranscriptionPreview
						text={lastText}
						onUndo={handleUndo}
						onRepaste={handleRepaste}
					/>
				</div>
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
				{stats && cfg && (
					<StatsShareImage stats={computeShareStats(stats, cfg.asr_backend)} />
				)}
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
					onViewAll={() => navigate("history")}
				/>
			)}
		</div>
	);
}
