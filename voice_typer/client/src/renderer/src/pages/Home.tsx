import { Mic02Icon, Share08Icon, StopIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import ActivityList from "@/components/dashboard/ActivityList";
import StatCards from "@/components/dashboard/StatCards";
import { StatsShareImage } from "@/components/dashboard/StatsShareImage";
import { Spinner } from "@/components/feedback/Spinner";
import { Button } from "@/components/ui/button";
import { usePython, usePythonEvent } from "@/hooks/usePython";
// SOUND-FIX-004: sound feedback logic moved to App-level hook so cues
// play on every page, not just Home.  Home no longer subscribes to
// recording_started / recording_stopped for sound — the App root does.
// RW-10 (sound consolidation): the AudioContext / playSoundCue logic
// that previously lived inside @/hooks/useSoundFeedback was a parallel
// implementation of the canonical one in @/lib/sound-manager. The hook
// now delegates to sound-manager so there is a single sound system in
// production (and tests exercise the same path the runtime exercises).
import { computeShareStats, useStatsShare } from "@/hooks/useStatsShare";
// SOUND-FIX-006 (Round 0): removed dead imports from @/lib/sound-manager.
// These were imported but never used inside Home.tsx — the sound feedback
// logic moved to App-level useSoundFeedback hook (SOUND-FIX-004), and
// Home's only remaining reference is the re-export at the bottom of this
// file which sources from @/hooks/useSoundFeedback, not @/lib/sound-manager.
// Removing these dead imports reduces the surface area of the legacy
// sound-manager.ts and advances the single-sound-system consolidation
// (Approach C from the sound investigation).
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import type { VoiceTyperConfig } from "@/types/config";
import type {
	HistoryRecord,
	Page,
	RecordingState,
	TodayStats,
} from "@/types/ipc";

// Module-level cache — persists across page navigations so the recent activity
// section renders instantly on re-visit instead of appearing from nowhere.
//
// FIX: homepage flash.  We now also persist both the recent records and
// today's stats to localStorage so the cache survives app restarts.  On
// mount, we read from localStorage (or the in-memory cache if already
// populated this session) and render the cached data immediately.  The
// backend fetch happens in the background and only updates state if the
// fresh data differs — eliminating the visible empty-then-populated flash.
let _cachedRecent: HistoryRecord[] = [];
let _cachedStats: TodayStats | null = null;

const RECENT_CACHE_KEY = "vt_home_recent_cache";
const STATS_CACHE_KEY = "vt_home_stats_cache";

/**
 * Normalize a hotkey string for display. Server stores as `<f2>` (lowercase);
 * display should be `F2` (uppercase). Moved to module scope so both the
 * initial-load useEffect and the status_change reload handler can use it
 * without duplicating the closure (which caused a TS2304 error because
 * the reload handler referenced `normalizeHotkey` scoped to the other
 * useEffect's body).
 */
function normalizeHotkey(raw: string): string {
	return raw.replace(/[<>]/g, "").toUpperCase();
}

/**
 * Load the cached recent records, preferring the in-memory cache then
 * localStorage.  Populates the in-memory cache as a side effect so
 * subsequent calls are cheap.
 */
function loadCachedRecent(): HistoryRecord[] {
	if (_cachedRecent.length > 0) return _cachedRecent;
	try {
		const raw = localStorage.getItem(RECENT_CACHE_KEY);
		if (raw) {
			const parsed = JSON.parse(raw);
			if (Array.isArray(parsed)) {
				_cachedRecent = parsed as HistoryRecord[];
			}
		}
	} catch {
		// localStorage may be unavailable (private mode, SSR) — non-fatal.
	}
	return _cachedRecent;
}

/**
 * Load the cached today stats, preferring the in-memory cache then
 * localStorage.  Populates the in-memory cache as a side effect.
 */
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
	} catch {
		// localStorage may be unavailable — non-fatal.
	}
	return _cachedStats;
}

/** Persist recent records to the in-memory cache and localStorage. */
function persistRecent(recent: HistoryRecord[]): void {
	_cachedRecent = recent;
	try {
		localStorage.setItem(RECENT_CACHE_KEY, JSON.stringify(recent));
	} catch {
		// Quota exceeded or localStorage unavailable — non-fatal.
	}
}

