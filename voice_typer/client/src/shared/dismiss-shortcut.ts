/**
 * Canonical bubble-dismiss shortcut binding, shared between the
 * Tauri Rust host and the renderer.
 *
 * - `accelerator` is the system-wide accelerator string the Rust host
 *   registers via the Tauri global-shortcut plugin
 *   (`src-tauri/src/shortcuts.rs`), so the shortcut works even
 *   without app focus.
 * - `display` is the renderer's keycap-chip string ("Ctrl+Shift+D")
 *   consumed by the hotkey catalog entry `SHORTCUTS.dismissBubble`.
 *   It holds the RAW key names only; rendering stays on the shared
 *   HotkeyChips path (`components/hotkey/HotkeyChips.tsx`).
 *
 * The two forms describe the SAME binding: `CommandOrControl` maps to
 * Ctrl on Windows/Linux and ⌘ on macOS. Change them together or not
 * at all.
 */
export const DISMISS_SHORTCUT = {
	/** System accelerator form (Tauri global-shortcut plugin). */
	accelerator: "CommandOrControl+Shift+D",
	/** Renderer display form (keycap-chip string, HotkeyChips path). */
	display: "Ctrl+Shift+D",
} as const;
