/**
 * SoundManager — centralized audio cue system for VoiceTyper.
 *
 * The previous implementation in Home.tsx had four bugs:
 *
 *  1. ``_audioContextInitAttempted = true`` was set BEFORE the constructor
 *     ran, so if the AudioContext construction threw (e.g. on a locked-down
 *     browser/Electron config), the manager NEVER retried — every
 *     subsequent ``playSoundCue`` was a silent no-op.
 *
 *  2. AudioContext starts in "suspended" state until a user gesture
 *     resumes it (autoplay policy). ``initAudioContext`` called
 *     ``resume()`` on mount, but if no user gesture had happened yet
 *     the resume() promise rejected silently, and ``playSoundCue``
 *     swallowed the rejection — the cue never played.
 *
 *  3. ``playSoundCue`` checked ``ctx.state === "suspended"`` and called
 *     ``resume().then(doPlay).catch(() => {})``. If resume() rejected,
 *     ``doPlay`` was never called — the cue was lost. There was no
 *     fallback path.
 *
 *  4. The localStorage ``vt_sound_feedback_enabled`` flag was ONLY
 *     written when the user toggled the switch in Settings. On a fresh
 *     install or after clearing localStorage, the flag was ``null`` and
 *     ``playSoundCue`` defaulted to enabled (true), but if the user had
 *     disabled it in config and re-installed, the cue would play even
 *     though the user wanted it off.
 *
 * This module fixes all four issues:
 *
 *  - The init flag is set ONLY after a successful construction. A failed
 *    construction is retried on the next ``playSoundCue`` call.
 *
 *  - A global one-time user-gesture listener (click/keydown/touchstart)
 *    eagerly resumes the AudioContext on the FIRST user interaction,
 *    satisfying the autoplay policy.
 *
 *  - ``playSoundCue`` falls back to a one-shot HTMLAudioElement
 *    data-URL cue if the AudioContext is unavailable or rejects. This
 *    guarantees the cue plays even when the AudioContext is closed or
 *    the environment doesn't support Web Audio.
 *
 *  - The ``setEnabled`` function is called from BOTH the Settings
 *    toggle AND the initial config load (App.tsx), so the localStorage
 *    flag is always in sync with the actual config value.
 *
 * The module is a singleton — there's only one AudioContext per page
 * (browsers throttle extra contexts). The Home component's
 * ``initAudioContext`` re-exports are kept for backward compat with
 * existing tests but delegate to this manager.
 */

import { START_BEEP_WAV, STOP_BEEP_WAV } from "./sound-manager/beeps";

// ──────────────────────────────────────────────────────────────────────────
// State
// ──────────────────────────────────────────────────────────────────────────

let _sharedAudioContext: AudioContext | null = null;
let _initAttempted = false; // True ONLY after successful construction
let _initSucceeded = false;
let _enabled: boolean = true; // Mirror of config.sound_feedback_enabled
let _gestureListenerInstalled = false;
//store the gesture-resume handler so _resetSoundManagerForTests
// can detach it (previously the handler was a closure-local variable
// inside installGestureListener and the test reset only flipped the
// boolean flag — leaving the actual DOM listeners attached across
// tests, which leaked into subsequent test cases and produced
// cross-test flakiness when a subsequent test's synthetic click
// triggered the leaked handler).
let _resumeOnceHandler: (() => void) | null = null;

const STORAGE_KEY = "vt_sound_feedback_enabled";

// ──────────────────────────────────────────────────────────────────────────
// localStorage sync
// ──────────────────────────────────────────────────────────────────────────

/**
 * Update the in-memory enabled flag and persist to localStorage.
 *
 * Called from:
 *  - Settings.tsx when the user toggles the Sound Feedback switch.
 *  - App.tsx on initial config load (to sync the localStorage flag
 *    with the actual config value, fixing bug #4 above).
 */
export function setSoundFeedbackEnabled(enabled: boolean): void {
	_enabled = enabled;
	try {
		localStorage.setItem(STORAGE_KEY, enabled ? "1" : "0");
	} catch (e) {
		// localStorage unavailable (e.g. SSR, private browsing) —
		// non-fatal; the in-memory flag still works for this session.
		console.warn(
			"[renderer:sound-manager] setSoundFeedbackEnabled localStorage.setItem failed:",
			e,
		);
	}
}

