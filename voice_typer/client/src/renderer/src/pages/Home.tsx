import { Mic02Icon, Share08Icon, StopIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import ActivityList from "@/components/ActivityList";
import StatCards from "@/components/StatCards";
import { StatsShareImage } from "@/components/StatsShareImage";
import { Button } from "@/components/ui/button";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { computeShareStats, useStatsShare } from "@/hooks/useStatsShare";
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
let _cachedRecent: HistoryRecord[] = [];

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

const STATUS_LABELS: Record<string, string> = {
	idle: "READY",
	recording: "RECORDING",
	transcribing: "TRANSCRIBING",
	loading: "LOADING",
	cancelling: "CANCELLING",
	error: "ERROR",
};

function statusKeyFor(state: RecordingState, hasError: boolean): string {
	// NEW-IPC-010: removed the ``listening → idle`` normalization —
	// ``listening`` is no longer in the RecordingState union (it was
	// never emitted by the backend; the Python AppState uses ``idle``).
	// When there's an error and the state is error, keep it as error.
	if (state === "error" && hasError) return "error";
	return state;
}

// NEW-UX-029: play a short audio cue using the Web Audio API.
// No asset files needed — works offline, no network.  The cue
// is gated by the ``sound_feedback_enabled`` config flag (read
// from localStorage cache updated by Settings).
//
// We use a shared AudioContext (created lazily on first call) so
// we don't pay the cost of creating a new context per cue.
let _sharedAudioContext: AudioContext | null = null;
function getAudioContext(): AudioContext | null {
	if (typeof window === "undefined") return null;
	if (_sharedAudioContext) return _sharedAudioContext;
	try {
		const Ctor =
			window.AudioContext ||
			(window as unknown as { webkitAudioContext: typeof AudioContext })
				.webkitAudioContext;
		if (!Ctor) return null;
		_sharedAudioContext = new Ctor();
		return _sharedAudioContext;
	} catch {
		return null;
	}
}

function playSoundCue(kind: "start" | "stop") {
	// Check the sound feedback toggle.  The Settings page writes this
	// to localStorage on every change, so we read it fresh each time
	// (cheap).
	try {
		// The actual source of truth is the Python config, but we cache
		// it in localStorage from the Settings page so the audio cue
		// plays immediately without waiting for an IPC round-trip.
		// If the cache is missing (e.g. fresh install before the user
		// has visited Settings), default to ON — matches the Python
		// Config default (sound_feedback_enabled = True).
		const raw = localStorage.getItem("vt_sound_feedback_enabled");
		const enabled = raw === null ? true : raw === "1";
		if (!enabled) return;
	} catch {
		return; // localStorage unavailable — skip cue.
	}

	const ctx = getAudioContext();
	if (!ctx) return;

	// Resume the context if it's suspended (browsers suspend AudioContext
	// until the user interacts with the page).
	if (ctx.state === "suspended") {
		ctx.resume().catch(() => {});
	}

	const now = ctx.currentTime;
	const osc = ctx.createOscillator();
	const gain = ctx.createGain();

	// Start cue: 660Hz (E5), 120ms, gentle attack/release.
	// Stop cue: 440Hz (A4), 180ms, slightly longer release.
	if (kind === "start") {
		osc.frequency.setValueAtTime(660, now);
		osc.frequency.exponentialRampToValueAtTime(880, now + 0.08);
		gain.gain.setValueAtTime(0.0001, now);
		gain.gain.exponentialRampToValueAtTime(0.15, now + 0.01);
		gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.12);
		osc.connect(gain).connect(ctx.destination);
		osc.start(now);
		osc.stop(now + 0.13);
	} else {
		// stop cue
		osc.frequency.setValueAtTime(523, now); // C5
		osc.frequency.exponentialRampToValueAtTime(392, now + 0.1); // G4
		gain.gain.setValueAtTime(0.0001, now);
		gain.gain.exponentialRampToValueAtTime(0.15, now + 0.01);
		gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);
		osc.connect(gain).connect(ctx.destination);
		osc.start(now);
		osc.stop(now + 0.19);
	}
}

