import type { Meta, StoryObj } from "@storybook/react";
import { InfoTooltip } from "./InfoTooltip";

const meta: Meta<typeof InfoTooltip> = {
	title: "Components/InfoTooltip",
	component: InfoTooltip,
	parameters: {
		docs: {
			description: {
				component:
					"Small inline `(?)` glyph that opens a Radix UI tooltip on hover/focus. Used throughout the Settings page to explain technical options (VAD aggressiveness, noise gate, etc.) without cluttering the form. The tooltip is keyboard-accessible and respects the user's `prefers-reduced-motion` setting.",
			},
		},
	},
	tags: ["autodocs"],
};

export default meta;

type Story = StoryObj<typeof InfoTooltip>;

export const Default: Story = {
	args: {
		text: "Higher values detect speech more aggressively but may cut off quiet words.",
	},
	name: "Short help text",
};

export const LongText: Story = {
	args: {
		text: "The noise gate suppresses audio below this RMS threshold. Set it just above your ambient room noise to avoid spurious transcriptions, but not so high that you miss quiet speech. A value of 0.02–0.05 is typical for a quiet office; 0.10+ may be needed in noisy environments.",
	},
	name: "Long help text (wraps)",
};

export const InlineUsage: Story = {
	render: () => (
		<p className="text-sm text-(--text-primary)">
			VAD aggressiveness
			<InfoTooltip text="Higher values detect speech more aggressively." />
		</p>
	),
	name: "Inline (next to a label)",
	parameters: {
		docs: {
			description: {
				story:
					"Shows the typical usage pattern: the tooltip is placed inline immediately after a setting's label so the `(?)` glyph sits naturally next to the text.",
			},
		},
	},
};
