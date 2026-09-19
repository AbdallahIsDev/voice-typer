/**
 * Singleton sound-cue manager (start/stop/error only — C-SOUND-1).
 * Init flag set only after successful AudioContext construction; gesture
 */

import { START_BEEP_WAV, STOP_BEEP_WAV } from "./sound-manager/beeps";

let _sharedAudioContext: AudioContext | null = null;
let _initAttempted = false; // True ONLY after successful construction
let _initSucceeded = false;
let _enabled: boolean = true; // Mirror of config.sound_feedback_enabled
let _volume: number = 1;
let _gestureListenerInstalled = false;
let _resumeOnceHandler: (() => void) | null = null;

const STORAGE_KEY = "vt_sound_feedback_enabled";

/**
 * Update the in-memory enabled flag and persist to localStorage.
 *
 */
export function setSoundFeedbackEnabled(enabled: boolean): void {
	_enabled = enabled;
	try {
		localStorage.setItem(STORAGE_KEY, enabled ? "1" : "0");
	} catch (e) {
		console.warn(
			"[renderer:sound-manager] setSoundFeedbackEnabled localStorage.setItem failed:",
			e,
		);
	}
}

/**
 * Update the in-memory sound-volume multiplier (mirror of
 * config.sound_volume). Values are clamped to [0, 1]; non-finite
 */
export function setSoundVolume(volume: number): void {
	_volume = Number.isFinite(volume) ? Math.min(1, Math.max(0, volume)) : 1;
}

/**
 * Read the current volume multiplier (test + debug surface; the
 * production paths read the module state directly).
 */
export function getSoundVolume(): number {
	return _volume;
}

/**
 * Read the enabled flag from localStorage. Returns the cached in-memory
 * value if localStorage is unavailable.
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
		console.debug(
			"[renderer:sound-manager] isEnabled localStorage.getItem failed:",
			err,
		);
		return _enabled;
	}
}

/**
 * Eagerly construct the shared AudioContext and attempt to resume it.
 *
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
		if (_sharedAudioContext?.state === "closed") {
			_sharedAudioContext = null;
			_initSucceeded = false;
		}
		if (!_sharedAudioContext) {
			_sharedAudioContext = new Ctor();
		}
		_initAttempted = true;
		_initSucceeded = true;
		if (_sharedAudioContext.state === "suspended") {
			_sharedAudioContext.resume().catch((err: unknown) => {
				console.debug(
					"[renderer:sound-manager] initAudioContext resume() rejected:",
					err,
				);
			});
		}
		installGestureListener();
		return true;
	} catch (err) {
		// Construction threw, do NOT set _initSucceeded so the next
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
				console.debug(
					"[renderer:sound-manager] gesture-listener resume() rejected:",
					err,
				);
			});
		}
		if (ctx.state === "running") {
			_detachGestureListeners();
		}
	};
	_resumeOnceHandler = resumeOnce;

	const opts = { capture: true, passive: true } as const;
	window.addEventListener("click", resumeOnce, opts);
	window.addEventListener("keydown", resumeOnce, opts);
	window.addEventListener("touchstart", resumeOnce, opts);
	window.addEventListener("pointerdown", resumeOnce, opts);
}

/**
 * : detach the gesture-resume listeners explicitly.
 *
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
		_initSucceeded = false;
		initAudioContext();
	}
	return _sharedAudioContext;
}

/**
 * The three cue types the sound manager can play.
 *
 */
type SoundCueKind = "start" | "stop" | "error";

/**
 * One scheduled automation step for a cue. ``at`` is a seconds offset
 * from the cue's start time (``ctx.currentTime``); ``method`` maps 1:1
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
 */
type CueSpec = {
	oscillatorType: OscillatorType;
	/**
	 * ``osc.stop()`` offset from the cue start, in seconds.
	 */
	duration: number;
	steps: CueAutomationStep[];
};

const CUE_SPECS: Record<SoundCueKind, CueSpec> = {
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
};

/**
 * Synthesize and play a short audio cue via the Web Audio API, using
 * the per-kind ``CUE_SPECS`` table above. The shared synthesis loop
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
		const master = ctx.createGain();
		master.gain.value = _volume;

		osc.type = spec.oscillatorType;
		for (const step of spec.steps) {
			const param = step.param === "frequency" ? osc.frequency : gain.gain;
			if (step.method === "setValueAtTime") {
				param.setValueAtTime(step.value, now + step.at);
			} else {
				param.exponentialRampToValueAtTime(step.value, now + step.at);
			}
		}

		osc.connect(gain).connect(master).connect(ctx.destination);
		osc.start(now);
		osc.stop(now + spec.duration);
		osc.onended = () => {
			osc.disconnect();
			gain.disconnect();
			master.disconnect();
		};
	};

	if (ctx.state === "running") {
		doPlay();
		return true;
	}
	if (ctx.state === "suspended") {
		ctx
			.resume()
			.then(() => {
				try {
					doPlay();
				} catch (e) {
					console.warn("[renderer:sound-manager] synthesis doPlay failed:", e);
				}
			})
			.catch((err: unknown) => {
				console.debug(
					"[renderer:sound-manager] playViaAudioContext resume() rejected:",
					err,
				);
			});
		return false;
	}
	return false;
}

/**
 * Fallback: play a short cue via HTMLAudioElement with a data URL.
 *
 */

let _fallbackAudio: HTMLAudioElement | null = null;
function getFallbackAudio(): HTMLAudioElement | null {
	if (typeof window === "undefined") return null;
	if (!_fallbackAudio) {
		try {
			_fallbackAudio = new Audio();
			_fallbackAudio.preload = "auto";
		} catch (err) {
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
		if (kind === "start") {
			audio.src = START_BEEP_WAV;
		} else {
			audio.src = STOP_BEEP_WAV;
		}
		audio.volume = Math.min(1, Math.max(0, 0.15 * _volume));
		audio.currentTime = 0;
		const p = audio.play();
		if (p && typeof p.then === "function") {
			p.catch((err: unknown) => {
				console.debug(
					"[renderer:sound-manager] playViaHtmlAudio audio.play() rejected:",
					err,
				);
			});
		}
		return true;
	} catch (err) {
		console.debug("[renderer:sound-manager] playViaHtmlAudio failed:", err);
		return false;
	}
}

/**
 * Play a short audio cue for recording start/stop/error.
 *
 */
export function playSoundCue(kind: SoundCueKind): void {
	if (!isEnabled()) return;

	initAudioContext();

	const played = playViaAudioContext(kind);
	if (played) return;

	playViaHtmlAudio(kind);
}

/**
 * Close and release the shared AudioContext, if one exists.
 *
 */
export function closeAudioContext(): void {
	_detachGestureListeners();
	if (_sharedAudioContext) {
		_sharedAudioContext.close();
		_sharedAudioContext = null;
		_initAttempted = false;
		_initSucceeded = false;
	}
}

/**
 * Reset all state, used by tests to ensure isolation between cases.
 *
 */
export function _resetSoundManagerForTests(): void {
	_detachGestureListeners();
	_sharedAudioContext = null;
	_initAttempted = false;
	_initSucceeded = false;
	_enabled = true;
	_volume = 1;
	_gestureListenerInstalled = false;
	_fallbackAudio = null;
}