/**
 * Read the enabled flag from localStorage. Returns the cached in-memory
 * value if localStorage is unavailable.
 *
 * Used by playSoundCue to avoid an IPC round-trip on every cue.
 *
 * Also exported publicly so ``useSoundFeedback`` can gate
 * ``initAudioContext()`` on the enabled flag — previously the hook
 * unconditionally constructed the AudioContext on App mount, which
 * left an alive AudioContext in "running" state even when the user
 * had ``sound_feedback_enabled=false`` in config.
 */
export function isSoundFeedbackEnabled(): boolean {
	return isEnabled();
}

function isEnabled(): boolean {
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (raw === null) return _enabled; // Fall back to in-memory default
		return raw === "1";
	} catch (err) {
		//log the localStorage read failure at debug so silent
		// audio-flag read failures are visible (e.g. SSR environments,
		// private browsing mode where localStorage is unavailable).
		console.debug(
			"[renderer:sound-manager] isEnabled localStorage.getItem failed:",
			err,
		);
		return _enabled;
	}
}

// The visual-feedback flag (deaf-accessibility mirror) lives in
// ./accessibility-manager.ts — a settings concern, not a sound one.

// ──────────────────────────────────────────────────────────────────────────
// AudioContext lifecycle
// ──────────────────────────────────────────────────────────────────────────

/**
 * Eagerly construct the shared AudioContext and attempt to resume it.
 *
 * Safe to call multiple times — subsequent calls are no-ops after a
 * successful init. A failed construction is retried on the next call
 * (fixes bug #1).
 *
 * Returns true if the AudioContext is ready (running or suspended-but-
 * resumable), false if construction failed or the context is closed.
 */
export function initAudioContext(): boolean {
	if (_initSucceeded && _sharedAudioContext?.state !== "closed") {
		return true;
	}
	if (typeof window === "undefined") return false;

	try {
		const Ctor =
			window.AudioContext ||
			(window as unknown as { webkitAudioContext?: typeof AudioContext })
				.webkitAudioContext;
		if (!Ctor) {
			_initAttempted = true;
			return false;
		}
		// If we have a closed context, drop it and start fresh.
		if (_sharedAudioContext?.state === "closed") {
			_sharedAudioContext = null;
			_initSucceeded = false;
		}
		if (!_sharedAudioContext) {
			_sharedAudioContext = new Ctor();
		}
		_initAttempted = true;
		_initSucceeded = true;
		// If suspended (autoplay policy), optimistically resume — the
		// gesture listener below will retry on the first user interaction.
		if (_sharedAudioContext.state === "suspended") {
			_sharedAudioContext.resume().catch((err: unknown) => {
				//log the resume rejection at debug so the
				// operator can see why the AudioContext stayed suspended.
				// Will retry on first user gesture via installGestureListener.
				console.debug(
					"[renderer:sound-manager] initAudioContext resume() rejected:",
					err,
				);
			});
		}
		installGestureListener();
		return true;
	} catch (err) {
		// Construction threw — do NOT set _initSucceeded so the next
		// call retries. Set _initAttempted to throttle logs.
		//log the construction failure so silent audio
		// failures are visible at debug level.
		console.debug(
			"[renderer:sound-manager] initAudioContext construction failed:",
			err,
		);
		_initAttempted = true;
		_sharedAudioContext = null;
		return false;
	}
}

/**
 * Install a one-time global listener that resumes the AudioContext on
 * the first user gesture (click/keydown/touchstart).
 *
 * Browsers require a user gesture before AudioContext can leave the
 * "suspended" state. The Home component mounts before any gesture, so
 * the initial resume() rejects. This listener catches the first
 * interaction anywhere in the app and resumes the context.
 *
 * The listener removes itself after the first successful resume to
 * avoid ongoing overhead.
 */