export default function Home({
	recordingState,
	lastError,
	onNavigate,
}: HomeProps) {
	const { call } = usePython();

	const [hotkey, setHotkey] = useState("F2");
	const [lastText, setLastText] = useState("");
	const [stats, setStats] = useState<TodayStats | null>(null);
	const [recent, setRecent] = useState<HistoryRecord[]>(_cachedRecent);
	const [toggling, setToggling] = useState(false);
	const [cfg, setCfg] = useState<VoiceTyperConfig | null>(null);
	const { imageRef, shareAsImage } = useStatsShare();

	// NEW-TS-008: normalize hotkey to uppercase for display. Server
	// stores as `<f2>` (lowercase); display should be `F2` (uppercase).
	useEffect(() => {
		const normalizeHotkey = (raw: string): string =>
			raw.replace(/[<>]/g, "").toUpperCase();
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
				setStats(s);
			} catch {}
			try {
				const h = await call<HistoryRecord[]>("get_history", { limit: 4 });
				if (cancelled) return;
				_cachedRecent = h ?? [];
				setRecent(_cachedRecent);
			} catch {}
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
								import("sonner").then(({ toast }) => {
									toast.success("🎉 Your first dictation!", {
										description:
											"Welcome to Voice Typer. Press the hotkey anytime to dictate.",
										duration: 6000,
									});
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

			// NEW-UX-029: play a stop-recording sound (gated by the
			// sound_feedback_enabled setting inside playSoundCue).
			playSoundCue("stop");
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
					_cachedRecent = newRecent;
					setRecent(newRecent);
				}
				if (newStats) setStats(newStats);
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
		// NEW-UX-029: play a start-recording sound if the user has enabled
		// sound feedback.  We use the Web Audio API to synthesize a short
		// tone — no asset files needed, works offline, no network.  The
		// tone is a soft 660Hz sine for 120ms with a quick fade-out.
		playSoundCue("start");
	});

	// Clean up pending refresh timer on unmount
	useEffect(() => {
		return () => {
			if (refreshTimer.current) clearTimeout(refreshTimer.current);
		};
	}, []);

	const shareStats = useCallback(() => {
		if (!stats || !cfg) return;
		const _share = computeShareStats(stats, cfg.asr_backend);
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
	const statusLabel = STATUS_LABELS[key] ?? "READY";

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
					aria-label={isRecording ? "Stop dictation" : "Start dictation"}
					title={isRecording ? "Stop dictation" : "Start dictation"}
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
				<span>Press</span>
				<span className="inline-flex items-center justify-center rounded-md border border-border bg-(--bg-subtle) px-1.75 py-0.75 font-mono text-[11px] font-medium text-(--text-primary) shadow-[0_1px_3px_rgba(0,0,0,0.06),inset_0_1px_0_rgba(255,255,255,0.4)] dark:shadow-[0_1px_3px_rgba(0,0,0,0.15),inset_0_1px_0_rgba(255,255,255,0.06)] leading-none tracking-tight">
					{hotkey}
				</span>
				<span>or click to dictate</span>
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
							Today's Stats
						</span>
						<div className="flex items-center gap-1">
							<Button
								variant="outline"
								size="sm"
								onClick={shareStats}
								disabled={!cfg || stats.count === 0}
								className="gap-2"
							>
								<HugeiconsIcon
									icon={Share08Icon}
									strokeWidth={2}
									className="h-4 w-4"
								/>
								Share Stats
							</Button>
						</div>
					</div>
					<StatCards stats={stats} />
				</div>
			)}

			{/* ── Hidden share image capture target ──────────────── */}
			<div ref={imageRef} style={{ position: "fixed", left: -9999, top: 0 }}>
				{stats && cfg && (
					<StatsShareImage stats={computeShareStats(stats, cfg.asr_backend)} />
				)}
			</div>

			<ActivityList
				items={recent}
				lineClamp={2}
				title="Recent Activity"
				showViewAll
				onViewAll={() => onNavigate?.("history")}
			/>
		</div>
	);
}
