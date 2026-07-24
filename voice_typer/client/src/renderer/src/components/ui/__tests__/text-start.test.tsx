/**
 * AlertDialog + Accordion text-start tests — covers BG-69 (physical
 * `text-left` was replaced with logical `text-start` so dialog header
 * and accordion trigger text aligns to the inline-start edge in both
 * LTR and RTL locales).
 *
 * The tests assert on `className` strings because jsdom has no CSS
 * engine; verifying the logical Tailwind utilities are present (and
 * the physical ones are NOT) is sufficient to confirm the intent.
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
	Accordion,
	AccordionContent,
	AccordionItem,
	AccordionTrigger,
} from "../accordion";
import { AlertDialogHeader } from "../alert-dialog";

afterEach(() => {
	cleanup();
});

describe("Accordion — BG-69 text-start (logical)", () => {
	it("uses text-start on the trigger (was text-left)", () => {
		render(
			<Accordion type="single">
				<AccordionItem value="item-1">
					<AccordionTrigger>Click me</AccordionTrigger>
					<AccordionContent>Panel content</AccordionContent>
				</AccordionItem>
			</Accordion>,
		);

		const trigger = document.querySelector(
			'[data-slot="accordion-trigger"]',
		) as HTMLElement;
		expect(trigger).toBeTruthy();
		// BG-69: logical `text-start` replaces physical `text-left` so
		// the trigger's text-align follows the document's inline-start
		// edge (left in LTR, right in RTL) without per-locale overrides.
		expect(trigger.className).toContain("text-start");
		expect(trigger.className).not.toMatch(/\btext-left\b/);
	});
});

describe("AlertDialog — BG-69 text-start (logical)", () => {
	it("uses text-start on the header (was text-left)", () => {
		render(<AlertDialogHeader data-testid="header" />);

		const header = document.querySelector(
			'[data-slot="alert-dialog-header"]',
		) as HTMLElement;
		expect(header).toBeTruthy();
		// BG-69: the sm: breakpoint override previously used physical
		// `text-left`; now uses logical `text-start` so the header's
		// text-align follows the document's inline-start edge (left in
		// LTR, right in RTL) on screens >= sm.
		expect(header.className).toContain("text-start");
		expect(header.className).not.toMatch(/\btext-left\b/);
	});
});
