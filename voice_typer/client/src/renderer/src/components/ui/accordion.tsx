import { PlusSignIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Accordion as AccordionPrimitive } from "radix-ui";
import type * as React from "react";
import { cn } from "#utils";

function Accordion({
	className,
	...props
}: React.ComponentProps<typeof AccordionPrimitive.Root>) {
	return (
		<AccordionPrimitive.Root
			data-slot="accordion"
			className={cn(
				"flex w-full flex-col overflow-hidden rounded-2xl border",
				className,
			)}
			{...props}
		/>
	);
}

function AccordionItem({
	className,
	...props
}: React.ComponentProps<typeof AccordionPrimitive.Item>) {
	return (
		<AccordionPrimitive.Item
			data-slot="accordion-item"
			className={cn("not-last:border-b data-open:bg-muted/50", className)}
			{...props}
		/>
	);
}

function AccordionTrigger({
	className,
	children,
	...props
}: React.ComponentProps<typeof AccordionPrimitive.Trigger>) {
	return (
		<AccordionPrimitive.Header className="flex">
			<AccordionPrimitive.Trigger
				data-slot="accordion-trigger"
				className={cn(
					"group/accordion-trigger relative flex flex-1 items-start justify-between gap-6 border border-transparent p-4 text-start text-sm font-medium transition-all outline-hidden hover:underline focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:pointer-events-none disabled:opacity-50 **:data-[slot=accordion-trigger-icon]:ml-auto **:data-[slot=accordion-trigger-icon]:size-4 **:data-[slot=accordion-trigger-icon]:text-muted-foreground",
					className,
				)}
				{...props}
			>
				{children}
				{/*
					(2026-08-21): the expand/collapse glyph is a single
					PlusSignIcon that stays a `+` in BOTH the collapsed and
					expanded states — it never swaps to a minus, chevron, or
					any other symbol, and there is intentionally NO
					icon-state transition (the affordance is deliberately
					identical whether the group is open or closed). A
					previous iteration swapped `+`→`−` and then to a
					rotating chevron; both were reverted per the user's
					design decision. The Radix trigger still carries the
					accessible name + aria-expanded; the icon is
					aria-hidden.
				*/}
				<HugeiconsIcon
					icon={PlusSignIcon}
					strokeWidth={2}
					data-slot="accordion-trigger-icon"
					aria-hidden="true"
					className="pointer-events-none shrink-0"
				/>
			</AccordionPrimitive.Trigger>
		</AccordionPrimitive.Header>
	);
}

function AccordionContent({
	className,
	children,
	...props
}: React.ComponentProps<typeof AccordionPrimitive.Content>) {
	return (
		<AccordionPrimitive.Content
			data-slot="accordion-content"
			className="overflow-hidden px-4 text-sm data-open:animate-accordion-down data-closed:animate-accordion-up"
			{...props}
		>
			<div
				className={cn(
					// NO fixed height here: the open/close animations drive the
					// OUTER element's height via the --radix-accordion-content-height
					// keyframes, so the inner wrapper must stay auto-sized. A fixed
					// ``h-(--radix-accordion-content-height)`` froze the OPEN-time
					// measurement — any content that shrinks while open (preset
					// switch, disclosure collapse) left a large trailing void
					// inside the expanded panel.
					"pt-0 pb-4 [&_a]:underline [&_a]:underline-offset-3 [&_a]:hover:text-foreground [&_p:not(:last-child)]:mb-4",
					className,
				)}
			>
				{children}
			</div>
		</AccordionPrimitive.Content>
	);
}

export { Accordion, AccordionContent, AccordionItem, AccordionTrigger };
