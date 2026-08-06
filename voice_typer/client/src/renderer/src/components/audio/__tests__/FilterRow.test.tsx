/**
 * unit tests for `components/audio/FilterRow.tsx` — the
 * presentational per-row renderer for `<AudioFilterChain>`.
 *
 * `FilterRow` reads a single `AudioFilterRowDescriptor` + the live
 * `VoiceTyperConfig` + a labels dictionary + a `set(k, v)` callback,
 * and renders a `<SettingRow label=... info=...>` with a control
 * (`Switch` | `RangeSlider` | `Select`) inside.
 *
 * Coverage:
 *   1. Renders the resolved label + a control for each `kind`
 *      (toggle/slider/select).
 *   2. The control has an accessible name (`aria-label`) wired up — the
 *      `<SettingRow>`'s visible label is rendered as a `<span id=labelId>`
 *      and the control is given an `aria-label={aria}` resolved from the
 *      labels dict. (FilterRow uses `aria-label`, not `aria-labelledby` —
 *      the label string is duplicated on the control rather than pointed
 *      at the visible-label span. Pinning this contract catches an
 *      accidental switch to `aria-labelledby` that would drop the
 *      descriptor's dedicated `ariaKey` in favour of the label string.)
 *   3. The label and info strings are resolved from the labels dict
 *      (with a fallback to the raw key if the dict is missing the key).
 *   4. `set(configKey, value)` is called when the user interacts with the
 *      control — verifies the `write` closure wires the descriptor's
 *      configKey through to the parent's `set` callback.
 *   5. `parentToggle` propagation: when the parent toggle's config value
 *      is falsy (and its defaultValue is also falsy), FilterRow returns
 *      null (the row is hidden). When the parent toggle is truthy, the
 *      row renders normally. This is the "disabled-state propagation":
 *      the parent toggle's state propagates to whether the child row
 *      renders at all.
 *   6. `defaultValue` fallback: when `config[configKey]` is `undefined`,
 *      the control renders with the descriptor's `defaultValue`.
 *
 * Mock strategy: the heavy Radix UI components (`Switch`, `Select`,
 * `Slider`) and the `SettingRow`/`RangeSlider` wrappers are left
 * UN-mocked — jsdom supports them well enough for the assertions here
 * (label text, aria-label, click/focus events, container.queryAll). The
 * existing `SettingRow.test.tsx`, `RangeSlider.test.tsx`, and
 * `switch.test.tsx` cover the primitives' own contracts in depth.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { VoiceTyperConfig } from "@/types/config";
import {
	type AudioFilterRowDescriptor,
	audioFilterRowDescriptors,
} from "../audioFilterRowDescriptors";
import { FilterRow } from "../FilterRow";

/** Wrap a node in the shared TooltipProvider so SettingRow's InfoTooltip mounts. */
function withProvider(node: React.ReactNode) {
	return <TooltipProvider delayDuration={200}>{node}</TooltipProvider>;
}

/** Build a labels dict where every key resolves to `[KEY]` for easy matching. */
function makeLabels(keys: string[]): Record<string, string> {
	const out: Record<string, string> = {};
	for (const k of keys) out[k] = `[${k}]`;
	return out;
}

/** Find the descriptor for a given configKey (helper for legibility). */
function descriptorFor(configKey: string): AudioFilterRowDescriptor {
	const d = audioFilterRowDescriptors.find((d) => d.configKey === configKey);
	if (!d) throw new Error(`no descriptor for ${configKey}`);
	return d;
}

/** Empty config cast to VoiceTyperConfig — tests override individual keys. */
function emptyConfig(): VoiceTyperConfig {
	return {} as VoiceTyperConfig;
}

describe("FilterRow — toggle kind", () => {
	afterEach(() => cleanup());

	it("renders the resolved label + a Switch control", () => {
		const d = descriptorFor("noise_filter_highpass");
		const labels = makeLabels([
			d.labelKey,
			d.infoKey,
			d.ariaKey,
			d.sectionTitleKey,
		]);
		const set = vi.fn();
		render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={emptyConfig()}
					set={set}
					labels={labels}
				/>,
			),
		);
		// Label text is resolved from the labels dict.
		expect(screen.getByText(`[${d.labelKey}]`)).toBeInTheDocument();
		// The Switch has an aria-label matching the resolved aria string.
		const sw = screen.getByRole("switch");
		expect(sw).toHaveAttribute("aria-label", `[${d.ariaKey}]`);
	});

	it("clicking the Switch calls set(configKey, nextBool)", () => {
		const d = descriptorFor("noise_filter_highpass");
		const labels = makeLabels([
			d.labelKey,
			d.infoKey,
			d.ariaKey,
			d.sectionTitleKey,
		]);
		const set = vi.fn();
		render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={emptyConfig()}
					set={set}
					labels={labels}
				/>,
			),
		);
		const sw = screen.getByRole("switch");
		fireEvent.click(sw);
		expect(set).toHaveBeenCalledTimes(1);
		expect(set).toHaveBeenCalledWith(d.configKey, expect.any(Boolean));
	});

	it("uses descriptor.defaultValue when config[configKey] is undefined", () => {
		const d = descriptorFor("noise_filter_highpass");
		const labels = makeLabels([
			d.labelKey,
			d.infoKey,
			d.ariaKey,
			d.sectionTitleKey,
		]);
		render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={emptyConfig()}
					set={vi.fn()}
					labels={labels}
				/>,
			),
		);
		const sw = screen.getByRole("switch");
		// defaultValue is `true` for the high-pass toggle.
		expect(sw).toHaveAttribute("aria-checked", "true");
	});

	it("reads config[configKey] when set (overrides defaultValue)", () => {
		const d = descriptorFor("noise_filter_highpass");
		const labels = makeLabels([
			d.labelKey,
			d.infoKey,
			d.ariaKey,
			d.sectionTitleKey,
		]);
		const config = {
			...emptyConfig(),
			noise_filter_highpass: false,
		};
		render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={config}
					set={vi.fn()}
					labels={labels}
				/>,
			),
		);
		expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
	});
});

