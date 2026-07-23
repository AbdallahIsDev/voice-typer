import {
	ClipboardPasteIcon,
	Mic02Icon,
	Share08Icon,
	StopIcon,
	Undo02Icon,
} from "@hugeicons/core-free-icons";
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
// SOUND-FIX-004 / RW-10: sound feedback moved to App-level useSoundFeedback
// hook so cues play on every page (delegates to @/lib/sound-manager).
import { computeShareStats, useStatsShare } from "@/hooks/useStatsShare";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";
import type { HistoryRecord, RecordingState, TodayStats } from "@/types/ipc";

// Module-level cache — persists across page navigations AND app restarts
// (localStorage) so the homepage renders instantly on re-visit instead of
// flashing empty.
let _cachedRecent: HistoryRecord[] = [];
let _cachedStats: TodayStats | null = null;

const RECENT_CACHE_KEY = "vt_home_recent_cache";
const STATS_CACHE_KEY = "vt_home_stats_cache";
const FIRST_RECORD_CELEBRATED_KEY = "vt_first_recording_celebrated";
// PVT-fix-6: lowered from 60s → 5s. A genuinely stuck transcription is
// obvious within seconds; 60s of silence is far too patient.
const FORCE_CANCEL_DELAY_MS = 5_000;
const LAST_TEXT_AUTO_CLEAR_MS = 5_000;

function normalizeHotkey(raw: string): string {
	return raw.replace(/[<>]/g, "").toUpperCase();
}

function loadCachedRecent(): HistoryRecord[] {
	if (_cachedRecent.length > 0) return _cachedRecent;
	try {
		const raw = localStorage.getItem(RECENT_CACHE_KEY);
		if (raw) {
			const parsed = JSON.parse(raw);
			if (Array.isArray(parsed)) _cachedRecent = parsed as HistoryRecord[];
		}
	} catch (e) {
		// localStorage unavailable or payload malformed — non-fatal.
		console.warn("[Home] loadCachedRecent failed:", e);
	}
	return _cachedRecent;
}

function loadCachedStats(): TodayStats | null {
	if (_cachedStats !== null) return _cachedStats;
	try {
		const raw = localStorage.getItem(STATS_CACHE_KEY);
		if (raw) {
			const parsed = JSON.parse(raw);
			if (
				parsed &&
				typeof parsed === "object" &&
				typeof (parsed as { count?: unknown }).count === "number"
			) {
				_cachedStats = parsed as TodayStats;
			}
		}
	} catch (e) {
		// localStorage unavailable or payload malformed — non-fatal.
		console.warn("[Home] loadCachedStats failed:", e);
	}
	return _cachedStats;
}

function persistRecent(recent: HistoryRecord[]): void {
	_cachedRecent = recent;
	try {
		localStorage.setItem(RECENT_CACHE_KEY, JSON.stringify(recent));
	} catch (e) {
		// Quota exceeded or unavailable — non-fatal.
		console.warn("[Home] persistRecent failed:", e);
	}
}

function persistStats(stats: TodayStats): void {
	_cachedStats = stats;
	try {
		localStorage.setItem(STATS_CACHE_KEY, JSON.stringify(stats));
	} catch (e) {
		// Quota exceeded or unavailable — non-fatal.
		console.warn("[Home] persistStats failed:", e);
	}
}

// FIX-15 (CR-14): aligned with `voice_typer/server/tray_icon.py:277-284`
// color-blind-safe palette so the Home status pill matches the tray icon.
const STATUS_COLORS: Record<string, string> = {
	idle: "#787878",
	recording: "#2ECC71",
	transcribing: "#3498DB",
	loading: "#F39C12",
	cancelling: "#F39C12",
	error: "#E74C3C",
};

// NEW-I18N-FIX: resolves at render time so the pill honours the current
// locale on every render (not just at module-import time).
function statusLabelFor(key: string): string {
	switch (key) {
		case "recording":
			return t("home.recording");
		case "transcribing":
			return t("home.transcribing");
		case "loading":
			return t("home.loading");
		case "cancelling":
			return t("home.cancelling");
		case "error":
			return t("home.error");
		default:
			return t("home.ready");
	}
}

function statusKeyFor(state: RecordingState, hasError: boolean): string {
	if (state === "error" && hasError) return "error";
	return state;
}

