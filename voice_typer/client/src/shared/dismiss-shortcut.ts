/**
 * Canonical bubble-dismiss shortcut binding — shared between the
 * Electron main process and the renderer.
 *
 * Extracted from `main/shortcuts/global-shortcuts.ts` (accelerator
 * form) and `renderer/src/components/hotkey/shortcuts.ts` (display
 * form) to eliminate the cross-process duplicate that was previously
 * kept in sync only by comments pointing at each other. Both
 * `tsconfig.web.json` and `tsconfig.node.json` recursively include
 * everything under `src/shared` (their `include` arrays carry the
 * `src/shared` glob), so a single import resolves in either scope —
 * same convention as `python-call-error-code.ts`.
 *
 * - `accelerator` is the Electron accelerator string the main process
 *   registers via `globalShortcut` (`shortcuts/global-shortcuts.ts`)
 *   so the shortcut works system-wide, even without app focus.
 * - `display` is the renderer's keycap-chip string ("Ctrl+Shift+D")
 *   consumed by the hotkey catalog entry `SHORTCUTS.dismissBubble`.
 *   It holds the RAW key names only — rendering stays on the shared
 *   HotkeyChips path (`components/hotkey/HotkeyChips.tsx`), which
 *   splits the string into separate keycap chips and derives the
 *   macOS glyph form ("⌃⇧D") at render time.
 *
 * The two forms describe the SAME binding: `CommandOrControl` maps to
 * Ctrl on Windows/Linux and ⌘ on macOS. Change them together or not
 * at all — the main-side pin (`main/__tests__/global-shortcuts.test.ts`),
 * the renderer-side pin (`components/hotkey/__tests__/shortcuts.test.ts`),
 * and the cross-process contract (`main/__tests__/dismiss-shortcut-import.test.ts`)
 * all assert the two processes read this one object.
 */
export const DISMISS_SHORTCUT = {
	/** Electron accelerator form (main-process `globalShortcut`). */
	accelerator: "CommandOrControl+Shift+D",
	/** Renderer display form (keycap-chip string, HotkeyChips path). */
	display: "Ctrl+Shift+D",
} as const;