describe("FilterRow — slider kind", () => {
	afterEach(() => cleanup());

	it("renders the resolved label + a slider control with aria-label", () => {
		// high-pass cutoff is a slider with parentToggle=noise_filter_highpass.
		const d = descriptorFor("noise_filter_highpass_cutoff_hz");
		const labels = makeLabels([
			d.labelKey,
			d.infoKey,
			d.ariaKey,
			d.sectionTitleKey,
		]);
		const config = {
			...emptyConfig(),
			noise_filter_highpass: true,
		};
		render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={config}
					set={vi.fn()}
					labels={labels}
				/>,
			),
		);
		// Label is resolved.
		expect(screen.getByText(`[${d.labelKey}]`)).toBeInTheDocument();
		// Slider exposes the aria-label. RangeSlider forwards it to the
		// underlying Radix Slider thumb element (the focusable
		// `[role="slider"]` node), via Slider's per-thumb aria-label
		// fallback (`thumbLabels?.[i] ?? props["aria-label"]`).
		const slider = screen.getByRole("slider");
		expect(slider).toHaveAttribute("aria-label", `[${d.ariaKey}]`);
		// RangeSlider also forwards aria-valuenow / aria-valuemin /
		// aria-valuemax through to the Slider root (Radix propagates
		// them to the thumb). Verify min/max reflect the descriptor.
		expect(slider).toHaveAttribute("aria-valuemin", String(d.min));
		expect(slider).toHaveAttribute("aria-valuemax", String(d.max));
	});

	it("uses descriptor.defaultValue for the slider value when config is undefined", () => {
		const d = descriptorFor("noise_filter_highpass_cutoff_hz");
		const labels = makeLabels([
			d.labelKey,
			d.infoKey,
			d.ariaKey,
			d.sectionTitleKey,
		]);
		const config = {
			...emptyConfig(),
			noise_filter_highpass: true,
		};
		render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={config}
					set={vi.fn()}
					labels={labels}
				/>,
			),
		);
		const slider = screen.getByRole("slider");
		// defaultValue is 80 for the high-pass cutoff.
		expect(slider).toHaveAttribute("aria-valuenow", "80");
		expect(slider).toHaveAttribute("aria-valuemin", String(d.min));
		expect(slider).toHaveAttribute("aria-valuemax", String(d.max));
	});
});

describe("FilterRow — select kind", () => {
	afterEach(() => cleanup());

	it("renders the resolved label + a Select trigger with aria-label", () => {
		const d = descriptorFor("noise_suppression_method");
		const labels = makeLabels([
			d.labelKey,
			d.infoKey,
			d.ariaKey,
			d.sectionTitleKey,
			// include the noneOption labelKey so the option label resolves
			"settings.audioEnhancement.noneOption",
		]);
		render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={emptyConfig()}
					set={vi.fn()}
					labels={labels}
				/>,
			),
		);
		// Label resolved.
		expect(screen.getByText(`[${d.labelKey}]`)).toBeInTheDocument();
		// The Select trigger (a combobox) exposes aria-label.
		const trigger = screen.getByRole("combobox");
		expect(trigger).toHaveAttribute("aria-label", `[${d.ariaKey}]`);
	});
});

describe("FilterRow — label/info/aria fallback to raw key when labels dict is missing the key", () => {
	afterEach(() => cleanup());

	it("falls back to labelKey when labels[labelKey] is undefined", () => {
		const d = descriptorFor("noise_filter_highpass");
		// Empty labels dict — every lookup falls back to the raw key.
		render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={emptyConfig()}
					set={vi.fn()}
					labels={{}}
				/>,
			),
		);
		expect(screen.getByText(d.labelKey)).toBeInTheDocument();
	});

	it("falls back to ariaKey when labels[ariaKey] is undefined", () => {
		const d = descriptorFor("noise_filter_highpass");
		render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={emptyConfig()}
					set={vi.fn()}
					labels={{}}
				/>,
			),
		);
		const sw = screen.getByRole("switch");
		expect(sw).toHaveAttribute("aria-label", d.ariaKey);
	});
});

