import type { Meta, StoryObj } from "@storybook/react";
import { themeVariantDecorator } from "@/lib/storybook/decorators";
import { LevelBar } from "./LevelBar";

const meta: Meta<typeof LevelBar> = {
	title: "Components/LevelBar",
	component: LevelBar,
	parameters: {
		docs: {
			description: {
				component:
					"Renders the live microphone RMS level as a colored progress bar (`role='progressbar'`). The bar's color transitions from `--accent` (low) → `--primary` (mid) → `--destructive` (loud, >0.7) so the user can see when they're clipping. When `playing` is true the bar freezes and dims to 30% opacity, because the user is hearing playback rather than their own input.",
			},
		},
	},
	tags: ["autodocs"],
};

export default meta;

type Story = StoryObj<typeof LevelBar>;

export const Silent: Story = {
	args: { level: 0, playing: false },
	name: "Silent (0)",
};

export const Low: Story = {
	args: { level: 0.15, playing: false },
	name: "Low level (0.15) — accent color",
};

export const Medium: Story = {
	args: { level: 0.45, playing: false },
	name: "Medium level (0.45) — primary color",
};

export const Loud: Story = {
	args: { level: 0.85, playing: false },
	name: "Loud level (0.85) — destructive color",
};

export const Playing: Story = {
	args: { level: 0.6, playing: true },
	name: "Playing (frozen + dimmed)",
	parameters: {
		docs: {
			description: {
				story:
					"When audio playback is active, the bar visually freezes (opacity 30%) so the user can distinguish 'listening' from 'hearing playback'.",
			},
		},
	},
};

export const DarkBackground: Story = {
	args: { level: 0.45, playing: false },
	decorators: [themeVariantDecorator({ dark: true })],
	name: "Dark background (medium level)",
	parameters: {
		docs: {
			description: {
				story:
					"Rendered inside a dark-themed scoped wrapper (`dark` class on a container div, mirroring `useTheme`'s contract on `document.documentElement`) — the fill uses the same solid `bg-primary` token; the track (`bg-border`) and clipping ⚠ affordance must read against the dark palette.",
			},
		},
	},
};

export const RtlLayout: Story = {
	args: { level: 0.6, playing: false },
	decorators: [themeVariantDecorator({ rtl: true })],
	name: "RTL (Arabic)",
	parameters: {
		docs: {
			description: {
				story:
					'Rendered inside a `dir="rtl"` wrapper — the `scaleX` fill must mirror with the document direction (transform-origin flips with logical direction).',
			},
		},
	},
};
