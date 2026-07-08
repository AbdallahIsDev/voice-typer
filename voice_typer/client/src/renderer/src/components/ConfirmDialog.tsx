import { useCallback, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

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
	const dialogRef = useRef<HTMLDivElement>(null);
	const cancelRef = useRef<HTMLButtonElement>(null);

	// Auto-focus the cancel button on open so Enter doesn't accidentally confirm
	useEffect(() => {
		if (open) {
			// Small delay to let the portal mount
			const timer = setTimeout(() => {
				cancelRef.current?.focus();
			}, 50);
			return () => clearTimeout(timer);
		}
	}, [open]);

	// #9: Focus trap — Tab / Shift+Tab cycles within the dialog
	const handleKeyDown = useCallback(
		(e: React.KeyboardEvent) => {
			if (e.key === "Escape") {
				e.preventDefault();
				onCancel();
				return;
			}
			if (e.key !== "Tab") return;

			const dialog = dialogRef.current;
			if (!dialog) return;

			const focusable = dialog.querySelectorAll<HTMLElement>(
				'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
			);
			if (focusable.length === 0) return;

			const first = focusable[0];
			const last = focusable[focusable.length - 1];

			if (e.shiftKey) {
				// Shift+Tab: if on first element, wrap to last
				if (document.activeElement === first) {
					e.preventDefault();
					last.focus();
				}
			} else {
				// Tab: if on last element, wrap to first
				if (document.activeElement === last) {
					e.preventDefault();
					first.focus();
				}
			}
		},
		[onCancel],
	);

	const handleBackdropClick = (e: React.MouseEvent) => {
		if (e.target === e.currentTarget) onCancel();
	};

	if (!open) return null;

	return (
		<div
			ref={dialogRef}
			role="dialog"
			aria-modal="true"
			aria-labelledby="confirm-dialog-title"
			aria-describedby="confirm-dialog-desc"
			className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
			onClick={handleBackdropClick}
			onKeyDown={handleKeyDown}
		>
			<div
				className={cn(
					"animate-scale-in w-96 rounded-xl border border-border",
					"bg-(--bg) p-6",
				)}
			>
				<h2
					id="confirm-dialog-title"
					className="mb-2 text-lg font-semibold text-(--text-primary)"
				>
					{title}
				</h2>
				<p
					id="confirm-dialog-desc"
					className="mb-5 text-sm text-(--text-muted)"
				>
					{message}
				</p>
				<div className="flex justify-end gap-3">
					<Button
						variant="ghost"
						onClick={onCancel}
						ref={cancelRef}
						aria-label={cancelLabel}
					>
						{cancelLabel}
					</Button>
					<Button
						variant={variant === "destructive" ? "destructive" : "default"}
						onClick={onConfirm}
						aria-label={confirmLabel}
					>
						{confirmLabel}
					</Button>
				</div>
			</div>
		</div>
	);
}
