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
import { cn } from "@/lib/utils";
import {
	COMBO_PRESETS,
	formatHotkeyLabel,
	IS_MAC,
	KEY_CODE_TO_PYNPUT,
	MODIFIER_CODE_TO_PYNPUT,
	SINGLE_KEY_PRESETS,
	validateHotkey,
} from "./hotkey-utils";

interface HotkeyPickerProps {
	value: string;
	onChange: (hotkey: string) => void;
	mode: "single" | "combo";
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
}

/**
 * NATIVE-001: updated to support modifier-only hotkeys (Alt, Ctrl, Shift,
 * Win/Cmd, Fn) as single-key triggers. The native backends support
 * modifier-only release detection, which pynput did not.
 *
 * For the FN key on macOS: the browser doesn't fire keydown events for
 * Fn (it's a modifier the OS intercepts). Users on macOS who want Fn
 * must select it from the dropdown — capture won't work.
 */
export function HotkeyPicker({
	value,
	onChange,
	mode,
	className,
	"aria-label": ariaLabel = "Hotkey picker",
	onCaptureStart,
	onCaptureEnd,
}: HotkeyPickerProps) {
	const [recording, setRecording] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	const modifierHeldRef = useRef<Set<string>>(new Set());
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
	// HOTKEY-FIX-005: flag set when a non-modifier key is pressed during
	// the current capture session.  Prevents ``handleKeyUp``'s modifier-only
	// release detection from partially assigning a modifier when the full
	// combination (e.g. Shift+Z) was rejected as invalid.
	const nonModifierSeenRef = useRef(false);
	// HOTKEY-DEFER-001 (Task 2.3/2.4): candidate hotkey captured on
	// keyDOWN but NOT yet committed. The assignment is deferred to
	// keyUP of the main key so the key is no longer physically held
	// when the IPC set_config reaches the backend — this eliminates
	// the race where the newly-registered backend sees the still-held
	// key as a fresh press and immediately triggers recording.
	// The candidate is { mods: Set<string>, mainKey: string } | null.
	// null means no non-modifier key has been pressed yet in this
	// capture session (modifier-only release detection handles that
	// case separately in handleKeyUp).
	const candidateRef = useRef<{ mods: Set<string>; mainKey: string } | null>(
		null,
	);

	useEffect(() => {
		recordingRef.current = recording;
	}, [recording]);

	// Track the latest callbacks into refs after every render so the
	// always-attached listener (``useEffect([], ..)``) never goes stale.
	// eslint-disable-next-line react-hooks/exhaustive-deps
	useEffect(() => {
		onCaptureEndRef.current = onCaptureEnd;
		onCaptureStartRef.current = onCaptureStart;
		handleKeyDownRef.current = handleKeyDown;
		handleKeyUpRef.current = handleKeyUp;
		cancelRecordingRef.current = cancelRecording;
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
	// biome-ignore lint/correctness/useExhaustiveDependencies: mode and onChange ARE used inside the callback (mode is checked, onChange is called). Biome's heuristic incorrectly flags them as unnecessary. ESC-FIX-002: onCaptureEnd removed from deps → read from ref.
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

			// Track held modifiers so we can detect "modifier-only" presses
			// (user presses Alt alone and releases it without any other key).
			const modifierCode = MODIFIER_CODE_TO_PYNPUT[e.code];
			if (modifierCode) {
				modifierHeldRef.current.add(modifierCode);
				return;
			}

			// HOTKEY-FIX-005: a non-modifier key was pressed — mark the flag
			// so ``handleKeyUp``'s modifier-only release detection skips.
			// This prevents partial modifier assignment when the combination
			// is later rejected (e.g. Shift+Z where Z is unsupported).
			nonModifierSeenRef.current = true;

			const isModifier = e.code in MODIFIER_CODE_TO_PYNPUT;
			if (isModifier) return;

			// HOTKEY-FIX-002: use e.code (layout-independent) instead
			// of e.key (layout-dependent) so the lookup works on
			// AZERTY, Dvorak, etc. The KEY_CODE_TO_PYNPUT table now
			// includes letters and digits (was missing in Round 0).
			const pynputName = KEY_CODE_TO_PYNPUT[e.code];

			// Build the held-modifiers list for BOTH single and combo
			// modes. In single mode the dictation key ignores modifiers
			// (a single key only), but we still need them for the error
			// message so the user sees "Shift+Z is not supported"
			// instead of just "Key 'Z' is not supported".
			const mods: string[] = [];
			if (e.ctrlKey) mods.push("ctrl");
			if (e.shiftKey) mods.push("shift");
			if (e.altKey) mods.push("alt");
			if (e.metaKey) mods.push(IS_MAC ? "cmd" : "win");

			if (!pynputName) {
				// HOTKEY-FIX-003 (Round 1): include held modifiers in
				// the error message so the user sees the complete
				// attempted shortcut, not just the final key. Previously
				// pressing Shift+Z showed "Key 'Z' is not supported" —
				// dropping the Shift modifier entirely. Now it shows
				// "Shift+Z is not supported" (or the full combo).
				// This now applies to BOTH single and combo modes
				// (Task 2.2 — previously single mode was missed).
				const attemptedCombo =
					mods.length > 0 ? `${mods.join("+")}+${e.key}` : e.key;
				setError(
					`"${attemptedCombo}" is not supported. Try letters, numbers, F-keys, or Space.`,
				);
				return;
			}

			// HOTKEY-DEFER-001 (Task 2.3/2.4): capture the candidate but
			// DON'T commit yet. The assignment is deferred to keyUP of
			// this main key so the key is no longer physically held when
			// the IPC set_config reaches the backend. This eliminates the
			// race where the newly-registered backend's polling loop sees
			// the still-held key as a fresh press and immediately fires
			// the dictation callback.
			//
			// In single mode, mods are ignored at commit time (the
			// dictation key is a single key only), but we store them so
			// the error message at keyUP can still show the full combo
			// if validation fails.
			candidateRef.current = {
				mods: new Set(mods),
				mainKey: pynputName,
			};
			// Clear any prior error so the user sees the candidate is
			// pending (the error from a previous failed attempt would
			// otherwise stay visible during the new attempt).
			setError(null);
		},
		[mode, onChange],
	);

	// HOTKEY-DEFER-001 (Task 2.3/2.4): commit the candidate on keyUP of the
	// main key. This is the unified assignment path for ALL non-modifier
	// keys (Tab, Caps Lock, Delete, Insert, Home, End, Page Up/Down, F-keys,
	// letters, digits, etc.). Previously only modifiers (Ctrl/Shift/Alt/Cmd)
	// used the keyUP assignment path; every other key was assigned on
	// keyDOWN while still held, causing the capture-triggers-recording race.
	//
	// Modifier-only release detection (for <alt>, <ctrl>, <shift>, <cmd>/<win>
	// as single-key triggers) is preserved below — it fires when the last
	// held modifier is released and no non-modifier key was pressed.
	const handleKeyUp = useCallback(
		(e: KeyboardEvent) => {
			if (!recordingRef.current) return;

			// ESC-KEYUP-FIX: if ESC was pressed during this capture session,
			// exit capture mode on key-up (release). This matches how
			// regular key capture works — assignment happens on key-up.
			if (e.key === "Escape" && escPressedRef.current) {
				escPressedRef.current = false;
				recordingRef.current = false;
				setRecording(false);
				setError(null);
				modifierHeldRef.current.clear();
				nonModifierSeenRef.current = false;
				candidateRef.current = null;
				onCaptureEndRef.current?.();
				return;
			}

			const modifierCode = MODIFIER_CODE_TO_PYNPUT[e.code];
			if (modifierCode) {
				// Modifier key release — update held set.
				modifierHeldRef.current.delete(modifierCode);
				// Modifier-only release detection (single mode only):
				// if the user pressed a modifier and released it without
				// pressing any other key, treat that modifier alone as
				// the chosen hotkey. The nonModifierSeenRef guard
				// prevents partial assignment when a combination
				// (Shift+Z) was rejected but the modifier release still
				// fires.
				if (
					mode === "single" &&
					modifierHeldRef.current.size === 0 &&
					!nonModifierSeenRef.current &&
					!candidateRef.current
				) {
					const newHotkey = `<${modifierCode}>`;
					const validationError = validateHotkey(newHotkey, mode);
					if (validationError) {
						setError(validationError);
						return;
					}
					onChange(newHotkey);
					setError(null);
					recordingRef.current = false;
					setRecording(false);
					candidateRef.current = null;
					onCaptureEndRef.current?.();
				}
				return;
			}

			// Non-modifier key release — commit the candidate if one is
			// pending. The candidate was captured on keyDOWN; this keyUP
			// is the signal that the user has released the main key, so
			// it's now safe to commit (the key is no longer physically
			// held, eliminating the capture-triggers-recording race).
			const candidate = candidateRef.current;
			if (!candidate) return;
			// Only commit on the keyUP of the main key that was pressed.
			// Ignore keyUP of other non-modifier keys (e.g. if the user
			// presses Tab then accidentally hits Space, the Space keyUP
			// shouldn't commit the Tab candidate).
			const pynputName = KEY_CODE_TO_PYNPUT[e.code];
			if (pynputName !== candidate.mainKey) return;

			// Build the hotkey string. In single mode, mods are ignored
			// (dictation key is a single key only). In combo mode, mods
			// are part of the hotkey.
			let newHotkey: string;
			if (mode === "single") {
				newHotkey = `<${candidate.mainKey}>`;
			} else {
				const parts = [...candidate.mods, candidate.mainKey];
				newHotkey = parts.map((p) => `<${p}>`).join("+");
			}

			const validationError = validateHotkey(newHotkey, mode);
			if (validationError) {
				setError(validationError);
				candidateRef.current = null;
				return;
			}
			onChange(newHotkey);
			setError(null);
			recordingRef.current = false;
			setRecording(false);
			modifierHeldRef.current.clear();
			candidateRef.current = null;
			onCaptureEndRef.current?.();
		},
		[mode, onChange],
	);

	const startRecording = useCallback(() => {
		setRecording(true);
		// Sync the ref immediately alongside the state setter so the
		// keydown handler sees recording=true even before React's
		// re-render + effect cycle completes.
		recordingRef.current = true;
		setError(null);
		modifierHeldRef.current.clear();
		escPressedRef.current = false;
		// HOTKEY-FIX-005: fresh capture session — reset the flag so
		// modifier-only release detection works on the next attempt.
		nonModifierSeenRef.current = false;
		// HOTKEY-DEFER-001: clear any stale candidate from a previous
		// capture session.
		candidateRef.current = null;
		// ESC-FIX-001/002: read from ref so we always call the latest
		// onCaptureStart without depending on it in deps.
		onCaptureStartRef.current?.();
		// HOTKEY-FIX-003: No capture timeout — stay in capture mode
		// indefinitely. Exit only when: user clicks outside, clicks
		// the capture button again, or presses Esc.
	}, []); // ESC-FIX-002: empty deps — onCaptureStart read from ref

	const cancelRecording = useCallback(() => {
		// ESC-KEYUP-FIX: guard against duplicate onCaptureEnd calls.
		// If the backend pushes a hotkey_capture_cancel event while
		// the frontend has already exited capture via key-up handler,
		// recordingRef.current is already false — skip the redundant
		// IPC call that would log a duplicate "ESC cancel RESUMED".
		if (!recordingRef.current) return;
		setRecording(false);
		recordingRef.current = false;
		setError(null);
		modifierHeldRef.current.clear();
		escPressedRef.current = false;
		nonModifierSeenRef.current = false;
		// HOTKEY-DEFER-001: clear any pending candidate so a stale
		// keyUP after cancel doesn't commit a hotkey.
		candidateRef.current = null;
		if (timeoutRef.current) clearTimeout(timeoutRef.current);
		// ESC-FIX-001/002: read from ref so we always call the latest
		// onCaptureEnd without depending on it in deps.
		onCaptureEndRef.current?.();
	}, []); // ESC-FIX-002: empty deps — onCaptureEnd read from ref

	const presets = mode === "single" ? SINGLE_KEY_PRESETS : COMBO_PRESETS;
	// HOTKEY-FIX-004 (Round 1): "Custom" sentinel. When the current
	// hotkey is not one of the preset values, the Select would render
	// an empty trigger (Radix Select quirk: a non-empty value that
	// matches no SelectItem suppresses the placeholder). We detect
	// custom values and map them to a "__custom__" sentinel that
	// displays the actual hotkey label, so the dropdown always shows
	// something meaningful.
	const rawPresetValue = mode === "single" ? value.replace(/[<>]/g, "") : value;
	const isPresetValue = presets.some((opt) => opt.value === rawPresetValue);
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
							? `Cancel recording \u2014 ${ariaLabel}`
							: `Record new hotkey \u2014 ${ariaLabel}`
					}
				>
					<HugeiconsIcon
						icon={recording ? Cancel01Icon : KeyboardIcon}
						strokeWidth={1.625}
						className="h-4 w-4"
					/>
					{recording ? (
						<span className="animate-pulse">Press a key</span>
					) : (
						<span>{formatHotkeyLabel(value) || "None"}</span>
					)}
				</Button>

				<DropdownMenu>
					<DropdownMenuTrigger asChild>
						<Button
							variant="outline"
							size="sm"
							className="w-40 justify-between font-mono"
							aria-label={`Preset hotkeys \u2014 ${ariaLabel}`}
						>
							<span>
								{(() => {
									if (!value) return "Presets\u2026";
									if (isPresetValue) {
										const opt = presets.find((o) => o.value === rawPresetValue);
										return opt?.label ?? formatHotkeyLabel(value);
									}
									return `Custom: ${customLabel}`;
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
						{presets.map((opt) => (
							<DropdownMenuItem
								key={opt.value}
								onSelect={() => {
									const newValue =
										mode === "single" ? `<${opt.value}>` : opt.value;
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
								Custom: {customLabel}
							</DropdownMenuItem>
						)}
					</DropdownMenuContent>
				</DropdownMenu>
			</div>
			{recording && (
				<p className="text-xs text-(--text-muted)" role="status">
					Press a key to assign, or press Esc to cancel
				</p>
			)}
			{error && (
				<p className="text-xs text-destructive" role="alert">
					{error}
				</p>
			)}
		</div>
	);
}
