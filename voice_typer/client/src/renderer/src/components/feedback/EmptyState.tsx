import { Add01Icon, Alert02Icon } from "@hugeicons/core-free-icons";
import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";
import type { ReactNode, RefObject } from "react";
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
	/**
	 * Optional ref forwarded to the action `<Button>`. Callers can use
	 * this to programmatically focus the action (e.g.
	 * ConnectionStatusScreen focuses the Retry button when the backend
	 * disconnects) without resorting to a brittle
	 * `document.querySelector` lookup. Only forwarded when both
	 * `actionLabel` and `onAction` are provided.
	 */
	actionRef?: RefObject<HTMLButtonElement | null>;
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
	icon,
	title,
	description,
	actionLabel,
	onAction,
	actionIcon,
	actionRef,
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
			role={isError ? "alert" : "status"}
			className={cn(
				"flex flex-col items-center justify-center gap-4 py-16",
				// Error variant: tinted ring + soft destructive wash so
				// load failures don't masquerade as "no data yet".
				isError &&
					"rounded-xl border border-destructive/40 bg-destructive/5 px-6",
			)}
		>
			<HugeiconsIcon
				icon={isError ? Alert02Icon : icon}
				strokeWidth={2}
				className={cn(
					"h-10 w-10",
					isError
						? // No opacity wash for the error variant —
							// destructive token already carries enough
							// contrast, and stacking opacity on top
							// pushes the icon below WCAG 1.4.11.
							"text-destructive"
						: // No opacity wash for the info variant either —
							// text-(--text-muted) alone carries the visual
							// hierarchy. Stacking opacity on top of the
							// already-muted token pushed the icon below the
							// WCAG 1.4.11 non-text contrast minimum (3:1)
							// (same rationale as the description below).
							"text-(--text-muted)",
				)}
			/>
			{/* Title is rendered as an <h3> (not a <p>) so screen-reader
			    users can navigate empty-state cards by heading. The heading
			    level (h3) is chosen to sit below the typical page <h1>/<h2>
			    hierarchy used across the app. */}
			<h3 className="text-sm text-(--text-muted)">{title}</h3>
			{/* Dropped opacity-70 — text-(--text-muted) is already a
			    low-contrast token, and stacking opacity on top pushed the
			    effective contrast below WCAG AA for body text. */}
			{description && (
				<p className="text-xs text-(--text-muted)">{description}</p>
			)}
			{children}
			{actionLabel && onAction && (
				<Button
					ref={actionRef}
					variant="default"
					className="gap-2"
					onClick={onAction}
				>
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
