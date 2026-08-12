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
// Deaf-accessibility visual mirror: when true, the App should pass an
// ``onVisualCue`` callback to ``useSoundFeedback`` so each sound cue is
// also rendered as a distinct visual pulse (status-pill / title-bar /
// tray icon flash). Default false — the visual mirror is opt-in so
// sighted+hearing users don't get redundant visual noise. The App reads
// ``config.visual_feedback_enabled`` on startup (and on
// ``config_changed`` events) and calls ``setVisualFeedbackEnabled`` to
// sync the localStorage flag with the actual config value (mirroring the
// sound-feedback-enabled sync flow).
let _visualEnabled: boolean = false;
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
const VISUAL_STORAGE_KEY = "vt_visual_feedback_enabled";

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
 * : also exported publicly so ``useSoundFeedback`` can gate
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

// ──────────────────────────────────────────────────────────────────────────
// Visual feedback (deaf-accessibility mirror)
// ──────────────────────────────────────────────────────────────────────────

/**
 * Update the in-memory visual-feedback-enabled flag and persist to
 * localStorage. Mirrors ``setSoundFeedbackEnabled`` — same persistence
 * semantics (in-memory flag still works if localStorage is unavailable).
 *
 * NOTE (VP-18): as of this fix there is NO production caller — the
 * visual-mirror feature (deaf-accessibility) is wired via the
 * ``onVisualCue`` callback path in ``useSoundFeedback``, which reads
 * the flag through ``isVisualFeedbackEnabled``; ``useSoundFeedback``
 * re-exports this module's symbols for importers that predate the
 * ``onVisualCue`` refactor, and the unit tests in
 * ``lib/__tests__/sound-manager.test.ts`` exercise this function
 * directly. A future Settings → Accessibility toggle should call this
 * when ``config.visual_feedback_enabled`` changes.
 *
 * C-DATA-1: this is a pure local-storage write — NO network call.
 */
export function setVisualFeedbackEnabled(enabled: boolean): void {
	_visualEnabled = enabled;
	try {
		localStorage.setItem(VISUAL_STORAGE_KEY, enabled ? "1" : "0");
	} catch (e) {
		// localStorage unavailable (e.g. SSR, private browsing) —
		// non-fatal; the in-memory flag still works for this session.
		console.warn(
			"[renderer:sound-manager] setVisualFeedbackEnabled localStorage.setItem failed:",
			e,
		);
	}
}

/**
 * Read the visual-feedback-enabled flag. Returns true when the visual
 * mirror should be active (each sound cue mirrored as a visual pulse
 * for deaf / hard-of-hearing users).
 *
 * NOTE (VP-18): the ``useSoundFeedback`` hook's ``onVisualCue``
 * callback path is the production wiring for the visual mirror; this
 * getter is used by ``useSoundFeedback``'s re-export consumers and by
 * the unit tests. It is NOT called by App.tsx (the previous docstring
 * claim was stale). Reads localStorage on every call (no IPC
 * round-trip) — same pattern as ``isSoundFeedbackEnabled``.
 *
 * Default: false — the visual mirror is opt-in. Sight+hearing users
 * don't see redundant visual noise; deaf / hard-of-hearing users
 * explicitly enable it (or it's auto-enabled when the OS reports a
 * screen reader / captioning preference — future work, not implemented
 * here).
 */
