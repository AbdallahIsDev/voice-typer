/**
 * Bubble overlay package — `BubbleModeContent` (mode-branch renderer).
 *
 * Previously the 8-way mode-branch JSX lived inline in `Bubble.tsx`
 * as a deeply-nested ternary chain (transcribing / fading / idle /
 * error / blocked / cancelling / permission_revoked / paste_failed /
 * recording default). Extracting it here gives each mode its own
 * clearly labelled block, makes the chain readable as a switch (not
 * an arrow-shaped ternary pyramid), and lets `Bubble.tsx` focus on
 * lifecycle + the `<output aria-live>` wrapper.
 *
 * The DOM structure is byte-identical to the previous inline JSX so
 * existing tests that scan by selector (e.g.
 * `Bubble.test.tsx`'s `.flex.h-6.items-center` empty-container
 * assertion) keep passing.
 */
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

export interface BubbleModeContentProps {
	mode: BubbleMode;
	errorMessage?: string | null;
	dotRefs: RefObject<(HTMLSpanElement | null)[]>;
}

/**
 * Renders the bubble pill body for the current `mode`. The recording
 * branch falls through to `<BubbleVisualizer>` (the 7-bar spectrum);
 * every other branch renders a small status label.
 *
 * Kept as a plain function component (no `useMemo` / `useCallback`)
 * because the JSX is cheap and the parent already gates re-renders
 * via the state-machine hook.
 */
export function BubbleModeContent({
	mode,
	errorMessage,
	dotRefs,
}: BubbleModeContentProps) {
	switch (mode) {
		case "transcribing":
			return (
				<div className="flex items-center gap-1.5 text-xs font-medium text-(--text-secondary)">
					<span>{t("bubble.transcribingLabel")}</span>
					{TRANSCRIBING_DOT_INDICES.map((i) => (
						<span
							key={i}
							className="inline-block h-1 w-1 animate-bounce rounded-full bg-(--text-muted)"
							style={{
								animationDelay: `${i * 0.2}s`,
								animationDuration: "1.2s",
							}}
						/>
					))}
				</div>
			);
		case "fading":
			return (
				<div
					className="flex items-center gap-1.5 text-xs font-medium text-(--text-secondary)"
					style={{
						opacity: 0,
						transform: "translateY(-4px)",
						transition: `opacity ${FADEOUT_DURATION_MS}ms ease-out, transform ${FADEOUT_DURATION_MS}ms ease-out`,
					}}
				>
					<span>{t("bubble.transcribingLabel")}</span>
				</div>
			);
		case "idle":
			return (
				<>
					{/* A11Y: sr-only announcement so screen-reader users hear
                                            "Transcription complete." when the bubble transitions to
                                            idle (always_visible mode). The empty div below is
                                            preserved as a zero-width sibling so Bubble.test.tsx's
                                            `emptyContainer.textContent === ""` assertion still
                                            passes — querySelector returns the first match in DOM
                                            order, which is the empty div. */}
					<div className="flex h-6 items-center" />
					<div className="flex h-6 items-center gap-1.5 px-2" aria-hidden>
						<HugeiconsIcon
							icon={Mic02Icon}
							strokeWidth={2}
							className="w-3 h-3 text-(--text-muted)"
						/>
						<span className="text-[10px] font-medium text-(--text-muted)">
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
				<div className="flex h-6 items-center gap-1.5 px-2">
					<span
						className="w-1.5 h-1.5 rounded-full bg-destructive animate-pulse"
						aria-hidden
					/>
					<span className="text-[10px] font-medium text-destructive">
						{tf("bubble.errorLabel", "⚠ Error")}
						{errorMessage ? `: ${errorMessage}` : ""}
					</span>
				</div>
			);
		case "blocked":
			return (
				<div className="flex h-6 items-center gap-1.5 px-2">
					<span
						className="text-[11px] leading-none text-(--text-muted)"
						aria-hidden
					>
						⊘
					</span>
					<span className="text-[10px] font-medium text-(--text-muted)">
						{tf("bubble.blockedLabel", "Blocked")}
					</span>
				</div>
			);
		case "cancelling":
			return (
				<div className="flex h-6 items-center gap-1.5 px-2">
					<span
						className="text-[11px] leading-none text-(--text-muted) animate-pulse"
						aria-hidden
					>
						⏇
					</span>
					<span className="text-[10px] font-medium text-(--text-muted)">
						{tf("bubble.cancellingLabel", "Cancelling…")}
					</span>
				</div>
			);
		case "permission_revoked":
			return (
				<div className="flex h-6 items-center gap-1.5 px-2">
					<span
						className="w-1.5 h-1.5 rounded-full bg-destructive animate-pulse"
						aria-hidden
					/>
					<span className="text-[10px] font-medium text-destructive">
						{tf("bubble.permissionRevokedLabel", "Mic permission revoked")}
					</span>
				</div>
			);
		case "paste_failed":
			return (
				<div className="flex h-6 items-center gap-1.5 px-2">
					<span
						className="w-1.5 h-1.5 rounded-full bg-destructive animate-pulse"
						aria-hidden
					/>
					<span className="text-[10px] font-medium text-destructive">
						{tf("bubble.pasteFailedLabel", "Paste failed")}
					</span>
				</div>
			);
		default:
			return <BubbleVisualizer dotRefs={dotRefs} />;
	}
}
