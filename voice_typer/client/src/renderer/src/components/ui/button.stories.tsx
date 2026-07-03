import type { Meta, StoryObj } from "@storybook/react";
import { Button } from "./button";

const meta: Meta<typeof Button> = {
	title: "Components/UI/Button",
	component: Button,
	parameters: {
		docs: {
			description: {
				component:
					"Primary shadcn/ui button built on `class-variance-authority` and `radix-ui`'s `Slot`. Supports six variants (`default`, `outline`, `secondary`, `ghost`, `destructive`, `link`) and nine sizes (incl. `icon*` for square icon-only buttons). The `asChild` prop composes with a child element via Radix Slot.",
			},
		},
	},
	tags: ["autodocs"],
};

export default meta;

type Story = StoryObj<typeof Button>;

export const Default: Story = {
	args: {
		children: "Save changes",
	},
};

export const Outline: Story = {
	args: {
		variant: "outline",
		children: "Cancel",
	},
};

export const Secondary: Story = {
	args: {
		variant: "secondary",
		children: "Secondary action",
	},
};

export const Ghost: Story = {
	args: {
		variant: "ghost",
		children: "Ghost",
	},
};

export const Destructive: Story = {
	args: {
		variant: "destructive",
		children: "Delete forever",
	},
};

export const Link: Story = {
	args: {
		variant: "link",
		children: "Learn more",
	},
};

export const Sizes: Story = {
	args: {
		children: "Save",
		size: "lg",
	},
	name: "Large size",
};

export const Disabled: Story = {
	args: {
		children: "Disabled",
		disabled: true,
	},
};
