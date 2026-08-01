/**
 * SettingRow unit tests.
 *
 * SettingRow is the layout primitive used to render every labelled
 * setting in the Settings tabs. It composes a visible label + optional
 * InfoTooltip + child form control.
 *
 * : when the caller passes an `info` string, SettingRow forwards
 * its own `label` as the InfoTooltip's `contextLabel`. This lets a
 * screen-reader user tabbing through N SettingRows on the same Settings
 * tab distinguish each row's tooltip ("More info about VAD
 * aggressiveness" vs. "More info about Noise gate threshold") instead
 * of hearing the generic "More info" on every row.
 *
 * These tests pin the wiring so a future refactor that drops
 * `contextLabel={label}` is caught here.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SettingRow } from "@/components/common/SettingRow";
import { TooltipProvider } from "@/components/ui/tooltip";

/** Wrap a node in the shared TooltipProvider so isolated tests can mount. */
function withProvider(node: React.ReactNode) {
	return <TooltipProvider delayDuration={200}>{node}</TooltipProvider>;
}

describe("SettingRow", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the visible label text", () => {
		render(
			withProvider(
				<SettingRow label="VAD aggressiveness">
					<input type="range" />
				</SettingRow>,
			),
		);
		expect(screen.getByText("VAD aggressiveness")).toBeInTheDocument();
	});

	it("does NOT render an InfoTooltip trigger when `info` is omitted", () => {
		render(
			withProvider(
				<SettingRow label="VAD aggressiveness">
					<input type="range" />
				</SettingRow>,
			),
		);
		// No "More info" trigger button should be present.
		expect(screen.queryByRole("button", { name: /more info/i })).toBeNull();
	});

	it("ZU-32: renders an InfoTooltip with contextLabel={label} when `info` is provided", () => {
		render(
			withProvider(
				<SettingRow
					label="VAD aggressiveness"
					info="Adjusts how aggressively the voice activity detector filters out background noise."
				>
					<input type="range" />
				</SettingRow>,
			),
		);
		// en.json: a11y.moreInfoAbout = "More info about {label}"
		// SettingRow forwards its `label` as the InfoTooltip's
		// contextLabel, so the accessible name must include the
		// row's label (not the generic "More info").
		const trigger = screen.getByRole("button", {
			name: "More info about VAD aggressiveness",
		});
		expect(trigger).toBeInTheDocument();
	});

	it("ZU-32: each SettingRow's InfoTooltip has a distinct accessible name (disambiguation)", () => {
		// Two rows on the same Settings tab, each with an `info`
		// tooltip, must be distinguishable by their accessible
		// names so a screen-reader user tabbing through them
		// hears which row each tooltip belongs to.
		render(
			withProvider(
				<div>
					<SettingRow label="VAD aggressiveness" info="VAD help">
						<input type="range" />
					</SettingRow>
					<SettingRow label="Noise gate threshold" info="Noise gate help">
						<input type="range" />
					</SettingRow>
				</div>,
			),
		);
		expect(
			screen.getByRole("button", {
				name: "More info about VAD aggressiveness",
			}),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", {
				name: "More info about Noise gate threshold",
			}),
		).toBeInTheDocument();
	});
});
