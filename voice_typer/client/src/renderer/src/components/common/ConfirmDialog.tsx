import { useCallback, useEffect, useRef } from "react";
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
	/**
	 * Opt-in backdrop-click dismissal: when true, clicking the dimmed
	 * overlay outside the dialog closes it exactly like Cancel (no data
	 * change). Default false preserves the strict AlertDialog contract
	 * (explicit acknowledge only) everywhere else.
	 */
	dismissOnBackdrop?: boolean;
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
	dismissOnBackdrop = false,
	onConfirm,
	onCancel,
}: ConfirmDialogProps) {
	// Radix AlertDialog fires onOpenChange(false) once per close.
	// We use a ref to distinguish "user clicked Confirm" (which should NOT
	// call onCancel) from Cancel/Escape/backdrop (which should).
	// The old dismissedByButton ref guarded both actions; now only the
	// confirm action needs it.
	const confirmedRef = useRef(false);

	// BACKDROP-CLICK DISMISSAL (opt-in via `dismissOnBackdrop`).
	//
	// Radix's AlertDialogContent HARD-REPLACES any caller-supplied
	// `onPointerDownOutside` / `onInteractOutside` with
	// `(event) => event.preventDefault()` (see the alert-dialog source:
	// the AlertDialog contract deliberately forbids outside dismissal,
	// and it does NOT compose with a caller handler — the prop is
	// silently dropped). So a per-dialog onPointerDownOutside is dead
	// code; the only way to get backdrop-click-to-close on an
	// AlertDialog is a document-level pointerdown listener that checks
	// containment against the dialog content ourselves. This mirrors
	// what DismissableLayer does for the regular Dialog, scoped to the
	// dialogs that opt in. Pointer events still reach the document
	// (Radix only disables body pointer-events for hit-testing; the
	// listener fires regardless of what the browser resolves as the
	// target). Escape keeps working through Radix's own key handler →
	// onOpenChange(false) → onCancel, and clicks INSIDE the content
	// (Cancel/Confirm) are excluded by the containment check.
	const alertContentRef = useRef<HTMLElement | null>(null);

	useEffect(() => {
		if (!open || !dismissOnBackdrop) return;
		const onPointerDown = (event: PointerEvent) => {
			const content = alertContentRef.current;
			if (!content) return;
			const target = event.target;
			if (target instanceof Node && !content.contains(target)) {
				onCancel();
			}
		};
		// Capture so we see the pointerdown before anything else can
		// stop it, and before Radix's own outside-interaction handling.
		document.addEventListener("pointerdown", onPointerDown, true);
		return () =>
			document.removeEventListener("pointerdown", onPointerDown, true);
	}, [open, dismissOnBackdrop, onCancel]);

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
			<AlertDialogContent
				// Refs don't forward through the AlertDialogContent
				// wrapper, so resolve the content element via its stable
				// data-slot when the dialog opens. Used by the
				// document-level backdrop listener to exclude in-content
				// clicks (Cancel/Confirm must keep working).
				ref={(el) => {
					alertContentRef.current = el;
				}}
			>
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
						// Destructive confirm: solid, clearly-saturated red
						// background with near-white text at rest (reads
						// unambiguously as danger — the previous
						// bg-destructive/10 wash was a muddy, desaturated
						// red that read as disabled); on hover the red
						// lightens slightly (opacity reduction) as subtle
						// feedback, not a full color swap. Both light and
						// dark variants are overridden (the dark: variants
						// would otherwise win in dark mode via the base
						// destructive cva). Warning keeps its tinted amber
						// wash + primary-text label.
						className={cn(
							variant === "destructive" &&
								"bg-destructive text-destructive-foreground hover:bg-destructive/85 dark:bg-destructive dark:hover:bg-destructive/85",
							variant === "warning" && "text-(--text-primary)",
						)}
					>
						{confirmLabel}
					</AlertDialogAction>
				</AlertDialogFooter>
			</AlertDialogContent>
		</AlertDialog>
	);
}