// NOTE: App.tsx prop passing will be removed by EC-FIX-13.
// EC-FIX-14 (BACKLOG-004): Home now subscribes to recordingState /
// lastError via the appStore and obtains `navigate` via the
// useNavigation hook directly, eliminating prop drilling from App.
//
// ── Extracted subcomponents (PVT-062) ─────────────────────────────────
//
// PVT-fix-10: removed `aria-live="polite"` from this `<output>` — the
// App-level sr-only live region in App.tsx already announces state
// changes; a duplicate live region on the pill causes double-announcement.
function RecordingStatusPill({
	statusColor,
	statusLabel,
	isRecording,
}: {
	statusColor: string;
	statusLabel: string;
	isRecording: boolean;
}) {
	return (
		<output className="flex items-center gap-2 animate-fade-in">
			<span
				className={cn(
					"h-2 w-2 rounded-full transition-colors duration-300",
					isRecording && "animate-pulse",
				)}
				style={{ backgroundColor: statusColor }}
				aria-hidden
			/>
			<span
				key={statusLabel}
				className="text-[11px] font-medium uppercase tracking-wide text-(--text-muted) transition-opacity duration-200 animate-fade-in"
			>
				{statusLabel}
			</span>
		</output>
	);
}

// PVT-fix-9: spinner overlay shown on the mic button while `toggling` is
// true. PVT-fix-4 (PVT-065): the button is disabled during `transcribing`
// so clicks aren't silently swallowed by the backend.
function MicToggleButton({
	isRecording,
	toggling,
	disabled,
	onClick,
	label,
}: {
	isRecording: boolean;
	toggling: boolean;
	disabled: boolean;
	onClick: () => void;
	label: string;
}) {
	return (
		<div className="relative">
			{isRecording && (
				<span className="absolute inset-0 rounded-full animate-pulse-ring" />
			)}
			<button
				type="button"
				onClick={onClick}
				disabled={disabled}
				aria-label={label}
				title={label}
				className={cn(
					"press-scale relative z-10 flex h-21 w-21 items-center justify-center rounded-full",
					"transition-all duration-200 ease-out",
					"focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/30",
					"hover:scale-105",
					isRecording
						? "bg-black/15 dark:bg-white/18 hover:bg-black/25 dark:hover:bg-white/28"
						: "bg-destructive animate-glow-pulse hover:shadow-[0_8px_32px_rgba(255,51,51,0.5)]",
				)}
			>
				<HugeiconsIcon
					icon={isRecording ? StopIcon : Mic02Icon}
					strokeWidth={1.625}
					className={cn(
						"h-8 w-8 text-white transition-opacity",
						toggling && "opacity-30",
					)}
				/>
				{toggling && (
					<span
						aria-hidden
						className="pointer-events-none absolute inset-0 flex items-center justify-center"
					>
						<span className="h-7 w-7 animate-spin rounded-full border-2 border-white/80 border-t-transparent" />
					</span>
				)}
			</button>
		</div>
	);
}

function LastTranscriptionPreview({
	text,
	onUndo,
	onRepaste,
}: {
	text: string;
	onUndo: () => void;
	onRepaste: () => void;
}) {
	return (
		<div className="w-130 max-w-full rounded-[10px] bg-(--bg-subtle) px-4 py-3">
			<p className="line-clamp-2 overflow-hidden text-ellipsis text-[13px] text-(--text-muted)">
				{text}
			</p>
			<div className="mt-2 flex items-center justify-end gap-1">
				<Button
					variant="ghost"
					size="sm"
					onClick={onUndo}
					disabled={!text}
					title={t("home.undoAria")}
					aria-label={t("home.undoAria")}
					className="gap-1.5 text-xs text-(--text-muted) hover:text-(--text-primary)"
				>
					<HugeiconsIcon
						icon={Undo02Icon}
						strokeWidth={2}
						className="h-3.5 w-3.5"
					/>
					{t("home.undo")}
				</Button>
				<Button
					variant="ghost"
					size="sm"
					onClick={onRepaste}
					disabled={!text}
					title={t("home.repasteAria")}
					aria-label={t("home.repasteAria")}
					className="gap-1.5 text-xs text-(--text-muted) hover:text-(--text-primary)"
				>
					<HugeiconsIcon
						icon={ClipboardPasteIcon}
						strokeWidth={2}
						className="h-3.5 w-3.5"
					/>
					{t("home.repaste")}
				</Button>
			</div>
		</div>
	);
}

// PVT-fix-3 (PVT-064): surface recording errors. Previously `lastError`
// was tracked in the store but never rendered on Home — errors were
// invisible to the user (only the status pill colour changed to red).
// This card renders below the status pill whenever recordingState is
// "error", showing the backend's error message plus a Retry button.
function RecordingErrorCard({
	message,
	onRetry,
	retrying,
}: {
	message: string;
	onRetry: () => void;
	retrying: boolean;
}) {
	return (
		<div
			role="alert"
			className="flex w-130 max-w-full items-start gap-3 rounded-[10px] border border-destructive/30 bg-destructive/10 px-4 py-3"
		>
			<HugeiconsIcon
				icon={StopIcon}
				strokeWidth={2}
				className="mt-0.5 h-4 w-4 shrink-0 text-destructive"
				aria-hidden
			/>
			<div className="min-w-0 flex-1">
				<p className="text-[13px] font-medium text-destructive">
					{t("home.errorTitle")}
				</p>
				<p className="mt-0.5 line-clamp-3 overflow-hidden text-ellipsis text-[12px] text-(--text-muted)">
					{message}
				</p>
			</div>
			<Button
				variant="outline"
				size="sm"
				onClick={onRetry}
				disabled={retrying}
				className="shrink-0 gap-1.5 border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
			>
				{retrying && (
					<span
						aria-hidden
						className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent"
					/>
				)}
				{t("home.retry")}
			</Button>
		</div>
	);
}

