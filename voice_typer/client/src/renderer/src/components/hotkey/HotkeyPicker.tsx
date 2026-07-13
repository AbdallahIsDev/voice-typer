import { Cancel01Icon, KeyboardIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import {
	formatHotkeyLabel,
	IS_MAC,
	KEY_CODE_TO_PYNPUT,
	MODIFIER_CODE_TO_PYNPUT,
	validateHotkey,
} from "./hotkey-utils";

// HOTKEY-MULTIKEY-001: canonical modifier order. Modifiers are stored in
// the session set in INSERTION order (the order the user pressed them),
// but the captured hotkey must be IDENTICAL regardless of press order —
// so we always emit modifiers in this canonical order before committing.
// This satisfies the directive's requirement: "Whether the user releases
// Ctrl first, Shift first, or any other key first, the final captured
// shortcut should always be identical."
//
// Defined at module level so it's referentially stable across renders
// (avoids stale-closure warnings in useCallback deps).
const CANONICAL_MOD_ORDER = [
	"ctrl",
	"shift",
	"alt",
	"cmd",
	"win",
	"super",
	"fn",
] as const;

interface HotkeyPickerProps {
	value: string;
	onChange: (hotkey: string) => void;
	mode: "single" | "combo";
	/**
	 * Optional preset options for the dropdown menu.
	 * When provided, a dropdown is rendered so the user can pick from
	 * these presets. When omitted or empty, no dropdown is shown and
	 * only the capture button is available.
	 */
	presets?: { value: string; label: string }[];
	className?: string;
	"aria-label"?: string;
	/**
	 * ESC-FIX-001: optional callback invoked when capture mode starts.
	 * Used by the parent to pause the global ESC cancel hotkey in the
	 * backend so that pressing Escape during capture doesn't trigger
	 * recording cancellation.
	 */
	onCaptureStart?: () => void;
	/**
	 * ESC-FIX-001: optional callback invoked when capture mode ends
	 * (user pressed Escape, selected a key, or clicked the button
	 * again).  Used by the parent to resume the global ESC cancel
	 * hotkey in the backend.
	 */
	onCaptureEnd?: () => void;
	/**
	 * DUPLICATE-001: hotkey strings that are already occupied by other
	 * settings. When the user tries to set this picker to a value that's
	 * already in use, an error is shown and the change is rejected.
	 * This prevents two settings from having the same hotkey.
	 * Example: if the dictation key is set to "<shift>", passing
	 * occupiedHotkeys={["<shift>"]} to the repaste key picker prevents
	 * the user from also setting the repaste key to Shift.
	 */
	occupiedHotkeys?: string[];
}

/**
 * NATIVE-001: supports modifier-only hotkeys (Alt, Ctrl, Shift, Win/Cmd,
 * Fn) as single-key triggers via native backend modifier-only release
 * detection.
 *
 * For the FN key on macOS: the browser doesn't fire keydown events for
 * Fn (it's a modifier the OS intercepts). Users on macOS who want Fn
 * must select it from the dropdown — capture won't work.
 *
 * HOTKEY-MULTIKEY-001 (Tasks 1.1 + 1.3): the capture flow has been
 * redesigned to support true multi-key shortcuts. The previous
 * architecture stored a single ``{ mods, mainKey }`` candidate which
 * could only hold ONE non-modifier key; pressing a second non-modifier
 * overwrote the first, making combos like ``Delete+End`` impossible.
 *
 * The new architecture accumulates the full set of pressed keys across
 * the entire capture session:
 *   - ``heldModifiersRef`` — modifiers currently held down.
 *   - ``heldNonModifiersRef`` — non-modifier keys currently held down.
 *   - ``sessionModifiersRef`` — every modifier held at ANY point in the
 *     session (sticky: once added, stays until commit/cancel).
 *   - ``sessionNonModifiersRef`` — every non-modifier pressed during the
 *     session.
 *
 * The shortcut is committed when ALL pressed keys have been released
 * (i.e. both ``held*`` sets are empty AND at least one key was pressed).
 * Because we accumulate the session set rather than the currently-held
 * set, the captured shortcut is identical regardless of release order
 * (Ctrl→Shift release vs Shift→Ctrl release produce the same combo).
 *
 * HOTKEY-FULLMSG-001 (Task 1.1): error messages always reference the
 * complete attempted shortcut, including all modifiers. Previously
 * pressing ``Shift+Z`` in single mode showed ``"Single letters and
 * digits can't be used as hotkeys — 'z' would interfere with typing"``
 * (the Shift was dropped because the candidate was ``<z>`` without
 * mods). The new architecture keeps the modifiers in the session set
 * even in single mode, so the error can reference the full combo:
 * ``"Shift+Z can't be used as a dictation key — it would interfere
 * with typing. Use the re-paste key picker for combos."``.
 *
 * Preserved from prior architecture:
 *   - HOTKEY-DEFER-001: commit on keyUP (not keyDOWN) — eliminates the
 *     capture-triggers-recording race where the backend sees the still-
 *     held key as a fresh press.
 *   - ESC-KEYUP-FIX: ESC cancel fires on ESC release (key-up), not on
 *     key-down. The frontend DOM keyup is a secondary path; the backend
 *     push via ``hotkey_capture_cancel`` is the primary path.
 *   - ESC-FIX-003: always-attached DOM listeners via refs (no
 *     re-registration window).
 *   - cancelRecording guard: prevents duplicate ``onCaptureEnd`` calls
 *     when both backend push and frontend DOM keyup fire for the same
 *     ESC release.
 */
export function HotkeyPicker({
	value,
	onChange,
	mode,
	presets,
	className,
	"aria-label": ariaLabel = "Hotkey picker",
	onCaptureStart,
	onCaptureEnd,
	occupiedHotkeys,
}: HotkeyPickerProps) {
	const [recording, setRecording] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	// HOTKEY-MULTIKEY-001: refs tracking the full set of pressed keys.
	//   - heldModifiersRef / heldNonModifiersRef: keys currently held
	//     down (used to detect "all keys released → commit").
	//   - sessionModifiersRef / sessionNonModifiersRef: sticky sets
	//     accumulated across the entire capture session. These drive
	//     the final committed combo and the error message — even if
	//     the user releases a modifier before releasing the main key,
	//     the modifier is still part of the attempted shortcut.
	const heldModifiersRef = useRef<Set<string>>(new Set());
	const heldNonModifiersRef = useRef<Set<string>>(new Set());
	const sessionModifiersRef = useRef<Set<string>>(new Set());
	const sessionNonModifiersRef = useRef<Set<string>>(new Set());
	// Tracks the human-readable label of an unsupported key that was
	// pressed during the session, so the error message can include
	// the full attempted combo (mods + the unsupported key).
	const unsupportedComboRef = useRef<string | null>(null);
	// Ref mirror of recording state so event handlers always read the
	// latest value from a ref instead of relying on a re-created closure.
	// This eliminates the stale-closure window where the handler could
	// see recording=false after the user clicked the button but before
	// React re-registered the listener.
	const recordingRef = useRef(false);
	// ESC-FIX-002: store the latest onCaptureEnd / onCaptureStart in refs
	// so the unmount cleanup can call the latest version without depending
	// on the prop directly in the effect (which would fire the cleanup on
	// every re-render when the parent passes inline arrow functions).
	const onCaptureEndRef = useRef(onCaptureEnd);
	const onCaptureStartRef = useRef(onCaptureStart);
	// ESC-FIX-003: refs for handleKeyDown / handleKeyUp so the
	// always-attached listener effect (empty deps) always reads the
	// latest closure without needing to re-register the DOM listener.
	// Initialized with no-op functions to avoid TDZ errors —
	// ``handleKeyDown`` and ``handleKeyUp`` are ``const`` declarations
	// defined later in the component body. The tracking effect below
	// updates these refs with the real callbacks after every render.
	const handleKeyDownRef = useRef<(e: KeyboardEvent) => void>(() => {});
	const handleKeyUpRef = useRef<(e: KeyboardEvent) => void>(() => {});
	// ESC-CAPTURE-FIX: ref for cancelRecording so the backend-triggered
	// hotkey_capture_cancel event handler can always call the latest
	// version without depending on it in deps.
	const cancelRecordingRef = useRef<() => void>(() => {});
	// ESC-KEYUP-FIX: tracks whether ESC was pressed during the current
	// capture session, so that handleKeyUp can exit on ESC release
	// instead of on key-down. Set on keydown of ESC, cleared after
	// the key-up handler processes the release.
	const escPressedRef = useRef(false);

	useEffect(() => {
		recordingRef.current = recording;
	}, [recording]);

	// Track the latest callbacks into refs after every render so the
	// always-attached listener (``useEffect([], ..)``) never goes stale.
	useEffect(() => {
		onCaptureEndRef.current = onCaptureEnd;
		onCaptureStartRef.current = onCaptureStart;
		handleKeyDownRef.current = handleKeyDown;
		handleKeyUpRef.current = handleKeyUp;
		cancelRecordingRef.current = cancelRecording;
		// No deps array → runs after every commit. This is intentional: the
		// refs must stay in sync with the latest prop values so the
		// always-attached keyboard listener (registered once in a separate
		// useEffect with `[]` deps) reads fresh handlers via the refs.
	});

	// ESC-FIX-003: always-attached keyboard listener — NEVER re-register,
	// avoiding the race window where listeners are removed and re-added.
	// The handlers themselves check ``recordingRef.current`` so it's safe
	// to keep them registered even when NOT in capture mode.
	useEffect(() => {
		const onKeyDown = (e: KeyboardEvent) => handleKeyDownRef.current(e);
		const onKeyUp = (e: KeyboardEvent) => handleKeyUpRef.current(e);
		window.addEventListener("keydown", onKeyDown, true);
		window.addEventListener("keyup", onKeyUp, true);
		return () => {
			window.removeEventListener("keydown", onKeyDown, true);
			window.removeEventListener("keyup", onKeyUp, true);
		};
	}, []);

	// ESC-CAPTURE-FIX / ESC-KEYUP-FIX: listen for backend-triggered capture
	// cancel events. The backend pushes ``hotkey_capture_cancel`` on ESC
	// key-up (release) via its release callback (_on_esc_release). The
	// DOM keyup handler above is a secondary path; the backend push is
	// the reliable primary path across all Windows configurations.
	// The guard in cancelRecording prevents duplicate onCaptureEnd calls
	// when both paths fire.
	useEffect(() => {
		const unsubscribe = window.python?.onEvent?.((event) => {
			if (event.type === "hotkey_capture_cancel") {
				cancelRecordingRef.current?.();
			}
		});
		return () => {
			unsubscribe?.();
		};
	}, []);

	useEffect(() => {
		return () => {
			if (timeoutRef.current) clearTimeout(timeoutRef.current);
			// ESC-FIX-001: if the component unmounts while still capturing,
			// notify the parent so the backend ESC cancel is resumed.
			if (recordingRef.current) {
				onCaptureEndRef.current?.();
			}
		};
	}, []);

	// ── Helpers (defined first; referenced by handleKeyDown / handleKeyUp) ──

	// HOTKEY-MULTIKEY-001: snapshot the modifier state from the keyboard
	// event (``e.ctrlKey/shiftKey/altKey/metaKey``) and convert to pynput
	// modifier names. This is more reliable than tracking modifier key
	// events themselves, because:
	//   - On macOS, the Cmd key's e.code is ``MetaLeft``/``MetaRight`` and
	//     maps to "cmd" — but if the user holds Cmd+Q, the Q keydown event
	//     has ``e.metaKey=true`` which we read directly here.
	//   - If the user pressed a modifier BEFORE entering capture mode (the
	//     keydown event for the modifier itself was missed), we still see
	//     the modifier via the ``e.*Key`` flag on the next non-modifier
	//     keydown.
	// The returned set is added to both ``heldModifiersRef`` and
	// ``sessionModifiersRef`` so the modifier is "sticky" for the rest
	// of the session (the user might release it before releasing the
	// main key, but it's still part of the attempted shortcut).
	const snapshotModifiers = useCallback((e: KeyboardEvent): Set<string> => {
		const mods = new Set<string>();
		if (e.ctrlKey) mods.add("ctrl");
		if (e.shiftKey) mods.add("shift");
		if (e.altKey) mods.add("alt");
		if (e.metaKey) mods.add(IS_MAC ? "cmd" : "win");
		return mods;
	}, []);

	// HOTKEY-MULTIKEY-001: return the session modifiers in canonical order.
	const getCanonicalModifiers = useCallback((): string[] => {
		return CANONICAL_MOD_ORDER.filter((m) =>
			sessionModifiersRef.current.has(m),
		);
	}, []);

	// HOTKEY-FULLMSG-001: build the full attempted-shortcut label for an
	// error message. Combines session modifiers (canonical order), session
	// non-modifiers (insertion order), and (optionally) an extra key label
	// that isn't in the session sets (e.g. an unsupported key that was
	// pressed but not added to the session because it has no pynput
	// mapping).
	const buildAttemptedComboLabel = useCallback(
		(extraKeyLabel?: string): string => {
			const parts: string[] = [];
			// Modifiers in canonical order so the label is deterministic.
			for (const m of CANONICAL_MOD_ORDER) {
				if (sessionModifiersRef.current.has(m)) parts.push(m);
			}
			// Non-modifiers in insertion order.
			for (const k of sessionNonModifiersRef.current) parts.push(k);
			// Extra key (unsupported key with no pynput mapping).
			if (extraKeyLabel) parts.push(extraKeyLabel);
			if (parts.length === 0) return "";
			// Use the shared label formatter so the error message
			// matches the display format the user will see after
			// a successful assignment.
			const spec = parts.map((p) => `<${p}>`).join("+");
			return formatHotkeyLabel(spec);
		},
		[],
	);

	// HOTKEY-MULTIKEY-001: reset all capture-session refs to their empty
	// state. Called after a successful commit, after a cancel, or after
	// an error to give the user a fresh attempt.
	//
	// NOTE: this does NOT touch the ``error`` state. The error must be
	// cleared separately (via ``setError(null)``) when the user starts a
	// new attempt or cancels — but it must NOT be cleared right after
	// ``setError(msg)`` sets a validation error, or the user would never
	// see the error. Callers that need to clear the error should do so
	// explicitly.
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

	// HOTKEY-MULTIKEY-001: commit a modifier-only combo (e.g.
	// ``<ctrl>+<shift>``, ``<alt>``). Called when the user released the
	// last held modifier without pressing any non-modifier key.
	const commitModifierOnlyCombo = useCallback(() => {
		const mods = getCanonicalModifiers();
		if (mods.length === 0) return;

		// In single mode, only a SINGLE modifier is allowed (the dictation
		// key is one key). If the user held 2+ modifiers, show an error
		// referencing the full attempted combo and stay in capture mode.
		if (mode === "single" && mods.length > 1) {
			const label = formatHotkeyLabel(mods.map((m) => `<${m}>`).join("+"));
			setError(
				`"${label}" can't be used as a dictation key — dictation key must be a single key. Use the re-paste key picker for combos.`,
			);
			// Reset the session so the user can try again without the stale
			// multi-modifier set blocking the next attempt.
			sessionModifiersRef.current = new Set();
			heldModifiersRef.current = new Set();
			return;
		}

		const newHotkey = mods.map((m) => `<${m}>`).join("+");
		const validationError = validateHotkey(newHotkey, mode);
		if (validationError) {
			setError(validationError);
			resetCaptureSession();
			return;
		}
		// DUPLICATE-001: reject if another setting already uses this hotkey.
		// Skip the check when the hotkey hasn't actually changed (the
		// user is re-selecting the current value).
		if (newHotkey !== value && occupiedHotkeys?.includes(newHotkey)) {
			const label = formatHotkeyLabel(newHotkey);
			setError(
				`"${label}" is already in use by another setting. Each hotkey must be unique.`,
			);
			resetCaptureSession();
			return;
		}
		onChange(newHotkey);
		resetCaptureSession();
		setRecording(false);
		recordingRef.current = false;
		setError(null);
		onCaptureEndRef.current?.();
	}, [
		mode,
		onChange,
		resetCaptureSession,
		getCanonicalModifiers,
		occupiedHotkeys,
		value,
	]);

	// HOTKEY-MULTIKEY-001: commit the full combo when all non-modifier
	// keys have been released. The committed combo includes every
	// modifier and every non-modifier pressed during the session
	// (release-order independent).
	const commitFullCombo = useCallback(() => {
		const mods = getCanonicalModifiers();
		const keys = [...sessionNonModifiersRef.current];
		const parts = [...mods, ...keys];

		if (parts.length === 0) return;

		// HOTKEY-FULLMSG-001 (Task 1.1): in single mode, if the user
		// pressed modifiers alongside a non-modifier, the full combo
		// is NOT a valid dictation key. Show an error referencing the
		// FULL attempted combo (modifiers + non-modifiers), not just
		// the non-modifier.
		if (mode === "single" && parts.length > 1) {
			const label = formatHotkeyLabel(parts.map((p) => `<${p}>`).join("+"));
			setError(
				`"${label}" can't be used as a dictation key — dictation key must be a single key. Use the re-paste key picker for combos.`,
			);
			resetCaptureSession();
			return;
		}

		// Build the hotkey string. In single mode, parts.length === 1
		// (we handled the >1 case above) so the hotkey is a single key.
		// In combo mode, parts can be 1+ modifiers + 1+ non-modifiers.
		const newHotkey = parts.map((p) => `<${p}>`).join("+");

		const validationError = validateHotkey(newHotkey, mode);
		if (validationError) {
			// HOTKEY-FULLMSG-001: the validation error from the shared
			// validator already references the full combo (e.g.
			// "Shift+Z interferes with text capitalization"). We pass
			// it through unchanged.
			setError(validationError);
			resetCaptureSession();
			return;
		}
		// DUPLICATE-001: reject if another setting already uses this hotkey.
		// Skip the check when the hotkey hasn't actually changed (the
		// user is re-selecting the current value).
		if (newHotkey !== value && occupiedHotkeys?.includes(newHotkey)) {
			const label = formatHotkeyLabel(newHotkey);
			setError(
				`"${label}" is already in use by another setting. Each hotkey must be unique.`,
			);
			resetCaptureSession();
			return;
		}
		onChange(newHotkey);
		resetCaptureSession();
		setRecording(false);
		recordingRef.current = false;
		setError(null);
		onCaptureEndRef.current?.();
	}, [
		mode,
		onChange,
		resetCaptureSession,
		getCanonicalModifiers,
		occupiedHotkeys,
		value,
	]);

	const cancelRecording = useCallback(() => {
		// ESC-KEYUP-FIX: guard against duplicate onCaptureEnd calls.
		// If the backend pushes a hotkey_capture_cancel event while
		// the frontend has already exited capture via key-up handler,
		// recordingRef.current is already false — skip the redundant
		// IPC call that would log a duplicate "ESC cancel RESUMED".
		if (!recordingRef.current) return;
		setRecording(false);
		recordingRef.current = false;
		resetCaptureSession();
		setError(null);
		// ESC-FIX-001/002: read from ref so we always call the latest
		// onCaptureEnd without depending on it in deps.
		onCaptureEndRef.current?.();
	}, [resetCaptureSession]); // ESC-FIX-002: onCaptureEnd read from ref

	const startRecording = useCallback(() => {
		setRecording(true);
		// Sync the ref immediately alongside the state setter so the
		// keydown handler sees recording=true even before React's
		// re-render + effect cycle completes.
		recordingRef.current = true;
		resetCaptureSession();
		setError(null);
		// ESC-FIX-001/002: read from ref so we always call the latest
		// onCaptureStart without depending on it in deps.
		onCaptureStartRef.current?.();
		// HOTKEY-FIX-003: No capture timeout — stay in capture mode
		// indefinitely. Exit only when: user clicks outside, clicks
		// the capture button again, or presses Esc.
	}, [resetCaptureSession]);

	// ── Keydown handler ───────────────────────────────────────────────
	//
	// HOTKEY-MULTIKEY-001: each pressed key is added to the appropriate
	// ``held*`` set and the sticky ``session*`` set. No commit happens
	// here — the candidate is finalized only when all keys are released
	// (see keyUp handler).
	const handleKeyDown = useCallback(
		(e: KeyboardEvent) => {
			if (!recordingRef.current) return;

			if (e.key === "Escape") {
				// ESC-KEYUP-FIX: cancel hotkey capture on ESC RELEASE
				// (key-up), not on key-down. The user presses ESC and
				// releases it — cancel happens on release. This matches
				// how regular key capture works (capture on key-up).
				e.preventDefault();
				e.stopPropagation();
				escPressedRef.current = true;
				// Don't exit capture or call onCaptureEnd here.
				// handleKeyUp will do that when ESC is released.
				return;
			}

			e.preventDefault();
			e.stopPropagation();

			// Detect modifier keys by e.code (layout-independent).
			const modifierCode = MODIFIER_CODE_TO_PYNPUT[e.code];
			if (modifierCode) {
				// HOTKEY-MULTIKEY-001: accumulate the modifier into both
				// the held set (for release detection) and the session
				// set (for the final committed combo).
				heldModifiersRef.current.add(modifierCode);
				sessionModifiersRef.current.add(modifierCode);
				return;
			}

			// HOTKEY-FIX-002: use e.code (layout-independent) instead
			// of e.key (layout-dependent) so the lookup works on
			// AZERTY, Dvorak, etc. The KEY_CODE_TO_PYNPUT table now
			// includes letters and digits (was missing in Round 0).
			const pynputName = KEY_CODE_TO_PYNPUT[e.code];

			// Snapshot the currently-held modifiers from the event flags.
			// Even if the user pressed the modifier BEFORE entering capture
			// mode (so we never saw its keydown), the e.*Key flag tells us
			// it's currently held.
			const currentMods = snapshotModifiers(e);
			for (const m of currentMods) {
				heldModifiersRef.current.add(m);
				sessionModifiersRef.current.add(m);
			}

			if (!pynputName) {
				// HOTKEY-FULLMSG-001 (Task 1.1): include held modifiers
				// AND any non-modifier keys already pressed in this
				// session in the error message, so the user sees the
				// complete attempted shortcut. Previously pressing
				// ``Shift+Z`` showed ``"Key 'Z' is not supported."`` —
				// dropping the Shift modifier entirely. Now it shows
				// ``"Shift+Z is not supported. Try letters, numbers,
				// F-keys, or Space."`` (or the full combo, including
				// any previously-pressed keys like Delete in a
				// ``Delete+Shift+Z`` attempt).
				//
				// Use e.key as the human-readable label for the
				// unsupported key (it's layout-aware and gives the
				// user a recognizable name like "Z" or "F13").
				const attemptedCombo = buildAttemptedComboLabel(e.key);
				unsupportedComboRef.current = attemptedCombo;
				setError(
					`"${attemptedCombo}" is not supported. Try letters, numbers, F-keys, or Space.`,
				);
				return;
			}

			// HOTKEY-MULTIKEY-001: add the non-modifier to both the held
			// set (for release detection) and the session set (for the
			// final committed combo). Multiple non-modifiers can now be
			// accumulated — pressing Delete then End produces a
			// ``<delete>+<end>`` combo, instead of End overwriting Delete.
			heldNonModifiersRef.current.add(pynputName);
			sessionNonModifiersRef.current.add(pynputName);
			// Clear any prior error so the user sees the candidate is
			// pending (the error from a previous failed attempt would
			// otherwise stay visible during the new attempt).
			setError(null);
			unsupportedComboRef.current = null;
		},
		[snapshotModifiers, buildAttemptedComboLabel],
	);

	// ── Keyup handler ─────────────────────────────────────────────────
	//
	// HOTKEY-MULTIKEY-001: the shortcut is committed only when ALL pressed
	// keys have been released. Because we accumulate the session set (not
	// the held set), the captured combo is identical regardless of release
	// order — Ctrl→Shift release produces the same combo as Shift→Ctrl
	// release.
	//
	// HOTKEY-DEFER-001 (preserved): committing on keyUP (not keyDOWN)
	// eliminates the capture-triggers-recording race where the newly-
	// registered backend's polling loop sees the still-held key as a
	// fresh press and immediately fires the dictation callback.
	const handleKeyUp = useCallback(
		(e: KeyboardEvent) => {
			if (!recordingRef.current) return;

			// ESC-KEYUP-FIX: if ESC was pressed during this capture session,
			// exit capture mode on key-up (release). This matches how
			// regular key capture works — assignment happens on key-up.
			if (e.key === "Escape" && escPressedRef.current) {
				escPressedRef.current = false;
				resetCaptureSession();
				cancelRecording();
				return;
			}

			const modifierCode = MODIFIER_CODE_TO_PYNPUT[e.code];
			if (modifierCode) {
				// Modifier key release — remove from held set.
				heldModifiersRef.current.delete(modifierCode);
				// If no non-modifier was pressed AND all modifiers are
				// released, this is the modifier-only release path:
				// commit the modifier set as the hotkey. This is the
				// unified path for both single-key (e.g. ``<alt>``) and
				// modifier-combo (e.g. ``<ctrl>+<shift>``) hotkeys.
				if (
					sessionNonModifiersRef.current.size === 0 &&
					heldModifiersRef.current.size === 0 &&
					sessionModifiersRef.current.size > 0
				) {
					commitModifierOnlyCombo();
				}
				return;
			}

			// Non-modifier key release — remove from held set.
			const pynputName = KEY_CODE_TO_PYNPUT[e.code];
			if (pynputName) {
				heldNonModifiersRef.current.delete(pynputName);
			}

			// If an unsupported key was pressed during this session, keep
			// the error visible and DO NOT commit. The user has released
			// the unsupported key; we stay in capture mode so they can
			// try a different combo without re-clicking the Record button.
			if (unsupportedComboRef.current) {
				// Clear the unsupported flag so the next combo attempt
				// starts fresh, but keep the session sets (the user
				// might still be holding modifiers from the same attempt).
				unsupportedComboRef.current = null;
				// Don't clear sessionNonModifiersRef here — it's empty
				// anyway (the unsupported key was never added).
				return;
			}

			// HOTKEY-MULTIKEY-001: commit only when ALL non-modifier keys
			// have been released. This makes the captured combo
			// release-order independent: if the user holds Delete+End and
			// releases End first, then Delete, the combo is committed on
			// Delete release (the last held non-modifier) and includes
			// both keys from the session set.
			if (
				heldNonModifiersRef.current.size === 0 &&
				sessionNonModifiersRef.current.size > 0
			) {
				commitFullCombo();
			}
		},
		// commitModifierOnlyCombo / commitFullCombo / resetCaptureSession /
		// cancelRecording are all stable (useCallback with stable deps)
		// — including them here keeps the linter happy without re-creating
		// handleKeyUp on every render.
		[
			commitModifierOnlyCombo,
			commitFullCombo,
			resetCaptureSession,
			cancelRecording,
		],
	);

	// HOTKEY-FIX-004: "Custom" sentinel. When the current
	// hotkey is not one of the preset values, the Select would render
	// an empty trigger (Radix Select quirk: a non-empty value that
	// matches no SelectItem suppresses the placeholder). We detect
	// custom values and map them to a "__custom__" sentinel that
	// displays the actual hotkey label, so the dropdown always shows
	// something meaningful.
	//
	// The presets are now passed in from the parent via the `presets`
	// prop — no hard-coded preset logic in this component. If no
	// presets are provided, the dropdown is not rendered at all.
	const presetOptions = presets ?? [];
	const rawPresetValue = mode === "single" ? value.replace(/[<>]/g, "") : value;
	const isPresetValue = presetOptions.some(
		(opt) => opt.value === rawPresetValue,
	);
	const customLabel = value ? formatHotkeyLabel(value) : "";

	return (
		<div className="flex flex-col gap-2">
			<div className="flex items-center gap-2">
				<Button
					variant={recording ? "default" : "outline"}
					size="sm"
					onClick={recording ? cancelRecording : startRecording}
					className={cn("gap-2 font-mono", className)}
					aria-label={
						recording
							? t("hotkeyPicker.cancelRecordingAria", { label: ariaLabel })
							: t("hotkeyPicker.recordNewAria", { label: ariaLabel })
					}
				>
					<HugeiconsIcon
						icon={recording ? Cancel01Icon : KeyboardIcon}
						strokeWidth={1.625}
						className="h-4 w-4"
					/>
					{recording ? (
						<span className="animate-pulse">{t("hotkeyPicker.pressAKey")}</span>
					) : (
						<span>{formatHotkeyLabel(value) || t("hotkeyPicker.none")}</span>
					)}
				</Button>

				{presetOptions.length > 0 && (
					<DropdownMenu>
						<DropdownMenuTrigger asChild>
							<Button
								variant="outline"
								size="sm"
								className="w-40 justify-between font-mono"
								aria-label={t("hotkeyPicker.presetHotkeysAria", {
									label: ariaLabel,
								})}
							>
								<span>
									{(() => {
										if (!value) return t("hotkeyPicker.presets");
										if (isPresetValue) {
											const opt = presetOptions.find(
												(o) => o.value === rawPresetValue,
											);
											return opt?.label ?? formatHotkeyLabel(value);
										}
										return t("hotkeyPicker.customLabel", {
											label: customLabel,
										});
									})()}
								</span>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									width="16"
									height="16"
									viewBox="0 0 24 24"
									fill="none"
									stroke="currentColor"
									strokeWidth="2"
									strokeLinecap="round"
									strokeLinejoin="round"
									className="h-4 w-4 opacity-50"
									aria-hidden="true"
								>
									<path d="m6 9 6 6 6-6" />
								</svg>
							</Button>
						</DropdownMenuTrigger>
						<DropdownMenuContent className="w-40" align="start">
							{presetOptions.map((opt) => (
								<DropdownMenuItem
									key={opt.value}
									onSelect={() => {
										const newValue =
											mode === "single" ? `<${opt.value}>` : opt.value;
										// DUPLICATE-001: reject if another setting already uses this hotkey.
										// Skip the check when the hotkey hasn't actually changed (the
										// user is re-selecting the current value).
										if (
											newValue !== value &&
											occupiedHotkeys?.includes(newValue)
										) {
											const label = formatHotkeyLabel(newValue);
											setError(
												`"${label}" is already in use by another setting. Each hotkey must be unique.`,
											);
											return;
										}
										const validationError = validateHotkey(newValue, mode);
										if (validationError) {
											setError(validationError);
										} else {
											setError(null);
											onChange(newValue);
										}
									}}
								>
									{opt.label}
								</DropdownMenuItem>
							))}
							{!isPresetValue && value && (
								<DropdownMenuItem
									disabled
									className="text-(--text-muted) cursor-default"
								>
									{t("hotkeyPicker.customLabel", { label: customLabel })}
								</DropdownMenuItem>
							)}
						</DropdownMenuContent>
					</DropdownMenu>
				)}
			</div>
			{recording && (
				<output className="text-xs text-(--text-muted)">
					{t("hotkeyPicker.assignHint")}
				</output>
			)}
			{error && (
				<p className="text-xs text-destructive" role="alert">
					{error}
				</p>
			)}
		</div>
	);
}
