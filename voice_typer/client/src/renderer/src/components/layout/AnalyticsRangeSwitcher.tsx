/**
 * AnalyticsRangeSwitcher — the Analytics time range (Today / 7 Days /
 * 30 Days / All Time + Custom) in the title-bar middle strip.
 *
 * Same PLACEMENT pattern as ModelsTabSwitcher: the control lives in the
 * bar's middle strip while `currentPage === "analytics"` and unmounts
 * elsewhere, so the range is always reachable even once the page has
 * scrolled.
 *
 * The row is one ToggleGroup with five items: the four preset pills plus
 * an icon-only Custom pill (the primitive supports icon items natively
 * via `icon` + `title`, so no fork is needed). Picking the Custom pill
 * opens the DateRangePicker panel (single month, no preset rail, dates
 * auto-apply on selection); the picker runs trigger-less under a plain
 * absolutely-positioned anchor div, so the panel hangs 8px below the row
 * with its right edge under the Custom pill (the last item, so it opens
 * inward instead of off the window). The panel closes on Escape /
 * outside pointer / apply; `dismissGuard` tells it this row counts as
 * INSIDE, so re-clicking the Custom pill leaves both the panel and the
 * indicator alone rather than closing and reopening them. Applying a
 * range that coincides with a preset lands back on that preset's pill.
 * The pill opens through `onOptionActivate` rather than `onChange`,
 * because once a custom range is committed the pill IS the active radio
 * value and a radio ignores a re-click of itself.
 *
 * Deliberately NOT `variant="tabs"` (which ModelsTabSwitcher uses): that
 * variant renders a WAI-ARIA tablist whose every tab must control a real
 * `role="tabpanel"`. The range picks a data window — there is no panel to
 * point at — so this keeps the radiogroup variant, the primitive's
 * documented job ("one setting with mutually exclusive values"). It
 * therefore wears the recessed-track look rather than the Models
 * switcher's transparent tab strip.
 *
 * State lives in `stores/useAnalyticsRange` (not component state) so the
 * title bar and the page, which are separate component trees, read the
 * same value.
 */

import { Calendar01Icon } from "@hugeicons/core-free-icons";
import { memo, useCallback, useRef, useState } from "react";
import {
	type DateRange,
	DateRangePicker,
} from "@/components/ui/date-range-picker";
import {
	ToggleGroup,
	type ToggleGroupOption,
} from "@/components/ui/toggle-group";
import { getLocale, t, tChoice } from "@/i18n/i18n";
import type { RangeId } from "@/pages/dashboard/lib/streaks";
import {
	ANALYTICS_RANGES,
	CUSTOM_RANGE_MAX_DAYS,
	type PresetRange,
	useAnalyticsRange,
} from "@/stores/useAnalyticsRange";
import type { Page } from "@/types/ipc";

interface AnalyticsRangeSwitcherProps {
	currentPage: Page;
}

