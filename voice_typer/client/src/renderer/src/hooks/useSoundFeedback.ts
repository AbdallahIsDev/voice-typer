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
 *
 * XA-12-16: subscribes to ``transcription_final`` and plays the
 * ``complete`` cue so the user gets an audible confirmation that the
 * transcription is ready to paste. Previously the only signal was the
 * visual status pill changing color, which is easy to miss when the
 * user has looked away from the window.
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
 * / transcription_final / error events and plays the corresponding cue.
 * Mount this once at the App root so it stays active regardless of which
 * page is currently shown.
 *
 * PVT-fix-7: also subscribes to ``error`` events so the user gets an
 * audible alert when the backend reports a recording/transcription
 * failure. The error cue is a short low buzz (see ``sound-manager.ts``).
 *
 * XA-12-16: subscribes to ``transcription_final`` and plays the
 * ``complete`` cue (two-note rising chime). This fires once per
 * finalized transcription — the user hears an audible "done!" signal
 * even when the window is hidden to the tray or the user is looking
 * away. The subscription is mounted at the App root so it fires
 * regardless of which page is currently shown.
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

	// XA-12-16: audible "transcription ready" cue. The
	// transcription_final event fires once per finalized
	// transcription (after the engine has produced the final text
	// and the pipeline has pasted/committed it). The cue gives the
	// user an audible confirmation that they can resume typing —
	// particularly useful when the user has looked away from the
	// window or the window is hidden to the tray.
	usePythonEvent("transcription_final", (): (() => void) | undefined => {
		playSoundCue("complete");
		return undefined;
	});

	usePythonEvent("error", (): (() => void) | undefined => {
		playSoundCue("error");
		return undefined;
	});
}
