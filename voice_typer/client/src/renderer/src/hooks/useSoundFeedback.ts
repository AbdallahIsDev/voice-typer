/**
 * useSoundFeedback — App-level hook that plays start/stop recording cues.
 *
 * SOUND-FIX-004: previously the recording_started / recording_stopped
 * event subscriptions lived inside ``Home.tsx``, so the cue only played
 * when the user was on the Home page.  Moving the subscription to the
 * App root ensures the cue fires regardless of which page is currently
 * mounted (Settings, Microphone, Models, etc.) or whether the main
 * window is hidden to the tray.
 *
 * The hook is also responsible for eagerly initializing the shared
 * AudioContext on first mount so it is never in "suspended" state when
 * the first recording event arrives.
 *
 * Cross-platform: uses the Web Audio API (OscillatorNode + GainNode)
 * which is provided by Chromium on every platform — no platform-specific
 * audio libraries required, no asset files needed.
 */
import { useEffect } from "react";
import { usePythonEvent } from "@/hooks/usePython";

// ── Shared AudioContext singleton ──────────────────────────────────
// Module-level singleton so the context survives page navigations.
let _sharedAudioContext: AudioContext | null = null;
let _audioContextInitAttempted = false;

/**
 * Eagerly initialise the shared AudioContext so subsequent playSoundCue()
 * calls don't have to wait for resume().  Call once on App mount.
 *
 * SOUND-FIX-004: logs initialization outcome so future regressions are
 * diagnosable (previously every failure path was silent).
 */
export function initAudioContext(): void {
	if (_audioContextInitAttempted) return;
	_audioContextInitAttempted = true;
	if (typeof window === "undefined") return;
	try {
		const Ctor =
			window.AudioContext ||
			(window as unknown as { webkitAudioContext: typeof AudioContext })
				.webkitAudioContext;
		if (!Ctor) {
			console.warn(
				"[SOUND] AudioContext constructor unavailable — cues disabled",
			);
			return;
		}
		_sharedAudioContext = new Ctor();
		console.debug(
			`[SOUND] AudioContext created (state=${_sharedAudioContext.state}, sampleRate=${_sharedAudioContext.sampleRate}Hz)`,
		);
		// If suspended (autoplay policy), optimistically resume.
		// SOUND-FIX-004: with autoplayPolicy:"no-user-gesture-required"
		// set on the BrowserWindow (main/index.ts), this should succeed
		// immediately.  We still attempt resume() defensively.
		if (_sharedAudioContext.state === "suspended") {
			_sharedAudioContext
				.resume()
				.then(() => {
					console.debug("[SOUND] AudioContext resumed on init");
				})
				.catch((err) => {
					console.warn(
						"[SOUND] AudioContext resume failed on init — cues will retry at play time",
						err,
					);
				});
		}
	} catch (err) {
		console.warn(
			"[SOUND] AudioContext construction failed — cues disabled",
			err,
		);
		_sharedAudioContext = null;
	}
}

function getAudioContext(): AudioContext | null {
	// Lazily init if initAudioContext wasn't called (e.g. tests, SSR).
	if (!_audioContextInitAttempted) {
		initAudioContext();
	}
	return _sharedAudioContext;
}

/**
 * Play a short synthesized cue.  Returns true if the cue was scheduled,
 * false if it was skipped (disabled, no context, or playback failed).
 *
 * SOUND-FIX-004: logs every branch so the sound pipeline is observable:
 *  - "play request" when a cue is requested
 *  - "skipped (disabled)" when sound_feedback_enabled is false
 *  - "skipped (no context)" when AudioContext is unavailable
 *  - "playing" when the oscillator is scheduled
 *  - "resume failed" when the suspended-context resume rejects
 */
export function playSoundCue(kind: "start" | "stop"): boolean {
	console.debug(`[SOUND] play request: kind=${kind}`);

	// ── Config gate ──────────────────────────────────────────────
	let enabled = true;
	try {
		const raw = localStorage.getItem("vt_sound_feedback_enabled");
		enabled = raw === null ? true : raw === "1";
	} catch (err) {
		console.warn(
			"[SOUND] localStorage read failed — defaulting to enabled",
			err,
		);
	}
	if (!enabled) {
		console.debug("[SOUND] skipped (disabled)");
		return false;
	}

	// ── AudioContext ─────────────────────────────────────────────
	const ctx = getAudioContext();
	if (!ctx) {
		console.warn("[SOUND] skipped (no AudioContext)");
		return false;
	}
	if (ctx.state === "closed") {
		console.warn("[SOUND] skipped (AudioContext closed)");
		return false;
	}

	// ── Schedule the oscillator ──────────────────────────────────
	// SOUND-FIX-001: AudioContext starts suspended until a user gesture
	// resumes it. resume() is async — schedule the oscillator AFTER the
	// resume promise resolves, otherwise the sound never plays.
	const doPlay = () => {
		const now = ctx.currentTime;
		const osc = ctx.createOscillator();
		const gain = ctx.createGain();

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
			osc.frequency.setValueAtTime(523, now);
			osc.frequency.exponentialRampToValueAtTime(392, now + 0.1);
			gain.gain.setValueAtTime(0.0001, now);
			gain.gain.exponentialRampToValueAtTime(0.15, now + 0.01);
			gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);
			osc.connect(gain).connect(ctx.destination);
			osc.start(now);
			osc.stop(now + 0.19);
		}
		console.debug(`[SOUND] playing: kind=${kind} ctxState=${ctx.state}`);
	};

	if (ctx.state === "suspended") {
		ctx
			.resume()
			.then(doPlay)
			.catch((err) => {
				console.warn(`[SOUND] resume failed for kind=${kind}`, err);
			});
	} else if (ctx.state === "running") {
		doPlay();
	}
	return true;
}

/**
 * App-level hook that subscribes to recording_started / recording_stopped
 * events and plays the corresponding cue.  Mount this once at the App
 * root so it stays active regardless of which page is currently shown.
 */
export function useSoundFeedback(): void {
	// Eagerly initialize the AudioContext on first mount.
	useEffect(() => {
		initAudioContext();
	}, []);

	usePythonEvent("recording_started", () => {
		playSoundCue("start");
	});

	usePythonEvent("recording_stopped", () => {
		playSoundCue("stop");
	});
}
