/**
 * Canonical location for the hotkey capture-session state machine
 * (pure reducer + action/state types) and the UI-mode-aware
 * validation wrapper.
 *
 * Extracted from the former ``hotkey-utils.ts`` monolith.
 *
 * Two concerns live here:
 *
 *  1. ``validateHotkey`` — the UI wrapper that adds ``"single"`` /
 *     ``"combo"`` mode semantics on top of the shared structural
 *     validator in ``hotkey-validation.ts`` (which handles reserved
 *     shortcuts, structural, normalization). Used by
 *     ``HotkeyPicker.tsx`` and the test suite.
 *
 *  2. The capture-session state machine — a pure ``useReducer``-style
 *     reducer (no side effects) extracted from ``useHotkeyCapture.ts``
 *     so it can be unit-tested in isolation. The hook dispatches
 *     actions; timers / DOM listeners / IPC live in the hook, NOT here.
 *
 * The reducer tracks ONLY visible UI state: status
 * (idle → capturing → committed | cancelled | error), the localized
 * error string, the 30s capture countdown, and the live
 * "Holding: …" label. The hook retains refs for genuine mutable state
 * NOT in the reducer (held/session key sets, ESC-pressed flag, timer
 * IDs, container DOM ref, unsupported-combo label).
 *
 * Depends on:
 *  - ``hotkey-validation`` — shared validation API (detectPlatform,
 *    isReserved, validateHotkey as validateHotkeyShared).
 *  - ``hotkey-keymap`` — IS_MAC and MODIFIER_KEYS (for the mode-aware
 *    Fn-on-macOS-only and combo-must-end-with-non-modifier rules).
 *  - ``hotkey-format`` — formatHotkey / formatHotkeyLabel (used to
 *    build error-message labels and the live "Holding:" label).
 *  - ``./checkHotkeyConflict`` — duplicate-across-pickers check used
 *    by ``tryCommitHotkey``.
 */

import { t } from "@/i18n/i18n";
import { checkHotkeyConflict } from "./checkHotkeyConflict";
import { formatHotkey, formatHotkeyLabel } from "./hotkey-format";
import { IS_MAC, MODIFIER_KEYS } from "./hotkey-keymap";
import {
	detectPlatform,
	validateHotkey as validateHotkeyShared,
} from "./hotkey-validation";

// Note: the shared validation primitives (``detectPlatform`` /
// ``isReserved`` / ``normalizeHotkey`` / ``RESERVED_SHORTCUTS``) are
// re-exported from ``hotkey-keymap.ts`` (the platform-detection home).
// They are NOT re-exported here to avoid an ambiguous-export conflict
// when ``hotkey-utils.ts`` (the backward-compat shim) does
// ``export *`` from both modules. Callers wanting them via the shim
// still resolve them through the keymap re-export.

/**
 * Validate a hotkey for the UI, with an additional mode parameter
 * for single-key vs. combo constraints.
 *
 * For single mode: must be exactly one key (no modifiers together).
 * For combo mode: delegates to the shared validateHotkey.
 *
 * Returns null on success, or an error message string on failure.
 */
export function validateHotkey(
	hotkey: string,
	mode: "single" | "combo",
): string | null {
	// delegate to the shared validation system.
	// The shared validateHotkey handles:
	//  - empty / no-keys check
	//  - reserved-shortcut check (OS-specific)
	//  - structural check (combo must end with non-modifier)
	//
	// We add mode-specific checks (single key constraint, Fn-on-macOS-only)
	// on top, since those are UI-mode concerns the shared validator
	// doesn't know about.
	if (!hotkey?.trim()) {
		return t("hotkeyValidation.empty");
	}

	// Delegate reserved + structural checks to the shared validator.
	const platform = detectPlatform();
	const sharedResult = validateHotkeyShared(hotkey, platform);
	if (!sharedResult.valid) {
		return sharedResult.reason ?? t("hotkeyValidation.invalidHotkey");
	}

	const parts = hotkey
		.split("+")
		.map((p) => p.replace(/[<>]/g, "").trim())
		.filter(Boolean);
	if (parts.length === 0) {
		return t("hotkeyValidation.noKeys");
	}
	// allow single modifiers (alt, ctrl, shift, fn, cmd, win)
	// as the dictation key. The native backends support modifier-only
	// release detection.
	if (mode === "single") {
		if (parts.length > 1) {
			return t("hotkeyValidation.dictationKeySingle");
		}
		// Reject Fn on non-macOS platforms
		if (!IS_MAC && (parts[0] === "fn" || parts[0] === "globe")) {
			return t("hotkey.errors.fnMacOnly");
		}
		// Accept any single key (including modifiers and caps_lock)
		return null;
	}
	// Combo mode: pure-modifier combos (e.g.
	// ``<ctrl>+<shift>``, ``<ctrl>+<alt>``) are now ALLOWED. The structural
	// "must end with non-modifier" rule only applies to MIXED combos
	// (modifiers + non-modifiers). The shared validator already enforces
	// this; the redundant check below is kept only for mixed combos as
	// a defense-in-depth guard.
	const lastKey = parts[parts.length - 1];
	const hasNonModifier = parts.some(
		(p) => !MODIFIER_KEYS.includes(p as (typeof MODIFIER_KEYS)[number]),
	);
	if (
		hasNonModifier &&
		MODIFIER_KEYS.includes(lastKey as (typeof MODIFIER_KEYS)[number])
	) {
		return t("hotkeyValidation.mustEndWithNonModifier", {
			label: formatHotkey(hotkey),
		});
	}
	// Reject Fn in combos on non-macOS
	if (!IS_MAC && parts.some((p) => p === "fn" || p === "globe")) {
		return t("hotkey.errors.fnMacOnlyShort");
	}
	return null;
}

