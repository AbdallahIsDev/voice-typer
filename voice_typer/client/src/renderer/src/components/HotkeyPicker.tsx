import { Cancel01Icon, KeyboardIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
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
	// HOTKEY-FIX-005: flag set when a non-modifier key is pressed during
	// the current capture session.  Prevents ``handleKeyUp``'s modifier-only
	// release detection from partially assigning a modifier when the full
	// combination (e.g. Shift+Z) was rejected as invalid.
	const nonModifierSeenRef = useRef(false);

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
	const handleKeyDown = useCallback(
		(e: KeyboardEvent) => {
			if (!recordingRef.current) return;

			if (e.key === "Escape") {
				// ARCH-ESC-001: cancel hotkey capture on Escape. We call
				// preventDefault() and stopPropagation() so no other
				// listener (e.g. the App.tsx help-overlay handler, or
				// any future document-level Escape handler) can
				// interfere with the capture-mode cancel. The backend's
				// ESC cancel hotkey is handled separately via the
				// KeyboardOwnership singleton (see system_handlers.py).
				e.preventDefault();
				e.stopPropagation();
				// NB: sync the ref immediately so a second keydown arriving
				// before React's re-render sees the correct state.
				recordingRef.current = false;
				setRecording(false);
				setError(null);
				modifierHeldRef.current.clear();
				// ESC-FIX-001 / 002: read from ref so we always call the
				// latest onCaptureEnd without depending on it in deps.
				onCaptureEndRef.current?.();
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
			if (mode === "single") {
				if (isModifier) return;
				const pynputName = KEY_CODE_TO_PYNPUT[e.key];
				if (!pynputName) {
					setError(`Key "${e.key}" is not supported as a hotkey.`);
					return;
				}
				const newHotkey = `<${pynputName}>`;
				const validationError = validateHotkey(newHotkey, mode);
				if (validationError) {
					setError(validationError);
					return;
				}
				onChange(newHotkey);
				setError(null);
				setRecording(false);
				modifierHeldRef.current.clear();
				onCaptureEndRef.current?.();
				return;
			}

			if (isModifier) return;

			const mods: string[] = [];
			if (e.ctrlKey) mods.push("ctrl");
			if (e.shiftKey) mods.push("shift");
			if (e.altKey) mods.push("alt");
			if (e.metaKey) mods.push(IS_MAC ? "cmd" : "win");

			const pynputName = KEY_CODE_TO_PYNPUT[e.key];
			if (!pynputName) {
				setError(
					`Key "${e.key}" is not supported. Try letters, numbers, F-keys, or Space.`,
				);
				return;
			}

			const parts = [...mods, pynputName];
			const newHotkey = parts.map((p) => `<${p}>`).join("+");
			const validationError = validateHotkey(newHotkey, mode);
			if (validationError) {
				setError(validationError);
				return;
			}
			onChange(newHotkey);
			setError(null);
			setRecording(false);
			modifierHeldRef.current.clear();
			onCaptureEndRef.current?.();
		},
		[mode, onChange], // ESC-FIX-002: onCaptureEnd removed from deps → read from ref
	);

	// Detect modifier-only release (single mode): if the user pressed a
	// modifier and released it without pressing any other key, treat that
	// as the chosen hotkey. This enables <alt>, <ctrl>, <shift>, <cmd>/<win>
	// as single-key triggers.
	const handleKeyUp = useCallback(
		(e: KeyboardEvent) => {
			if (!recordingRef.current) return;
			if (mode !== "single") return;
			const modifierCode = MODIFIER_CODE_TO_PYNPUT[e.code];
			if (!modifierCode) return;

			modifierHeldRef.current.delete(modifierCode);
			// If this was the last held modifier and no non-modifier key was
			// pressed during this capture session, treat the modifier alone
			// as the hotkey.  The ``nonModifierSeenRef`` guard prevents
			// partial assignment when a combination (Shift+Z) was rejected
			// but the modifier release still fires (e.g. Shift keyup).
			if (modifierHeldRef.current.size === 0 && !nonModifierSeenRef.current) {
				const newHotkey = `<${modifierCode}>`;
				const validationError = validateHotkey(newHotkey, mode);
				if (validationError) {
					setError(validationError);
					return;
				}
				onChange(newHotkey);
				setError(null);
				setRecording(false);
				onCaptureEndRef.current?.();
			}
		},
		[mode, onChange], // ESC-FIX-002: onCaptureEnd removed from deps → read from ref
	);

	const startRecording = useCallback(() => {
		setRecording(true);
		// Sync the ref immediately alongside the state setter so the
		// keydown handler sees recording=true even before React's
		// re-render + effect cycle completes.
		recordingRef.current = true;
		setError(null);
		modifierHeldRef.current.clear();
		// HOTKEY-FIX-005: fresh capture session — reset the flag so
		// modifier-only release detection works on the next attempt.
		nonModifierSeenRef.current = false;
		// ESC-FIX-001/002: read from ref so we always call the latest
		// onCaptureStart without depending on it in deps.
		onCaptureStartRef.current?.();
		// HOTKEY-FIX-003: No capture timeout — stay in capture mode
		// indefinitely. Exit only when: user clicks outside, clicks
		// the capture button again, or presses Esc.
	}, []); // ESC-FIX-002: empty deps — onCaptureStart read from ref

	const cancelRecording = useCallback(() => {
		setRecording(false);
		recordingRef.current = false;
		setError(null);
		modifierHeldRef.current.clear();
		nonModifierSeenRef.current = false;
		if (timeoutRef.current) clearTimeout(timeoutRef.current);
		// ESC-FIX-001/002: read from ref so we always call the latest
		// onCaptureEnd without depending on it in deps.
		onCaptureEndRef.current?.();
	}, []); // ESC-FIX-002: empty deps — onCaptureEnd read from ref

	const presets = mode === "single" ? SINGLE_KEY_PRESETS : COMBO_PRESETS;
	const presetValue = mode === "single" ? value.replace(/[<>]/g, "") : value;

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

				<Select
					value={presetValue}
					onValueChange={(v) => {
						const newValue = mode === "single" ? `<${v}>` : v;
						const validationError = validateHotkey(newValue, mode);
						if (validationError) {
							setError(validationError);
						} else {
							setError(null);
							onChange(newValue);
						}
					}}
				>
					<SelectTrigger
						className="w-40"
						aria-label={`Preset hotkeys \u2014 ${ariaLabel}`}
					>
						<SelectValue placeholder="Presets\u2026" />
					</SelectTrigger>
					<SelectContent>
						{presets.map((opt) => (
							<SelectItem key={opt.value} value={opt.value}>
								{opt.label}
							</SelectItem>
						))}
					</SelectContent>
				</Select>
			</div>
			{error && (
				<p className="text-xs text-destructive" role="alert">
					{error}
				</p>
			)}
		</div>
	);
}
