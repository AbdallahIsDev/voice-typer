import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HotkeyPicker } from "@/components/hotkey/HotkeyPicker";

// The i18n mock renders `${key}(${label})` so each assertion can prove
// WHICH label string flowed into the aria-label template: the i18n
// fallback key (`hotkeyPicker.ariaLabel`) when the caller omitted the
// prop, or the caller's explicit label when provided.
vi.mock("@/i18n/i18n", () => ({
	t: (key: string, opts?: { label?: string }) =>
		opts && typeof opts.label === "string" ? `${key}(${opts.label})` : key,
	useT: () => (key: string, opts?: { label?: string }) =>
		opts && typeof opts.label === "string" ? `${key}(${opts.label})` : key,
}));

// Hugeicons are irrelevant to the aria contract under test — render a
// plain span so the test doesn't need the icon runtime.
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

describe("HotkeyPicker — fallback capture-button aria-label", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});
	afterEach(() => {
		cleanup();
	});

	it("falls back to the localized hotkeyPicker.ariaLabel when no aria-label prop is passed", () => {
		render(<HotkeyPicker value="" mode="single" onChange={vi.fn()} />);
		const button = screen.getByRole("button");
		expect(button.getAttribute("aria-label")).toBe(
			"hotkeyPicker.recordNewAria(hotkeyPicker.ariaLabel)",
		);
	});

	it("uses the caller-provided aria-label over the fallback", () => {
		render(
			<HotkeyPicker
				value=""
				mode="single"
				onChange={vi.fn()}
				aria-label="Dictation key"
			/>,
		);
		const button = screen.getByRole("button");
		expect(button.getAttribute("aria-label")).toBe(
			"hotkeyPicker.recordNewAria(Dictation key)",
		);
	});

	it("wires the fallback label into the preset-dropdown trigger aria-label too", () => {
		render(
			<HotkeyPicker
				value=""
				mode="single"
				onChange={vi.fn()}
				presets={[{ value: "ctrl", label: "Ctrl" }]}
			/>,
		);
		const buttons = screen.getAllByRole("button");
		const labels = buttons.map((b) => b.getAttribute("aria-label"));
		expect(labels).toContain(
			"hotkeyPicker.presetHotkeysAria(hotkeyPicker.ariaLabel)",
		);
	});
});
