import type { Meta, StoryObj } from "@storybook/react";
import type { ReactElement } from "react";
import { Spinner } from "./Spinner";

const meta: Meta<typeof Spinner> = {
	title: "Components/Spinner",
	component: Spinner,
	parameters: {
		docs: {
			description: {
				component:
					"Shared loading spinner (UX-021). Replaces the duplicated `h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent` pattern that was previously copy-pasted across 9 pages. Renders a `<span role='img' aria-label='Loading'>` element for accessibility (S5-CR-100 — the previous `<output>` root had an implicit `aria-live='polite'` region, which caused screen readers to announce 'Loading' on every page that rendered a Spinner). Pass `decorative` to render a plain `<div aria-hidden>` when the spinner is nested inside an already-labeled parent. Pages that want a status announcement (e.g. ConnectionStatusScreen) wrap the Spinner in their own `<output aria-live='polite'>`.",
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

export const Decorative: Story = {
	args: { size: 16, decorative: true },
	name: "Decorative (aria-hidden)",
	parameters: {
		docs: {
			description: {
				story:
					"Pass `decorative` to render a plain `<div aria-hidden='true'>` (no role, no aria-label). Use this when the spinner is nested inside an element that already provides an accessible name (e.g. a labeled button) so screen-reader users don't hear 'Loading…' redundantly on top of the parent's label.",
			},
		},
	},
};

// S5-CR-100 example: wrap the (now non-live) Spinner in an explicit
// <output aria-live="polite"> when the loading state IS the primary
// status message for the page (e.g. ConnectionStatusScreen while the
// backend is starting). The wrapper restores the polite live-region
// announcement the Spinner used to provide by default.

export const WithLiveRegion: Story = {
	render: (): ReactElement => (
		<output
			aria-live="polite"
			aria-label="Loading"
			style={{
				display: "flex",
				alignItems: "center",
				justifyContent: "center",
				padding: "1rem",
			}}
		>
			<Spinner size={24} />
		</output>
	),
	name: "With live region (wrap in <output>)",
	parameters: {
		docs: {
			description: {
				story:
					"S5-CR-100: the Spinner default no longer carries an implicit `aria-live='polite'` region. When the spinner IS the primary status message for the page (e.g. ConnectionStatusScreen while the backend is starting), wrap it in `<output aria-live='polite'>` to restore the polite live-region announcement. Pages where the spinner is incidental (History, Vocabulary, Models, etc.) should NOT wrap — they get the focusable-image semantics without the redundant live-region announcement.",
			},
		},
	},
};
