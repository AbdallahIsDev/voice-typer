// OfflinePackPreparingBanner, small "Preparing offline engine…" line shown in
// the mic-test / transcription areas when the runtime pack isn't ready
// yet AND the user has attempted offline transcription.
// Plan refs: `upload/plan-runtime-pack-split.md` §4.8 + §4.9 + §8.4 + §8.10.
// ── Recovery action ──────────────────────────────────────────────────
// When status is `missing` / `failed` / `corrupt`, the silent download
// has nothing left to do on its own. The banner shows a compact
// "Download offline engine" action that calls `check_offline_pack_update`
// so the user can recover without restarting. Transient states
// (downloading / verifying / worker-starting) stay passive per §4.8
// (no progress bar).
// ── A11y / i18n ─────────────────────────────────────────────────────
// Polite live region. Recovery button uses `pack.downloadOfflineEngine` /
// `pack.downloadOfflineEngineBusy` / `pack.downloadOfflineEngineAria`
// (all 8 locales).

import { useCallback, useEffect, useRef, useState } from "react";
import type { OfflinePackStatus } from "@/hooks/useOfflinePackDownload";
import { usePython } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

const RECOVERY_STATUSES: ReadonlySet<OfflinePackStatus> = new Set([
	"missing",
	"failed",
	"corrupt",
]);

export interface OfflinePackPreparingBannerProps {
	/** When `false`, the banner renders nothing. */
	visible: boolean;
	/** Current pack/worker status (also on `data-pack-status`). */
	status: OfflinePackStatus;
	/** Optional extra classes (merged via cn() / tailwind-merge). */
	className?: string;
}

export function OfflinePackPreparingBanner({
	visible,
	status,
	className,
}: OfflinePackPreparingBannerProps) {
	const { call } = usePython();
	const [retrying, setRetrying] = useState(false);
	const aliveRef = useRef(true);
	useEffect(() => {
		aliveRef.current = true;
		return () => {
			aliveRef.current = false;
		};
	}, []);
	const showRecovery = RECOVERY_STATUSES.has(status);

	const onDownload = useCallback(async () => {
		if (retrying) return;
		setRetrying(true);
		try {
			await call("check_offline_pack_update", {});
		} catch {
			// Bridge missing / IPC failure: user can click again.
		} finally {
			if (aliveRef.current) setRetrying(false);
		}
	}, [call, retrying]);

	if (!visible) return null;

	return (
		<output
			aria-live="polite"
			aria-label={t("pack.preparingOfflineEngineAria", { status })}
			data-pack-status={status}
			className={cn(
				"flex flex-wrap items-center gap-2 text-xs-plus text-muted-foreground animate-fade-in",
				className,
			)}
		>
			<span>{t("pack.preparingOfflineEngine")}</span>
			{showRecovery && (
				<button
					type="button"
					onClick={onDownload}
					disabled={retrying}
					aria-label={t("pack.downloadOfflineEngineAria")}
					className="rounded-md border border-border/10 bg-surface px-2 py-0.5 text-xs text-foreground hover:bg-foreground/5 focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
				>
					{retrying
						? t("pack.downloadOfflineEngineBusy")
						: t("pack.downloadOfflineEngine")}
				</button>
			)}
		</output>
	);
}
