// SettingsSaveIndicator — the 4-state save indicator shown in the
// sticky header of the Settings page.
//
// Extracted from src/renderer/src/pages/Settings.tsx (PVT-028) so the
// page component is responsible for layout/UX only.
//
// States:
//   saving   → "Saving…" with an amber pulse dot
//   pending  → "Pending…" with a sky-blue pulse dot (PVT-028 Fix #8)
//   saved    → "Saved ✓" with a green Tick02Icon (2s pulse after a
//               successful `set_config` roundtrip)
//   idle     → "All changes saved" (dim)
//
// WCAG 2.1 SC 1.4.4 (text resize): `text-xs` = 12px (was 10px).
// WCAG 2.1 SC 1.4.3 (contrast): full opacity — no `/40`.
// WCAG 2.1 SC 4.1.3 (status messages): `aria-live="polite"` so screen
// readers announce transitions without stealing focus. `aria-atomic`
// ensures the whole string is announced on each change.
//
// NOTE: "Pending…" is a literal English string because the
// `settings.pending` i18n key doesn't exist in en.json yet (adding it
// is out of this fix's file scope). The sky-blue pulse + label
// distinguish this state from "Saving…". When the i18n key lands,
// replace the literal with `t("settings.pending")`.

import { Tick02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { t } from "@/i18n/i18n";

export interface SettingsSaveIndicatorProps {
	saving: boolean;
	pending: boolean;
	saved: boolean;
}

export function SettingsSaveIndicator({
	saving,
	pending,
	saved,
}: SettingsSaveIndicatorProps) {
	return (
		<p
			className="text-xs whitespace-nowrap"
			aria-live="polite"
			aria-atomic="true"
		>
			{saving ? (
				<span className="inline-flex items-center gap-1 text-(--text-secondary)">
					<span
						className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400"
						aria-hidden="true"
					/>
					{t("settings.saving")}
				</span>
			) : pending ? (
				<span className="inline-flex items-center gap-1 text-(--text-secondary)">
					<span
						className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-sky-400"
						aria-hidden="true"
					/>
					Pending…
				</span>
			) : saved ? (
				<span className="inline-flex items-center gap-1 text-(--text-secondary) animate-fade-in">
					<HugeiconsIcon
						icon={Tick02Icon}
						strokeWidth={2.5}
						className="h-3 w-3 text-emerald-500"
						aria-hidden="true"
					/>
					{t("settings.savedToast")}
				</span>
			) : (
				<span className="inline-flex items-center gap-1 text-(--text-muted)">
					{t("settings.allChangesSaved")}
				</span>
			)}
		</p>
	);
}
