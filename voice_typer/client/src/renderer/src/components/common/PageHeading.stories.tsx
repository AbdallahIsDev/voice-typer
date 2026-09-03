import type { Meta, StoryObj } from "@storybook/react";
import { Button } from "@/components/ui/button";
import { themeVariantDecorator } from "@/lib/storybook/decorators";
import PageHeading from "./PageHeading";

const meta: Meta<typeof PageHeading> = {
	title: "Components/PageHeading",
	component: PageHeading,
	parameters: {
		docs: {
			description: {
				component:
					"Standard page header with a 2xl semibold title and an optional muted description. When `children` are provided, the header lays out as a two-column row (title/description on the left, actions on the right) — this is the pattern every Settings sub-page uses for its action buttons.",
			},
		},
	},
	tags: ["autodocs"],
};

export default meta;

type Story = StoryObj<typeof PageHeading>;

export const Default: Story = {
	args: {
		title: "Settings",
	},
};

export const WithDescription: Story = {
	args: {
		title: "Audio",
		description: "Configure microphone input, noise filtering and VAD.",
	},
	name: "With description",
};

export const WithActions: Story = {
	args: {
		title: "Templates",
		description: "Reusable text snippets triggered by hotkeys.",
		children: <Button>New template</Button>,
	},
	name: "With action button (right-aligned)",
};

export const EmptyDescription: Story = {
	args: {
		title: "About",
		description: "",
	},
	name: "Empty description (renders non-breaking space)",
	parameters: {
		docs: {
			description: {
				story:
					"When `description` is an empty string (rather than `undefined`), the component reserves the line with a `\\u00A0` non-breaking space so layout doesn't shift between pages that have and don't have a description.",
			},
		},
	},
};

export const DarkBackground: Story = {
	args: {
		title: "Settings",
		description: "Configure microphone input, noise filtering and VAD.",
	},
	decorators: [themeVariantDecorator({ dark: true })],
	name: "Dark background",
	parameters: {
		docs: {
			description: {
				story:
					"Rendered inside a dark-themed scoped wrapper (`dark` class on a container div, mirroring `useTheme`'s contract on `document.documentElement`).",
			},
		},
	},
};

export const RtlLayout: Story = {
	args: {
		title: "الإعدادات",
		description: "تكوين إدخال الميكروفون والتحويل الصوتي.",
	},
	decorators: [themeVariantDecorator({ rtl: true })],
	name: "RTL (Arabic)",
	parameters: {
		docs: {
			description: {
				story:
					'Rendered inside a `dir="rtl"` wrapper, mirroring how `i18n/store.ts` flips `document.documentElement.dir` for Arabic — the title/description column must start from the right and the action column must sit on the left.',
			},
		},
	},
};
