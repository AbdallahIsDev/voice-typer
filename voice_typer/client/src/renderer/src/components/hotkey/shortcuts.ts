/**
 * Single source of truth for the app's keyboard-shortcut *strings*.
 *
 * Every user-facing shortcut display must reference this catalog —
 * never a hand-typed literal:
 *
 *   - TitleBar tooltips / `aria-keyshortcuts` (toggle sidebar, back,
 *     forward, help `?`)
 *   - Sidebar nav-item tooltips + `aria-keyshortcuts` (Home, Settings)
 *   - Help overlay static rows (cancel, navigate, toggle, activate,
 *     open-help, nav-back/forward) and its Esc close hint
 *   - The About page's hotkey row (config-driven, but rendered through
 *     `formatHotkey` from `hotkey-utils.ts` — the same canonical
 *     formatting path)
 *
 * The hook implementations live in `useGlobalKeyboardShortcuts` /
 * `useNavigation`; this module only pins the strings so display sites
 * can't drift from each other or from the bindings the hooks implement.
 *
 * `keys` is the canonical cross-platform display string (e.g. "Ctrl+B")
 * — the literal text `HotkeyChips` renders as `Kbd` chips on
 * Windows/Linux; on macOS the modifier labels render as native glyphs
 * automatically ("Ctrl+B" → "⌃B", via `formatHotkeyForPlatform`, the
 * same treatment `formatHotkey` applies in the Sidebar). `pynput` is
 * the equivalent combo in pynput form ("<ctrl>+<b>") for
 * platform-aware rendering via `formatHotkey` (macOS glyphs).
 * `ariaKeyshortcuts` is the ARIA spec form ("Control+B") used on
 * controls that expose the binding to assistive tech.
 *
 * Keep `keys`/`pynput` in lockstep: `formatHotkey(pynput)` must equal
 * `keys` on Windows/Linux — `components/hotkey/__tests__/shortcuts.test.ts`
 * enforces that contract.
 */

/** The five in-app shortcuts the keyboard hook implements (help overlay rows). */
export interface InAppShortcut {
	/** User-facing key combination string (e.g. "Ctrl+B"). */
	keys: string;
	/** i18n key whose value localises the shortcut's description. */
	labelKey: string;
	/** Grouping for the overlay (e.g. "navigation" vs "view"). */
	category: string;
}

