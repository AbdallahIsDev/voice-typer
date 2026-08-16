import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { HotkeyChips } from "@/components/hotkey/HotkeyChips";

afterEach(() => {
	cleanup();
});

function chips(): HTMLElement[] {
	// Only the per-key chips carry data-slot="kbd"; the KbdGroup wrapper
	// is also a <kbd> element (data-slot="kbd-group") and must be
	// excluded from chip counts.
	return Array.from(document.querySelectorAll('kbd[data-slot="kbd"]'));
}

function groups(): HTMLElement[] {
	return Array.from(document.querySelectorAll('kbd[data-slot="kbd-group"]'));
}

describe("HotkeyChips", () => {
	it("renders a single key as one Kbd chip", () => {
		render(<HotkeyChips keys="Esc" />);
		const kbds = chips();
		expect(kbds).toHaveLength(1);
		expect(kbds[0]?.textContent).toBe("Esc");
	});

	it("splits a combo on '+' into a group of Kbd chips with a '+' separator", () => {
		render(<HotkeyChips keys="Ctrl+Alt+V" />);
		const kbds = chips();
		expect(kbds).toHaveLength(3);
		expect(kbds.map((k) => k.textContent)).toEqual(["Ctrl", "Alt", "V"]);
		// Each key chip carries the design-system kbd slot marker.
		for (const k of kbds) {
			expect(k.getAttribute("data-slot")).toBe("kbd");
		}
		// The group wrapper carries the kbd-group slot marker.
		expect(groups()).toHaveLength(1);
		// Two "+" separators between the three chips.
		const plusCount = Array.from(
			document.querySelectorAll('[data-slot="kbd-group"] span'),
		).filter((s) => s.textContent === "+").length;
		expect(plusCount).toBe(2);
	});

	it("splits alternative bindings on ' / ' into separate groups", () => {
		render(<HotkeyChips keys="Tab / Shift+Tab" />);
		const kbds = chips();
		expect(kbds).toHaveLength(3);
		expect(kbds.map((k) => k.textContent)).toEqual(["Tab", "Shift", "Tab"]);
		// "Tab" is a single key (plain chip) and "Shift+Tab" is a combo
		// (KbdGroup) → exactly one kbd-group wrapper.
		expect(groups()).toHaveLength(1);
		// The plain " / " separator text is present between alternatives
		// (it's aria-hidden so it must be queried via the DOM, not the
		// testing-library text matcher).
		const separators = Array.from(document.querySelectorAll("span")).filter(
			(s) => s.textContent === " / ",
		);
		expect(separators).toHaveLength(1);
	});

	it("falls back to a single chip holding the whole string for unexpected formats", () => {
		render(<HotkeyChips keys="Ctrl+Plus / Ctrl+Minus" />);
		const kbds = chips();
		// "Ctrl+Plus" → ["Ctrl", "Plus"], "Ctrl+Minus" → ["Ctrl", "Minus"].
		expect(kbds.map((k) => k.textContent)).toEqual([
			"Ctrl",
			"Plus",
			"Ctrl",
			"Minus",
		]);
	});

	it("handles an unresolved i18n key (no '+' / ' / ') as a single chip", () => {
		render(<HotkeyChips keys="help.keys.cancel" />);
		const kbds = chips();
		expect(kbds).toHaveLength(1);
		expect(kbds[0]?.textContent).toBe("help.keys.cancel");
	});
});
