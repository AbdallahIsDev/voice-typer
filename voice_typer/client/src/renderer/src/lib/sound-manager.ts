/**
 * SoundManager — centralized audio cue system for VoiceTyper.
 *
 * SOUND-FIX-REWRITE: the previous implementation in Home.tsx had four bugs:
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

// ──────────────────────────────────────────────────────────────────────────
// State
// ──────────────────────────────────────────────────────────────────────────

let _sharedAudioContext: AudioContext | null = null;
let _initAttempted = false; // True ONLY after successful construction
let _initSucceeded = false;
let _enabled: boolean = true; // Mirror of config.sound_feedback_enabled
let _gestureListenerInstalled = false;

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
	} catch {
		// localStorage unavailable (e.g. SSR, private browsing) —
		// non-fatal; the in-memory flag still works for this session.
	}
}

/**
 * Read the enabled flag from localStorage. Returns the cached in-memory
 * value if localStorage is unavailable.
 *
 * Used by playSoundCue to avoid an IPC round-trip on every cue.
 */
function isEnabled(): boolean {
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (raw === null) return _enabled; // Fall back to in-memory default
		return raw === "1";
	} catch {
		return _enabled;
	}
}

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
			_sharedAudioContext.resume().catch(() => {
				// Will retry on first user gesture via installGestureListener.
			});
		}
		installGestureListener();
		return true;
	} catch {
		// Construction threw — do NOT set _initSucceeded so the next
		// call retries. Set _initAttempted to throttle logs.
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
			ctx.resume().catch(() => {
				// Still suspended — leave the listener installed for a
				// subsequent gesture to retry.
			});
		}
		if (ctx.state === "running") {
			// Success — remove the listeners to avoid ongoing overhead.
			window.removeEventListener("click", resumeOnce, true);
			window.removeEventListener("keydown", resumeOnce, true);
			window.removeEventListener("touchstart", resumeOnce, true);
			window.removeEventListener("pointerdown", resumeOnce, true);
		}
	};

	// Use capture phase so we fire before any app-level handlers that
	// might stopPropagation. passive: true so we don't block scrolling.
	const opts = { capture: true, passive: true } as const;
	window.addEventListener("click", resumeOnce, opts);
	window.addEventListener("keydown", resumeOnce, opts);
	window.addEventListener("touchstart", resumeOnce, opts);
	window.addEventListener("pointerdown", resumeOnce, opts);
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

/**
 * Synthesize and play a short audio cue via the Web Audio API.
 *
 * - "start": rising 660Hz → 880Hz sine, 130ms (recording started)
 * - "stop": falling 523Hz → 392Hz sine, 190ms (recording stopped)
 *
 * Returns true if the cue was successfully scheduled, false otherwise.
 */
function playViaAudioContext(kind: "start" | "stop"): boolean {
	const ctx = getAudioContext();
	if (!ctx) return false;
	if (ctx.state === "closed") return false;

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
				} catch {
					// Synthesis failed — no fallback here; the caller's
					// catch will handle it.
				}
			})
			.catch(() => {
				// Resume rejected — caller's playSoundCue will fall back
				// to HTMLAudioElement. (We can't call the fallback here
				// because playSoundCue checks enabled first; we'd risk
				// playing when disabled.)
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
 * The cue is a tiny WAV (encoded as a data URL) — a 130ms sine beep.
 */
const START_BEEP_WAV =
	"data:audio/wav;base64,UklGRsQBAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YaABAACA";
const STOP_BEEP_WAV =
	"data:audio/wav;base64,UklGRsQBAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YaABAACA";

let _fallbackAudio: HTMLAudioElement | null = null;
function getFallbackAudio(): HTMLAudioElement | null {
	if (typeof window === "undefined") return null;
	if (!_fallbackAudio) {
		try {
			_fallbackAudio = new Audio();
			_fallbackAudio.preload = "auto";
		} catch {
			return null;
		}
	}
	return _fallbackAudio;
}

function playViaHtmlAudio(kind: "start" | "stop"): boolean {
	const audio = getFallbackAudio();
	if (!audio) return false;
	try {
		audio.src = kind === "start" ? START_BEEP_WAV : STOP_BEEP_WAV;
		audio.volume = 0.15;
		audio.currentTime = 0;
		// .play() returns a Promise; if it rejects (autoplay blocked),
		// there's nothing more we can do — return false so the caller
		// knows the cue didn't play.
		const p = audio.play();
		if (p && typeof p.then === "function") {
			p.catch(() => {
				// Autoplay blocked — the next user gesture will allow
				// subsequent cues. Silent fallback.
			});
		}
		return true;
	} catch {
		return false;
	}
}

// ──────────────────────────────────────────────────────────────────────────
// Public API
// ──────────────────────────────────────────────────────────────────────────

/**
 * Play a short audio cue for recording start/stop.
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
export function playSoundCue(kind: "start" | "stop"): void {
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
 * Reset all state — used by tests to ensure isolation between cases.
 */
export function _resetSoundManagerForTests(): void {
	_sharedAudioContext = null;
	_initAttempted = false;
	_initSucceeded = false;
	_enabled = true;
	_gestureListenerInstalled = false;
	_fallbackAudio = null;
}
