import { useCallback, useRef } from "react";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { t } from "@/i18n/i18n";

interface ConfirmDialogProps {
	open: boolean;
	title?: string;
	message: string;
	confirmLabel?: string;
	cancelLabel?: string;
	variant?: "destructive" | "warning";
	onConfirm: () => void;
	onCancel: () => void;
}

export default function ConfirmDialog({
	open,
	title = t("common.confirm"),
	message,
	confirmLabel = t("common.delete"),
	cancelLabel = t("common.cancel"),
	variant = "destructive",
	onConfirm,
	onCancel,
}: ConfirmDialogProps) {
	// DX-014: Radix AlertDialog fires onOpenChange(false) once per close.
	// We use a ref to distinguish "user clicked Confirm" (which should NOT
	// call onCancel) from Cancel/Escape/backdrop (which should).
	// The old dismissedByButton ref guarded both actions; now only the
	// confirm action needs it.
	const confirmedRef = useRef(false);

	const handleOpenChange = useCallback(
		(isOpen: boolean) => {
			if (!isOpen) {
				if (!confirmedRef.current) {
					onCancel();
				}
				confirmedRef.current = false;
			}
		},
		[onCancel],
	);

	const handleConfirm = useCallback(() => {
		confirmedRef.current = true;
		onConfirm();
	}, [onConfirm]);

	return (
		<AlertDialog open={open} onOpenChange={handleOpenChange}>
			<AlertDialogContent>
				<AlertDialogHeader>
					<AlertDialogTitle>{title}</AlertDialogTitle>
					<AlertDialogDescription>{message}</AlertDialogDescription>
				</AlertDialogHeader>
				<AlertDialogFooter>
					{/* DX-014: Cancel button has NO onClick — handleOpenChange
					    calls onCancel when the dialog closes. Letting Radix
					    trigger onOpenChange(false) is the single close signal. */}
					<AlertDialogCancel aria-label={cancelLabel}>
						{cancelLabel}
					</AlertDialogCancel>
					<AlertDialogAction
						variant={variant === "destructive" ? "destructive" : "default"}
						onClick={handleConfirm}
						aria-label={confirmLabel}
					>
						{confirmLabel}
					</AlertDialogAction>
				</AlertDialogFooter>
			</AlertDialogContent>
		</AlertDialog>
	);
}
