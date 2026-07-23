/**
 * Skeleton loading placeholder for settings sections.
 *
 * BACKLOG-008: Settings sections previously returned `null` when config
 * was null (still loading from backend). This caused a visual flash where
 * the section content popped in. The skeleton provides a visible loading
 * state that matches the section's layout.
 */

import { t } from "@/i18n/i18n";

interface SettingsSkeletonProps {
	/** Number of skeleton rows to render. Default 3. */
	rows?: number;
	/** Additional class names. */
	className?: string;
}

export function SettingsSkeleton({
	rows = 3,
	className = "",
}: SettingsSkeletonProps) {
	return (
		<output className={`space-y-3 ${className}`} aria-label={t("a11y.loading")}>
			{Array.from({ length: rows }, (_, i) => (
				<div
					// XS-64: restored biome-ignore — the rule fires under `preset: "recommended"` (the previous `recommended: true` was deprecated and silently skipped enforcement). Skeleton rows are static, identical, and never reorder; index is the only stable key.
					// biome-ignore lint/suspicious/noArrayIndexKey: skeleton rows are static and identical; index is the only stable key
					key={`row-${rows}-${i}`}
					className="flex items-center justify-between gap-4 px-3.5 py-2.5"
				>
					<div className="flex items-center gap-2">
						<div className="h-4 w-24 animate-pulse rounded bg-(--bg-subtle)" />
						<div className="h-4 w-4 animate-pulse rounded bg-(--bg-subtle)" />
					</div>
					<div className="h-6 w-12 animate-pulse rounded bg-(--bg-subtle)" />
				</div>
			))}
		</output>
	);
}