// PVT-fix-8: previously used `get_today_stats` and checked `count === 1`
// — this triggered on the first dictation of ANY day, not the user's
// lifetime first. We now check `get_history({limit: 1})` and celebrate
// only when the user has exactly one historical record (this just-added
// one). The flag is persisted to localStorage so we never celebrate twice.
function useFirstRecordingCelebration(
	call: <T = unknown>(
		type: string,
		data?: Record<string, unknown>,
	) => Promise<T>,
) {
	return useCallback(async () => {
		try {
			if (localStorage.getItem(FIRST_RECORD_CELEBRATED_KEY) === "1") return;
		} catch {
			// localStorage unavailable — treat as not-celebrated.
			return;
		}
		try {
			const history = await call<HistoryRecord[]>("get_history", { limit: 1 });
			if (Array.isArray(history) && history.length === 1) {
				toast.success(t("home.firstDictationTitle"), {
					description: t("home.firstDictationDesc"),
					duration: 6000,
				});
				try {
					localStorage.setItem(FIRST_RECORD_CELEBRATED_KEY, "1");
				} catch (e) {
					// localStorage unavailable — non-fatal.
					console.warn("[Home] setItem first-record-celebrated failed:", e);
				}
			}
		} catch (e) {
			// Non-critical — skip celebration if history fetch fails.
			console.warn("[Home] first-recording get_history failed:", e);
		}
	}, [call]);
}

