import { Mic02Icon, StopIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo } from "react";
import { cn } from "@/lib/utils";

/**
 * The large circular mic toggle button. Pulses while recording. A
 * spinner overlay is shown while `toggling` is true (the IPC round-trip
 * to `toggle_dictation` is in flight). The button is disabled during
 * `transcribing` so clicks aren't silently swallowed by the backend.
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
}

export function MicToggleButton({
	isRecording,
	toggling,
	disabled,
	onClick,
	label,
	disabledReason,
}: MicToggleButtonProps) {
	// When disabled with a reason, surface the reason as the accessible
	// name + tooltip so users understand why the mic can't be toggled
	// right now (the inline `<p>` in Home.tsx mirrors the same text
	// visually, but the button itself must remain self-describing for
	// screen-reader users who focus it directly).
	const effectiveLabel = disabled && disabledReason ? disabledReason : label;
	return (
		<div className="relative">
			{isRecording && (
				<span className="absolute inset-0 rounded-full animate-pulse-ring" />
			)}
			<button
				type="button"
				onClick={onClick}
				disabled={disabled}
				aria-label={effectiveLabel}
				aria-pressed={isRecording}
				title={effectiveLabel}
				className={cn(
					"press-scale relative z-10 flex h-21 w-21 items-center justify-center rounded-full",
					"transition-all duration-200 ease-out",
					"focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
					"hover:scale-105",
					isRecording
						? "bg-foreground/15 hover:bg-foreground/25"
						: "bg-destructive animate-glow-pulse hover:shadow-[0_8px_32px_rgba(255,51,51,0.5)]",
				)}
			>
				<HugeiconsIcon
					icon={isRecording ? StopIcon : Mic02Icon}
					strokeWidth={1.625}
					// The mic lives on the red (destructive) button, so it
					// stays white in BOTH themes — `text-(--text-primary)`
					// turned black in light mode. The stop icon on the
					// recording state is white too.
					className={cn(
						"h-8 w-8 transition-opacity text-white",
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