export interface ShortcutDef extends InAppShortcut {
	/** ARIA `aria-keyshortcuts` spec form (e.g. "Control+B"), when a control exposes it. */
	ariaKeyshortcuts?: string;
	/** pynput combo form for platform-aware rendering via formatHotkey (e.g. "<ctrl>+<b>"). */
	pynput?: string;
	/**
	 * The actual `KeyboardEvent.key` values the binding reacts to in
	 * `useGlobalKeyboardShortcuts` (e.g. "b" for Ctrl+B). Only the five
	 * in-app bindings carry this — the dictation bindings (Esc, Tab,
	 * Space, Enter) are handled server-side by the backend's hotkey
	 * engine, not by the renderer hook. Keep these in lockstep with
	 * `keys`/`pynput`: `IN_APP_BINDINGS` (below) derives the hook's
	 * dispatch table from them, so a key rename in the catalog is the
	 * ONLY place that needs to change.
	 */
	eventKeys?: readonly string[];
	/**
	 * Modifier profile the binding requires. "ctrlCmd" = Ctrl OR Cmd
	 * pressed, never Shift/Alt — the guard `useGlobalKeyboardShortcuts`
	 * applies to the navigation/zoom bindings. "ctrlShiftCmd" = Ctrl OR
	 * Cmd AND Shift, never Alt — the guard the toggleDictation binding
	 * requires (Shift is part of the combo, and e.key arrives
	 * uppercased, hence the "M" eventKey entry).
	 */
	modifier?: "ctrlCmd" | "ctrlShiftCmd";
	/**
	 * Which engine handles the binding. "renderer" = the keydown
	 * listener in `useGlobalKeyboardShortcuts` (or another renderer
	 * handler, e.g. useNavigation's Alt+arrows / App's "?"); "server"
	 * = the backend hotkey engine (pynput), e.g. the dictation keys
	 * Esc / Tab / Space / Enter; "main" = an Electron main-process
	 * OS-global accelerator (`globalShortcut`, works without app
	 * focus). Server/main-handled entries must NOT carry
	 * `eventKeys` — the renderer never dispatches them, and the
	 * catalog contract test enforces that split.
	 */
	handledBy?: "renderer" | "server" | "main";
}
export const SHORTCUTS = {
	toggleSidebar: {
		keys: "Ctrl+B",
		ariaKeyshortcuts: "Control+B",
		pynput: "<ctrl>+<b>",
		eventKeys: ["b"],
		modifier: "ctrlCmd",
		handledBy: "renderer",
		labelKey: "help.shortcuts.toggleSidebar",
		category: "navigation",
	},
	openSettings: {
		keys: "Ctrl+,",
		ariaKeyshortcuts: "Control+,",
		pynput: "<ctrl>+<,>",
		eventKeys: [","],
		modifier: "ctrlCmd",
		handledBy: "renderer",
		labelKey: "help.shortcuts.settings",
		category: "navigation",
	},
	goHome: {
		keys: "Ctrl+H",
		ariaKeyshortcuts: "Control+h",
		pynput: "<ctrl>+<h>",
		eventKeys: ["h"],
		modifier: "ctrlCmd",
		handledBy: "renderer",
		labelKey: "help.shortcuts.home",
		category: "navigation",
	},
	zoomIn: {
		keys: "Ctrl+=",
		eventKeys: ["=", "+"],
		modifier: "ctrlCmd",
		handledBy: "renderer",
		labelKey: "help.shortcuts.textSizeUp",
		category: "view",
	},
	zoomOut: {
		keys: "Ctrl+-",
		eventKeys: ["-"],
		modifier: "ctrlCmd",
		handledBy: "renderer",
		labelKey: "help.shortcuts.textSizeDown",
		category: "view",
	},
	navBack: {
		keys: "Alt+←",
		labelKey: "help.navBack",
		category: "navigation",
	},
	navForward: {
		keys: "Alt+→",
		labelKey: "help.navBack",
		category: "navigation",
	},
	openHelp: {
		keys: "?",
		ariaKeyshortcuts: "?",
		labelKey: "help.openHelp",
		category: "navigation",
	},
	cancel: {
		keys: "Esc",
		// Backend hotkey engine (pynput) — NOT the renderer keydown
		// listener, so no eventKeys (the catalog contract test enforces
		// that server-handled entries never claim renderer dispatch).
		handledBy: "server",
		labelKey: "help.cancel",
		category: "dictation",
	},
	navigate: {
		keys: "Tab / Shift+Tab",
		handledBy: "server",
		labelKey: "help.navigate",
		category: "dictation",
	},
	toggle: {
		keys: "Space",
		handledBy: "server",
		labelKey: "help.toggle",
		category: "dictation",
	},
	activate: {
		keys: "Enter",
		handledBy: "server",
		labelKey: "help.activate",
		category: "dictation",
	},
	toggleDictation: {
		// Renderer keydown binding (Ctrl OR Cmd + Shift + M): toggles
		// dictation through the same `toggle_dictation` IPC the Home
		// page's mic button uses. NOT an OS-global accelerator and NOT
		// a pynput reserved-key candidate — the backend's reserved list
		// only gates the user-configurable global dictation hotkey
		// (pure Ctrl+<letter> blocks and OS combos), so a renderer-level
		// Ctrl+Shift+M doesn't conflict with it (Ctrl+Shift is outside
		// the pure-Ctrl profile). `eventKeys` + the `ctrlShiftCmd`
		// modifier profile make the binding catalog-driven like the
		// other in-app shortcuts.
		keys: "Ctrl+Shift+M",
		ariaKeyshortcuts: "Control+Shift+M",
		pynput: "<ctrl>+<shift>+<m>",
		eventKeys: ["m", "M"],
		modifier: "ctrlShiftCmd",
		handledBy: "renderer",
		labelKey: "help.shortcuts.toggleDictation",
		category: "dictation",
	},
	dismissBubble: {
		// OS-global accelerator registered in the Electron main process
		// (`shortcuts/global-shortcuts.ts`, accelerator form
		// "CommandOrControl+Shift+D"). Displayed as plain keycap chips —
		// no eventKeys: the renderer never dispatches this binding, the
		// main process owns it.
		keys: "Ctrl+Shift+D",
		handledBy: "main",
		labelKey: "help.shortcuts.dismissBubble",
		category: "dictation",
	},
} as const satisfies Record<string, ShortcutDef>;

