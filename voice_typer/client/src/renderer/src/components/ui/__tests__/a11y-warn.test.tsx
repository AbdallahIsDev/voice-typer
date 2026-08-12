/**
 * Dev-mode a11y warning regression tests.
 *
 * The interactive primitives fire a dev-mode ``console.warn`` when an
 * accessible name is missing — the goal is to surface the gap during
 * development without failing the production bundle. Each warn is
 * gated behind ``process.env.NODE_ENV !== "production"`` so the
 * production bundle is unaffected.
 *
 * These tests stub ``console.warn`` and assert the expected message
 * fires (or does NOT fire) for each primitive's a11y contract:
 *   - Button without text children or aria-label → warn
 *   - Button with text children → no warn
 *   - Switch without aria-label / aria-labelledby → warn
 *   - Slider without aria-label / aria-labelledby / thumbLabels → warn
 *   - SelectTrigger without children / aria-label → warn
 *   - SegmentedControl without ariaLabel → warn
 *   - SegmentedControl icon-only option without title → warn
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Button } from "@/components/ui/button";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { Select, SelectTrigger } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";

// Stub the icon library so SegmentedControl's icon-only path can be
// exercised without pulling in the real (heavy) hugeicons renderer.
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

afterEach(() => {
	cleanup();
});

beforeEach(() => {
	vi.resetModules();
});

describe("Button — dev-mode a11y warn", () => {
	it("warns when there are no children and no aria-label (icon-only without name)", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(<Button />);
		expect(warn).toHaveBeenCalledTimes(1);
		expect(warn.mock.calls[0]?.[0]).toMatch(
			/\[renderer:Button\] no `aria-label`/,
		);
		warn.mockRestore();
	});

	it("does NOT warn when there is visible text content", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(<Button>Save</Button>);
		expect(warn).not.toHaveBeenCalled();
		warn.mockRestore();
	});

	it("does NOT warn when aria-label is provided (icon-only button with name)", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(<Button aria-label="Close" />);
		expect(warn).not.toHaveBeenCalled();
		warn.mockRestore();
	});

	it("does NOT warn when aria-labelledby is provided", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(<Button aria-labelledby="close-label" />);
		expect(warn).not.toHaveBeenCalled();
		warn.mockRestore();
	});
});

describe("Switch — dev-mode a11y warn", () => {
	it("warns when no aria-label / aria-labelledby is provided", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(<Switch />);
		expect(warn).toHaveBeenCalledTimes(1);
		expect(warn.mock.calls[0]?.[0]).toMatch(
			/\[renderer:Switch\] no `aria-label`/,
		);
		warn.mockRestore();
	});

	it("does NOT warn when aria-label is provided", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(<Switch aria-label="Enable captions" />);
		expect(warn).not.toHaveBeenCalled();
		warn.mockRestore();
	});

	it("does NOT warn when aria-labelledby is provided", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(<Switch aria-labelledby="captions-label" />);
		expect(warn).not.toHaveBeenCalled();
		warn.mockRestore();
	});
});

describe("Slider — dev-mode a11y warn", () => {
	it("warns when no aria-label / aria-labelledby / thumbLabels is provided", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(<Slider defaultValue={[50]} />);
		expect(warn).toHaveBeenCalledTimes(1);
		expect(warn.mock.calls[0]?.[0]).toMatch(
			/\[renderer:Slider\] no `aria-label`/,
		);
		warn.mockRestore();
	});

	it("does NOT warn when aria-label is provided (single-thumb)", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(<Slider aria-label="Volume" defaultValue={[50]} />);
		expect(warn).not.toHaveBeenCalled();
		warn.mockRestore();
	});

	it("does NOT warn when thumbLabels is provided (multi-thumb)", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(
			<Slider defaultValue={[20, 80]} thumbLabels={["Minimum", "Maximum"]} />,
		);
		expect(warn).not.toHaveBeenCalled();
		warn.mockRestore();
	});

	it("forwards thumbLabels[i] as aria-label on each thumb", () => {
		render(
			<Slider
				defaultValue={[20, 80]}
				thumbLabels={["Minimum", "Maximum"]}
				aria-label="ignored-because-thumbLabels-present"
			/>,
		);
		const thumbs = document.querySelectorAll('[data-slot="slider-thumb"]');
		expect(thumbs).toHaveLength(2);
		expect(thumbs[0]).toHaveAttribute("aria-label", "Minimum");
		expect(thumbs[1]).toHaveAttribute("aria-label", "Maximum");
	});

	it("forwards getThumbAriaValueText as aria-valuetext on each thumb", () => {
		render(
			<Slider
				defaultValue={[3, 7]}
				aria-label="range"
				getThumbAriaValueText={(v) => `${v} decibels`}
			/>,
		);
		const thumbs = document.querySelectorAll('[data-slot="slider-thumb"]');
		expect(thumbs).toHaveLength(2);
		expect(thumbs[0]).toHaveAttribute("aria-valuetext", "3 decibels");
		expect(thumbs[1]).toHaveAttribute("aria-valuetext", "7 decibels");
	});

	it("forwards the root aria-label to each thumb when thumbLabels is absent", () => {
		// Without this fallback, the focusable thumb elements are nameless
		// to SRs even though the dev set ``aria-label`` on the slider root:
		// Radix Slider's root aria-label does not propagate to the thumb.
		render(<Slider aria-label="Volume" defaultValue={[50]} />);
		const thumbs = document.querySelectorAll('[data-slot="slider-thumb"]');
		expect(thumbs).toHaveLength(1);
		expect(thumbs[0]).toHaveAttribute("aria-label", "Volume");
	});

	it("thumbLabels still takes precedence over a root aria-label", () => {
		// Multi-thumb sliders need distinct per-thumb names ("Minimum" /
		// "Maximum"); the root aria-label must NOT clobber them.
		render(
			<Slider
				defaultValue={[20, 80]}
				aria-label="range"
				thumbLabels={["Minimum", "Maximum"]}
			/>,
		);
		const thumbs = document.querySelectorAll('[data-slot="slider-thumb"]');
		expect(thumbs).toHaveLength(2);
		expect(thumbs[0]).toHaveAttribute("aria-label", "Minimum");
		expect(thumbs[1]).toHaveAttribute("aria-label", "Maximum");
	});
});

describe("SelectTrigger — dev-mode a11y warn", () => {
	it("warns when no children / aria-label / aria-labelledby is provided", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(
			<Select>
				<SelectTrigger />
			</Select>,
		);
		expect(warn).toHaveBeenCalledTimes(1);
		expect(warn.mock.calls[0]?.[0]).toMatch(
			/\[renderer:select\] SelectTrigger: no `aria-label`/,
		);
		warn.mockRestore();
	});

	it("does NOT warn when aria-label is provided", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(
			<Select>
				<SelectTrigger aria-label="Microphone" />
			</Select>,
		);
		expect(warn).not.toHaveBeenCalled();
		warn.mockRestore();
	});
});

describe("SegmentedControl — dev-mode a11y warn", () => {
	it("warns when ariaLabel is missing", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(
			<SegmentedControl
				options={[
					{ value: "a", label: "Alpha" },
					{ value: "b", label: "Bravo" },
				]}
				value="a"
				onChange={() => {}}
			/>,
		);
		const a11yWarns = warn.mock.calls.filter((c) =>
			String(c[0]).includes("[renderer:SegmentedControl] `ariaLabel`"),
		);
		expect(a11yWarns).toHaveLength(1);
		warn.mockRestore();
	});

	it("does NOT warn when ariaLabel is provided", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(
			<SegmentedControl
				options={[
					{ value: "a", label: "Alpha" },
					{ value: "b", label: "Bravo" },
				]}
				value="a"
				onChange={() => {}}
				ariaLabel="recording-mode"
			/>,
		);
		const a11yWarns = warn.mock.calls.filter((c) =>
			String(c[0]).includes("[renderer:SegmentedControl] `ariaLabel`"),
		);
		expect(a11yWarns).toHaveLength(0);
		warn.mockRestore();
	});

	it("warns when an icon-only option is missing title", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(
			<SegmentedControl
				options={[
					{
						value: "a",
						label: "",
						icon: (() => null) as unknown as never,
					},
				]}
				value="a"
				onChange={() => {}}
				ariaLabel="recording-mode"
			/>,
		);
		const iconWarns = warn.mock.calls.filter((c) =>
			String(c[0]).includes("icon-only"),
		);
		expect(iconWarns).toHaveLength(1);
		warn.mockRestore();
	});

	it("does NOT warn for icon-only option when title is provided", () => {
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		render(
			<SegmentedControl
				options={[
					{
						value: "a",
						label: "",
						icon: (() => null) as unknown as never,
						title: "Alpha",
					},
				]}
				value="a"
				onChange={() => {}}
				ariaLabel="recording-mode"
			/>,
		);
		const iconWarns = warn.mock.calls.filter((c) =>
			String(c[0]).includes("icon-only"),
		);
		expect(iconWarns).toHaveLength(0);
		warn.mockRestore();
	});

	it("uses a stable useId-derived name for radio inputs when ariaLabel is missing", () => {
		render(
			<SegmentedControl
				options={[
					{ value: "a", label: "Alpha" },
					{ value: "b", label: "Bravo" },
				]}
				value="a"
				onChange={() => {}}
			/>,
		);
		const radios = document.querySelectorAll<HTMLInputElement>(
			'input[type="radio"]',
		);
		expect(radios.length).toBeGreaterThanOrEqual(2);
		const names = new Set(Array.from(radios).map((r) => r.name));
		// All radios in the same control share one name (so they toggle
		// as a group), and that name is NOT the legacy collision-prone
		// "segmented-control" literal — it's prefixed with "segmented-control-"
		// followed by the useId-derived base id.
		expect(names.size).toBe(1);
		const theName = names.values().next().value as string;
		expect(theName.startsWith("segmented-control-")).toBe(true);
		expect(theName).not.toBe("segmented-control");
	});
});
