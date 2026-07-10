import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { fn } from "storybook/test";
import { RangeSlider } from "./RangeSlider";

const meta: Meta<typeof RangeSlider> = {
	title: "Components/RangeSlider",
	component: RangeSlider,
	parameters: {
		docs: {
			description: {
				component:
					"Labeled `<input type='range'>` with a tabular-nums value readout. Used by every numeric setting in the Settings page (VAD aggressiveness, silence duration, noise gate, etc.). The unit `suffix` is appended to the displayed value (e.g. `ms`, `%`, `Hz`).",
			},
		},
	},
	tags: ["autodocs"],
};

export default meta;

type Story = StoryObj<typeof RangeSlider>;

export const Default: Story = {
	args: {
		value: 50,
		min: 0,
		max: 100,
		step: 1,
		ariaLabel: "Volume",
		suffix: "%",
		onChange: fn(),
	},
};

export const Milliseconds: Story = {
	args: {
		value: 750,
		min: 100,
		max: 2000,
		step: 50,
		ariaLabel: "Silence duration",
		suffix: "ms",
		onChange: fn(),
	},
	name: "Silence duration (ms)",
};

export const Hertz: Story = {
	args: {
		value: 16000,
		min: 8000,
		max: 48000,
		step: 1000,
		ariaLabel: "Sample rate",
		suffix: "Hz",
		onChange: fn(),
	},
	name: "Sample rate (Hz)",
};

export const Interactive: Story = {
	render: (args) => {
		const [value, setValue] = useState(args.value);
		return <RangeSlider {...args} value={value} onChange={setValue} />;
	},
	args: {
		value: 0.5,
		min: 0,
		max: 1,
		step: 0.01,
		ariaLabel: "Noise gate threshold",
		suffix: "",
		onChange: fn(),
	},
	name: "Interactive (drag the slider)",
	parameters: {
		docs: {
			description: {
				story:
					"Wraps RangeSlider in a `useState` so you can drag the slider and see the value readout update live. Useful for verifying keyboard accessibility (arrow keys, PageUp/PageDown).",
			},
		},
	},
};
