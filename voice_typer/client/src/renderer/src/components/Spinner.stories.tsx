import type { Meta, StoryObj } from "@storybook/react";
import { Spinner } from "./Spinner";

const meta: Meta<typeof Spinner> = {
	title: "Components/Spinner",
	component: Spinner,
	parameters: {
		docs: {
			description: {
				component:
					"Shared loading spinner (UX-021). Replaces the duplicated `h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent` pattern that was previously copy-pasted across 9 pages. Renders an `<output aria-label='Loading'>` element for accessibility.",
			},
		},
	},
	tags: ["autodocs"],
};

export default meta;

type Story = StoryObj<typeof Spinner>;

export const Default: Story = {
	args: { size: 16 },
};

export const Small: Story = {
	args: { size: 12 },
	name: "Small (12px)",
};

export const Medium: Story = {
	args: { size: 20 },
	name: "Medium (20px)",
};

export const Large: Story = {
	args: { size: 24 },
	name: "Large (24px)",
};

export const CustomColor: Story = {
	args: { size: 24, className: "border-current" },
	name: "Custom color (border-current)",
	parameters: {
		docs: {
			description: {
				story:
					"Pass `className='border-current'` to inherit the surrounding text color instead of `--accent`.",
			},
		},
	},
};