function installGestureListener(): void {
	if (_gestureListenerInstalled) return;
	if (typeof window === "undefined") return;
	_gestureListenerInstalled = true;

	const resumeOnce = () => {
		const ctx = _sharedAudioContext;
		if (!ctx) return;
		if (ctx.state === "suspended") {
			ctx.resume().catch((err: unknown) => {
				//log the gesture-resume rejection at debug
				// so the operator can see why the AudioContext stayed suspended.
				// Still suspended — leave the listener installed for a
				// subsequent gesture to retry.
				console.debug(
					"[renderer:sound-manager] gesture-listener resume() rejected:",
					err,
				);
			});
		}
		if (ctx.state === "running") {
			// Success — remove the listeners to avoid ongoing overhead.
			_detachGestureListeners();
		}
	};
	//store the handler so _resetSoundManagerForTests can detach it.
	_resumeOnceHandler = resumeOnce;

	// Use capture phase so we fire before any app-level handlers that
	// might stopPropagation. passive: true so we don't block scrolling.
	const opts = { capture: true, passive: true } as const;
	window.addEventListener("click", resumeOnce, opts);
	window.addEventListener("keydown", resumeOnce, opts);
	window.addEventListener("touchstart", resumeOnce, opts);
	window.addEventListener("pointerdown", resumeOnce, opts);
}

/**
 * : detach the gesture-resume listeners explicitly.
 *
 * Called from:
 *  - ``installGestureListener``'s ``resumeOnce`` after a successful resume
 *    (the listener has done its job).
 *  - ``_resetSoundManagerForTests`` so the next test starts clean.
 *  - ``useSoundFeedback`` cleanup (via ``closeAudioContext``) when the user
 *    disables sound feedback so the listeners don't keep pinning the
 *    AudioContext in memory.
 */
function _detachGestureListeners(): void {
	if (typeof window === "undefined") return;
	const handler = _resumeOnceHandler;
	if (handler === null) return;
	window.removeEventListener("click", handler, true);
	window.removeEventListener("keydown", handler, true);
	window.removeEventListener("touchstart", handler, true);
	window.removeEventListener("pointerdown", handler, true);
	_resumeOnceHandler = null;
	_gestureListenerInstalled = false;
}

/**
 * Get the shared AudioContext, lazily initializing it if needed.
 */
function getAudioContext(): AudioContext | null {
	if (!_initAttempted) {
		initAudioContext();
	}
	if (_sharedAudioContext?.state === "closed") {
		// Context was closed (e.g. by the browser after a long idle).
		// Re-init to get a fresh one.
		_initSucceeded = false;
		initAudioContext();
	}
	return _sharedAudioContext;
}

// ──────────────────────────────────────────────────────────────────────────
// Cue synthesis
// ──────────────────────────────────────────────────────────────────────────

/** The four cue types the sound manager can play. */
type SoundCueKind = "start" | "stop" | "error" | "complete";

/**
 * One scheduled automation step for a cue. ``at`` is a seconds offset
 * from the cue's start time (``ctx.currentTime``); ``method`` maps 1:1
 * onto the AudioParam automation call of the same name.
 */
type CueAutomationStep = {
	param: "frequency" | "gain";
	method: "setValueAtTime" | "exponentialRampToValueAtTime";
	value: number;
	at: number;
};

/**
 * Per-kind synthesis recipe for the Web Audio path. Collapses the
 * previous 4-branch if/else (four copies of the
 * connect/start/stop/disconnect boilerplate) into declarative data:
 * changing a cue means editing one table row, and the shared synthesis
 * loop in ``playViaAudioContext`` applies the steps in order.
 */
type CueSpec = {
	oscillatorType: OscillatorType;
	/** ``osc.stop()`` offset from the cue start, in seconds. */
	duration: number;
	steps: CueAutomationStep[];
};