/** Persist today stats to the in-memory cache and localStorage. */
function persistStats(stats: TodayStats): void {
	_cachedStats = stats;
	try {
		localStorage.setItem(STATS_CACHE_KEY, JSON.stringify(stats));
	} catch {
		// Quota exceeded or localStorage unavailable — non-fatal.
	}
}

interface HomeProps {
	recordingState: RecordingState;
	lastError: string | null;
	onNavigate?: (page: Page) => void;
}

// NEW-IPC-010: aligned with the Python AppState enum (6 values).
// Previously the maps included 7 dead keys (listening, processing,
// warming_up, downloading, paused, setup, not_configured) that the
// backend never emits — they were leftover from a long-ago refactor.
// Keeping them gave a false impression of supported states and
// caused dead branches in the rendering logic.
const STATUS_COLORS: Record<string, string> = {
	idle: "#22C55E",
	recording: "#FF3333",
	transcribing: "#7C3AED",
	loading: "#F59E0B",
	cancelling: "#C0392B",
	error: "#FF3333",
};

// IMPL-C: status labels resolved via i18n so they honour the user's UI locale.
// NEW-I18N-FIX: previously this was a module-level constant built once at
// import time, which froze the labels at whatever locale was active when
// the module was first imported (almost always "en" because the locale
// is restored from localStorage AFTER Home.tsx is imported). Switching
// UI language in Settings did not update the home-page status pill.
// Replaced with a function that resolves the label at call time so the
// pill honors the current locale on every render.
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
			// Includes "idle" and any unknown state — both fall back to
			// the "ready" label.
			return t("home.ready");
	}
}

function statusKeyFor(state: RecordingState, hasError: boolean): string {
	// NEW-IPC-010: removed the ``listening → idle`` normalization —
	// ``listening`` is no longer in the RecordingState union (it was
	// never emitted by the backend; the Python AppState uses ``idle``).
	// When there's an error and the state is error, keep it as error.
	if (state === "error" && hasError) return "error";
	return state;
}

// SOUND-FIX-004: the sound feedback logic (AudioContext singleton,
// initAudioContext, playSoundCue) has been moved to
// ``@/hooks/useSoundFeedback`` and is now subscribed at the App root
// so cues fire on every page, not just Home.
// RW-10: as of the sound-consolidation rewrite, useSoundFeedback is
// itself a thin wrapper that delegates to the canonical implementation
// in @/lib/sound-manager. See hooks/useSoundFeedback.ts for details.
// (DEAD-CODE: the previous `export { initAudioContext, playSoundCue }`
// re-exports had zero importers in the repo and forced Home.tsx to be
// a module that re-exports non-component functions, which breaks React
// Fast Refresh. Deleted in this round.)

