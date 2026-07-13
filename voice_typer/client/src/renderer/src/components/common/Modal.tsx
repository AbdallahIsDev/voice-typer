/**
 * Modal — accessible dialog with focus trap and backdrop dismissal.
 *
 * F-3: Wraps Radix Dialog primitive to provide consistent focus management
 * across all dialogs in the app. Features:
 *   - Automatic focus trap (Radix built-in)
 *   - Escape key closes
 *   - Backdrop click closes
 *   - Focus restored to trigger element on close
 *   - Proper aria-modal / aria-labelledby / aria-describedby
 *   - Center-aligned with the same sizing across all call sites
 *
 * Usage:
 *   <Modal open={isOpen} onClose={() => setOpen(false)} title="Delete?">
 *     <p>Are you sure?</p>
 *     <ModalFooter>
 *       <Button variant="ghost" onClick={...}>Cancel</Button>
 *       <Button variant="destructive" onClick={...}>Delete</Button>
 *     </ModalFooter>
 *   </Modal>
 */
import { useCallback, useRef } from "react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

interface ModalProps {
	/** Whether the dialog is open */
	open: boolean;
	/** Called when the user dismisses the dialog (Escape, backdrop click, Cancel) */
	onClose: () => void;
	/** Dialog title (required for a11y — sets aria-labelledby) */
	title: string;
	/** Optional description (sets aria-describedby) */
	description?: string;
	/** Content children — typically the message body + ModalFooter */
	children: React.ReactNode;
	/** Optional size override */
	size?: "default" | "sm";
	/** Optional class name for the content panel */
	className?: string;
}

export function Modal({
	open,
	onClose,
	title,
	description,
	children,
	size = "default",
	className,
}: ModalProps) {
	// Track whether the close was triggered by the user's explicit action
	// (button click) vs. Escape/backdrop so we don't double-fire onClose.
	const dismissedByAction = useRef(false);

	const handleOpenChange = useCallback(
		(isOpen: boolean) => {
			if (!isOpen && !dismissedByAction.current) {
				// Escape key or backdrop click
				onClose();
			}
			dismissedByAction.current = false;
		},
		[onClose],
	);

	const handleClose = useCallback(() => {
		dismissedByAction.current = true;
		onClose();
	}, [onClose]);

	return (
		<Dialog open={open} onOpenChange={handleOpenChange}>
			<DialogContent
				size={size}
				onEscapeKeyDown={handleClose}
				onPointerDownOutside={handleClose}
				className={cn(className)}
			>
				<DialogHeader>
					<DialogTitle>{title}</DialogTitle>
					{description && <DialogDescription>{description}</DialogDescription>}
				</DialogHeader>
				{children}
			</DialogContent>
		</Dialog>
	);
}

/**
 * ModalFooter — standard button row at the bottom of the modal.
 * Exported for convenience so call sites don't need to import DialogFooter.
 */
export function ModalFooter({
	className,
	children,
	...props
}: React.ComponentProps<"div">) {
	return (
		<DialogFooter className={className} {...props}>
			{children}
		</DialogFooter>
	);
}
