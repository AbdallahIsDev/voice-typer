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
import { useCallback, useEffect, useRef } from "react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

/**
 * Fired when the user attempts to close the dialog (Escape, backdrop
 * click, or the corner close button) BEFORE the close completes.
 * Return `false` (or a promise resolving to `false`) to veto the
 * close — e.g. to first confirm discarding unsaved edits. Returning
 * `true`/`undefined` lets the close proceed.
 */
export type ModalCloseIntentVeto = () => boolean | Promise<boolean>;

interface ModalProps {
	/** Whether the dialog is open */
	open: boolean;
	/** Called when the user dismisses the dialog (Escape, backdrop click, Cancel) */
	onClose: () => void;
	/**
	 * Optional veto gate evaluated on every user-initiated close
	 * attempt BEFORE the dialog closes. While the gate is pending
	 * (async) the dialog stays open. Rejections are treated as a veto.
	 */
	onCloseIntent?: ModalCloseIntentVeto;
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
	onCloseIntent,
	title,
	description,
	children,
	size = "default",
	className,
}: ModalProps) {
	const gatePendingRef = useRef(false);
	const mountedRef = useRef(true);
	useEffect(() => {
		mountedRef.current = true;
		return () => {
			mountedRef.current = false;
		};
	}, []);

	const handleOpenChange = useCallback(
		(isOpen: boolean) => {
			if (isOpen) return;
			if (!onCloseIntent) {
				onClose();
				return;
			}
			// The user attempted a close (Esc / overlay / corner X).
			// Radix fires onOpenChange(false) as a close REQUEST — the
			// controlled `open` prop simply doesn't flip while the gate
			// is being evaluated, which keeps the dialog open. While a
			// gate is already pending, further requests are swallowed so
			// double-Esc can't bypass the confirm.
			if (gatePendingRef.current) return;
			gatePendingRef.current = true;
			Promise.resolve()
				.then(() => onCloseIntent())
				.then((allowed) => {
					if (allowed && mountedRef.current) onClose();
				})
				.catch(() => {
					// A rejecting gate vetoes the close.
				})
				.finally(() => {
					gatePendingRef.current = false;
				});
		},
		[onClose, onCloseIntent],
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
 * ConfirmDiscardDialog — thin ConfirmDialog preset for the "you have
 * unsaved edits" veto flow. Centralizes the copy keys so every dialog
 * that gates its close intent presents the same confirm/discard
 * choice.
 */
interface ConfirmDiscardDialogProps {
	open: boolean;
	onDiscard: () => void;
	onStay: () => void;
}

export function ConfirmDiscardDialog({
	open,
	onDiscard,
	onStay,
}: ConfirmDiscardDialogProps) {
	return (
		<ConfirmDialog
			open={open}
			variant="warning"
			title={t("dialog.discardChangesTitle")}
			message={t("dialog.discardChangesMessage")}
			confirmLabel={t("dialog.discardChangesConfirm")}
			cancelLabel={t("dialog.discardChangesStay")}
			onConfirm={onDiscard}
			onCancel={onStay}
		/>
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