const CUE_SPECS: Record<SoundCueKind, CueSpec> = {
	// "start": rising 660Hz → 880Hz sine sweep, 130ms (recording started).
	start: {
		oscillatorType: "sine",
		duration: 0.13,
		steps: [
			{ param: "frequency", method: "setValueAtTime", value: 660, at: 0 },
			{
				param: "frequency",
				method: "exponentialRampToValueAtTime",
				value: 880,
				at: 0.08,
			},
			{ param: "gain", method: "setValueAtTime", value: 0.0001, at: 0 },
			{
				param: "gain",
				method: "exponentialRampToValueAtTime",
				value: 0.15,
				at: 0.01,
			},
			{
				param: "gain",
				method: "exponentialRampToValueAtTime",
				value: 0.0001,
				at: 0.12,
			},
		],
	},
	// "stop": falling 523Hz → 392Hz sine sweep, 190ms (recording stopped).
	stop: {
		oscillatorType: "sine",
		duration: 0.19,
		steps: [
			{ param: "frequency", method: "setValueAtTime", value: 523, at: 0 },
			{
				param: "frequency",
				method: "exponentialRampToValueAtTime",
				value: 392,
				at: 0.1,
			},
			{ param: "gain", method: "setValueAtTime", value: 0.0001, at: 0 },
			{
				param: "gain",
				method: "exponentialRampToValueAtTime",
				value: 0.15,
				at: 0.01,
			},
			{
				param: "gain",
				method: "exponentialRampToValueAtTime",
				value: 0.0001,
				at: 0.18,
			},
		],
	},
	// "error": low 200Hz square buzz, 250ms — a fast attack and a quick
	// decay so it reads as an "alert" rather than a sustained tone. The
	// square waveform gives the harsh "buzz" character that distinguishes
	// an error cue from the normal start/stop tones.
	error: {
		oscillatorType: "square",
		duration: 0.25,
		steps: [
			{ param: "frequency", method: "setValueAtTime", value: 200, at: 0 },
			{ param: "gain", method: "setValueAtTime", value: 0.0001, at: 0 },
			{
				param: "gain",
				method: "exponentialRampToValueAtTime",
				value: 0.18,
				at: 0.005,
			},
			{
				param: "gain",
				method: "exponentialRampToValueAtTime",
				value: 0.0001,
				at: 0.24,
			},
		],
	},
	// "complete": two-note rising chime (A5 → D6, 880Hz → 1175Hz) — a
	// major-third interval that reads as a positive "done!" cadence
	// (transcription finalized and ready to paste). Triangle wave for a
	// softer, less mechanical timbre than the square-wave error buzz.
	// Total 220ms: 100ms on the first note, 120ms on the second, with a
	// 5ms attack and 10ms release on each.
	complete: {
		oscillatorType: "triangle",
		duration: 0.22,
		steps: [
			{ param: "frequency", method: "setValueAtTime", value: 880, at: 0 },
			{ param: "frequency", method: "setValueAtTime", value: 1175, at: 0.1 },
			{ param: "gain", method: "setValueAtTime", value: 0.0001, at: 0 },
			{
				param: "gain",
				method: "exponentialRampToValueAtTime",
				value: 0.14,
				at: 0.005,
			},
			{ param: "gain", method: "setValueAtTime", value: 0.14, at: 0.095 },
			{
				param: "gain",
				method: "exponentialRampToValueAtTime",
				value: 0.0001,
				at: 0.1,
			},
			{
				param: "gain",
				method: "exponentialRampToValueAtTime",
				value: 0.14,
				at: 0.105,
			},
			{ param: "gain", method: "setValueAtTime", value: 0.14, at: 0.21 },
			{
				param: "gain",
				method: "exponentialRampToValueAtTime",
				value: 0.0001,
				at: 0.22,
			},
		],
	},
};

/**
 * Synthesize and play a short audio cue via the Web Audio API, using
 * the per-kind ``CUE_SPECS`` table above. The shared synthesis loop
 * applies each cue's automation steps in table order, so all four cues
 * share one connect/start/stop/teardown sequence.
 *
 * Returns true if the cue was successfully scheduled, false otherwise.
 */
