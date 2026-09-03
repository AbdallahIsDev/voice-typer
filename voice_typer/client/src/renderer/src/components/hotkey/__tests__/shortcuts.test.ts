/**
 * Contract tests for the SHORTCUTS catalog — the single source of
 * truth for the app's keyboard-shortcut strings.
 *
 * TitleBar, Sidebar, the Help overlay, and the About page all render
 * from this catalog. These tests pin the canonical values so a
 * binding change is a deliberate, visible edit, and keep the pynput
 * forms in lockstep with the display strings (formatHotkey(pynput)
 * must equal keys on Windows/Linux — the only platform where the
 * display string is the literal text HotkeyChips renders).
 */

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type {
	InAppShortcutId,
	ShortcutDef,
} from "@/components/hotkey/shortcuts";

async function importCatalog() {
	vi.resetModules();
	return (await import(
		"@/components/hotkey/shortcuts"
	)) as typeof import("@/components/hotkey/shortcuts");
}

async function importUtils() {
	vi.resetModules();
	return (await import(
		"@/components/hotkey/hotkey-utils"
	)) as typeof import("@/components/hotkey/hotkey-utils");
}

describe("SHORTCUTS catalog — single source of truth", () => {
	beforeAll(() => {
		vi.resetModules();
	});
	afterEach(() => {
		vi.unstubAllGlobals();
		vi.resetModules();
	});

	it("pins the canonical key strings every display site renders", async () => {
		const { SHORTCUTS } = await importCatalog();
		expect(SHORTCUTS.toggleSidebar.keys).toBe("Ctrl+B");
		expect(SHORTCUTS.openSettings.keys).toBe("Ctrl+,");
		expect(SHORTCUTS.goHome.keys).toBe("Ctrl+H");
		expect(SHORTCUTS.zoomIn.keys).toBe("Ctrl+=");
		expect(SHORTCUTS.zoomOut.keys).toBe("Ctrl+-");
		expect(SHORTCUTS.navBack.keys).toBe("Alt+←");
		expect(SHORTCUTS.navForward.keys).toBe("Alt+→");
		expect(SHORTCUTS.openHelp.keys).toBe("?");
		expect(SHORTCUTS.cancel.keys).toBe("Esc");
		expect(SHORTCUTS.navigate.keys).toBe("Tab / Shift+Tab");
		expect(SHORTCUTS.toggle.keys).toBe("Space");
		expect(SHORTCUTS.activate.keys).toBe("Enter");
		expect(SHORTCUTS.toggleDictation.keys).toBe("Ctrl+Shift+M");
		expect(SHORTCUTS.dismissBubble.keys).toBe("Ctrl+Shift+D");
	});

	it("pins the ARIA keyshortcuts forms exposed on controls", async () => {
		const { SHORTCUTS } = await importCatalog();
		expect(SHORTCUTS.toggleSidebar.ariaKeyshortcuts).toBe("Control+B");
		expect(SHORTCUTS.openSettings.ariaKeyshortcuts).toBe("Control+,");
		expect(SHORTCUTS.goHome.ariaKeyshortcuts).toBe("Control+h");
		expect(SHORTCUTS.openHelp.ariaKeyshortcuts).toBe("?");
		expect(SHORTCUTS.toggleDictation.ariaKeyshortcuts).toBe("Control+Shift+M");
	});

	it("keeps every pynput form in lockstep with its display string on win/linux", async () => {
		vi.stubGlobal("navigator", {
			userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
		});
		const { SHORTCUTS } = await importCatalog();
		const { formatHotkey } = await importUtils();
		// Cast to the catalog's declared type: entries without a pynput
		// form (zoom, dictation keys) legitimately omit the field, so
		// the precise `as const` union makes it look missing.
		for (const [id, def] of Object.entries(SHORTCUTS) as Array<
			[string, ShortcutDef]
		>) {
			if (!def.pynput) continue;
			expect(
				formatHotkey(def.pynput),
				`${id}: formatHotkey(pynput) should render the catalog keys string`,
			).toBe(def.keys);
		}
	});

	it("renders macOS glyph forms for every catalog string (⌃B not Ctrl+B)", async () => {
		// On macOS the chips must show the platform-native glyphs — the
		// same output formatHotkey produces from the pynput forms — so
		// the tooltips match the Sidebar. Pinned per entry so a binding
		// change (or a glyph-table regression) is a deliberate, visible
		// edit. For entries with a pynput form, formatHotkeyForPlatform
		// must agree with formatHotkey exactly.
		vi.stubGlobal("navigator", {
			userAgent:
				"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
		});
		const { SHORTCUTS } = await importCatalog();
		const { formatHotkey, formatHotkeyForPlatform } = await importUtils();
		const expected: Record<string, string> = {
			toggleSidebar: "\u2303B", // ⌃B
			openSettings: "\u2303,", // ⌃,
			goHome: "\u2303H", // ⌃H
			zoomIn: "\u2303=", // ⌃=
			zoomOut: "\u2303-", // ⌃-
			navBack: "\u2325←", // ⌥←
			navForward: "\u2325→", // ⌥→
			openHelp: "?",
			cancel: "Esc",
			navigate: "Tab / \u21E7Tab", // Tab / ⇧Tab
			toggle: "Space",
			activate: "Enter",
			toggleDictation: "\u2303\u21E7M", // ⌃⇧M
			dismissBubble: "\u2303\u21E7D", // ⌃⇧D
		};
		for (const [id, def] of Object.entries(SHORTCUTS) as Array<
			[string, ShortcutDef]
		>) {
			const glyph = formatHotkeyForPlatform(def.keys);
			expect(
				glyph,
				`${id}: formatHotkeyForPlatform(keys) should be the pinned macOS glyph form`,
			).toBe(expected[id]);
			if (def.pynput) {
				expect(
					glyph,
					`${id}: on macOS, formatHotkeyForPlatform must match formatHotkey(pynput)`,
				).toBe(formatHotkey(def.pynput));
			}
		}
	});

	it("has unique display strings (no two bindings share a key label)", async () => {
		const { SHORTCUTS } = await importCatalog();
		const seen = new Set<string>();
		for (const def of Object.values(SHORTCUTS)) {
			expect(seen.has(def.keys), `duplicate keys string: ${def.keys}`).toBe(
				false,
			);
			seen.add(def.keys);
		}
	});

	it("IN_APP_SHORTCUTS exposes exactly the six in-app bindings in display order", async () => {
		const { IN_APP_SHORTCUTS } = await importCatalog();
		expect(IN_APP_SHORTCUTS.map((s) => s.keys)).toEqual([
			"Ctrl+B",
			"Ctrl+,",
			"Ctrl+H",
			"Ctrl+=",
			"Ctrl+-",
			"Ctrl+Shift+M",
		]);
	});

	it("IN_APP_BINDINGS pins the actual KeyboardEvent keys the hook dispatches on", async () => {
		// The bindings themselves (not just the display strings) now
		// come from the catalog — `useGlobalKeyboardShortcuts` iterates
		// `IN_APP_BINDINGS`. Pin the event descriptors so a binding
		// change is a deliberate, visible edit here, and so the
		// dispatch table can never silently drift from the keys the
		// Help overlay / tooltips advertise.
		const { IN_APP_BINDINGS } = await importCatalog();
		expect(IN_APP_BINDINGS.map((b) => b.id)).toEqual([
			"toggleSidebar",
			"openSettings",
			"goHome",
			"zoomIn",
			"zoomOut",
			"toggleDictation",
		]);
		expect(IN_APP_BINDINGS.map((b) => b.keys)).toEqual([
			"Ctrl+B",
			"Ctrl+,",
			"Ctrl+H",
			"Ctrl+=",
			"Ctrl+-",
			"Ctrl+Shift+M",
		]);
		expect(IN_APP_BINDINGS.map((b) => b.eventKeys)).toEqual([
			["b"],
			[","],
			["h"],
			["=", "+"],
			["-"],
			["m", "M"],
		]);
		// The modifier profile per binding: the five navigation/zoom
		// bindings use "ctrlCmd" (Ctrl OR Cmd, no Shift) and the
		// toggleDictation binding uses "ctrlShiftCmd" (Ctrl OR Cmd AND
		// Shift) — each profile must have a matching guard in the
		// hook's MODIFIER_GUARDS map (type-enforced) or the binding
		// would never fire.
		expect(IN_APP_BINDINGS.map((b) => b.modifier)).toEqual([
			"ctrlCmd",
			"ctrlCmd",
			"ctrlCmd",
			"ctrlCmd",
			"ctrlCmd",
			"ctrlShiftCmd",
		]);
	});

	it("every catalog entry with eventKeys is wired into IN_APP_BINDINGS (no silent dispatch drift)", async () => {
		// IN_APP_BINDINGS is derived from IN_APP_SHORTCUT_IDS, so a new
		// catalog entry that declares eventKeys but is NOT added to that
		// list would silently never fire in useGlobalKeyboardShortcuts.
		// This test makes that an explicit, loud failure.
		const { SHORTCUTS, IN_APP_BINDINGS } = await importCatalog();
		const bindingIds = new Set<InAppShortcutId>(
			IN_APP_BINDINGS.map((b) => b.id),
		);
		for (const [id, def] of Object.entries(SHORTCUTS) as Array<
			[string, ShortcutDef]
		>) {
			if (!def.eventKeys) continue;
			expect(
				bindingIds.has(id as InAppShortcutId),
				`${id} declares eventKeys but is missing from IN_APP_BINDINGS — ` +
					"add it to IN_APP_SHORTCUT_IDS (and a handler in " +
					"useGlobalKeyboardShortcuts) or drop eventKeys",
			).toBe(true);
			// Renderer-dispatched entries must be marked renderer.
			expect(def.handledBy).toBe("renderer");
		}
		// Server-handled (pynput) bindings must NOT claim renderer
		// dispatch — Esc/Tab/Space/Enter are global keys the backend
		// owns, and the renderer keydown listener must never intercept
		// them (e.g. Tab/Enter inside a form would break).
		for (const [id, def] of Object.entries(SHORTCUTS) as Array<
			[string, ShortcutDef]
		>) {
			if (def.handledBy !== "server") continue;
			expect(
				def.eventKeys,
				`${id} is server-handled but declares renderer eventKeys`,
			).toBeUndefined();
		}
	});

	it("is re-exported from the keyboard-shortcuts hook for backwards compat", async () => {
		// Import BOTH modules in the same module registry (no reset
		// between them) so the re-export identity is observable.
		vi.resetModules();
		const hook = (await import(
			"@/hooks/useGlobalKeyboardShortcuts"
		)) as typeof import("@/hooks/useGlobalKeyboardShortcuts");
		const catalog = (await import(
			"@/components/hotkey/shortcuts"
		)) as typeof import("@/components/hotkey/shortcuts");
		expect(hook.IN_APP_SHORTCUTS).toBe(catalog.IN_APP_SHORTCUTS);
	});
});
