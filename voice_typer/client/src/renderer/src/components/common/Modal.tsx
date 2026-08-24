/**
 * Modal — accessible dialog with focus trap and backdrop dismissal.
 *
 * Wraps the Radix Dialog primitive to provide consistent focus management
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
import { useCallback } from "react";
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
	/** Dialog title (sets aria-labelledby). */
	title?: string;
	/** Optional description (sets aria-describedby) */
	description?: string;
	/** Content children — typically the message body + ModalFooter */
	children: React.ReactNode;
	/** Optional size override — `lg` is a roomier panel (max-w-xl on
	 * desktop) for content-heavy overlays like the help reference. */
	size?: "default" | "sm" | "lg";
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
	const handleOpenChange = useCallback(
		(isOpen: boolean) => {
			if (!isOpen) {
				onClose();
			}
		},
		[onClose],
	);

	return (
		<Dialog open={open} onOpenChange={handleOpenChange}>
			<DialogContent size={size} className={cn(className)}>
				{title && (
					<DialogHeader>
						<DialogTitle>{title}</DialogTitle>
						{description && (
							<DialogDescription>{description}</DialogDescription>
						)}
					</DialogHeader>
				)}
				{children}
			</DialogContent>
		</Dialog>
	);
}

/**
 * ModalFooter — thin re-export of {@link DialogFooter} so call sites that
 * already import `Modal` don't need a second import from
 * `@/components/ui/dialog`. Production code that needs more control
 * should import `DialogFooter` directly.
 *
 * Previously this was a zero-value shim that just forwarded props to
 * `DialogFooter`; the re-export below preserves the public API while
 * removing the duplicated wrapper code.
 */
export { DialogFooter as ModalFooter };