function playViaAudioContext(kind: SoundCueKind): boolean {
	const ctx = getAudioContext();
	if (!ctx) return false;
	if (ctx.state === "closed") return false;

	const spec = CUE_SPECS[kind];

	const doPlay = () => {
		const now = ctx.currentTime;
		const osc = ctx.createOscillator();
		const gain = ctx.createGain();

		osc.type = spec.oscillatorType;
		// Apply the cue's automation schedule in table order — the steps
		// preserve the exact call sequence (values and absolute times) of
		// the pre-table implementation, so the scheduled audio is identical.
		for (const step of spec.steps) {
			const param = step.param === "frequency" ? osc.frequency : gain.gain;
			if (step.method === "setValueAtTime") {
				param.setValueAtTime(step.value, now + step.at);
			} else {
				param.exponentialRampToValueAtTime(step.value, now + step.at);
			}
		}

		osc.connect(gain).connect(ctx.destination);
		osc.start(now);
		osc.stop(now + spec.duration);
		// Explicitly disconnect the per-cue nodes once the oscillator
		// stops so the AudioContext can release them promptly (instead of
		// relying on internal GC of connected-but-stopped nodes, which the
		// spec does not guarantee).
		osc.onended = () => {
			osc.disconnect();
			gain.disconnect();
		};
	};

	if (ctx.state === "running") {
		doPlay();
		return true;
	}
	if (ctx.state === "suspended") {
		// Try to resume — if it succeeds, play. If it rejects, fall
		// through to the HTMLAudioElement fallback in playSoundCue.
		ctx
			.resume()
			.then(() => {
				try {
					doPlay();
				} catch (e) {
					// Synthesis failed — no fallback here; the caller's
					// catch will handle it.
					console.warn("[renderer:sound-manager] synthesis doPlay failed:", e);
				}
			})
			.catch((err: unknown) => {
				//log the resume rejection at debug so silent
				// audio failures are visible. Resume rejected — caller's
				// playSoundCue will fall back to HTMLAudioElement. (We
				// can't call the fallback here because playSoundCue checks
				// enabled first; we'd risk playing when disabled.)
				console.debug(
					"[renderer:sound-manager] playViaAudioContext resume() rejected:",
					err,
				);
			});
		// Return false so playSoundCue falls back to HTMLAudioElement
		// for an immediate cue (the AudioContext path is async and
		// may not fire if resume() rejects).
		return false;
	}
	return false;
}

/**
 * Fallback: play a short cue via HTMLAudioElement with a data URL.
 *
 * HTMLAudioElement.play() is also subject to autoplay policy, BUT
 * if a user gesture has ever happened in the page (even on a different
 * element), .play() will succeed. This is a more reliable fallback
 * than AudioContext when the context is suspended.
 *
 * The cues are short WAVs (encoded as data URLs) — distinct rising and
 * falling sine sweeps so the user can audibly tell "started" from
 * "stopped" even when the Web Audio API path fails.
 *
 * START_BEEP_WAV: 150 ms rising sweep 660 Hz -> 880 Hz.
 * STOP_BEEP_WAV:  200 ms falling sweep 523 Hz -> 392 Hz.
 *
 * Both are 44.1 kHz, 16-bit, mono, with a 5 ms linear attack and
 * 5 ms linear release to avoid clicks. Regenerate via
 * ``python scripts/build/generate_beeps.py --write``.
 *
 * The two data-URL constants live in ``./sound-manager/beeps.ts`` —
 * ``scripts/build/generate_beeps.py`` (both its ``--check`` CI gate and
 * its ``--write`` mode) locates and rewrites them there by name (its
 * ``SOUND_MANAGER_PATH`` points at that module).
 */

let _fallbackAudio: HTMLAudioElement | null = null;
function getFallbackAudio(): HTMLAudioElement | null {
	if (typeof window === "undefined") return null;
	if (!_fallbackAudio) {
		try {
			_fallbackAudio = new Audio();
			_fallbackAudio.preload = "auto";
		} catch (err) {
			//log the construction failure at debug so silent
			// audio failures are visible (e.g. SSR environments where the
			// Audio constructor is not available).
			console.debug(
				"[renderer:sound-manager] getFallbackAudio new Audio() failed:",
				err,
			);
			return null;
		}
	}
	return _fallbackAudio;
}

