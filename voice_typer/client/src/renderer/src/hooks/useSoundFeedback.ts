/**
 * useSoundFeedback — App-level hook that plays start/stop recording cues.
 *
 * RW-10 (sound consolidation): previously this file contained a parallel
 * AudioContext / playSoundCue implementation that DUPLICATED the canonical
 * implementation in ``@/lib/sound-manager``. The duplicate was dead in
 * production in the sense that *tests* only exercised ``sound-manager.ts``,
 * while the *runtime* only exercised this file — so test runs and prod
 * ran different code. Strategy A: the hook now delegates every cue to the
 * canonical implementation in ``sound-manager.ts`` (which has the
 * HTMLAudioElement fallback, the gesture-listener resume, and the
 * retry-on-failed-init fix). The re-exports below preserve the public
 * surface for any external importer.
 *
 * SOUND-FIX-004: previously the recording_started / recording_stopped
 * event subscriptions lived inside ``Home.tsx``, so the cue only played
 * when the user was on the Home page.  Moving the subscription to the
 * App root ensures the cue fires regardless of which page is currently
 * mounted (Settings, Microphone, Models, etc.) or whether the main
 * window is hidden to the tray.
 *
 * Cross-platform: uses the Web Audio API (OscillatorNode + GainNode)
 * which is provided by Chromium on every platform — no platform-specific
 * audio libraries required, no asset files needed.
 */
import { useEffect } from "react";
import { usePythonEvent } from "@/hooks/usePython";
import { initAudioContext, playSoundCue } from "@/lib/sound-manager";

// Re-export the canonical API so existing importers of this module
// continue to work. No production code currently imports these symbols
// from here (the only consumer is the hook itself, below), but the
// re-exports keep the public surface stable for tests and external
// integrations and document that this module is the single entry point.
export { initAudioContext, playSoundCue };

/**
 * App-level hook that subscribes to recording_started / recording_stopped
 * events and plays the corresponding cue.  Mount this once at the App
 * root so it stays active regardless of which page is currently shown.
 *
 * PVT-fix-7: also subscribes to ``error`` events so the user gets an
 * audible alert when the backend reports a recording/transcription
 * failure. The error cue is a short low buzz (see ``sound-manager.ts``).
 */
export function useSoundFeedback(): void {
	// Eagerly initialize the AudioContext on first mount.
	useEffect(() => {
		initAudioContext();
	}, []);

	usePythonEvent("recording_started", (): (() => void) | undefined => {
		playSoundCue("start");
		return undefined;
	});

	usePythonEvent("recording_stopped", (): (() => void) | undefined => {
		playSoundCue("stop");
		return undefined;
	});

	usePythonEvent("error", (): (() => void) | undefined => {
		playSoundCue("error");
		return undefined;
	});
}