// ────────────────────────────────────────────────────────────────────
// Hotkey capture state machine (reducer + types)
// ────────────────────────────────────────────────────────────────────
//
// Extracted from ``useHotkeyCapture.ts`` so the reducer is a pure,
// unit-testable function. The hook calls ``useReducer(hotkeyCaptureReducer,
// initialHotkeyCaptureState)`` and dispatches actions; side-effects
// (calling ``onChange``, ``onCaptureStart``/``onCaptureEnd``, clearing
// timers) live in the hook, NOT in the reducer.
//
// The reducer tracks ONLY the visible UI state:
//   - status: idle → capturing → committed | cancelled (→ error is unused
//     in practice because validation failures keep the user in capture
//     mode; kept in the union for future use)
//   - error: the localized error string shown under the picker
//   - secondsRemaining: the 30s capture countdown
//   - heldModifiersLabel: the live "Holding: …" label
//
// The hook retains refs for genuine mutable state NOT in the reducer
// (held/session key sets, ESC-pressed flag, timer IDs, container DOM
// ref, unsupported-combo label).
// ────────────────────────────────────────────────────────────────────

/** How long the picker stays in capture mode before auto-exiting. */
export const CAPTURE_TIMEOUT_SECONDS = 30;

/**
 * Canonical modifier order. Modifiers are stored in the session set in
 * insertion order, but the captured hotkey must be identical regardless
 * of press order — so we always emit modifiers in this canonical order
 * before committing.
 */
export const CANONICAL_MOD_ORDER = [
	"ctrl",
	"shift",
	"alt",
	"cmd",
	"win",
	"super",
	"fn",
] as const;

export type HotkeyCaptureStatus =
	| "idle"
	| "capturing"
	| "committed"
	| "cancelled"
	| "error";

export type HotkeyCaptureState = {
	status: HotkeyCaptureStatus;
	error: string | null;
	secondsRemaining: number;
	heldModifiersLabel: string;
};

export type HotkeyCaptureAction =
	| { type: "Start" }
	| { type: "KeyDown"; modifiers: string; nonModifiers: string }
	| { type: "KeyUp"; modifiers: string; nonModifiers: string }
	| { type: "EscPressed" }
	| { type: "EscReleased" }
	| { type: "Timeout" }
	| { type: "BackendCancel" }
	| { type: "OutsideClick" }
	| { type: "CommitAttempt"; hotkey: string }
	| { type: "CommitSuccess" }
	| { type: "CommitFailure"; error: string }
	| { type: "Tick"; secondsRemaining: number }
	//SetError is a slight extension to the  spec's action list:
	// the hook exposes ``setError`` for the preset-dropdown onSelect
	// (which can both set and clear the error), and that path doesn't
	// represent a commit failure. ``CommitFailure`` requires a non-null
	// error string, so we need a separate action that accepts ``null``.
	| { type: "SetError"; error: string | null };

export const initialHotkeyCaptureState: HotkeyCaptureState = {
	status: "idle",
	error: null,
	secondsRemaining: 0,
	heldModifiersLabel: "",
};

/**
 * Build the human-readable "Holding: …" label from a comma-joined
 * modifier list (the format the hook dispatches in ``KeyDown`` /
 * ``KeyUp`` actions). Empty/blank string → empty label.
 */
function buildHeldModifiersLabelFromAction(modifiers: string): string {
	if (!modifiers) return "";
	const mods = modifiers
		.split(",")
		.map((m) => m.trim())
		.filter(Boolean);
	if (mods.length === 0) return "";
	return formatHotkeyLabel(mods.map((m) => `<${m}>`).join("+"));
}

