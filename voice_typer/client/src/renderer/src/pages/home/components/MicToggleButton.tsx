import {
	AlertCircleIcon,
	Mic02Icon,
	StopIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo } from "react";
import { cn } from "@/lib/utils";

/**
 * The large circular mic toggle button. Pulses while recording. A
 * spinner overlay is shown while `toggling` is true (the IPC round-trip
 * to `toggle_dictation` is in flight). The button is disabled during
 * `transcribing` so clicks aren't silently swallowed by the backend.
 *
 * When `error` is true (the last recording attempt failed), the idle
 * treatment swaps from the solid destructive glow to a hollow
 * destructive surface with an alert glyph — a distinct state that
 * reads as "the last attempt errored", announced politely to screen
 * readers.
 *
 *  / : extracted from Home.tsx so the page file stays a
 * thin composition root. Behaviour + props are preserved byte-for-byte.
 */
export interface MicToggleButtonProps {
	isRecording: boolean;
	toggling: boolean;
	disabled: boolean;
	onClick: () => void;
	label: string;
	/**
	 * Why the button is currently disabled. When `disabled` is true
	 * and `disabledReason` is provided, it replaces `label` as the
	 * `aria-label` and `title` so screen readers / hover tooltips
	 * explain why the action is unavailable (e.g. "Transcribing…
	 * please wait") instead of repeating the now-unusable action
	 * label. When `disabled` is false this prop is ignored.
	 */
	disabledReason?: string;
	/**
	 * The last recording attempt failed. Renders the idle button with a
	 * distinct hollow-destructive treatment + alert glyph and exposes
	 * `aria-live="polite"` so the transition is announced. Ignored while
	 * recording (the active recording state takes precedence).
	 */
	error?: boolean;
}

export function MicToggleButton({
	isRecording,
	toggling,
	disabled,
	onClick,
	label,
	disabledReason,
	error = false,
}: MicToggleButtonProps) {
	// When disabled with a reason, surface the reason as the accessible
	// name + tooltip so users understand why the mic can't be toggled
	// right now (the inline `<p>` in Home.tsx mirrors the same text
	// visually, but the button itself must remain self-describing for
	// screen-reader users who focus it directly).
	const effectiveLabel = disabled && disabledReason ? disabledReason : label;

	// LO-22: use `aria-disabled` + a click guard instead of the native
	// `disabled` attribute so the button stays hoverable/focusable — the
	// `title` tooltip (carrying `disabledReason`) must remain readable on
	// a disabled mic, which a native `disabled` attribute suppresses.
	// Screen readers still announce the disabled state via aria-disabled.
	const handleClick = () => {
		if (disabled) return;
		onClick();
	};

	const showError = error && !isRecording;

	return (
		<div className="relative">
			{isRecording && (
				<span className="absolute inset-0 rounded-full animate-pulse-ring" />
			)}
			<button
				type="button"
				onClick={handleClick}
				aria-disabled={disabled || undefined}
				aria-label={effectiveLabel}
				aria-pressed={isRecording}
				title={effectiveLabel}
				aria-live={showError ? "polite" : undefined}
				data-testid="mic-toggle-button"
				className={cn(
					"press-scale relative z-10 flex h-21 w-21 items-center justify-center rounded-full",
					"transition-all duration-200 ease-out",
					"focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
					"hover:scale-105",
					isRecording
						? "bg-foreground/15 hover:bg-foreground/25"
						: showError
							? // Error state: hollow destructive — a distinct
								// "last attempt failed" treatment next to the
								// solid glow of the healthy idle button.
								"bg-destructive/15 ring-2 ring-inset ring-destructive hover:bg-destructive/25"
							: "bg-destructive animate-glow-pulse hover:shadow-[0_8px_32px_rgba(255,51,51,0.5)]",
				)}
			>
				<HugeiconsIcon
					icon={
						isRecording ? StopIcon : showError ? AlertCircleIcon : Mic02Icon
					}
					strokeWidth={1.625}
					// The mic lives on the red (destructive) button in the
					// idle state, so its glyph uses the destructive
					// foreground token — near-white in BOTH light and dark
					// (index.css defines --destructive-foreground as
					// oklch(0.97 0 0) in :root AND .dark, and every theme
					// preset + the custom-theme generator backfill it).
					// `text-(--text-primary)` turned black in light mode;
					// raw `text-white` ignored custom palettes entirely.
					// The stop icon on the recording state
					// (bg-foreground/15) keeps the same near-white glyph.
					// The error-state alert glyph sits on the hollow
					// surface, so it uses the destructive TOKEN itself
					// (tracks every theme's destructive red).
					className={cn(
						"h-8 w-8 transition-opacity",
						isRecording || showError
							? "text-destructive"
							: "text-(--destructive-foreground)",
						toggling && "opacity-30",
					)}
				/>
				{toggling && (
					<span
						aria-hidden
						className="pointer-events-none absolute inset-0 flex items-center justify-center"
					>
						<span className="h-7 w-7 animate-spin rounded-full border-2 border-white/80 border-t-transparent" />
					</span>
				)}
			</button>
		</div>
	);
}

export default memo(MicToggleButton);