/** Local `YYYY-MM-DD` key for a Date (picker hands us Dates). */
function keyOfLocal(d: Date): string {
	return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Shift a validated day key by whole local days (validated input only). */
function shiftLocalKey(key: string, delta: number): string {
	const [y = 1970, m = 1, d = 1] = key.split("-").map(Number);
	return keyOfLocal(new Date(y, m - 1, d + delta));
}

function dateFromKey(key: string): Date {
	const [y = 1970, m = 1, d = 1] = key.split("-").map(Number);
	return new Date(y, m - 1, d);
}

/**
 * Match an applied picker range back to a preset (so a hand-picked span
 * that coincides with one lands on the same state as the pill row).
 * Anchored on today: anything else — including a single past day — is a
 * genuine custom window.
 */
function matchPreset(startKey: string, endKey: string): PresetRange | null {
	const todayKey = keyOfLocal(new Date());
	if (endKey !== todayKey) return null;
	if (startKey === todayKey) return "today";
	if (startKey === shiftLocalKey(todayKey, -6)) return "7d";
	if (startKey === shiftLocalKey(todayKey, -29)) return "30d";
	return null;
}

export const AnalyticsRangeSwitcher = memo(function AnalyticsRangeSwitcher({
	currentPage,
}: AnalyticsRangeSwitcherProps) {
	const range = useAnalyticsRange((s) => s.range);
	const customWindow = useAnalyticsRange((s) => s.customWindow);
	const setRange = useAnalyticsRange((s) => s.setRange);
	const setCustomRange = useAnalyticsRange((s) => s.setCustomRange);
	const [picking, setPicking] = useState(false);
	// The row itself: it holds BOTH the pills and the panel, so a
	// pointerdown on a pill must not read as "outside" the panel.
	const rowRef = useRef<HTMLDivElement>(null);
	const guardRow = useCallback(() => rowRef.current, []);

	const handleToggleChange = (value: RangeId) => {
		if (value === "custom") {
			// Idempotent on purpose: clicking the Custom pill while the
			// panel is already open must not restart anything. (The
			// panel's outside-dismiss treats this row as inside, so the
			// pointerdown cannot close it either — see `dismissGuard`.)
			setPicking(true);
			return;
		}
		// Leaving for a preset also drops an open picker: its value
		// would otherwise keep reading "custom" below.
		setPicking(false);
		setRange(value);
	};

	const handlePickerOpenChange = useCallback((open: boolean) => {
		if (!open) setPicking(false);
	}, []);

	// The Custom pill is both a radio value and a disclosure trigger. Once
	// a custom window is committed the pill IS the active value, and a
	// radio ignores a re-click of itself — so `onChange` never fires and
	// the panel could never be reopened. `onOptionActivate` is the
	// primitive's hook for exactly that second click; it fires before
	// `onChange`, so this is a no-op when the value is also changing.
	const handleOptionActivate = useCallback((value: RangeId) => {
		if (value === "custom") setPicking(true);
	}, []);

	const applyPickedRange = (picked: DateRange) => {
		const preset = matchPreset(
			keyOfLocal(picked.start),
			keyOfLocal(picked.end),
		);
		if (preset) setRange(preset);
		else
			setCustomRange({
				startKey: keyOfLocal(picked.start),
				endKey: keyOfLocal(picked.end),
			});
		setPicking(false);
	};

	if (currentPage !== "analytics") return null;

	const options: ToggleGroupOption<RangeId>[] = [
		...ANALYTICS_RANGES.map((r) => ({
			value: r,
			label: t(`analytics.range.${r}`),
		})),
		// Icon-only Custom item (no text): the primitive renders
		// `icon` + `title` as an accessible radio without a label.
		{
			value: "custom" as const,
			label: "",
			icon: Calendar01Icon,
			title: t("analytics.range.custom"),
		},
	];

	const today = new Date();
	const minDate = new Date(
		today.getFullYear(),
		today.getMonth(),
		today.getDate() - (CUSTOM_RANGE_MAX_DAYS - 1),
	);
	const maxDate = new Date(
		today.getFullYear(),
		today.getMonth(),
		today.getDate(),
	);

	return (
		<div ref={rowRef} className="relative flex items-center gap-1">
			<ToggleGroup
				options={options}
				// While the picker is open the pending selection is
				// Custom: the icon reads active and the old preset
				// releases, instead of stranding the indicator under a
				// muted icon beside a still-white preset. Cancelling
				// flips back to `range` and the row follows.
				value={picking ? "custom" : range}
				onChange={handleToggleChange}
				onOptionActivate={handleOptionActivate}
				ariaLabel={t("analytics.rangeAria")}
				// `no-drag`: the strip sits inside the title bar's drag region,
				// so without it a click would start a window move instead of
				// selecting a range. `h-full` fills the 36px bar the way the
				// Models switcher does.
				className="no-drag h-full"
			/>
			{/* Trigger-less picker: the Custom pill above opens the panel;
			    the anchor below only places it. The anchor is a plain div,
			    so the utility classes win — the picker's own root is
			    `position: relative` from its stylesheet, which would
			    silently ignore an `absolute` class and leave the panel
			    hanging off the end of the row instead of under it.
			    `right-0` + align="end" keeps the panel's right edge under
			    the Custom pill (the last item), so it opens into the row
			    rather than off the right of the window. */}
			<div className="no-drag absolute top-[calc(100%+8px)] right-0">
				<DateRangePicker
					value={
						customWindow
							? {
									start: dateFromKey(customWindow.startKey),
									end: dateFromKey(customWindow.endKey),
								}
							: null
					}
					onChange={applyPickedRange}
					label={t("analytics.rangeAria")}
					placeholder={t("analytics.range.custom")}
					presets={[]}
					minDate={minDate}
					maxDate={maxDate}
					months={1}
					locale={getLocale()}
					open={picking}
					onOpenChange={handlePickerOpenChange}
					hideTrigger
					autoApply
					align="end"
					dismissGuard={guardRow}
					className="no-drag"
					strings={{
						presetsLabel: t("analytics.rangePicker.presets"),
						prevMonthLabel: t("analytics.rangePicker.prevMonth"),
						nextMonthLabel: t("analytics.rangePicker.nextMonth"),
						cancelLabel: t("analytics.rangePicker.cancel"),
						applyLabel: t("analytics.rangePicker.apply"),
						pickEndDateHint: t("analytics.rangePicker.pickEndDate"),
						noDatesText: t("analytics.rangePicker.noDates"),
						formatDayCount: (days) =>
							tChoice("analytics.rangePicker.days", days),
						formatStatusStart: (startText) =>
							t("analytics.rangePicker.statusStart", { date: startText }),
						formatStatusShown: (shownText, countText) =>
							t("analytics.rangePicker.statusShown", {
								shown: shownText,
								count: countText,
							}),
					}}
				/>
			</div>
		</div>
	);
});