/**
 * Pure reducer for the hotkey capture state machine.
 *
 * Invariants:
 *   - No side effects. No DOM access, no timer manipulation, no IPC.
 *   - All "leave capturing" transitions (cancel/commit/timeout) reset
 *     ``heldModifiersLabel`` and ``secondsRemaining`` to 0 so the UI
 *     doesn't flash a stale label/timer after exit.
 *   - ``KeyDown``/``KeyUp`` are no-ops when not capturing (the hook
 *     also short-circuits via ``capturingRef``, but the reducer guards
 *     defensively in case a stale dispatch slips through).
 *   - ``CommitFailure`` / ``SetError`` only touch ``error`` — they do
 *     NOT change ``status``. The user stays in capture mode after a
 *     validation error so they can try again without re-clicking
 *     Record.
 */
export function hotkeyCaptureReducer(
	state: HotkeyCaptureState,
	action: HotkeyCaptureAction,
): HotkeyCaptureState {
	switch (action.type) {
		case "Start":
			return {
				...state,
				status: "capturing",
				error: null,
				secondsRemaining: CAPTURE_TIMEOUT_SECONDS,
				heldModifiersLabel: "",
			};
		case "KeyDown":
			if (state.status !== "capturing") return state;
			return {
				...state,
				heldModifiersLabel: buildHeldModifiersLabelFromAction(action.modifiers),
			};
		case "KeyUp":
			if (state.status !== "capturing") return state;
			return {
				...state,
				heldModifiersLabel: buildHeldModifiersLabelFromAction(action.modifiers),
			};
		case "EscPressed":
			// no visible state change here — the ESC
			// press is tracked in ``escPressedRef`` (genuine mutable
			// state) and the cancel happens on ESC release via
			// ``EscReleased``.
			return state;
		case "EscReleased":
		case "Timeout":
		case "BackendCancel":
		case "OutsideClick":
			return state.status === "capturing"
				? {
						...state,
						status: "cancelled",
						error: null,
						heldModifiersLabel: "",
						secondsRemaining: 0,
					}
				: state;
		case "CommitAttempt":
			// The caller validates the hotkey (via ``tryCommitHotkey``)
			// and dispatches ``CommitSuccess`` or ``CommitFailure``.
			// No state change here.
			return state;
		case "CommitSuccess":
			return state.status === "capturing"
				? {
						...state,
						status: "committed",
						error: null,
						heldModifiersLabel: "",
						secondsRemaining: 0,
					}
				: state;
		case "CommitFailure":
			// Validation/conflict failure: keep status as-is (user
			// stays in capture mode). Only set the error.
			return { ...state, error: action.error };
		case "SetError":
			return { ...state, error: action.error };
		case "Tick":
			return { ...state, secondsRemaining: action.secondsRemaining };
		default:
			return state;
	}
}

/**
 * : shared commit-validation helper. Consolidates the duplicated
 * validate-then-conflict-check sequence that previously lived inline in
 * ``commitModifierOnlyCombo``, ``commitFullCombo`` (useHotkeyCapture.ts)
 * and the preset-dropdown ``onSelect`` (HotkeyPicker.tsx).
 *
 * Pure: returns ``{ ok: true }`` or ``{ ok: false, error }`` without
 * touching any React state or calling any callbacks. The caller decides
 * what to do with the result.
 *
 * @param newHotkey       The hotkey string to validate (e.g. ``"<ctrl>+<shift>+v"``).
 * @param opts.mode       ``"single"`` (dictation key) or ``"combo"`` (re-paste etc.).
 * @param opts.value      The picker's current value — re-selecting the same
 *                        value is allowed (conflict check skips it).
 * @param opts.occupiedHotkeys  Hotkey strings already claimed by sibling pickers.
 * @param opts.t          The i18n ``t`` function.
 * @param opts.resetSession  Hint flag for the caller: ``true`` for capture
 *                        sessions (call ``resetCaptureSession()`` after),
 *                        ``false`` for the preset dropdown (no session to
 *                        reset). Currently unused inside this helper — the
 *                        caller uses it to decide whether to reset.
 */
export function tryCommitHotkey(
	newHotkey: string,
	opts: {
		mode: "single" | "combo";
		value: string;
		occupiedHotkeys: string[] | undefined;
		t: (key: string, params?: Record<string, string>) => string;
		resetSession: boolean;
	},
): { ok: true } | { ok: false; error: string } {
	const validationError = validateHotkey(newHotkey, opts.mode);
	if (validationError) return { ok: false, error: validationError };
	const conflict = checkHotkeyConflict(
		newHotkey,
		opts.value,
		opts.occupiedHotkeys,
		opts.t,
	);
	if (conflict) return { ok: false, error: conflict };
	return { ok: true };
}