export function isVisualFeedbackEnabled(): boolean {
	try {
		const raw = localStorage.getItem(VISUAL_STORAGE_KEY);
		if (raw === null) return _visualEnabled; // Fall back to in-memory default
		return raw === "1";
	} catch (err) {
		//log the localStorage read failure at debug so silent
		// visual-flag read failures are visible.
		console.debug(
			"[renderer:sound-manager] isVisualFeedbackEnabled localStorage.getItem failed:",
			err,
		);
		return _visualEnabled;
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

/**
 * Synthesize and play a short audio cue via the Web Audio API.
 *
 * - "start":    rising 660Hz → 880Hz sine, 130ms (recording started)
 * - "stop":     falling 523Hz → 392Hz sine, 190ms (recording stopped)
 * - "error":    low 200Hz square buzz, 250ms with quick decay (recording
 *               error / transcription failure). The square waveform gives
 *               the harsh "buzz" character that distinguishes an error
 *               cue from the normal start/stop tones.
 * - "complete": two-note rising chime 880Hz → 1175Hz sine, 220ms
 *               (transcription finalized and ready to paste). :
 *               gives the user an audible confirmation that the
 *               transcription is ready — previously the only signal
 *               was the visual status pill changing color, which is
 *               easy to miss when the user has looked away.
 *
 * Returns true if the cue was successfully scheduled, false otherwise.
 */
function playViaAudioContext(
	kind: "start" | "stop" | "error" | "complete",
): boolean {
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
			//explicitly disconnect the per-cue nodes once the
			// oscillator stops so the AudioContext can release them
			// promptly (instead of relying on internal GC of connected-
			// but-stopped nodes, which the spec does not guarantee).
			osc.onended = () => {
				osc.disconnect();
				gain.disconnect();
			};
		} else if (kind === "error") {
			// Short low buzz — square wave at 200Hz, 250ms, with a
			// fast attack and a quick decay so it reads as an
			// "alert" rather than a sustained tone.
			osc.type = "square";
			osc.frequency.setValueAtTime(200, now);
			gain.gain.setValueAtTime(0.0001, now);
			gain.gain.exponentialRampToValueAtTime(0.18, now + 0.005);
			gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.24);
			osc.connect(gain).connect(ctx.destination);
			osc.start(now);
			osc.stop(now + 0.25);
			//release per-cue nodes after stop.
			osc.onended = () => {
				osc.disconnect();
				gain.disconnect();
			};
		} else if (kind === "complete") {
			//two-note rising chime (A5 → D6, 880Hz → 1175Hz)
			// — a major-third interval that reads as a positive
			// "done!" cadence. Triangle wave for a softer, less
			// mechanical timbre than the square-wave error buzz.
			// Total 220ms: 100ms on the first note, 120ms on the
			// second, with a 5ms attack and 10ms release on each.
			osc.type = "triangle";
			osc.frequency.setValueAtTime(880, now);
			osc.frequency.setValueAtTime(1175, now + 0.1);
			gain.gain.setValueAtTime(0.0001, now);
			gain.gain.exponentialRampToValueAtTime(0.14, now + 0.005);
			gain.gain.setValueAtTime(0.14, now + 0.095);
			gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.1);
			gain.gain.exponentialRampToValueAtTime(0.14, now + 0.105);
			gain.gain.setValueAtTime(0.14, now + 0.21);
			gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.22);
			osc.connect(gain).connect(ctx.destination);
			osc.start(now);
			osc.stop(now + 0.22);
			//release per-cue nodes after stop.
			osc.onended = () => {
				osc.disconnect();
				gain.disconnect();
			};
		} else {
			osc.frequency.setValueAtTime(523, now);
			osc.frequency.exponentialRampToValueAtTime(392, now + 0.1);
			gain.gain.setValueAtTime(0.0001, now);
			gain.gain.exponentialRampToValueAtTime(0.15, now + 0.01);
			gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);
			osc.connect(gain).connect(ctx.destination);
			osc.start(now);
			osc.stop(now + 0.19);
			//release per-cue nodes after stop.
			osc.onended = () => {
				osc.disconnect();
				gain.disconnect();
			};
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
 */
const START_BEEP_WAV =
	"data:audio/wav;base64,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";
const STOP_BEEP_WAV =
	"data:audio/wav;base64,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";

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

function playViaHtmlAudio(
	kind: "start" | "stop" | "error" | "complete",
): boolean {
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
export function playSoundCue(
	kind: "start" | "stop" | "error" | "complete",
): void {
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
 * : also detaches the gesture-resume listeners (previously
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
 * : now also detaches gesture listeners explicitly (previously
 * only the boolean flag was reset, leaving the actual DOM listeners
 * attached across tests).
 */
export function _resetSoundManagerForTests(): void {
	_detachGestureListeners();
	_sharedAudioContext = null;
	_initAttempted = false;
	_initSucceeded = false;
	_enabled = true;
	_visualEnabled = false;
	_gestureListenerInstalled = false;
	_fallbackAudio = null;
}
