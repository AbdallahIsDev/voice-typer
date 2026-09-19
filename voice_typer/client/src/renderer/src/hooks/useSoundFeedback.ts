/**
 * useSoundFeedback, App-level hook that plays start/stop recording cues.
 * AudioContext / playSoundCue implementation that DUPLICATED the canonical
 * implementation in ``@/lib/sound-manager``. The duplicate was dead in
 * There is NO success/complete audio cue (C-SOUND-1). The only
 * ``complete`` remains a VISUAL-only cue (no audio); see C-SOUND-1.
 * No network calls (C-DATA-1): the ``onVisualCue`` callback is a
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

/** The four cue types the sound-feedback system plays. */
export type SoundCueType = "start" | "stop" | "complete" | "error";

export interface UseSoundFeedbackOptions {
	onVisualCue?: (cueType: SoundCueType) => void;
}

/**
 * App-level hook that subscribes to recording_started / recording_stopped
 * / transcription_final / error events and plays the corresponding cue.
 * Mount this once at the App root so it stays active regardless of which
 * (deaf-accessibility mirror) but NEVER an audio cue — C-SOUND-1.
 */
export function useSoundFeedback(options?: UseSoundFeedbackOptions): void {
	// Capture the latest onVisualCue so the Python-event subscriptions
	// below can read the current callback without re-subscribing on
	// every render. (usePythonEvent already memoises its handler via
	// an internal ref, we just read the option at the point of each
	// event handler invocation.)
	const onVisualCue = options?.onVisualCue;
	//gate AudioContext construction on the enabled flag.
	// on App mount, so the AudioContext was constructed and (after first
	// user gesture) transitioned to "running" state even when the user
	// had ``sound_feedback_enabled=false`` in config. The ``playSoundCue``
	// early-return ``if (!isEnabled()) return;`` prevented oscillator
	// creation but did NOT close the already-alive AudioContext. Each
	// AudioContext in "running" state holds the audio output device open
	// and runs an internal audio-thread.
	// The fix:
	//   - On mount: only call ``initAudioContext()`` if sound feedback is
	//     currently enabled. If disabled, the AudioContext is never
	//     constructed and the gesture-listener is never installed.
	//   - On unmount: call ``closeAudioContext()`` to release the
	//     AudioContext + detach gesture listeners (the cleanup runs when
	//     the App root unmounts, which is rare, but the close is still
	//     correct behavior, a re-mount will re-init if still enabled).
	//   - At RUNTIME (see the ``config_changed`` subscription below):
	//     a ``sound_feedback_enabled`` flip closes / re-inits the
	//     AudioContext immediately, so turning the feature off no longer
	//     leaves an idle audio context (and its audio thread) held until
	//     app restart.
	useEffect(() => {
		if (isSoundFeedbackEnabled()) {
			initAudioContext();
		}
		return () => {
			//on unmount, release the AudioContext + gesture
			// listeners so we don't leak an alive audio thread when
			// the hook is unmounted (e.g. during HMR or test teardown).
			closeAudioContext();
		};
	}, []);

	// Runtime toggle: the backend broadcasts ``config_changed`` for
	// every config write (Settings → Recording's toggle, config
	// import, CLI tool). When the payload carries
	// ``sound_feedback_enabled``, close / re-init the AudioContext
	// kept the already-alive AudioContext (and its audio thread)
	// held until the next app restart (a documented limitation,
	// now fixed). Reading the flag from the PAYLOAD (not
	// localStorage) makes the handler independent of the
	// themeSync → setSoundFeedbackEnabled write ordering.
	usePythonEvent("config_changed", (data): (() => void) | undefined => {
		const payload = (data ?? {}) as {
			sound_feedback_enabled?: unknown;
		};
		if (typeof payload.sound_feedback_enabled !== "boolean") {
			return undefined;
		}
		if (payload.sound_feedback_enabled) {
			// Safe when already initialized, initAudioContext
			// short-circuits on a live context.
			initAudioContext();
		} else {
			closeAudioContext();
		}
		return undefined;
	});

	usePythonEvent("recording_started", (): (() => void) | undefined => {
		playSoundCue("start");
		// Deaf-accessibility visual mirror: invoke AFTER playSoundCue so
		// the audio cue is scheduled first (minimises perceived AV skew).
		onVisualCue?.("start");
		return undefined;
	});

	usePythonEvent("recording_stopped", (): (() => void) | undefined => {
		playSoundCue("stop");
		onVisualCue?.("stop");
		return undefined;
	});

	// Visual-only mirror for deaf users (C-SOUND-1: no success
	// audio). transcription_final still lights the blue pulse so
	// hard-of-hearing users see that the text is ready to paste.
	usePythonEvent("transcription_final", (): (() => void) | undefined => {
		onVisualCue?.("complete");
		return undefined;
	});

	usePythonEvent("error", (): (() => void) | undefined => {
		playSoundCue("error");
		onVisualCue?.("error");
		return undefined;
	});
}
