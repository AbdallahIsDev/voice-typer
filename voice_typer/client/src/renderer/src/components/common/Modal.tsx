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

export type ModalCloseIntentVeto = () => boolean | Promise<boolean>;

interface ModalProps {
	/** Whether the dialog is open */
	open: boolean;
	/** Called when the user dismisses the dialog (Escape, backdrop click, Cancel) */
	onClose: () => void;
	onCloseIntent?: ModalCloseIntentVeto;
	/** Dialog title (sets aria-labelledby). */
	title?: string;
	/** Optional description (sets aria-describedby) */
	description?: string;
	/** Content children, typically the message body + ModalFooter */
	children: React.ReactNode;
	/** Optional size override, `lg` is a roomier panel (max-w-xl on
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
			// Radix fires onOpenChange(false) as a close REQUEST, the
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

export { DialogFooter as ModalFooter };
