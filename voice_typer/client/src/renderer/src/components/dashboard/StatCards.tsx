import { Mic02Icon, TextIcon, Time02Icon } from "@hugeicons/core-free-icons";
import { memo } from "react";
import { t } from "@/i18n/i18n";
import { compactNumber, formatDuration } from "@/lib/format";
import type { TodayStats } from "@/types/ipc";
import { StatCard } from "./StatCard";

// ``formatCompactNumber`` was previously defined inline here (with the
// "K+" suffix on remainder) AND separately in Dashboard.tsx (with just
// "K"). Both have been replaced by the shared ``compactNumber`` in
// ``lib/format.ts``. The StatCards legacy behaviour (K+ on remainder,
// locale-aware sub-1000 grouping) is preserved by passing
// ``{ plusSuffix: true, localeAware: true }``.
//
// Exported so the Analytics page's Characters card reuses the SAME
// formatting the Home page's Characters card uses (K-abbreviation +
// rounding config) instead of reimplementing it.
export function formatCompactNumber(n: number): string {
	return compactNumber(n, { plusSuffix: true, localeAware: true });
}

// ``formatDuration`` was previously defined inline here with hardcoded
// English ``"h"`` / ``"m"`` suffixes AND separately in Dashboard.tsx
// with subtly different edge-case handling (the two copies had
// drifted). Both copies are now replaced by the shared
// ``formatDuration`` in ``lib/format.ts``, which resolves the
// ``h`` / ``m`` glyphs through ``t()`` (``analytics.durationHours`` /
// ``durationMinutes`` / ``durationHoursMinutes`` / ``durationZero``)
// so non-English locales see translated suffixes once F1 translates
// the new keys.

// Card labels are i18n-driven. We look up the translation key at
// render time so the active locale is always reflected — the previous
// implementation hard-coded English strings, which broke i18n for es /
// fr / de / ar / hi / zh users.
const CARDS: {
	labelKey:
		| "dashboard.cards.dictations"
		| "dashboard.cards.chars"
		| "dashboard.cards.duration";
	key: keyof TodayStats;
	icon: typeof Mic02Icon;
	format: (v: number) => string;
}[] = [
	{
		labelKey: "dashboard.cards.dictations",
		key: "count",
		icon: Mic02Icon,
		format: formatCompactNumber,
	},
	{
		labelKey: "dashboard.cards.chars",
		key: "chars",
		icon: TextIcon,
		format: formatCompactNumber,
	},
	{
		labelKey: "dashboard.cards.duration",
		key: "duration",
		icon: Time02Icon,
		format: formatDuration,
	},
];

interface StatCardsProps {
	stats: TodayStats;
}

function StatCards({ stats }: StatCardsProps) {
	return (
		<div className="grid grid-cols-3 gap-3">
			{CARDS.map((card) => (
				<StatCard
					key={card.labelKey}
					label={t(card.labelKey)}
					value={card.format(stats[card.key])}
					icon={card.icon}
				/>
			))}
		</div>
	);
}

export default memo(StatCards);
