/**
 * Capture-session state machine for ``HotkeyPicker``.
 *
 * DR-13: the hook now uses ``useReducer(hotkeyCaptureReducer, ...)`` for
 * the visible UI state (status / error / secondsRemaining /
 * heldModifiersLabel). The reducer is a pure function exported from
 * ``hotkey-utils.ts`` so it can be unit-tested in isolation.
 *
 * Side effects (calling ``onChange``, ``onCaptureStart``/``onCaptureEnd``,
 * starting/stopping the 30s countdown interval, clearing the session
 * refs) live in the hook, NOT in the reducer.
 *
 * Refs retained for genuine mutable state NOT in the reducer:
 *   - containerRef, timeoutRef, countdownIntervalRef (DOM + timers)
 *   - heldModifiersRef, heldNonModifiersRef (release detection)
 *   - sessionModifiersRef, sessionNonModifiersRef (sticky session set)
 *   - unsupportedComboRef, escPressedRef (capture-session flags)
 *   - capturingRef (mirrors ``state.status === "capturing"`` so the
 *     always-attached DOM listeners can short-circuit when idle; needed
 *     because ``state.status`` is stale inside the listener closure)
 *   - commitModifierOnlyRef, commitFullComboRef (inline-updated every
 *     render so ``handleKeyUp`` can stay stable — the commit logic
 *     depends on unstable parent props ``mode``/``value``/
 *     ``occupiedHotkeys``/``onChange``)
 *
 * Removed (vs. pre-DR-13):
 *   - recordingRef sync effect (replaced by capturingRef sync effect on
 *     ``state.status``)
 *   - handleKeyDownRef / handleKeyUpRef (handlers are now stable thanks
 *     to stable ``dispatch`` + commit-function refs)
 *   - cancelRecordingRef (``cancelRecording`` is now stable)
 *   - onCaptureEndRef / onCaptureStartRef (the parent passes stable
 *     ``useCallback`` callbacks, so they can be used directly in deps)
 *   - the "Track latest callbacks into refs after every render" effect
 */

import type { RefObject } from "react";
import { useCallback, useEffect, useReducer, useRef } from "react";
import { usePythonEvent } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
import {
	CANONICAL_MOD_ORDER,
	CAPTURE_TIMEOUT_SECONDS,
	formatHotkeyLabel,
	getModifierCodeMap,
	hotkeyCaptureReducer,
	IS_MAC,
	KEY_CODE_TO_PYNPUT,
	initialHotkeyCaptureState,
	tryCommitHotkey,
} from "./hotkey-utils";

// hoist the per-platform modifier-code lookup table to module
// scope. `getModifierCodeMap` returns a fresh 8-key object literal on
// every call — previously allocated inside `handleKeyDown` /
// `handleKeyUp` on every keystroke (60–120 calls/sec during typing
// bursts). `IS_MAC` is a module-load constant (it never changes at
// runtime — the platform is fixed for the lifetime of the renderer
// process), so the map can be computed once at import time and shared
// by both handlers.
const MODIFIER_CODE_MAP: Record<string, string> = getModifierCodeMap(IS_MAC);

export interface UseHotkeyCaptureParams {
	value: string;
	mode: "single" | "combo";
	onChange: (hotkey: string) => void;
	/**
	 * ESC-FIX-001: optional callback invoked when capture mode starts.
	 * Used by the parent to pause the global ESC cancel hotkey in the
	 * backend so that pressing Escape during capture doesn't trigger
	 * recording cancellation. Should be a stable ``useCallback`` so
	 * the transition effect doesn't re-fire.
	 */
	onCaptureStart?: () => void;
	/**
	 * ESC-FIX-001: optional callback invoked when capture mode ends
	 * (user pressed Escape, selected a key, or clicked the button
	 * again).  Used by the parent to resume the global ESC cancel
	 * hotkey in the backend. Should be a stable ``useCallback``.
	 */
	onCaptureEnd?: () => void;
	/**
	 * DUPLICATE-001: hotkey strings that are already occupied by other
	 * settings. When the user tries to set this picker to a value that's
	 * already in use, an error is shown and the change is rejected.
	 * This prevents two settings from having the same hotkey.
	 */
	occupiedHotkeys?: string[];
}

export interface UseHotkeyCaptureResult {
	recording: boolean;
	error: string | null;
	secondsRemaining: number;
	heldModifiersLabel: string;
	startRecording: () => void;
	cancelRecording: () => void;
	/**
	 * Exposed so the presentational shell's preset-dropdown
	 * ``onSelect`` handler can surface conflict / validation errors
	 * through the same error state the capture session uses.
	 */
	setError: (error: string | null) => void;
	containerRef: RefObject<HTMLDivElement | null>;
}

