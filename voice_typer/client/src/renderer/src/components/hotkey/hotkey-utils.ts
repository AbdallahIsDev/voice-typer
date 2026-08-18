/**
 * Backward-compat re-export shim. Canonical locations:
 *  - hotkey-keymap.ts       — key codes, modifier list, platform
 *                            detection (IS_MAC/IS_WIN/IS_LINUX),
 *                            preset lists, and re-exports of the
 *                            shared validation primitives
 *                            (detectPlatform/isReserved/normalizeHotkey/
 *                             RESERVED_SHORTCUTS).
 *  - hotkey-format.ts       — display formatting (formatHotkey /
 *                            formatHotkeyLabel / formatHotkeyForPlatform)
 *                            + config-default constants
 *                            (HOTKEY_DEFAULT/REPASTE_HOTKEY_DEFAULT) +
 *                            the config→label helper configHotkeyLabels.
 *  - hotkey-capture-state.ts — UI-mode-aware validateHotkey wrapper +
 *                            the capture-session state machine
 *                            (hotkeyCaptureReducer + types + tryCommitHotkey).
 *
 * New code should import from the canonical modules directly. This
 * shim preserves the public API of the former 859-LOC monolith so
 * existing callers (HotkeyPicker, useHotkeyCapture, HotkeyChips,
 * TitleBar, Sidebar, App, RecordingSettingsSection,
 * DiagnosticsSettingsSection, onboarding/lib/constants, and the test
 * suite) keep resolving unchanged. The shim will be removed once all
 * callers are migrated.
 *
 * No name collisions exist across the three modules' exports —
 * the shared validation primitives are re-exported from
 * hotkey-keymap.ts only (see comment in hotkey-capture-state.ts).
 */

export * from "./hotkey-capture-state";
export * from "./hotkey-format";
export * from "./hotkey-keymap";
