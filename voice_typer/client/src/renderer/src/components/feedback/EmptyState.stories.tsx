import { Add01Icon, Alert02Icon, Mic02Icon } from "@hugeicons/core-free-icons";
import type { Meta, StoryObj } from "@storybook/react";
import { themeVariantDecorator } from "@/lib/storybook/decorators";
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

//ErrorVariant story. The `variant="error"` prop switches the
// icon to Alert02Icon, tints the icon + ring with `--destructive`,
// and sets `role="alert"` on the wrapper so screen readers announce
// the load failure immediately (vs. the polite `role="status"` used
// for genuine "no data yet" placeholders). Without this story the
// error variant was invisible in the Storybook docs — designers and
// QA had no canonical reference for what a load-failed empty state
// should look like, which led to drift across pages.
//
// The variant is consumed by ConnectionStatusScreen, History,
//Templates, Vocabulary, and Microphone (see  finding for the
// full list). This story serves as the visual contract: any page
// that renders `<EmptyState variant="error" />` should match this
// appearance.
export const ErrorVariant: Story = {
	args: {
		icon: Alert02Icon,
		title: "Failed to load vocabulary",
		description: "Check your connection and try again.",
		actionLabel: "Retry",
		onAction: () => {},
		variant: "error",
	},
	name: "Error variant (load failed)",
};

export const DarkBackground: Story = {
	args: {
		icon: Mic02Icon,
		title: "No dictations yet",
		description: "Press the mic button to start your first recording.",
	},
	decorators: [themeVariantDecorator({ dark: true })],
	name: "Dark background",
	parameters: {
		docs: {
			description: {
				story:
					"Rendered inside a dark-themed scoped wrapper (`dark` class on a container div, mirroring `useTheme`'s contract on `document.documentElement`) — the muted icon, title, description and CTA contrast must hold on the dark palette (including the destructive-tinted error variant).",
			},
		},
	},
};

export const RtlLayout: Story = {
	args: {
		icon: Mic02Icon,
		title: "لا توجد إملاءات بعد",
		description: "اضغط على زر الميكروفون لبدء أول تسجيل.",
		actionLabel: "إضافة",
		onAction: () => {},
	},
	decorators: [themeVariantDecorator({ rtl: true })],
	name: "RTL (Arabic)",
	parameters: {
		docs: {
			description: {
				story:
					'Rendered inside a `dir="rtl"` wrapper — the icon, centered text and action button must mirror correctly for Arabic.',
			},
		},
	},
};
