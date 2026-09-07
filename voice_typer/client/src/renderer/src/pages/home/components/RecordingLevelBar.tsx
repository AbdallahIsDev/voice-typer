// RecordingLevelBar — compact live audio-level indicator for Home while
// recording. Feeds off the `recording_level` push event (≤8 Hz,
// latest-wins main-window mirror of the recorder's level, published by
// the bubble-level worker on the generic event path — the typed
// `bubble_level` channel is consumed by the bubble window only on BOTH
// runtimes, so the main renderer cannot ride it). The subscription is
// read-only push consumption (no backend `level_monitor_start`
// round-trip, so no privacy-sensitive stream is opened from here).
//
// Display gain: raw speech RMS sits in [0, ~0.3] (see WaveformBubble's
// docstring). The server applies an 8x display gain to its own level
// push (`level_monitor._LEVEL_DISPLAY_GAIN`) for exactly the same
// readability reason; mirror that constant client-side so a normal
// voice fills a readable share of the bar. Display-only — the fill
// stays the shared LevelBar's solid `bg-primary` (C-MIC-12: no color
// ladder, no per-tier recoloring).
//
// React state is throttled (~8 Hz) inside this leaf component so event
// bursts never re-render the Home page; the LevelBar fill animates via
// its own 75 ms `transform: scaleX` transition, which smooths between
// the throttled updates.
import { useRef, useState } from "react";
import { LevelBar } from "@/components/feedback/LevelBar";
import { usePythonEvent } from "@/hooks/usePython";

/** Client-side display gain mirroring the server's
 *  `level_monitor._LEVEL_DISPLAY_GAIN` (raw speech RMS ≈ [0, 0.3]). */
const LEVEL_DISPLAY_GAIN = 8;
/** React-state sync interval — matches the server's ≤8 Hz mirror rate. */
const LEVEL_SYNC_INTERVAL_MS = 120;

export function RecordingLevelBar() {
	const [level, setLevel] = useState(0);
	const latestRef = useRef(0);
	const lastSyncRef = useRef(0);

	usePythonEvent("recording_level", (data): (() => void) | undefined => {
		const rms = typeof data?.rms === "number" ? data.rms : 0;
		latestRef.current = Math.min(1, Math.max(0, rms * LEVEL_DISPLAY_GAIN));
		const now = Date.now();
		if (now - lastSyncRef.current >= LEVEL_SYNC_INTERVAL_MS) {
			lastSyncRef.current = now;
			setLevel(latestRef.current);
		}
		return undefined;
	});

	return (
		<div className="w-40" data-testid="recording-level-bar">
			<LevelBar level={level} playing={false} />
		</div>
	);
}

export default RecordingLevelBar;