export default function Home() {
	// EC-FIX-14 (BACKLOG-004): subscribe to the store directly instead
	// of receiving recordingState / lastError as props from App.tsx.
	const recordingState = useAppStore((s) => s.recordingState);
	const lastError = useAppStore((s) => s.lastError);
	// EC-FIX-14: obtain `navigate` directly from the navigation hook
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
	const [stats, setStats] = useState<TodayStats | null>(loadCachedStats);
	const [recent, setRecent] = useState<HistoryRecord[]>(loadCachedRecent);
	// Only show a loading spinner when we have NO cached data to render.
	const [initialLoading, setInitialLoading] = useState(
		() => loadCachedStats() === null && loadCachedRecent().length === 0,
	);
	const [toggling, setToggling] = useState(false);
	const [cfg, setCfg] = useState<VoiceTyperConfig | null>(null);
	const { agoLabel, markUpdated } = useLastUpdated();
	const [refreshing, setRefreshing] = useState(false);
	const { imageRef, shareAsImage } = useStatsShare();

	// PVT-G5-054 (session 5): track mount state so async callbacks that
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

	// NEW-TS-006: timer refs declared BEFORE the usePythonEvent handlers
	// that use them (avoids temporal-dead-zone errors).
	const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	// UX-025: timer that auto-clears lastText after 5s of idle.
	const lastTextTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

	// Shared refresh routine — used by both `transcription_final` and
	// `history_changed` handlers (F11-FIX + R7-F13 consolidation).
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
					persistRecent(newRecent);
					setRecent(newRecent);
				}
				if (newStats) {
					persistStats(newStats);
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
				setHotkey(normalizeHotkey(cfg?.hotkey ?? "<f2>"));
			} catch (e) {
				console.warn("[Home] initial get_config failed:", e);
			}
			try {
				const s = await call<TodayStats>("get_today_stats");
				if (cancelled) return;
				if (s) {
					persistStats(s);
					setStats(s);
				}
			} catch (e) {
				console.warn("[Home] initial get_today_stats failed:", e);
			}
			try {
				const h = await call<HistoryRecord[]>("get_history", { limit: 4 });
				if (cancelled) return;
				const recs = h ?? [];
				persistRecent(recs);
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
			// PVT-G5-054 (session 5): guard against setState-after-unmount.
			// Promise.allSettled awaits three concurrent IPC calls; if the
			// user navigated away mid-refresh, all subsequent setX calls
			// would land on an unmounted component. `mountedRef` is flipped
			// to `false` by the unmount cleanup effect declared above.
			if (!mountedRef.current) return;
			if (cfgTry.status === "fulfilled") {
				setCfg(cfgTry.value);
				setHotkey(normalizeHotkey(cfgTry.value?.hotkey ?? "<f2>"));
			}
			if (sTry.status === "fulfilled" && sTry.value) {
				persistStats(sTry.value);
				setStats(sTry.value);
			}
			if (hTry.status === "fulfilled") {
				const recs = hTry.value ?? [];
				persistRecent(recs);
				setRecent(recs);
			}
			markUpdated();
		} finally {
			if (mountedRef.current) setRefreshing(false);
		}
	}, [call, markUpdated]);

	// UX-016 + UX-7: status_change listener — re-fetches the hotkey
	// (so the chip stays in sync after Settings changes) and tracks
	// entry into "transcribing" so we can show "Force cancel" after
	// FORCE_CANCEL_DELAY_MS. Using the event payload (rather than the
	// recordingState prop) means we capture the transition at source.
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
				setHotkey(normalizeHotkey(cfg?.hotkey ?? "<f2>"));
			} catch (e) {
				console.warn("[Home] status_change reloadHotkey get_config failed:", e);
			}
		};
		reloadHotkey();
		return () => {
			cancelled = true;
		};
	});

	// UX-7: surface "Force cancel" after FORCE_CANCEL_DELAY_MS in "transcribing".
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

	// UX-9: subscribe to download_progress events emitted while a
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
			// UX-025: auto-clear lastText after 5s of idle so the previous
			// transcription isn't exposed on a shared/locked screen.
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
			// PVT-045 (session 2): also clear lastTextTimer to prevent
			// setLastText("") firing on an unmounted component if a
			// transcription arrived within 5s of navigation.
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
		} finally {
			setToggling(false);
		}
	}, [call]);

	// UX-1: undo the last transcription (backend sends backspaces).
	const handleUndo = useCallback(async () => {
		try {
			await call("undo_last");
		} catch (err) {
			console.error("Undo failed:", err);
			// PVT-fix-5 (PVT-069): dedicated error key, not the button label.
			toast.error(t("home.undoFailed"));
		}
		setLastText("");
		if (lastTextTimer.current) {
			clearTimeout(lastTextTimer.current);
			lastTextTimer.current = null;
		}
	}, [call]);

	// UX-23: re-paste the most recent transcription.
	const handleRepaste = useCallback(async () => {
		try {
			await call("repaste_last");
		} catch (err) {
			console.error("Re-paste failed:", err);
			toast.error(t("home.repasteFailed"));
		}
	}, [call]);

	// UX-7: force-cancel an in-flight transcription that has been
	// running too long.
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

			{/* PVT-064: surface recording errors below the status pill. */}
			{recordingState === "error" && lastError && (
				<RecordingErrorCard
					message={lastError}
					onRetry={handleToggle}
					retrying={toggling}
				/>
			)}

			{/* UX-9: thin progress bar under the status pill while a model
			    download is in flight. */}
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

			{/* UX-7: subtle amber "Force cancel" affordance shown after
			    FORCE_CANCEL_DELAY_MS in "transcribing" state. */}
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
				// PVT-065: disable during `transcribing` so clicks aren't silently swallowed.
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
				<LastTranscriptionPreview
					text={lastText}
					onUndo={handleUndo}
					onRepaste={handleRepaste}
				/>
			)}

			{stats && (
				<div className="mt-4 w-full">
					<div className="mb-3 flex items-center justify-between">
						<span className="text-xs font-medium text-(--text-muted) capitalize tracking-wide">
							{t("home.todayStats")}
						</span>
						<div className="flex items-center gap-2">
							{/* F4: "Last updated" indicator + manual refresh button. */}
							<LastUpdatedIndicator
								agoLabel={agoLabel}
								onRefresh={handleManualRefresh}
								refreshing={refreshing}
							/>
							<Button
								variant="outline"
								size="sm"
								onClick={shareStats}
								disabled={!cfg || stats.count === 0}
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

			{/* When there's no cached stats AND we're still loading the
			    initial data, show a small spinner that preserves layout
			    height (avoids the "page is empty" flash). */}
			{!stats && initialLoading && (
				<section
					className="mt-4 w-full flex items-center justify-center py-6"
					aria-label={t("home.loadingTodayStatsAria")}
				>
					<Spinner />
				</section>
			)}

			{/* Hidden share image capture target. EXPORT-FIX: `position:fixed;
			    left:-9999` caused Chromium's paint optimization to skip painting
			    the element, so html-to-image captured a 0×0 blank image. Using
			    clip-path keeps the element painted while hiding it. */}
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

			{recent.length > 0 ? (
				<ActivityList
					items={recent}
					lineClamp={2}
					title={t("home.recentActivity")}
					showViewAll
					onViewAll={() => navigate("history")}
				/>
			) : (
				initialLoading && (
					<section
						className="mt-4 w-full flex items-center justify-center py-6"
						aria-label={t("home.loadingRecentAria")}
					>
						<Spinner />
					</section>
				)
			)}
		</div>
	);
}