export function useHotkeyCapture({
	value,
	mode,
	onChange,
	onCaptureStart,
	onCaptureEnd,
	occupiedHotkeys,
}: UseHotkeyCaptureParams): UseHotkeyCaptureResult {
	const [state, dispatch] = useReducer(
		hotkeyCaptureReducer,
		initialHotkeyCaptureState,
	);

	// ── Genuine mutable state refs (NOT in the reducer) ───────────────
	const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	const countdownIntervalRef = useRef<ReturnType<typeof setInterval> | null>(
		null,
	);
	const containerRef = useRef<HTMLDivElement | null>(null);
	// HOTKEY-MULTIKEY-001: refs tracking the full set of pressed keys.
	const heldModifiersRef = useRef<Set<string>>(new Set());
	const heldNonModifiersRef = useRef<Set<string>>(new Set());
	const sessionModifiersRef = useRef<Set<string>>(new Set());
	const sessionNonModifiersRef = useRef<Set<string>>(new Set());
	const unsupportedComboRef = useRef<string | null>(null);
	// ESC-KEYUP-FIX: tracks whether ESC was pressed during the current
	// capture session so handleKeyUp can exit on ESC release instead of
	// on key-down.
	const escPressedRef = useRef(false);

	// capturingRef mirrors ``state.status === "capturing"`` for use in
	// stable callbacks (handleKeyDown/handleKeyUp early-return) and the
	// unmount cleanup. State is stale inside the always-attached DOM
	// listener closure (registered once with [] deps), so we need a ref
	// to read the latest capturing flag.
	// DEVIATION from DR-13 spec: spec lists recordingRef among the 6
	// mirror refs to remove, but the DOM-listener short-circuit and the
	// unmount cleanup genuinely need it. Renamed to capturingRef to
	// reflect that it tracks reducer status (not a separate useState).
	const capturingRef = useRef(false);
	useEffect(() => {
		capturingRef.current = state.status === "capturing";
	}, [state.status]);

	// commitModifierOnlyRef / commitFullComboRef hold the latest commit
	// functions. They depend on unstable parent props (mode / value /
	// occupiedHotkeys / onChange), so they're re-assigned every render.
	// handleKeyUp reads them via the ref so it can stay stable (deps
	// only on stable callbacks), avoiding the need for a handleKeyUpRef
	// mirror (the pre-DR-13 pattern).
	const commitModifierOnlyRef = useRef<() => void>(() => {});
	const commitFullComboRef = useRef<() => void>(() => {});

	// ── Helpers (defined first; referenced by handlers / commit fns) ──

	const snapshotModifiers = useCallback((e: KeyboardEvent): Set<string> => {
		const mods = new Set<string>();
		if (e.ctrlKey) mods.add("ctrl");
		if (e.shiftKey) mods.add("shift");
		if (e.altKey) mods.add("alt");
		if (e.metaKey) mods.add(IS_MAC ? "cmd" : "win");
		return mods;
	}, []);

	const getCanonicalModifiers = useCallback((): string[] => {
		return CANONICAL_MOD_ORDER.filter((m) =>
			sessionModifiersRef.current.has(m),
		);
	}, []);

	// HOTKEY-FULLMSG-001: build the full attempted-shortcut label for an
	// error message. Combines session modifiers (canonical order),
	// session non-modifiers (insertion order), and optionally an extra
	// key label that isn't in the session sets.
	const buildAttemptedComboLabel = useCallback(
		(extraKeyLabel?: string): string => {
			const parts: string[] = [];
			for (const m of CANONICAL_MOD_ORDER) {
				if (sessionModifiersRef.current.has(m)) parts.push(m);
			}
			for (const k of sessionNonModifiersRef.current) parts.push(k);
			if (extraKeyLabel) parts.push(extraKeyLabel);
			if (parts.length === 0) return "";
			const spec = parts.map((p) => `<${p}>`).join("+");
			return formatHotkeyLabel(spec);
		},
		[],
	);

	const clearCountdown = useCallback(() => {
		if (countdownIntervalRef.current) {
			clearInterval(countdownIntervalRef.current);
			countdownIntervalRef.current = null;
		}
		dispatch({ type: "Tick", secondsRemaining: 0 });
	}, []);

	// (Re)start the 30s countdown. The interval tracks the remaining
	// seconds in a local closure variable and dispatches ``Tick`` to
	// update the visible state. When the countdown hits 0, dispatch
	// ``Timeout`` (the reducer transitions status to "cancelled" and
	// the transition effect below calls ``onCaptureEnd`` + clears the
	// interval).
	const startCountdown = useCallback(() => {
		if (countdownIntervalRef.current) {
			clearInterval(countdownIntervalRef.current);
			countdownIntervalRef.current = null;
		}
		let seconds = CAPTURE_TIMEOUT_SECONDS;
		countdownIntervalRef.current = setInterval(() => {
			seconds -= 1;
			if (seconds <= 0) {
				if (countdownIntervalRef.current) {
					clearInterval(countdownIntervalRef.current);
					countdownIntervalRef.current = null;
				}
				dispatch({ type: "Timeout" });
			} else {
				dispatch({ type: "Tick", secondsRemaining: seconds });
			}
		}, 1000);
	}, []);

	// HOTKEY-MULTIKEY-001: reset all capture-session refs to their empty
	// state. Called after a successful commit, after a cancel, or after
	// an error to give the user a fresh attempt. Does NOT touch the
	// reducer error state (caller dispatches SetError separately).
	const resetCaptureSession = useCallback(() => {
		heldModifiersRef.current = new Set();
		heldNonModifiersRef.current = new Set();
		sessionModifiersRef.current = new Set();
		sessionNonModifiersRef.current = new Set();
		unsupportedComboRef.current = null;
		escPressedRef.current = false;
		if (timeoutRef.current) {
			clearTimeout(timeoutRef.current);
			timeoutRef.current = null;
		}
	}, []);

	// ── Commit functions (assigned to refs every render) ─────────────
	//
	// DR-14: the duplicated validate-then-conflict-check sequence is
	// extracted into ``tryCommitHotkey`` (hotkey-utils.ts). Each commit
	// function reduces to: validate via tryCommitHotkey → on failure
	// dispatch SetError + resetCaptureSession; on success call onChange
	// + dispatch CommitSuccess (the transition effect handles
	// onCaptureEnd + clearCountdown).
	commitModifierOnlyRef.current = () => {
		const mods = getCanonicalModifiers();
		if (mods.length === 0) return;

		// In single mode, only a SINGLE modifier is allowed. If the user
		// held 2+ modifiers, show an error referencing the full attempted
		// combo and stay in capture mode.
		if (mode === "single" && mods.length > 1) {
			const label = formatHotkeyLabel(mods.map((m) => `<${m}>`).join("+"));
			dispatch({
				type: "SetError",
				error: t("hotkeyValidation.dictationKeyMustBeSingle", {
					label,
				}),
			});
			// Reset the held+session modifiers so the next attempt starts
			// fresh, but DON'T call full resetCaptureSession — the user
			// is still in capture mode and the countdown should keep
			// running.
			sessionModifiersRef.current = new Set();
			heldModifiersRef.current = new Set();
			return;
		}

		const newHotkey = mods.map((m) => `<${m}>`).join("+");
		const r = tryCommitHotkey(newHotkey, {
			mode,
			value,
			occupiedHotkeys,
			t,
			resetSession: true,
		});
		if (!r.ok) {
			dispatch({ type: "SetError", error: r.error });
			resetCaptureSession();
			return;
		}
		onChange(newHotkey);
		resetCaptureSession();
		dispatch({ type: "CommitSuccess" });
	};

	commitFullComboRef.current = () => {
		const mods = getCanonicalModifiers();
		const keys = [...sessionNonModifiersRef.current];
		const parts = [...mods, ...keys];
		if (parts.length === 0) return;

		// HOTKEY-FULLMSG-001: in single mode, if the user pressed
		// modifiers alongside a non-modifier, the full combo is NOT a
		// valid dictation key. Show an error referencing the FULL
		// attempted combo.
		if (mode === "single" && parts.length > 1) {
			const label = formatHotkeyLabel(parts.map((p) => `<${p}>`).join("+"));
			dispatch({
				type: "SetError",
				error: t("hotkeyValidation.dictationKeyMustBeSingle", {
					label,
				}),
			});
			resetCaptureSession();
			return;
		}

		const newHotkey = parts.map((p) => `<${p}>`).join("+");
		const r = tryCommitHotkey(newHotkey, {
			mode,
			value,
			occupiedHotkeys,
			t,
			resetSession: true,
		});
		if (!r.ok) {
			dispatch({ type: "SetError", error: r.error });
			resetCaptureSession();
			return;
		}
		onChange(newHotkey);
		resetCaptureSession();
		dispatch({ type: "CommitSuccess" });
	};

	// ── Public actions ───────────────────────────────────────────────

	const cancelRecording = useCallback(() => {
		// ESC-KEYUP-FIX guard: if the backend pushes a
		// hotkey_capture_cancel event while the frontend has already
		// exited capture (e.g. via key-up handler), the reducer's
		// OutsideClick case is a no-op when status !== "capturing", so
		// onCaptureEnd won't be called twice. We still call
		// resetCaptureSession unconditionally to clear any stale refs.
		resetCaptureSession();
		dispatch({ type: "OutsideClick" });
	}, [resetCaptureSession]);

	const startRecording = useCallback(() => {
		resetCaptureSession();
		dispatch({ type: "Start" });
		startCountdown();
	}, [resetCaptureSession, startCountdown]);

	const setError = useCallback((error: string | null) => {
		dispatch({ type: "SetError", error });
	}, []);

	// ── Keydown handler (stable — only depends on stable helpers) ────
	//
	// HOTKEY-MULTIKEY-001: each pressed key is added to the appropriate
	// ``held*`` set and the sticky ``session*`` set. No commit happens
	// here — the candidate is finalized only when all keys are released
	// (see keyUp handler).
	const handleKeyDown = useCallback(
		(e: KeyboardEvent) => {
			if (!capturingRef.current) return;

			if (e.key === "Escape") {
				// ESC-KEYUP-FIX: cancel on ESC RELEASE (key-up), not on
				// key-down. Just record that ESC was pressed; handleKeyUp
				// does the cancel on release.
				e.preventDefault();
				e.stopPropagation();
				escPressedRef.current = true;
				dispatch({ type: "EscPressed" });
				return;
			}

			e.preventDefault();
			e.stopPropagation();

			const modifierCode = MODIFIER_CODE_MAP[e.code];
			if (modifierCode) {
				heldModifiersRef.current.add(modifierCode);
				sessionModifiersRef.current.add(modifierCode);
				dispatch({
					type: "KeyDown",
					modifiers: [...heldModifiersRef.current].join(","),
					nonModifiers: "",
				});
				return;
			}

			const pynputName = KEY_CODE_TO_PYNPUT[e.code];

			// Snapshot currently-held modifiers from the event flags —
			// even if the user pressed the modifier BEFORE entering
			// capture mode (so we never saw its keydown), the e.*Key
			// flag tells us it's currently held.
			const currentMods = snapshotModifiers(e);
			for (const m of currentMods) {
				heldModifiersRef.current.add(m);
				sessionModifiersRef.current.add(m);
			}
			if (currentMods.size > 0) {
				dispatch({
					type: "KeyDown",
					modifiers: [...heldModifiersRef.current].join(","),
					nonModifiers: "",
				});
			}

			if (!pynputName) {
				// HOTKEY-FULLMSG-001: include held modifiers AND any
				// non-modifier keys already pressed in this session in
				// the error message, so the user sees the complete
				// attempted shortcut.
				const attemptedCombo = buildAttemptedComboLabel(e.key);
				unsupportedComboRef.current = attemptedCombo;
				dispatch({
					type: "SetError",
					error: t("hotkeyValidation.keyNotSupported", {
						label: attemptedCombo,
					}),
				});
				return;
			}

			heldNonModifiersRef.current.add(pynputName);
			sessionNonModifiersRef.current.add(pynputName);
			// Clear any prior error so the user sees the candidate is
			// pending.
			dispatch({ type: "SetError", error: null });
			unsupportedComboRef.current = null;
		},
		[snapshotModifiers, buildAttemptedComboLabel],
	);

	// ── Keyup handler (stable — reads commit fns via refs) ───────────
	//
	// HOTKEY-DEFER-001 (preserved): committing on keyUP (not keyDOWN)
	// eliminates the capture-triggers-recording race where the backend
	// sees the still-held key as a fresh press.
	const handleKeyUp = useCallback(
		(e: KeyboardEvent) => {
			if (!capturingRef.current) return;

			// ESC-KEYUP-FIX: exit capture mode on ESC release.
			if (e.key === "Escape" && escPressedRef.current) {
				escPressedRef.current = false;
				resetCaptureSession();
				dispatch({ type: "EscReleased" });
				return;
			}

			const modifierCode = MODIFIER_CODE_MAP[e.code];
			if (modifierCode) {
				heldModifiersRef.current.delete(modifierCode);
				dispatch({
					type: "KeyUp",
					modifiers: [...heldModifiersRef.current].join(","),
					nonModifiers: "",
				});
				// Modifier-only release path: no non-modifier was pressed
				// AND all modifiers are released → commit the modifier
				// set as the hotkey (single-key or modifier-combo).
				if (
					sessionNonModifiersRef.current.size === 0 &&
					heldModifiersRef.current.size === 0 &&
					sessionModifiersRef.current.size > 0
				) {
					commitModifierOnlyRef.current();
				}
				return;
			}

			const pynputName = KEY_CODE_TO_PYNPUT[e.code];
			if (pynputName) {
				heldNonModifiersRef.current.delete(pynputName);
			}

			// If an unsupported key was pressed during this session,
			// keep the error visible and DO NOT commit. Stay in capture
			// mode so the user can try a different combo.
			if (unsupportedComboRef.current) {
				unsupportedComboRef.current = null;
				return;
			}

			// HOTKEY-MULTIKEY-001: commit only when ALL non-modifier keys
			// have been released — makes the captured combo release-order
			// independent.
			if (
				heldNonModifiersRef.current.size === 0 &&
				sessionNonModifiersRef.current.size > 0
			) {
				commitFullComboRef.current();
			}
		},
		[resetCaptureSession],
	);

	// ── Transition effect: fire onCaptureStart / onCaptureEnd on
	// status changes. Also clears the countdown when leaving "capturing".
	//
	// The parent passes stable ``useCallback`` callbacks for
	// onCaptureStart / onCaptureEnd (verified in
	// RecordingSettingsSection.tsx), so this effect only re-fires on
	// actual status transitions — not on every parent re-render.
	useEffect(() => {
		if (state.status === "capturing") {
			onCaptureStart?.();
		} else if (state.status === "cancelled" || state.status === "committed") {
			clearCountdown();
			onCaptureEnd?.();
		}
	}, [state.status, onCaptureStart, onCaptureEnd, clearCountdown]);

	// ESC-FIX-003: always-attached keyboard listener — NEVER re-register,
	// avoiding the race window where listeners are removed and re-added.
	// handleKeyDown / handleKeyUp are stable (dispatch-only deps), so
	// this effect runs once on mount.
	useEffect(() => {
		const onKeyDown = (e: KeyboardEvent) => handleKeyDown(e);
		const onKeyUp = (e: KeyboardEvent) => handleKeyUp(e);
		window.addEventListener("keydown", onKeyDown, true);
		window.addEventListener("keyup", onKeyUp, true);
		return () => {
			window.removeEventListener("keydown", onKeyDown, true);
			window.removeEventListener("keyup", onKeyUp, true);
		};
	}, [handleKeyDown, handleKeyUp]);

	// ESC-CAPTURE-FIX / ESC-KEYUP-FIX: listen for backend-triggered
	// capture cancel events. The backend pushes
	// ``hotkey_capture_cancel`` on ESC key-up (release) via its release
	// callback. cancelRecording is stable, so the subscription doesn't
	// need re-registration.
	usePythonEvent("hotkey_capture_cancel", (): (() => void) | undefined => {
		cancelRecording();
		return undefined;
	});

	// Unmount cleanup: clear timers and, if we were still capturing,
	// call onCaptureEnd so the backend ESC-cancel hotkey is resumed.
	// ``onCaptureEnd`` is captured from the first render — safe because
	// the parent passes a stable ``useCallback`` (see
	// RecordingSettingsSection.tsx).
	useEffect(() => {
		return () => {
			if (timeoutRef.current) clearTimeout(timeoutRef.current);
			if (countdownIntervalRef.current) {
				clearInterval(countdownIntervalRef.current);
				countdownIntervalRef.current = null;
			}
			if (capturingRef.current) {
				onCaptureEnd?.();
			}
		};
	}, [onCaptureEnd]);

	// Outside-click handler. Registered only while capturing so we don't
	// intercept clicks when idle. Uses ``pointerdown`` (not ``click``)
	// so the cancel happens BEFORE the click target receives focus.
	useEffect(() => {
		if (state.status !== "capturing") return;
		const onPointerDown = (e: PointerEvent) => {
			const target = e.target as Node | null;
			if (!target) return;
			if (containerRef.current?.contains(target)) return;
			cancelRecording();
		};
		document.addEventListener("pointerdown", onPointerDown, true);
		return () => {
			document.removeEventListener("pointerdown", onPointerDown, true);
		};
	}, [state.status, cancelRecording]);

	return {
		recording: state.status === "capturing",
		error: state.error,
		secondsRemaining: state.secondsRemaining,
		heldModifiersLabel: state.heldModifiersLabel,
		startRecording,
		cancelRecording,
		setError,
		containerRef,
	};
}
