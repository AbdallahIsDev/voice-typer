import { Add01Icon, Alert02Icon } from "@hugeicons/core-free-icons";
import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type EmptyStateVariant = "info" | "error";

interface EmptyStateProps {
	icon: IconSvgElement;
	title: string;
	description?: string;
	/** Optional action button label */
	actionLabel?: string;
	/** Optional action button click handler */
	onAction?: () => void;
	/** Optional — overrides the default Add01Icon for the action button */
	actionIcon?: IconSvgElement;
	/** Optional extra content below the description */
	children?: ReactNode;
	/**
	 * Visual variant. ``"info"`` (default) renders the muted, neutral
	 * placeholder used for "no items yet" states. ``"error"`` switches
	 * the icon to ``--destructive`` and wraps the card in a destructive
	 * tinted ring so failure states (e.g. "failed to load vocabulary")
	 * are visually distinct from genuine empty states — without this,
	 * a load failure looks identical to "you haven't added anything
	 * yet", which sends the user down the wrong recovery path.
	 */
	variant?: EmptyStateVariant;
}

export function EmptyState({
	icon: _icon,
	title,
	description,
	actionLabel,
	onAction,
	actionIcon,
	children,
	variant = "info",
}: EmptyStateProps) {
	const displayIcon = actionIcon ?? Add01Icon;
	const isError = variant === "error";
	// For the action button: when the empty state represents a failure,
	// the CTA is typically "Retry" / "Refresh" — surface that with the
	// Alert02Icon instead of the default Add01Icon so the affordance
	// matches the context.
	const actionGlyph = isError ? Alert02Icon : displayIcon;
	return (
		<div
			className={cn(
				"flex flex-col items-center justify-center gap-4 py-16",
				// Error variant: tinted ring + soft destructive wash so
				// load failures don't masquerade as "no data yet".
				isError &&
					"rounded-xl border border-destructive/40 bg-destructive/5 px-6",
			)}
		>
			<HugeiconsIcon
				icon={isError ? Alert02Icon : _icon}
				strokeWidth={2}
				className={cn(
					"h-10 w-10",
					isError
						? // No opacity wash for the error variant —
							// destructive token already carries enough
							// contrast, and stacking opacity on top
							// pushes the icon below WCAG 1.4.11.
							"text-destructive"
						: // NF-R15-16: opacity-50 (up from 30) so the icon passes WCAG
							// 1.4.11 non-text contrast (3:1) against typical backgrounds.
							"text-(--text-muted) opacity-50",
				)}
			/>
			<p className="text-sm text-(--text-muted)">{title}</p>
			{/* NF-R15-16: dropped opacity-70 — text-(--text-muted) is already
                            a low-contrast token, and stacking opacity on top pushed the
                            effective contrast below WCAG AA for body text. */}
			{description && (
				<p className="text-xs text-(--text-muted)">{description}</p>
			)}
			{children}
			{actionLabel && onAction && (
				<Button variant="default" className="mt-2 gap-2" onClick={onAction}>
					<HugeiconsIcon
						icon={actionGlyph}
						strokeWidth={2}
						className="h-4 w-4"
					/>
					{actionLabel}
				</Button>
			)}
		</div>
	);
}
