import type { Meta, StoryObj } from "@storybook/react";
import type { TodayStats } from "@/types/ipc";
import StatCards from "./StatCards";

const meta: Meta<typeof StatCards> = {
	title: "Components/StatCards",
	component: StatCards,
	parameters: {
		docs: {
			description: {
				component:
					"Three-card row shown at the top of the Home / Dashboard page summarizing today's dictation usage: Voice Dictations (count), Text Transcribed (chars, formatted as `1.2K+`), and Dictation Time (duration, formatted as `1h 23m`). Each card uses an icon from `@hugeicons/core-free-icons` and `--bg-subtle` for the card background.",
			},
		},
	},
	tags: ["autodocs"],
};

export default meta;

type Story = StoryObj<typeof StatCards>;

const emptyStats: TodayStats = {
	count: 0,
	chars: 0,
	word_count: 0,
	duration: 0,
};

const smallStats: TodayStats = {
	count: 12,
	chars: 4580,
	word_count: 815,
	duration: 348, // ~6 minutes
};

const largeStats: TodayStats = {
	count: 137,
	chars: 24380,
	word_count: 4297,
	duration: 5235, // 1h 27m
};

export const Empty: Story = {
	args: { stats: emptyStats },
	name: "Empty (zero stats)",
};

export const Small: Story = {
	args: { stats: smallStats },
	name: "Small numbers (12 dictations)",
};

export const Large: Story = {
	args: { stats: largeStats },
	name: "Large numbers (137 dictations, 1h 27m)",
	parameters: {
		docs: {
			description: {
				story:
					"Demonstrates the `formatCompactNumber` and `formatDuration` helpers — 24,380 chars is rendered as `24.3K+` and 5,235 seconds as `1h 27m`.",
			},
		},
	},
};