export type ShortcutId = keyof typeof SHORTCUTS;

/**
 * The six in-app bindings `useGlobalKeyboardShortcuts` actually
 * handles, in display order for the Help overlay — sourced from the
 * catalog so the overlay can never drift from the tooltips.
 */
export const IN_APP_SHORTCUT_IDS = [
	"toggleSidebar",
	"openSettings",
	"goHome",
	"zoomIn",
	"zoomOut",
	"toggleDictation",
] as const satisfies readonly ShortcutId[];

export type InAppShortcutId = (typeof IN_APP_SHORTCUT_IDS)[number];

/**
 * The in-app shortcuts in display order for the Help overlay — the
 * five bindings `useGlobalKeyboardShortcuts` actually handles, sourced
 * from the catalog so the overlay can never drift from the tooltips.
 */
export const IN_APP_SHORTCUTS: InAppShortcut[] = IN_APP_SHORTCUT_IDS.map(
	(id) => SHORTCUTS[id],
);

/**
 * One entry per in-app binding, carrying the ACTUAL keyboard event
 * descriptor (`eventKeys` + `modifier`) the hook dispatches on — the
 * display strings (`keys`) and the bindings themselves now share one
 * catalog, so they can't drift.
 */ export interface InAppBinding {
	id: InAppShortcutId;
	/** Display string (e.g. "Ctrl+B") for tooltips/docs. */
	keys: string;
	/** `KeyboardEvent.key` values that trigger this binding. */
	eventKeys: readonly string[];
	/** Modifier profile ("ctrlCmd" = Ctrl OR Cmd, no Shift/Alt).
	 *  Derived from ShortcutDef so a NEW profile in the catalog
	 *  automatically widens this union — which forces the hook's
	 *  MODIFIER_GUARDS map to gain an entry (type error otherwise). */
	modifier: NonNullable<ShortcutDef["modifier"]>;
}

/**
 * The dispatch table `useGlobalKeyboardShortcuts` iterates — derived
 * from `SHORTCUTS` so the bindings themselves can't drift from the
 * catalog. Throws at import time if a catalog edit drops the
 * `eventKeys`/`modifier` descriptors, so the failure is loud and
 * immediate instead of a silently-orphaned binding.
 */
export const IN_APP_BINDINGS: readonly InAppBinding[] = IN_APP_SHORTCUT_IDS.map(
	(id) => {
		const def = SHORTCUTS[id];
		if (!def.eventKeys?.length || !def.modifier) {
			throw new Error(
				`[hotkey] IN_APP_BINDINGS: "${id}" is missing eventKeys/modifier — ` +
					"every in-app catalog entry must declare its keyboard descriptor " +
					"so useGlobalKeyboardShortcuts can't drift from the catalog.",
			);
		}
		return {
			id,
			keys: def.keys,
			eventKeys: def.eventKeys,
			modifier: def.modifier,
		};
	},
);
