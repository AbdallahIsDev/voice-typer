import { Cancel01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Dialog as DialogPrimitive } from "radix-ui";
import type * as React from "react";
import { useRef } from "react";
import { cn } from "#utils";
import { t } from "@/i18n/i18n";

function Dialog({
	...props
}: React.ComponentProps<typeof DialogPrimitive.Root>) {
	return <DialogPrimitive.Root data-slot="dialog" {...props} />;
}

function DialogTrigger({
	...props
}: React.ComponentProps<typeof DialogPrimitive.Trigger>) {
	return <DialogPrimitive.Trigger data-slot="dialog-trigger" {...props} />;
}

function DialogPortal({
	...props
}: React.ComponentProps<typeof DialogPrimitive.Portal>) {
	return <DialogPrimitive.Portal data-slot="dialog-portal" {...props} />;
}

function DialogOverlay({
	className,
	...props
}: React.ComponentProps<typeof DialogPrimitive.Overlay>) {
	return (
		<DialogPrimitive.Overlay
			data-slot="dialog-overlay"
			className={cn(
				"fixed inset-0 z-50 bg-black/30 duration-100 supports-backdrop-filter:backdrop-blur-sm data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0",
				className,
			)}
			{...props}
		/>
	);
}

function DialogContent({
	className,
	size = "default",
	children,
	...props
}: React.ComponentProps<typeof DialogPrimitive.Content> & {
	size?: "default" | "sm" | "lg";
}) {
	const contentRef = useRef<HTMLDivElement>(null);
	// Radix Dialog's default onOpenAutoFocus scans for the FIRST
	// focusable descendant — because DialogContent renders the
	// visible X close button as its first child, every modal opened
	// with keyboard focus landing on the X button. SR users heard
	// "Close button" first instead of the dialog title. Suppress the
	// default scan and explicitly focus the title element (which is
	// programmatically focusable via tabIndex=-1). Note: this default
	// is declared BEFORE the props spread so a caller-provided
	// onOpenAutoFocus override wins.
	return (
		<DialogPortal>
			<DialogOverlay />
			<DialogPrimitive.Content
				ref={contentRef}
				data-slot="dialog-content"
				data-size={size}
				aria-modal={true}
				className={cn(
					"group/dialog-content fixed top-1/2 left-1/2 z-50 grid w-full -translate-x-1/2 -translate-y-1/2 gap-6 rounded-xl bg-popover p-6 text-popover-foreground ring-1 ring-foreground/5 duration-100 outline-hidden data-[size=default]:max-w-xs data-[size=sm]:max-w-xs data-[size=default]:sm:max-w-md data-[size=lg]:max-w-xs data-[size=lg]:sm:max-w-xl dark:ring-foreground/10 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
					className,
				)}
				onOpenAutoFocus={(e) => {
					// Only suppress Radix's default first-focusable scan when
					// we have a title to focus instead. Dialogs without a
					// DialogTitle (edge cases / custom compositions) must
					// fall through to Radix's default focus behaviour so
					// keyboard users always land somewhere inside the dialog
					// (preventDefault with no focus target would strand them
					// on the inert background).
					const title = contentRef.current?.querySelector<HTMLElement>(
						'[data-slot="dialog-title"]',
					);
					if (title) {
						e.preventDefault();
						title.focus();
					}
				}}
				{...props}
			>
				{/*visible close (X) button in the top-end corner of every
				   dialog. Sighted users without keyboard expertise expect an X
				   close button. Radix DialogPrimitive.Close auto-fires
				   onOpenChange(false), so callers' existing close handlers
				   (passed via <Dialog onOpenChange={...}>) receive the same
				   signal as Escape / backdrop click. Uses logical-property
				   positioning (`inset-e-2 top-2`) so the button flips with the
				   document direction (Arabic RTL). */}
				<DialogPrimitive.Close
					data-slot="dialog-close-button"
					aria-label={t("common.close")}
					className={cn(
						// Circular hover background (rounded-full) so the X
						// reads as an interactive affordance, consistent with
						// the rest of the dark UI's icon buttons. ``size-9``
						// matches the normalized icon-button size (all icon
						// buttons are 36px app-wide).
						"absolute inset-e-2 top-2 inline-flex size-9 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground focus-visible:outline-hidden focus-visible:ring-3 focus-visible:ring-ring [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
					)}
				>
					<HugeiconsIcon icon={Cancel01Icon} />
				</DialogPrimitive.Close>
				{children}
			</DialogPrimitive.Content>
		</DialogPortal>
	);
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
	return (
		<div
			data-slot="dialog-header"
			className={cn(
				"grid place-items-center gap-1.5 text-center sm:place-items-start sm:text-start",
				className,
			)}
			{...props}
		/>
	);
}

function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
	return (
		<div
			data-slot="dialog-footer"
			className={cn(
				"flex flex-col-reverse gap-2 sm:flex-row sm:justify-end",
				className,
			)}
			{...props}
		/>
	);
}

function DialogTitle({
	className,
	...props
}: React.ComponentProps<typeof DialogPrimitive.Title>) {
	return (
		<DialogPrimitive.Title
			data-slot="dialog-title"
			//tabIndex={-1} makes the heading programmatically
			// focusable (so DialogContent's onOpenAutoFocus can focus
			// it on open) without inserting it into the keyboard tab
			// order.
			tabIndex={-1}
			className={cn("font-heading text-lg font-medium", className)}
			{...props}
		/>
	);
}

function DialogDescription({
	className,
	...props
}: React.ComponentProps<typeof DialogPrimitive.Description>) {
	return (
		<DialogPrimitive.Description
			data-slot="dialog-description"
			className={cn("text-sm text-balance text-muted-foreground", className)}
			{...props}
		/>
	);
}

function DialogClose({
	className,
	...props
}: React.ComponentProps<typeof DialogPrimitive.Close>) {
	return (
		<DialogPrimitive.Close
			data-slot="dialog-close"
			className={cn(className)}
			{...props}
		/>
	);
}

export {
	Dialog,
	DialogClose,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogOverlay,
	DialogPortal,
	DialogTitle,
	DialogTrigger,
};
