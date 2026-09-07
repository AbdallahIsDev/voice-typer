/**
 * useSoundFeedback — App-level hook that plays start/stop recording cues.
 *
 *  (sound consolidation): previously this file contained a parallel
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
 * SOUND-: previously the recording_started / recording_stopped
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
 * : subscribes to ``transcription_final`` and plays the
 * ``complete`` cue so the user gets an audible confirmation that the
 * transcription is ready to paste. Previously the only signal was the
 * visual status pill changing color, which is easy to miss when the
 * user has looked away from the window.
 *
 * ── Deaf-accessibility visual mirror ──────────────────────────────
 *
 * The four audio cues (``start`` / ``stop`` / ``complete`` / ``error``)
 * are useless to deaf / hard-of-hearing users — the original analysis
 * considered sighted+hearing users only. When the App passes an
 * ``onVisualCue`` callback, this hook ALSO invokes that callback per
 * cue type so the App can flash the status pill / title-bar / tray
 * icon with a distinct color pulse:
 *
 *   - ``start``    → green pulse   (recording started)
 *   - ``stop``     → red pulse     (recording stopped)
 *   - ``complete`` → blue pulse    (transcription finalized, ready to paste)
 *   - ``error``    → orange pulse  (backend / transcription error)
 *
 * The actual visual rendering is owned by ``App.tsx`` (it owns the
 * status-pill / title-bar / tray-icon state). This hook's job is only
 * to expose the per-cue callback. The App decides whether to pass the
 * callback at all (typically gated on ``isVisualFeedbackEnabled()``
 * from ``@/lib/sound-manager`` so the visual mirror is opt-in via
 * Settings → Accessibility). The cue fires in addition to the audio
 * cue, NOT instead of it — sighted+hearing users who enable the visual
 * mirror for redundancy still hear the sound.
 *
 * No network calls (C-DATA-1): the ``onVisualCue`` callback is a
 * pure renderer-side callback invocation; the App's implementation is
 * responsible for keeping its visual rendering local.
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
	/**
	 * Deaf-accessibility visual mirror. When provided, the hook
	 * invokes this callback for every cue type (in addition to
	 * playing the audio cue) so the App can render a distinct
	 * color pulse per cue type.
	 *
	 * The App is the visual-rendering owner — this hook only
	 * exposes the per-cue event. The App decides whether to pass
	 * the callback at all (typically gated on
	 * ``isVisualFeedbackEnabled()`` from ``@/lib/sound-manager``).
	 *
	 * The callback fires AFTER ``playSoundCue`` is called so the
	 * audio cue is scheduled first (audio has higher latency to
	 * the user's ear than the visual pulse has to the eye —
	 * scheduling audio first minimises the perceived AV skew).
	 */
	onVisualCue?: (cueType: SoundCueType) => void;
}

/**
 * App-level hook that subscribes to recording_started / recording_stopped
 * / transcription_final / error events and plays the corresponding cue.
 * Mount this once at the App root so it stays active regardless of which
 * page is currently shown.
 *
 * : also subscribes to ``error`` events so the user gets an
 * audible alert when the backend reports a recording/transcription
 * failure. The error cue is a short low buzz (see ``sound-manager.ts``).
 *
 * : subscribes to ``transcription_final`` and plays the
 * ``complete`` cue (two-note rising chime). This fires once per
 * finalized transcription — the user hears an audible "done!" signal
 * even when the window is hidden to the tray or the user is looking
 * away. The subscription is mounted at the App root so it fires
 * regardless of which page is currently shown.
 */
export function useSoundFeedback(options?: UseSoundFeedbackOptions): void {
	// Capture the latest onVisualCue so the Python-event subscriptions
	// below can read the current callback without re-subscribing on
	// every render. (usePythonEvent already memoises its handler via
	// an internal ref — we just read the option at the point of each
	// event handler invocation.)
	const onVisualCue = options?.onVisualCue;
	//gate AudioContext construction on the enabled flag.
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
	// right away — previously toggling the feature off at runtime
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
			// Safe when already initialized — initAudioContext
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

	//audible "transcription ready" cue. The
	// transcription_final event fires once per finalized
	// transcription (after the engine has produced the final text
	// and the pipeline has pasted/committed it). The cue gives the
	// user an audible confirmation that they can resume typing —
	// particularly useful when the user has looked away from the
	// window or the window is hidden to the tray.
	usePythonEvent("transcription_final", (): (() => void) | undefined => {
		playSoundCue("complete");
		onVisualCue?.("complete");
		return undefined;
	});

	usePythonEvent("error", (): (() => void) | undefined => {
		playSoundCue("error");
		onVisualCue?.("error");
		return undefined;
	});
}
