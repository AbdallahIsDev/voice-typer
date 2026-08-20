import { MinusSignIcon, PlusSignIcon } from "@hugeicons/core-free-icons";
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
					(UI/UX overhaul 2026-08-20): the expand/collapse
					glyph is a plus (+) / minus (–) pair instead of the
					chevron-down/up pair. The chevron is strongly
					associated with FAQ/accordion "reveal an answer"
					patterns, which implies clicking will reveal
					explanatory text rather than a list of downloadable
					items. A plus in the collapsed state signals "there's
					more to see here" without the FAQ connotation; the
					minus in the expanded state signals "click to
					collapse". Both icons are aria-hidden (the Radix
					trigger carries the accessible name + aria-expanded).
				*/}
				<HugeiconsIcon
					icon={PlusSignIcon}
					strokeWidth={2}
					data-slot="accordion-trigger-icon"
					aria-hidden="true"
					className="pointer-events-none shrink-0 group-aria-expanded/accordion-trigger:hidden"
				/>
				<HugeiconsIcon
					icon={MinusSignIcon}
					strokeWidth={2}
					data-slot="accordion-trigger-icon"
					aria-hidden="true"
					className="pointer-events-none hidden shrink-0 group-aria-expanded/accordion-trigger:inline"
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
					"h-(--radix-accordion-content-height) pt-0 pb-4 [&_a]:underline [&_a]:underline-offset-3 [&_a]:hover:text-foreground [&_p:not(:last-child)]:mb-4",
					className,
				)}
			>
				{children}
			</div>
		</AccordionPrimitive.Content>
	);
}

export { Accordion, AccordionContent, AccordionItem, AccordionTrigger };