describe("FilterRow — parentToggle propagation (disabled-state hides the row)", () => {
	afterEach(() => cleanup());

	it("returns null when parentToggle's config value is false (parent default is true)", () => {
		// high-pass cutoff has parentToggle=noise_filter_highpass
		// (parent default = true). Setting config[noise_filter_highpass]=false
		// must hide the cutoff row.
		const d = descriptorFor("noise_filter_highpass_cutoff_hz");
		const labels = makeLabels([
			d.labelKey,
			d.infoKey,
			d.ariaKey,
			d.sectionTitleKey,
		]);
		const config = {
			...emptyConfig(),
			noise_filter_highpass: false,
		};
		const { container } = render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={config}
					set={vi.fn()}
					labels={labels}
				/>,
			),
		);
		// FilterRow returns null — container is empty.
		expect(container.firstChild).toBeNull();
		expect(screen.queryByText(`[${d.labelKey}]`)).toBeNull();
	});

	it("renders the row when parentToggle's config value is true", () => {
		const d = descriptorFor("noise_filter_highpass_cutoff_hz");
		const labels = makeLabels([
			d.labelKey,
			d.infoKey,
			d.ariaKey,
			d.sectionTitleKey,
		]);
		const config = {
			...emptyConfig(),
			noise_filter_highpass: true,
		};
		render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={config}
					set={vi.fn()}
					labels={labels}
				/>,
			),
		);
		expect(screen.getByText(`[${d.labelKey}]`)).toBeInTheDocument();
	});

	it("renders the row when parentToggle's config is undefined (falls back to parent's defaultValue=true)", () => {
		const d = descriptorFor("noise_filter_highpass_cutoff_hz");
		const labels = makeLabels([
			d.labelKey,
			d.infoKey,
			d.ariaKey,
			d.sectionTitleKey,
		]);
		// config has neither the parent nor the child set — both fall
		// back to their descriptor defaultValues (parent=true, child=80).
		const config = emptyConfig();
		render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={config}
					set={vi.fn()}
					labels={labels}
				/>,
			),
		);
		expect(screen.getByText(`[${d.labelKey}]`)).toBeInTheDocument();
		expect(screen.getByRole("slider")).toHaveAttribute("aria-valuenow", "80");
	});

	it("renders when descriptor has no parentToggle (top-level rows always visible)", () => {
		// noise_filter_highpass (toggle) has no parentToggle.
		const d = descriptorFor("noise_filter_highpass");
		const labels = makeLabels([
			d.labelKey,
			d.infoKey,
			d.ariaKey,
			d.sectionTitleKey,
		]);
		render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={emptyConfig()}
					set={vi.fn()}
					labels={labels}
				/>,
			),
		);
		expect(screen.getByText(`[${d.labelKey}]`)).toBeInTheDocument();
	});

	it("notch row is hidden when parent (notchFilter toggle, defaultValue=false) is undefined", () => {
		// notchFilter's defaultValue is `false`, so an undefined config
		// means the parent is OFF, and the notchFrequency sub-row must
		// NOT render.
		const d = descriptorFor("noise_filter_notch_frequency_hz");
		const labels = makeLabels([
			d.labelKey,
			d.infoKey,
			d.ariaKey,
			d.sectionTitleKey,
		]);
		const { container } = render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={emptyConfig()}
					set={vi.fn()}
					labels={labels}
				/>,
			),
		);
		expect(container.firstChild).toBeNull();
	});

	it("notch row renders when parent notchFilter toggle is explicitly set to true", () => {
		const d = descriptorFor("noise_filter_notch_frequency_hz");
		const labels = makeLabels([
			d.labelKey,
			d.infoKey,
			d.ariaKey,
			d.sectionTitleKey,
		]);
		const config = {
			...emptyConfig(),
			noise_filter_notch: true,
		};
		render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={config}
					set={vi.fn()}
					labels={labels}
				/>,
			),
		);
		expect(screen.getByText(`[${d.labelKey}]`)).toBeInTheDocument();
	});
});

describe("FilterRow — write closure wires descriptor.configKey to set()", () => {
	afterEach(() => cleanup());

	it("toggle write forwards the new boolean under descriptor.configKey", () => {
		const d = descriptorFor("noise_filter_eq");
		const labels = makeLabels([
			d.labelKey,
			d.infoKey,
			d.ariaKey,
			d.sectionTitleKey,
		]);
		const set = vi.fn();
		render(
			withProvider(
				<FilterRow
					descriptor={d}
					config={emptyConfig()}
					set={set}
					labels={labels}
				/>,
			),
		);
		fireEvent.click(screen.getByRole("switch"));
		expect(set).toHaveBeenCalledWith(d.configKey, expect.any(Boolean));
	});
});