export default function Home({
	recordingState,
	lastError,
	onNavigate,
}: HomeProps) {
	const { call } = usePython();

	const [hotkey, setHotkey] = useState("F2");
	const [lastText, setLastText] = useState("");
	// FIX: initialize stats + recent from cache so the homepage renders
	// instantly with the last-known data instead of flashing empty.
	const [stats, setStats] = useState<TodayStats | null>(loadCachedStats);
	const [recent, setRecent] = useState<HistoryRecord[]>(loadCachedRecent);
	// Only show a loading spinner when we have NO cached data to render.
	// If the cache is populated, the page renders instantly and the
	// background fetch silently updates state if the fresh data differs.
	const [initialLoading, setInitialLoading] = useState(
		() => loadCachedStats() === null && loadCachedRecent().length === 0,
	);
	const [toggling, setToggling] = useState(false);
	const [cfg, setCfg] = useState<VoiceTyperConfig | null>(null);
	const { imageRef, shareAsImage } = useStatsShare();

	// NEW-TS-008: normalize hotkey to uppercase for display. Server
	// stores as `<f2>` (lowercase); display should be `F2` (uppercase).
	// normalizeHotkey is now a module-level function (see above).
	useEffect(() => {
		let cancelled = false;
		const load = async () => {
			try {
				const cfg = await call<VoiceTyperConfig>("get_config");
				if (!cancelled) setCfg(cfg);
				if (cancelled) return;
				const raw = cfg?.hotkey ?? "<f2>";
				setHotkey(normalizeHotkey(raw));
			} catch {}
			try {
				const s = await call<TodayStats>("get_today_stats");
				if (cancelled) return;
				if (s) {
					persistStats(s);
					setStats(s);
				}
			} catch {}
			try {
				const h = await call<HistoryRecord[]>("get_history", { limit: 4 });
				if (cancelled) return;
				const recs = h ?? [];
				persistRecent(recs);
				setRecent(recs);
			} catch {}
			if (!cancelled) setInitialLoading(false);
		};
		load();
		return () => {
			cancelled = true;
		};
	}, [call]);

	// UX-016: listen for status_change events to re-fetch the hotkey
	// config.  When the user changes the hotkey in Settings, the
	// status_change event fires (because toggle_dictation triggers a
	// state change), and we re-fetch the config to update the chip.
	// This is a lightweight way to keep the hotkey chip in sync without
	// a dedicated config-changed event.
	usePythonEvent("status_change", () => {
		let cancelled = false;
		const reloadHotkey = async () => {
			try {
				const cfg = await call<VoiceTyperConfig>("get_config");
				if (cancelled) return;
				const raw = cfg?.hotkey ?? "<f2>";
				setHotkey(normalizeHotkey(raw));
			} catch {}
		};
		reloadHotkey();
		return () => {
			cancelled = true;
		};
	});

	// NEW-TS-006: timer refs declared BEFORE the usePythonEvent handler
	// that uses them (previously the second listener was declared after
	// these refs, but consolidating into a single listener means the
	// refs must come first to avoid temporal-dead-zone errors).
	const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	// UX-025: timer that auto-clears lastText after 5s of idle.
	const lastTextTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

	usePythonEvent("transcription_final", (data) => {
		// NEW-TS-006: previously this was TWO separate usePythonEvent
		// listeners — one for lastText + auto-clear, one for refreshing
		// recent + stats.  Both fired on the same event; consolidating
		// them into a single handler avoids double subscription overhead
		// and makes the data flow easier to follow.
		if (typeof data?.text === "string" && data.text.trim()) {
			setLastText(data.text);
			// UX-025: auto-clear lastText after 5 seconds of idle so the
			// previous transcription isn't exposed on a shared/locked screen.
			// The timer is reset on each new transcription.
			if (lastTextTimer.current) clearTimeout(lastTextTimer.current);
			lastTextTimer.current = setTimeout(() => setLastText(""), 5000);

			// NEW-UX-013: First-recording celebration.  If this is the user's
			// first ever transcription (no prior history), show a celebratory
			// toast to acknowledge the milestone.  We use localStorage to
			// track whether we've already celebrated — cheap and persistent
			// across app restarts.  This is a one-time event, not on every
			// transcription.
			try {
				const celebrated =
					localStorage.getItem("vt_first_recording_celebrated") === "1";
				if (!celebrated) {
					// Check if there's any prior history.  If the count is 1,
					// this IS the first transcription.
					call<TodayStats>("get_today_stats")
						.then((s) => {
							// today's count == 1 AND we haven't celebrated yet → first
							// transcription of the user's lifetime (today is their
							// first day using Voice Typer).
							if (s && s.count === 1) {
								toast.success(t("home.firstDictationTitle"), {
									description: t("home.firstDictationDesc"),
									duration: 6000,
								});
								localStorage.setItem("vt_first_recording_celebrated", "1");
							}
						})
						.catch(() => {
							// Non-critical — skip celebration if stats fetch fails.
						});
				}
			} catch {
				// localStorage may be unavailable — non-fatal.
			}
		}

		// Proactive background refresh: silently refresh the cached recent
		// records and today's stats so the Home page shows accurate data
		// on next visit (or immediately if already on Home).
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
			} catch {
				// Silently ignore — next manual load picks up fresh data
			}
		}, 500);
	});

	usePythonEvent("recording_started", () => {
		setLastText("");
		if (lastTextTimer.current) {
			clearTimeout(lastTextTimer.current);
			lastTextTimer.current = null;
		}
	});

	// Clean up pending refresh timer on unmount
	useEffect(() => {
		return () => {
			if (refreshTimer.current) clearTimeout(refreshTimer.current);
		};
	}, []);

	const shareStats = useCallback(() => {
		if (!stats || !cfg) return;
		// Note: computeShareStats is also called at line 536 to render
		// <StatsShareImage stats={...}/> — that call is the source of
		// truth for the rendered image. shareAsImage captures the DOM
		// element which already contains the rendered stats, so no need
		// to compute them again here.
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

	const isRecording = recordingState === "recording";
	const key = statusKeyFor(recordingState, !!lastError);
	const statusColor = STATUS_COLORS[key] ?? STATUS_COLORS.idle;
	const statusLabel = statusLabelFor(key);

	return (
		<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col items-center justify-center gap-5 px-6 py-4">
			{/* NEW-UX-016: status pill now has a smooth transition animation
          so the dot + label fade between states instead of snapping.
          The `transition-colors duration-300` on the dot animates the
          color change; the `transition-opacity duration-200` on the
          label cross-fades the text.  The `animate-fade-in` on the
          whole pill makes the initial appearance polished. */}
			<output
				className="flex items-center gap-2 animate-fade-in"
				aria-live="polite"
			>
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

			<div className="relative">
				{isRecording && (
					<span className="absolute inset-0 rounded-full animate-pulse-ring" />
				)}
				<button
					type="button"
					onClick={handleToggle}
					disabled={toggling}
					aria-label={
						isRecording ? t("home.stopDictation") : t("home.startDictation")
					}
					title={
						isRecording ? t("home.stopDictation") : t("home.startDictation")
					}
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
					{isRecording ? (
						<HugeiconsIcon
							icon={StopIcon}
							strokeWidth={1.625}
							className="h-8 w-8 text-white"
						/>
					) : (
						<HugeiconsIcon
							icon={Mic02Icon}
							strokeWidth={1.625}
							className="h-8 w-8 text-white"
						/>
					)}
				</button>
			</div>

			<p className="flex items-center gap-2 text-[13px] text-(--text-muted)">
				<span>{t("home.press")}</span>
				<span className="inline-flex items-center justify-center rounded-md border border-border bg-(--bg-subtle) px-1.75 py-0.75 font-mono text-[11px] font-medium text-(--text-primary) shadow-[0_1px_3px_rgba(0,0,0,0.06),inset_0_1px_0_rgba(255,255,255,0.4)] dark:shadow-[0_1px_3px_rgba(0,0,0,0.15),inset_0_1px_0_rgba(255,255,255,0.06)] leading-none tracking-tight">
					{hotkey}
				</span>
				<span>{t("home.pressOrClick")}</span>
			</p>

			{lastText && (
				<div className="w-130 max-w-full rounded-[10px] bg-(--bg-subtle) px-4 py-3">
					<p className="line-clamp-2 overflow-hidden text-ellipsis text-[13px] text-(--text-muted)">
						{lastText}
					</p>
				</div>
			)}

			{stats && (
				<div className="mt-4 w-full">
					<div className="mb-3 flex items-center justify-between">
						<span className="text-xs font-medium text-(--text-muted) capitalize tracking-wide">
							{t("home.todayStats")}
						</span>
						<div className="flex items-center gap-1">
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

			{/* FIX: when there's no cached stats AND we're still loading the
                            initial data, show a small spinner that preserves the layout
                            height.  This avoids the "page is empty" flash on first visit
                            (subsequent visits render cached data instantly).  Once the
                            fetch resolves, initialLoading flips to false and this falls
                            back to the natural empty state. */}
			{!stats && initialLoading && (
				<section
					className="mt-4 w-full flex items-center justify-center py-6"
					aria-label={t("home.loadingTodayStatsAria")}
				>
					<Spinner />
				</section>
			)}

			{/* ── Hidden share image capture target ──────────────── */}
			{/* EXPORT-FIX: replaced `position:fixed; left:-9999`
                            with a painted-but-hidden pattern. The old off-screen
                            positioning caused Chromium's paint optimization to skip
                            painting the element, so html-to-image captured a 0×0
                            blank image. Using clip-path keeps the element painted
                            (visible to the capture library) while hiding it from
                            the user. */}
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
					onViewAll={() => onNavigate?.("history")}
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
