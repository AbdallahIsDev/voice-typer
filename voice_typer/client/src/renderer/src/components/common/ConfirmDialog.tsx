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
import type { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";

interface ConfirmDialogProps {
	open: boolean;
	title?: string;
	message: string;
	confirmLabel?: string;
	cancelLabel?: string;
	/**
	 * Visual variant forwarded to the underlying {@link Button}. Defaults
	 * to `"destructive"` for the common "delete / discard" case. Pass
	 * `"warning"` for mid-tier destructive actions (e.g. skip onboarding)
	 * or any other Button variant the call site needs.
	 */
	variant?: React.ComponentProps<typeof Button>["variant"];
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
	// Radix AlertDialog fires onOpenChange(false) once per close.
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
					{/* Cancel button has NO onClick — handleOpenChange
                                            calls onCancel when the dialog closes. Letting Radix
                                            trigger onOpenChange(false) is the single close signal. */}
					<AlertDialogCancel>{cancelLabel}</AlertDialogCancel>
					<AlertDialogAction
						// map variant through to the button's
						// cva variant. destructive → destructive, warning →
						// warning (the warning variant in button.tsx uses the
						// --warning design token so the amber tint tracks the
						// active theme), and any other value → default.
						// Previously `warning` fell through to `default`,
						// making the confirm button visually identical to a
						// safe primary action.
						variant={variant}
						onClick={handleConfirm}
					>
						{confirmLabel}
					</AlertDialogAction>
				</AlertDialogFooter>
			</AlertDialogContent>
		</AlertDialog>
	);
}
