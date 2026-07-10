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
	title = "Confirm",
	message,
	confirmLabel = "Delete",
	cancelLabel = "Cancel",
	variant = "destructive",
	onConfirm,
	onCancel,
}: ConfirmDialogProps) {
	const dismissedByButton = useRef(false);

	const handleCancel = useCallback(() => {
		dismissedByButton.current = true;
		onCancel();
	}, [onCancel]);

	const handleConfirm = useCallback(() => {
		dismissedByButton.current = true;
		onConfirm();
	}, [onConfirm]);

	const handleOpenChange = useCallback(
		(isOpen: boolean) => {
			if (!isOpen && !dismissedByButton.current) {
				onCancel(); // Escape key or backdrop click
			}
			dismissedByButton.current = false;
		},
		[onCancel],
	);

	return (
		<AlertDialog open={open} onOpenChange={handleOpenChange}>
			<AlertDialogContent onInteractOutside={handleCancel}>
				<AlertDialogHeader>
					<AlertDialogTitle>{title}</AlertDialogTitle>
					<AlertDialogDescription>{message}</AlertDialogDescription>
				</AlertDialogHeader>
				<AlertDialogFooter>
					<AlertDialogCancel onClick={handleCancel} aria-label={cancelLabel}>
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
