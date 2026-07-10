import { Add01Icon, Mic02Icon } from "@hugeicons/core-free-icons";
import type { Meta, StoryObj } from "@storybook/react";
import { EmptyState } from "./EmptyState";

const meta: Meta<typeof EmptyState> = {
	title: "Components/EmptyState",
	component: EmptyState,
	parameters: {
		docs: {
			description: {
				component:
					"Generic empty-state placeholder used by History, Templates, Vocabulary and Microphone pages. Renders a large muted icon, a short title, an optional description and an optional call-to-action button. Extra content can be passed via children.",
			},
		},
	},
	tags: ["autodocs"],
};

export default meta;

type Story = StoryObj<typeof EmptyState>;

export const Default: Story = {
	args: {
		icon: Mic02Icon,
		title: "No dictations yet",
	},
};

export const WithDescription: Story = {
	args: {
		icon: Mic02Icon,
		title: "No dictations yet",
		description: "Press the mic button to start your first recording.",
	},
	name: "With description",
};

export const WithAction: Story = {
	args: {
		icon: Mic02Icon,
		title: "No templates yet",
		description: "Templates let you insert pre-written text with a hotkey.",
		actionLabel: "Add template",
		onAction: () => {
			// no-op — Storybook's action panel handles logging via args.
		},
	},
	name: "With action button",
};

export const CustomActionIcon: Story = {
	args: {
		icon: Mic02Icon,
		title: "No microphones found",
		description: "Connect a microphone and click refresh.",
		actionLabel: "Refresh",
		actionIcon: Add01Icon,
		onAction: () => {},
	},
	name: "Custom action icon",
};
