/**
 * A11y attribute regression tests for the UI primitives.
 *
 * Covers:
 * - NumberInputStepper: visually-hidden aria-live region echoes the
 *   new value on step; errorId / aria-errormessage forwarding.
 * - Dialog: aria-modal={true} on the content; close button uses the
 *   design-system <Button> surface.
 * - AlertDialog: aria-modal={true} on the content.
 * - Accordion: aria-hidden="true" on decorative chevron icons.
 */
import { cleanup, render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: (
		props: Record<string, unknown> & { icon?: { name?: string } },
	) => {
		// Mimic the real HugeiconsIcon: render an SVG-like element that
		// forwards DOM attributes (data-slot, aria-hidden, className, etc.)
		// so tests can assert on them. The real component renders an <svg>;
		// we use a <span> with the same attributes for jsdom assertions.
		const { icon, strokeWidth, ...rest } = props;
		return <span data-testid="hugeicon" data-name={icon?.name} {...rest} />;
	},
}));

vi.mock("@hugeicons/core-free-icons", () => ({
	Cancel01Icon: { name: "Cancel01Icon" },
	ArrowDown01Icon: { name: "ArrowDown01Icon" },
	ArrowUp01Icon: { name: "ArrowUp01Icon" },
}));

import {
	Accordion,
	AccordionContent,
	AccordionItem,
	AccordionTrigger,
} from "@/components/ui/accordion";
import {
	AlertDialog,
	AlertDialogContent,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { NumberInputStepper } from "@/components/ui/number-input-stepper";

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
	getLocale: () => "en",
	isRtlLocale: () => false,
}));

afterEach(() => {
	cleanup();
});

describe("NumberInputStepper — aria-live region", () => {
	it("renders a visually-hidden aria-live=polite sibling next to the input", () => {
		render(
			<NumberInputStepper value="5" onChange={() => {}} aria-label="count" />,
		);
		const liveRegion = document.querySelector('[aria-live="polite"]');
		expect(liveRegion).toBeTruthy();
		expect(liveRegion?.getAttribute("aria-atomic")).toBe("true");
		// Visually-hidden — Tailwind .sr-only utility.
		expect(liveRegion?.className).toContain("sr-only");
	});

	it("echoes the new value into the live region on step-up", async () => {
		const onChange = vi.fn();
		const user = userEvent.setup();
		render(
			<NumberInputStepper value="5" onChange={onChange} aria-label="count" />,
		);
		const liveRegion = document.querySelector(
			'[aria-live="polite"]',
		) as HTMLElement;
		expect(liveRegion).toBeTruthy();
		// The live region starts empty (only mutated when a step fires).
		expect(liveRegion.textContent).toBe("");

		const upBtn = document.querySelector(
			'button[aria-label="a11y.increase"]',
		) as HTMLButtonElement;
		expect(upBtn).toBeTruthy();
		await user.click(upBtn);

		// After the click, the synthetic change event fires (verifying
		// the onChange path) AND the live region is populated with the
		// new value.
		expect(onChange).toHaveBeenCalledTimes(1);
		expect(liveRegion.textContent).toBe("6");
	});

	it("echoes the new value into the live region on step-down", async () => {
		const onChange = vi.fn();
		const user = userEvent.setup();
		render(
			<NumberInputStepper value="5" onChange={onChange} aria-label="count" />,
		);
		const liveRegion = document.querySelector(
			'[aria-live="polite"]',
		) as HTMLElement;
		const downBtn = document.querySelector(
			'button[aria-label="a11y.decrease"]',
		) as HTMLButtonElement;
		await user.click(downBtn);
		expect(onChange).toHaveBeenCalledTimes(1);
		expect(liveRegion.textContent).toBe("4");
	});
});

describe("NumberInputStepper — errorId / aria-errormessage forwarding", () => {
	it("forwards errorId as aria-errormessage on the underlying input", () => {
		render(
			<NumberInputStepper
				value="5"
				onChange={() => {}}
				aria-label="count"
				errorId="count-error"
			/>,
		);
		const input = document.querySelector(
			'input[type="number"]',
		) as HTMLInputElement;
		expect(input).toBeTruthy();
		expect(input.getAttribute("aria-errormessage")).toBe("count-error");
	});

	it("forwards a directly-passed aria-errormessage when errorId is omitted", () => {
		render(
			<NumberInputStepper
				value="5"
				onChange={() => {}}
				aria-label="count"
				aria-errormessage="inline-error"
			/>,
		);
		const input = document.querySelector(
			'input[type="number"]',
		) as HTMLInputElement;
		expect(input.getAttribute("aria-errormessage")).toBe("inline-error");
	});

	it("omits aria-errormessage when neither errorId nor aria-errormessage is provided", () => {
		render(
			<NumberInputStepper value="5" onChange={() => {}} aria-label="count" />,
		);
		const input = document.querySelector(
			'input[type="number"]',
		) as HTMLInputElement;
		expect(input.hasAttribute("aria-errormessage")).toBe(false);
	});

	it("errorId takes precedence over aria-errormessage", () => {
		render(
			<NumberInputStepper
				value="5"
				onChange={() => {}}
				aria-label="count"
				errorId="primary"
				aria-errormessage="secondary"
			/>,
		);
		const input = document.querySelector(
			'input[type="number"]',
		) as HTMLInputElement;
		expect(input.getAttribute("aria-errormessage")).toBe("primary");
	});
});

describe("Dialog — aria-modal={true}", () => {
	it("the DialogContent declares aria-modal=true explicitly", () => {
		render(
			<Dialog open={true}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Test</DialogTitle>
					</DialogHeader>
				</DialogContent>
			</Dialog>,
		);
		const content = document.querySelector(
			'[data-slot="dialog-content"]',
		) as HTMLElement;
		expect(content).toBeTruthy();
		expect(content.getAttribute("aria-modal")).toBe("true");
	});
});

describe("AlertDialog — aria-modal={true}", () => {
	it("the AlertDialogContent declares aria-modal=true explicitly", () => {
		render(
			<AlertDialog open={true}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Confirm</AlertDialogTitle>
					</AlertDialogHeader>
				</AlertDialogContent>
			</AlertDialog>,
		);
		const content = document.querySelector(
			'[data-slot="alert-dialog-content"]',
		) as HTMLElement;
		expect(content).toBeTruthy();
		expect(content.getAttribute("aria-modal")).toBe("true");
	});
});

describe("Accordion — decorative chevron aria-hidden", () => {
	it("both chevron icons are marked aria-hidden=true", () => {
		render(
			<Accordion type="single">
				<AccordionItem value="item-1">
					<AccordionTrigger>Section</AccordionTrigger>
					<AccordionContent>Panel</AccordionContent>
				</AccordionItem>
			</Accordion>,
		);
		const icons = document.querySelectorAll(
			'[data-slot="accordion-trigger-icon"]',
		);
		expect(icons.length).toBe(2);
		for (const icon of icons) {
			expect(icon.getAttribute("aria-hidden")).toBe("true");
		}
	});
});
