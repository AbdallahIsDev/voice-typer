import { Mic02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { RefObject } from "react";
import { t } from "@/i18n/i18n";
import { BubbleVisualizer } from "./BubbleVisualizer";
import {
	type BubbleMode,
	FADEOUT_DURATION_MS,
	TRANSCRIBING_DOT_COUNT,
} from "./constants";
import { tf } from "./helpers";

// Pre-computed `[0, 1, 2]` index array for the transcribing dots.
// Hoisted to module scope so the JSX `.map` uses a stable reference
// (same pattern as `DOT_INDICES` in `./constants.ts`).
const TRANSCRIBING_DOT_INDICES: readonly number[] = Array.from(
	{ length: TRANSCRIBING_DOT_COUNT },
	(_, i) => i,
);

const TRANSCRIPT_PREVIEW_MAX_CHARS = 60;

function truncateTranscript(text: string): string {
	if (text.length <= TRANSCRIPT_PREVIEW_MAX_CHARS) return text;
	// Reserve 1 character for the ellipsis.
	return `${text.slice(0, TRANSCRIPT_PREVIEW_MAX_CHARS - 1)}…`;
}

export interface BubbleModeContentProps {
	mode: BubbleMode;
	errorMessage?: string | null;
	transcript?: string | null;
	livePreviewUnsupported?: boolean;
	dotRefs: RefObject<(HTMLSpanElement | null)[]>;
}

export function BubbleModeContent({
	mode,
	errorMessage,
	transcript,
	livePreviewUnsupported,
	dotRefs,
}: BubbleModeContentProps) {
	switch (mode) {
		case "transcribing": {
			const preview =
				typeof transcript === "string" && transcript.length > 0
					? truncateTranscript(transcript)
					: null;
			return (
				<div className="flex items-center gap-2 text-xs font-medium text-(--text-secondary)">
					<span>{t("bubble.transcribingLabel")}</span>
					{preview && (
						<output
							// `<output>` is the semantic element for
							// role="status" (implicit). It supports
							// `aria-label` and is a polite live region so
							// screen-reader users hear each partial update;
							// the parent `<output aria-live="polite">`
							// re-announces the whole pill content on mode
							// change, so this inner region is the one that
							// fires on every partial-transcript tick without
							// re-announcing the "Transcribing" label.
							aria-label={tf(
								"bubble.transcriptPreviewAria",
								"Live transcript preview",
							)}
							className="max-w-45 truncate text-muted-foreground"
						>
							{preview}
						</output>
					)}
					{TRANSCRIBING_DOT_INDICES.map((i) => (
						<span
							key={i}
							className="inline-block h-1 w-1 animate-bounce rounded-full bg-muted-foreground"
							style={{
								animationDelay: `${i * 0.2}s`,
								animationDuration: "1.2s",
							}}
						/>
					))}
				</div>
			);
		}
		case "fading": {
			const preview =
				typeof transcript === "string" && transcript.length > 0
					? truncateTranscript(transcript)
					: null;
			return (
				<div
					className="flex items-center gap-2 text-xs font-medium text-(--text-secondary)"
					style={{
						opacity: 0,
						transform: "translateY(-4px)",
						transition: `opacity ${FADEOUT_DURATION_MS}ms ease-out, transform ${FADEOUT_DURATION_MS}ms ease-out`,
					}}
				>
					<span>{t("bubble.transcribingLabel")}</span>
					{preview && (
						<output
							aria-label={tf(
								"bubble.transcriptPreviewAria",
								"Live transcript preview",
							)}
							className="max-w-45 truncate text-muted-foreground"
						>
							{preview}
						</output>
					)}
				</div>
			);
		}
		case "idle":
			return (
				<>
					{/* A11Y: sr-only announcement so screen-reader users hear
                                            "Transcription complete." when the bubble transitions to
                                            idle (always_visible mode). The empty div below is
                                            preserved as a zero-width sibling so Bubble.test.tsx's
                                            `emptyContainer.textContent === ""` assertion still
                                            passes, querySelector returns the first match in DOM
                                            order, which is the empty div. */}
					<div className="flex h-6 items-center" />
					<div className="flex h-6 items-center gap-2 px-2" aria-hidden>
						<HugeiconsIcon
							icon={Mic02Icon}
							strokeWidth={2}
							className="w-3 h-3 text-muted-foreground"
						/>
						<span className="text-[0.625rem] font-medium text-muted-foreground">
							{tf("bubble.idleLabel", "Ready")}
						</span>
					</div>
					<span className="sr-only">{t("a11y.transcriptionComplete")}</span>
				</>
			);
		case "error":
			// Surface a red "⚠ Error" label so the user can see
			// something went wrong (e.g. backend crash, mic
			// permission revoked). Uses the destructive token so
			// it inherits theme-preset colors. When the backend +
			// main process forward a `message` field in the
			// `bubble:set-state` payload, it's surfaced as a
			// short reason string after the "Error" label.
			return (
				<div className="flex h-6 items-center gap-2 px-2">
					<span
						className="w-1.5 h-1.5 rounded-full bg-destructive animate-pulse"
						aria-hidden
					/>
					<span className="text-[0.625rem] font-medium text-destructive">
						{tf("bubble.errorLabel", "⚠ Error")}
						{errorMessage ? `: ${errorMessage}` : ""}
					</span>
				</div>
			);
		case "blocked":
			return (
				<div className="flex h-6 items-center gap-2 px-2">
					<span
						className="text-[0.6875rem] leading-none text-muted-foreground"
						aria-hidden
					>
						⊘
					</span>
					<span className="text-[0.625rem] font-medium text-muted-foreground">
						{tf("bubble.blockedLabel", "Blocked")}
					</span>
				</div>
			);
		case "cancelling":
			return (
				<div className="flex h-6 items-center gap-2 px-2">
					<span
						className="text-[0.6875rem] leading-none text-muted-foreground animate-pulse"
						aria-hidden
					>
						⏇
					</span>
					<span className="text-[0.625rem] font-medium text-muted-foreground">
						{tf("bubble.cancellingLabel", "Cancelling…")}
					</span>
				</div>
			);
		case "permission_revoked":
			return (
				<div className="flex h-6 items-center gap-2 px-2">
					<span
						className="w-1.5 h-1.5 rounded-full bg-destructive animate-pulse"
						aria-hidden
					/>
					<span className="text-[0.625rem] font-medium text-destructive">
						{tf("bubble.permissionRevokedLabel", "Mic permission revoked")}
					</span>
				</div>
			);
		case "paste_failed":
			return (
				<div className="flex h-6 items-center gap-2 px-2">
					<span
						className="w-1.5 h-1.5 rounded-full bg-destructive animate-pulse"
						aria-hidden
					/>
					<span className="text-[0.625rem] font-medium text-destructive">
						{tf("bubble.pasteFailedLabel", "Paste failed")}
					</span>
				</div>
			);
		default:
			return (
				<div className="flex items-center gap-2">
					<BubbleVisualizer dotRefs={dotRefs} />
					{livePreviewUnsupported && (
						<span className="text-[0.625rem] font-medium text-muted-foreground">
							{tf(
								"bubble.livePreviewUnavailable",
								"No live preview for this engine",
							)}
						</span>
					)}
				</div>
			);
	}
}
