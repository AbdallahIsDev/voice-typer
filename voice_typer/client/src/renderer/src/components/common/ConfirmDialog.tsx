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
import { cn } from "@/lib/utils";

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
	// Generic default so non-delete callers (skip onboarding, discard
	// draft, etc.) don't get a misleading "Delete" button. Delete
	// callers pass `confirmLabel={t("common.delete")}` explicitly.
	confirmLabel = t("common.confirm"),
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
					{/* Tertiary treatment (ghost + muted-until-hover), NOT the
					    default outline variant: no border, no fill, muted
					    text at rest, brightening to the primary text colour
					    (with a subtle background) on hover — consistent with
					    the app's secondary buttons (e.g. the header Import /
					    Export controls). The old outline styling kept a
					    visible border + solid white text at all times, which
					    competed with the destructive action for prominence. */}
					<AlertDialogCancel
						variant="ghost"
						className="text-(--text-muted) hover:bg-foreground/5 hover:text-(--text-primary)"
					>
						{cancelLabel}
					</AlertDialogCancel>
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
						// Destructive/warning actions render on a tinted
						// background (bg-destructive/10 / bg-warning/15). The
						// variant's own tinted text (text-destructive /
						// text-warning) sits on that same-colour wash and reads
						// poorly (red-on-red). Override the label to the
						// primary text colour — white on the dark theme's
						// maroon wash, near-black on the light theme's pink
						// wash — keeping the tinted background as the
						// destructive signal while making the label legible at
						// rest, not just on hover.
						className={cn(
							(variant === "destructive" || variant === "warning") &&
								"text-(--text-primary)",
						)}
					>
						{confirmLabel}
					</AlertDialogAction>
				</AlertDialogFooter>
			</AlertDialogContent>
		</AlertDialog>
	);
}
