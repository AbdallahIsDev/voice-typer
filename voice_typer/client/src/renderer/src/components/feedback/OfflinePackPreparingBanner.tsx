// OfflinePackPreparingBanner — small "Preparing offline engine…" line shown in
// the mic-test / transcription areas when the runtime pack isn't ready
// yet AND the user has attempted offline transcription.
//
// Plan refs: `upload/plan-runtime-pack-split.md` §4.8 ("The download
// experience (revised)") + §4.9 ("What works without the pack") + §8.4
// (consent gate) + §8.10 (degradation matrix).
//
// §4.8 spec:
//   "No progress bar in the main UI, but a small 'Preparing offline
//    engine…' line appears in the mic test / transcription areas if
//    the user tries to use offline transcription before the pack is
//    ready."
//
// §4.9 degradation matrix:
//   | User action             | Pack present | Pack missing                       |
//   | Mic test page           | full levels  | RMS meter works; VAD "smartness"   |
//   |                         | + VAD sens.  | degrades silently                  |
//   | Cloud transcription     | works        | works — cloud never needs the pack |
//   | Local whisper / Parakeet| instant      | silent download starts,           |
//   |                         |              | "Preparing…" line, then works      |
//
// Per the plan note on mic test: the actual mic test
// (`server/service/microphone_test.py`) uses RMS only — no VAD. So the
// banner on the Microphone page is purely informational ("the offline
// engine that powers transcription is being prepared") and MUST NOT
// imply that the mic test itself is broken or blocked. The level
// meter + test still work without the pack.
//
// ── Visibility contract ──────────────────────────────────────────────
//
// The banner is shown ONLY when BOTH:
//   1. `useOfflinePackDownload().isReady === false` (pack/worker not ready), AND
//   2. The user has attempted offline transcription in the current
//      page (the parent page owns this flag — e.g. Home.tsx sets it
//      when `handleToggle` is invoked, Microphone.tsx sets it when
//      `startTest` is invoked).
//
// Cloud transcription never needs the pack (§4.9), so the parent page
// MAY choose to suppress the banner when the active ASR backend is a
// cloud one (Groq/OpenAI/Deepgram). This banner is purely
// presentational — it doesn't decide that; the parent passes
// `visible={false}` when cloud is in use.
//
// ── A11y ─────────────────────────────────────────────────────────────
//
// The banner is a polite live region (`role="status"` + implicit
// `aria-live="polite"` from `<output>`) so screen readers announce
// "Preparing offline engine…" once when it appears. We do NOT use
// `aria-live="assertive"` — the message is informational, not an error
// that interrupts the user.
//
// The `data-pack-status` attribute exposes the underlying
// `OfflinePackStatus` for integration tests + diagnostic scraping (the test
// suite can assert on the attribute instead of parsing visible text).
//
// ── i18n ─────────────────────────────────────────────────────────────
//
// The visible string is `t("pack.preparingOfflineEngine")` — the
// canonical "Preparing offline engine…" copy. The aria-label is
// `t("pack.preparingOfflineEngineAria")` and includes the status
// (`{status}` placeholder) so AT users get the same diagnostic
// context as the `data-pack-status` attribute. Sub-agent 14 will add
// these keys to all 8 locale files.

import type { OfflinePackStatus } from "@/hooks/useOfflinePackDownload";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

export interface OfflinePackPreparingBannerProps {
	/** When `false`, the banner renders nothing. The parent page is
	 *  responsible for computing visibility (typically
	 *  `!isReady && hasAttemptedOfflineTranscription` and optionally
	 *  `&& !isCloudBackend`). */
	visible: boolean;
	/** The current pack/worker status from `useOfflinePackDownload().status`.
	 *  Exposed via `data-pack-status` for integration tests + the
	 *  aria-label so AT users get the diagnostic context. */
	status: OfflinePackStatus;
	/** Optional extra classes (merged via cn() / tailwind-merge). */
	className?: string;
}

/**
 * Renders the small "Preparing offline engine…" line. Returns `null`
 * when `visible === false` so the parent's layout doesn't reserve
 * space for a hidden element.
 */
export function OfflinePackPreparingBanner({
	visible,
	status,
	className,
}: OfflinePackPreparingBannerProps) {
	if (!visible) return null;

	return (
		<output
			aria-live="polite"
			aria-label={t("pack.preparingOfflineEngineAria", { status })}
			data-pack-status={status}
			className={cn(
				"block text-[13px] text-(--text-muted) animate-fade-in",
				className,
			)}
		>
			{t("pack.preparingOfflineEngine")}
		</output>
	);
}
