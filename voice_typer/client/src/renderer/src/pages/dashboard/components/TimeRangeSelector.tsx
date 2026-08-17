// Time-range selector for the Dashboard.
//
// A single control (Today / 7 Days / 30 Days / All Time) that drives
// the stat cards AND the activity chart together — no more "each card
// silently uses a different fixed window". Built on the shared
// SegmentedControl (role="radiogroup", keyboard accessible, animated
// indicator).

import { SegmentedControl } from "@/components/ui/segmented-control";
import { t } from "@/i18n/i18n";

import type { RangeId } from "../lib/streaks";

const RANGES: RangeId[] = ["today", "7d", "30d", "all"];

interface TimeRangeSelectorProps {
	value: RangeId;
	onChange: (range: RangeId) => void;
}

export function TimeRangeSelector({ value, onChange }: TimeRangeSelectorProps) {
	return (
		<SegmentedControl
			options={RANGES.map((r) => ({
				value: r,
				label: t(`analytics.range.${r}`),
			}))}
			value={value}
			onChange={onChange}
			ariaLabel={t("analytics.rangeAria")}
			// Rectangle with a subtle ~4px rounding — matches the stat
			// cards' corners instead of a pill (see SegmentedControl
			// `radius` prop).
			radius="sm"
		/>
	);
}
