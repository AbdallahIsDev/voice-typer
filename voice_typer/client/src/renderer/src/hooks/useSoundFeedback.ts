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
import {
	closeAudioContext,
	initAudioContext,
	isSoundFeedbackEnabled,
	playSoundCue,
} from "@/lib/sound-manager";

// Re-export the canonical API so existing importers of this module
// continue to work. No production code currently imports these symbols
// from here (the only consumer is the hook itself, below), but the
// re-exports keep the public surface stable for tests and external
// integrations and document that this module is the single entry point.
export { initAudioContext, isSoundFeedbackEnabled, playSoundCue };

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
	// ER-28: gate AudioContext construction on the enabled flag.
	//
	// Previously this effect called ``initAudioContext()`` unconditionally
	// on App mount — so the AudioContext was constructed and (after first
	// user gesture) transitioned to "running" state even when the user
	// had ``sound_feedback_enabled=false`` in config. The ``playSoundCue``
	// early-return ``if (!isEnabled()) return;`` prevented oscillator
	// creation but did NOT close the already-alive AudioContext. Each
	// AudioContext in "running" state holds the audio output device open
	// and runs an internal audio-thread.
	//
	// The fix:
	//   - On mount: only call ``initAudioContext()`` if sound feedback is
	//     currently enabled. If disabled, the AudioContext is never
	//     constructed and the gesture-listener is never installed.
	//   - On unmount: call ``closeAudioContext()`` to release the
	//     AudioContext + detach gesture listeners (the cleanup runs when
	//     the App root unmounts, which is rare, but the close is still
	//     correct behavior — a re-mount will re-init if still enabled).
	//
	// Note: if the user toggles sound feedback at runtime via Settings,
	// ``setSoundFeedbackEnabled`` writes the flag to localStorage but
	// does NOT itself close/re-init the AudioContext. The next App
	// re-mount (or a manual ``closeAudioContext()`` call) is what
	// releases the context. This is a known limitation — fully
	// reactive enable/disable would require a settings-change
	// subscription here, which is out of scope for the ER-28 fix.
	useEffect(() => {
		if (isSoundFeedbackEnabled()) {
			initAudioContext();
		}
		return () => {
			// ER-28: on unmount, release the AudioContext + gesture
			// listeners so we don't leak an alive audio thread when
			// the hook is unmounted (e.g. during HMR or test teardown).
			closeAudioContext();
		};
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