function playViaHtmlAudio(kind: SoundCueKind): boolean {
	const audio = getFallbackAudio();
	if (!audio) return false;
	try {
		//"error" kind reuses STOP_BEEP_WAV (the falling
		// pitch reads as an "alert" cadence) rather than minting a
		// third base64 WAV asset — the AudioContext square-wave path
		// above is the primary error cue; this is only the fallback
		// for environments where Web Audio is unavailable.
		//"complete" kind reuses START_BEEP_WAV (rising
		// pitch) as a positive-sounding fallback — the AudioContext
		// two-note chime above is the primary complete cue.
		if (kind === "start" || kind === "complete") {
			audio.src = START_BEEP_WAV;
		} else {
			audio.src = STOP_BEEP_WAV;
		}
		audio.volume = 0.15;
		audio.currentTime = 0;
		// .play() returns a Promise; if it rejects (autoplay blocked),
		// there's nothing more we can do — return false so the caller
		// knows the cue didn't play.
		const p = audio.play();
		if (p && typeof p.then === "function") {
			p.catch((err: unknown) => {
				//log the autoplay-block rejection at debug so
				// silent audio failures are visible. Autoplay blocked —
				// the next user gesture will allow subsequent cues.
				console.debug(
					"[renderer:sound-manager] playViaHtmlAudio audio.play() rejected:",
					err,
				);
			});
		}
		return true;
	} catch (err) {
		//log the catch-all failure at debug so silent audio
		// failures are visible (e.g. invalid data URL, media element
		// decode error).
		console.debug("[renderer:sound-manager] playViaHtmlAudio failed:", err);
		return false;
	}
}

// ──────────────────────────────────────────────────────────────────────────
// Public API
// ──────────────────────────────────────────────────────────────────────────

/**
 * Play a short audio cue for recording start/stop/error.
 *
 * Gated by the ``sound_feedback_enabled`` config flag (synced to
 * localStorage via setSoundFeedbackEnabled).
 *
 * Tries the Web Audio API first (cleaner, no asset loading). Falls
 * back to HTMLAudioElement if the AudioContext is unavailable,
 * suspended, or rejects.
 *
 * Safe to call from any context (renderer, tests, SSR) — silently
 * no-ops if audio is unavailable or disabled.
 */
export function playSoundCue(kind: SoundCueKind): void {
	if (!isEnabled()) return;

	// Ensure the AudioContext is initialized so the gesture listener
	// is installed (this is what fixes the "no sound on first record"
	// bug — the gesture listener will resume the context on the next
	// click/keydown, which is often the hotkey press itself).
	initAudioContext();

	// Try Web Audio API first.
	const played = playViaAudioContext(kind);
	if (played) return;

	// Fall back to HTMLAudioElement. This path is taken when:
	//  - AudioContext construction failed (rare).
	//  - AudioContext is suspended and resume() rejected (common on
	//    first record before any user gesture).
	//  - AudioContext is closed (e.g. after a long idle period).
	playViaHtmlAudio(kind);
}

/**
 * Close and release the shared AudioContext, if one exists.
 *
 * Also detaches the gesture-resume listeners (previously
 * ``closeAudioContext`` only closed the AudioContext but left the
 * gesture listeners attached — they would re-resume a future context
 * if ``initAudioContext`` was called again, which is undesired when
 * the user explicitly disabled sound feedback).
 */
export function closeAudioContext(): void {
	//detach gesture listeners BEFORE closing the context so
	// a stray gesture during teardown doesn't race with close().
	_detachGestureListeners();
	if (_sharedAudioContext) {
		_sharedAudioContext.close();
		_sharedAudioContext = null;
		_initAttempted = false;
		_initSucceeded = false;
	}
}

/**
 * Reset all state — used by tests to ensure isolation between cases.
 *
 * Now also detaches gesture listeners explicitly (previously
 * only the boolean flag was reset, leaving the actual DOM listeners
 * attached across tests).
 */
export function _resetSoundManagerForTests(): void {
	_detachGestureListeners();
	_sharedAudioContext = null;
	_initAttempted = false;
	_initSucceeded = false;
	_enabled = true;
	_gestureListenerInstalled = false;
	_fallbackAudio = null;
}
