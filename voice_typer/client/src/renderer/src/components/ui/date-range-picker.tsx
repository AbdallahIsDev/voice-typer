"use client";

// Vendored from 21st.dev (kuratlielia/date-range-picker, MIT), adapted:
// lucide-react icons swapped for the app's Hugeicons set, user-facing copy
// moved behind the `strings` prop for translation. The linter is disabled
// for this file only (biome.json overrides): its grid-as-divs markup and
// hook patterns are the vendor's design, not ours to rewrite.

import {
	ArrowLeft01Icon,
	ArrowRight01Icon,
	Calendar01Icon,
	ChevronDownIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { HTMLMotionProps, Transition, Variants } from "motion/react";
import {
	AnimatePresence,
	animate,
	motion,
	useIsPresent,
	useMotionValue,
	useReducedMotion,
	useTransform,
} from "motion/react";
import type {
	CSSProperties,
	FocusEvent as ReactFocusEvent,
	KeyboardEvent as ReactKeyboardEvent,
	ReactNode,
} from "react";
import {
	useCallback,
	useEffect,
	useId,
	useLayoutEffect,
	useMemo,
	useRef,
	useState,
	useSyncExternalStore,
} from "react";
import styles from "./date-range-picker-utils/date-range-picker.module.css";
import { motionTokens } from "./date-range-picker-utils/motion-tokens";

/** An inclusive range of whole days. */
export interface DateRange {
	start: Date;
	end: Date;
}

/** A shortcut in the preset rail. `range` receives the viewer's local today. */
export interface DateRangePreset {
	label: string;
	range: (today: Date) => DateRange;
}

/** Localizable copy. Every visible string defaults to English; callers
 *  pass translations (functions where plural/word-order varies). */
export interface DateRangePickerStrings {
	presetsLabel?: string;
	prevMonthLabel?: string;
	nextMonthLabel?: string;
	cancelLabel?: string;
	applyLabel?: string;
	pickEndDateHint?: string;
	noDatesText?: string;
	formatDayCount?: (days: number) => string;
	formatStatusStart?: (startText: string) => string;
	formatStatusShown?: (shownText: string, countText: string) => string;
}

/**
 * A range picker whose trigger grows into the panel it opens. The panel shows two months beside a preset rail, or one
 * month with a scrolling preset row when space is tight. The range highlight stretches across each week as you hover
 * or move with the keyboard, the ends glide between days, months slide in the direction you travel, and on Apply the
 * formatted label flies back into the trigger as the surface shrinks around it.
 * Use it for reports, filters, and bookings where a start and end date are chosen together.
 *
 * Trigger-less mode (`hideTrigger`): the caller's own control opens the
 * panel, so there is no trigger box to grow out of. The morphing surface
 * is dropped, the panel becomes the single floating container, and it
 * reveals with opacity + scale. See `align` and `dismissGuard` for the
 * two things a trigger-less caller has to tell the panel about itself.
 */
export interface DateRangePickerProps {
	/** Controlled value. Pass `null` for no selection. */
	value?: DateRange | null;
	/** Uncontrolled starting value. */
	defaultValue?: DateRange | null;
	/** Called with the applied range. */
	onChange?: (range: DateRange) => void;
	/** Accessible name of the trigger and the dialog. Defaults to "Date range". */
	label?: string;
	placeholder?: string;
	presets?: DateRangePreset[];
	minDate?: Date;
	maxDate?: Date;
	/** 0 is Sunday, 1 is Monday. Defaults to 0. */
	weekStartsOn?: 0 | 1;
	locale?: string;
	/** Force one or two months. `auto` picks two when the boundary is wide enough. */
	months?: "auto" | 1 | 2;
	/** The element the panel should stay inside. Defaults to the viewport. */
	boundary?: () => HTMLElement | null;
	className?: string;
	/** Translated copy (see DateRangePickerStrings). English defaults. */
	strings?: DateRangePickerStrings;
	/**
	 * Controlled open state. When omitted the trigger owns it. A caller
	 * driving open itself (e.g. a sibling button) pairs this with
	 * onOpenChange + hideTrigger.
	 */
	open?: boolean;
	/** Fires on every open/close transition (Escape, outside pointer, apply). */
	onOpenChange?: (open: boolean) => void;
	/**
	 * Hide the trigger button (the panel still anchors at the root
	 * origin). For callers whose own control opens the panel.
	 */
	hideTrigger?: boolean;
	/**
	 * Commit the range the moment the end date is picked (no footer
	 * Cancel/Apply row; it is hidden too). Draft + anchor behave as
	 * usual, the commit path is shared with Apply.
	 */
	autoApply?: boolean;
	/**
	 * Which edge of the panel hangs under the root origin. `"start"`
	 * (default) grows it rightward from the origin, `"end"` grows it
	 * leftward so the panel's right edge stays under a trigger that
	 * sits at the end of a row. Only consulted for trigger-less
	 * callers: a trigger owner's panel is already anchored to the
	 * trigger's box.
	 */
	align?: "start" | "end";
	/**
	 * Extra element that must NOT count as "outside" for the
	 * outside-pointer dismiss, on top of the root itself. A trigger-less
	 * caller opens the panel from its OWN control (e.g. a pill in a
	 * toggle row), so without this the pointerdown on that control
	 * dismisses the panel and the following click reopens it: the panel
	 * flickers and the caller's active indicator bounces off and back.
	 * Must be a stable callback, like `onOpenChange`.
	 */
	dismissGuard?: () => HTMLElement | null;
}

type Size = { w: number; h: number };
type Bezier = [number, number, number, number];

const DAY = 864e5;
const startOfDay = (date: Date) =>
	new Date(date.getFullYear(), date.getMonth(), date.getDate());
const monthStart = (date: Date) =>
	new Date(date.getFullYear(), date.getMonth(), 1);
const monthEnd = (date: Date) =>
	new Date(date.getFullYear(), date.getMonth() + 1, 0);
const addDays = (date: Date, amount: number) =>
	new Date(date.getFullYear(), date.getMonth(), date.getDate() + amount);
const addMonths = (date: Date, amount: number) =>
	new Date(date.getFullYear(), date.getMonth() + amount, 1);
const shiftMonths = (date: Date, amount: number) =>
	new Date(
		date.getFullYear(),
		date.getMonth() + amount,
		Math.min(
			date.getDate(),
			new Date(date.getFullYear(), date.getMonth() + amount + 1, 0).getDate(),
		),
	);
const dayDiff = (a: Date, b: Date) =>
	Math.round((startOfDay(a).getTime() - startOfDay(b).getTime()) / DAY);
const monthDiff = (a: Date, b: Date) =>
	(a.getFullYear() - b.getFullYear()) * 12 + a.getMonth() - b.getMonth();
const sameDay = (a?: Date | null, b?: Date | null) =>
	Boolean(a && b && dayDiff(a, b) === 0);
const keyOf = (date: Date) =>
	`${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const fromKey = (key: string) => {
	// noUncheckedIndexedAccess: split parts may be missing on malformed
	// keys; fall back to the epoch instead of constructing Invalid Date.
	const parts = key.split("-").map(Number);
	const year = parts[0] ?? 1970;
	const month = parts[1] ?? 1;
	const day = parts[2] ?? 1;
	return new Date(year, month - 1, day);
};
const ordered = (a: Date, b: Date): DateRange =>
	dayDiff(a, b) <= 0 ? { start: a, end: b } : { start: b, end: a };
const sameRange = (a?: DateRange | null, b?: DateRange | null) =>
	Boolean(a && b && sameDay(a.start, b.start) && sameDay(a.end, b.end));
const clampDate = (date: Date, min?: Date, max?: Date) =>
	min && dayDiff(date, min) < 0
		? startOfDay(min)
		: max && dayDiff(date, max) > 0
			? startOfDay(max)
			: date;

export const defaultDateRangePresets: DateRangePreset[] = [
	{ label: "Today", range: (today) => ({ start: today, end: today }) },
	{
		label: "Yesterday",
		range: (today) => ({ start: addDays(today, -1), end: addDays(today, -1) }),
	},
	{
		label: "Last 7 days",
		range: (today) => ({ start: addDays(today, -6), end: today }),
	},
	{
		label: "Last 30 days",
		range: (today) => ({ start: addDays(today, -29), end: today }),
	},
	{
		label: "This month",
		range: (today) => ({ start: monthStart(today), end: today }),
	},
	{
		label: "Last month",
		range: (today) => ({
			start: addMonths(today, -1),
			end: monthEnd(addMonths(today, -1)),
		}),
	},
	{
		label: "This quarter",
		range: (today) => ({
			start: new Date(
				today.getFullYear(),
				Math.floor(today.getMonth() / 3) * 3,
				1,
			),
			end: today,
		}),
	},
	{
		label: "Year to date",
		range: (today) => ({
			start: new Date(today.getFullYear(), 0, 1),
			end: today,
		}),
	},
];

/** Today turns over at local midnight; returning to the tab reads it again. Empty on the server so markup never depends on its clock. */
const subscribeToday = (notify: () => void) => {
	let timer = 0;
	const schedule = () => {
		const now = new Date();
		timer = window.setTimeout(
			() => {
				notify();
				schedule();
			},
			addDays(now, 1).getTime() - now.getTime() + 1000,
		);
	};
	const onVisible = () => {
		if (document.visibilityState === "visible") notify();
	};
	schedule();
	document.addEventListener("visibilitychange", onVisible);
	return () => {
		window.clearTimeout(timer);
		document.removeEventListener("visibilitychange", onVisible);
	};
};
const readToday = () => keyOf(new Date());
const serverToday = () => "";
/** The viewer's local date, or undefined during server render and hydration. */
export function useToday() {
	const key = useSyncExternalStore(subscribeToday, readToday, serverToday);
	return useMemo(() => (key ? fromKey(key) : undefined), [key]);
}

const noop = () => () => {};
function useReducedFlag() {
	const hydrated = useSyncExternalStore(
		noop,
		() => true,
		() => false,
	);
	return !!useReducedMotion() && hydrated;
}

const { blur } = motionTokens;
const enterEase = [...motionTokens.ease.enter] as Bezier;
const standardEase = [...motionTokens.ease.standard] as Bezier;
/** Duration springs restated as stiffness and damping, so every retarget keeps the velocity already in flight. */
const physical = (visualDuration: number, bounce: number): Transition => {
	const root = (2 * Math.PI) / (visualDuration * 1.2);
	return {
		type: "spring",
		stiffness: root * root,
		damping: 2 * (1 - bounce) * root,
		mass: 1,
	};
};
const GROW = physical(0.48, 0.12),
	SHRINK = physical(0.38, 0),
	STRETCH = physical(0.3, 0.04),
	GLIDE = physical(0.3, 0.1),
	SLIDE = physical(0.4, 0.06);
const TRIGGER_RADIUS = 18,
	PANEL_RADIUS = 26,
	WIDE_CELL = 36,
	WIDE_MIN = 712,
	EDGE = 8;

const roll: Variants = {
	enter: (direction: number) => ({
		opacity: 0,
		y: `${direction * 0.45}em`,
		filter: `blur(${blur.subtle}px)`,
	}),
	center: {
		opacity: 1,
		y: "0em",
		filter: "blur(0px)",
		transition: { duration: 0.26, ease: enterEase },
	},
	exit: (direction: number) => ({
		opacity: 0,
		y: `${direction * -0.45}em`,
		filter: `blur(${blur.subtle}px)`,
		transition: { duration: 0.14, ease: standardEase },
	}),
};
const fade: Variants = {
	enter: { opacity: 0 },
	center: {
		opacity: 1,
		y: "0em",
		filter: "blur(0px)",
		transition: { duration: 0.12 },
	},
	exit: { opacity: 0, transition: { duration: 0.08 } },
};
/** Panel content arrives just behind the growing surface and leaves ahead of it. */
const faceIn: Variants = {
	hidden: { opacity: 0, y: -4, filter: `blur(${blur.soft}px)` },
	shown: {
		opacity: 1,
		y: 0,
		filter: "blur(0px)",
		transition: { duration: 0.26, ease: enterEase, delay: 0.07 },
	},
	gone: {
		opacity: 0,
		y: -2,
		filter: `blur(${blur.soft}px)`,
		transition: { duration: 0.12, ease: standardEase },
	},
};
const faceFade: Variants = {
	hidden: { opacity: 0 },
	shown: { opacity: 1, transition: { duration: 0.12 } },
	gone: { opacity: 0, transition: { duration: 0.08 } },
};
const slide: Variants = {
	enter: (direction: number) => ({
		opacity: 0,
		x: direction * 48,
		filter: `blur(${blur.soft}px)`,
	}),
	center: {
		opacity: 1,
		x: 0,
		filter: "blur(0px)",
		transition: {
			x: SLIDE,
			opacity: { duration: 0.22, ease: enterEase },
			filter: { duration: 0.24, ease: enterEase },
		},
	},
	exit: (direction: number) => ({
		opacity: 0,
		x: direction * -36,
		filter: `blur(${blur.soft}px)`,
		transition: {
			x: SLIDE,
			opacity: { duration: 0.14, ease: standardEase },
			filter: { duration: 0.14, ease: standardEase },
		},
	}),
};

/** Words roll one by one: a later date rises from below, an earlier one drops from above; words that stay the same hold still. */
function Rolling({
	text,
	direction,
	reduced,
}: {
	text: string;
	direction: number;
	reduced: boolean;
}) {
	const words = text.split(/\s+/).filter(Boolean);
	return (
		<span className={styles.rolling} aria-hidden="true">
			{words.map((word, index) => (
				<span key={index} className={styles.word}>
					<AnimatePresence initial={false} mode="popLayout" custom={direction}>
						<motion.span
							key={word}
							className={styles.wordInner}
							custom={direction}
							variants={reduced ? fade : roll}
							initial="enter"
							animate="center"
							exit="exit"
						>
							{word}
						</motion.span>
					</AnimatePresence>
				</span>
			))}
		</span>
	);
}

/** Remembers which way a range moved, so labels roll in time's direction. */
function useDirection(range: DateRange | null) {
	const time = range ? range.start.getTime() * 2 + range.end.getTime() : null;
	const [previous, setPrevious] = useState(time);
	const [direction, setDirection] = useState(1);
	if (time !== previous) {
		setPrevious(time);
		setDirection(
			time === null || previous === null || time >= previous ? 1 : -1,
		);
	}
	return direction;
}

type Segment = { left: number; width: number } | null;
const cellIn = (date: Date, first: Date) => {
	const index = dayDiff(date, first);
	return { row: Math.floor(index / 7), col: index % 7 };
};

/** One week's slice of the highlight. It grows out of the edge the range arrives from, so hovering reads as stretching. */
function Bar({
	row,
	segment,
	reduced,
}: {
	row: number;
	segment: Segment;
	reduced: boolean;
}) {
	const left = useMotionValue(segment?.left ?? 0),
		width = useMotionValue(segment?.width ?? 0),
		opacity = useMotionValue(segment ? 1 : 0);
	const leftPct = useTransform(left, (value) => `${value}%`),
		widthPct = useTransform(width, (value) => `${value}%`);
	const wasEmpty = useRef(!segment);
	useLayoutEffect(() => {
		if (!segment) {
			wasEmpty.current = true;
			animate(opacity, 0, { duration: reduced ? 0 : 0.14 });
			return;
		}
		if (wasEmpty.current || reduced) {
			left.jump(segment.left);
			width.jump(segment.width);
		} else {
			animate(left, segment.left, STRETCH);
			animate(width, segment.width, STRETCH);
		}
		wasEmpty.current = false;
		animate(opacity, 1, { duration: reduced ? 0 : 0.16 });
	}, [left, opacity, reduced, segment, width]);
	return (
		<motion.span
			className={styles.bar}
			style={{
				left: leftPct,
				width: widthPct,
				opacity,
				top: `calc(${row} * var(--cell) + 3px)`,
			}}
		/>
	);
}

/** A solid end of the range. It glides from day to day and only fades when the end leaves this month. */
function Thumb({
	cell,
	reduced,
}: {
	cell: { row: number; col: number } | null;
	reduced: boolean;
}) {
	const x = useMotionValue(cell?.col ?? 0),
		y = useMotionValue(cell?.row ?? 0),
		opacity = useMotionValue(cell ? 1 : 0),
		scale = useMotionValue(cell ? 1 : 0.8);
	const transform = useTransform(
		() =>
			`translate(${x.get() * 100}%, ${y.get() * 100}%) scale(${scale.get()})`,
	);
	const hidden = useRef(!cell);
	useLayoutEffect(() => {
		if (!cell) {
			hidden.current = true;
			animate(opacity, 0, { duration: reduced ? 0 : 0.12 });
			animate(scale, 0.8, { duration: reduced ? 0 : 0.12 });
			return;
		}
		if (hidden.current || reduced) {
			x.jump(cell.col);
			y.jump(cell.row);
		} else {
			animate(x, cell.col, GLIDE);
			animate(y, cell.row, GLIDE);
		}
		hidden.current = false;
		animate(opacity, 1, { duration: reduced ? 0 : 0.16 });
		animate(scale, 1, reduced ? { duration: 0 } : GLIDE);
	}, [cell, opacity, reduced, scale, x, y]);
	return (
		<motion.span className={styles.thumb} style={{ transform, opacity }} />
	);
}

interface MonthProps {
	month: Date;
	range: DateRange | null;
	tabbable: string;
	today?: Date;
	minDate?: Date;
	maxDate?: Date;
	weekStartsOn: 0 | 1;
	reduced: boolean;
	formatters: {
		title: Intl.DateTimeFormat;
		day: Intl.DateTimeFormat;
		weekday: Intl.DateTimeFormat;
		weekdayLong: Intl.DateTimeFormat;
	};
	onPick: (date: Date) => void;
	onHover: (date: Date) => void;
	onKey: (event: ReactKeyboardEvent<HTMLButtonElement>, date: Date) => void;
	onFocusDay: (date: Date) => void;
	idBase: string;
}

function Month({
	month,
	range,
	tabbable,
	today,
	minDate,
	maxDate,
	weekStartsOn,
	reduced,
	formatters,
	onPick,
	onHover,
	onKey,
	onFocusDay,
	idBase,
}: MonthProps) {
	const first = useMemo(
		() => addDays(month, -((month.getDay() - weekStartsOn + 7) % 7)),
		[month, weekStartsOn],
	);
	const rows = useMemo(
		() =>
			Array.from({ length: 6 }, (_, row) =>
				Array.from({ length: 7 }, (_, col) => addDays(first, row * 7 + col)),
			),
		[first],
	);
	const titleId = `${idBase}-${keyOf(month)}`;
	const inMonth = (date: Date) => monthDiff(date, month) === 0;

	const lo = range?.start,
		hi = range?.end;
	const loKey = lo ? keyOf(lo) : "",
		hiKey = hi ? keyOf(hi) : "";
	/** Each week's slice of the range, in percent of the row. Keyed by the range's days so bars only move when it does. */
	const segments = useMemo<Segment[]>(() => {
		if (!loKey || !hiKey) return rows.map(() => null);
		const start = fromKey(loKey),
			end = fromKey(hiKey);
		const unit = 100 / 7;
		const colOf = (date: Date) => dayDiff(date, first) % 7;
		return rows.map((row) => {
			const days = row.filter((date) => monthDiff(date, month) === 0);
			if (!days.length) return null;
			// Length-checked above, but noUncheckedIndexedAccess still widens
			// the element type; bail explicitly instead of asserting.
			const rowFirst = days[0];
			const rowLast = days[days.length - 1];
			if (rowFirst === undefined || rowLast === undefined) return null;
			const from = dayDiff(start, rowFirst) > 0 ? start : rowFirst,
				to = dayDiff(end, rowLast) < 0 ? end : rowLast;
			if (dayDiff(from, to) <= 0) {
				const a = colOf(from),
					b = colOf(to);
				return { left: a * unit, width: (b - a + 1) * unit };
			}
			// The week sits wholly before or after the range: collapse at the edge the range would grow in from.
			if (dayDiff(rowLast, start) < 0)
				return { left: (colOf(rowLast) + 1) * unit, width: 0 };
			return { left: colOf(rowFirst) * unit, width: 0 };
		});
	}, [first, hiKey, loKey, month, rows]);
	const startCell = useMemo(
		() =>
			loKey && monthDiff(fromKey(loKey), month) === 0
				? cellIn(fromKey(loKey), first)
				: null,
		[loKey, month, first],
	);
	const endCell = useMemo(
		() =>
			hiKey && hiKey !== loKey && monthDiff(fromKey(hiKey), month) === 0
				? cellIn(fromKey(hiKey), first)
				: null,
		[hiKey, loKey, month, first],
	);

	return (
		<div className={styles.month}>
			<p id={titleId} className={styles.monthTitle}>
				{formatters.title.format(month)}
			</p>
			<div role="grid" aria-labelledby={titleId} className={styles.grid}>
				<div role="row" className={styles.weekdays}>
					{(rows[0] ?? []).map((date) => (
						<span
							key={date.getDay()}
							role="columnheader"
							aria-label={formatters.weekdayLong.format(date)}
						>
							{formatters.weekday.format(date).slice(0, 2)}
						</span>
					))}
				</div>
				<div className={styles.weeks}>
					<span className={styles.layer} aria-hidden="true">
						{segments.map((segment, row) => (
							<Bar key={row} row={row} segment={segment} reduced={reduced} />
						))}
						<Thumb cell={startCell} reduced={reduced} />
						<Thumb cell={endCell} reduced={reduced} />
					</span>
					{rows.map((row, index) => (
						<div key={index} role="row" className={styles.week}>
							{row.map((date) => {
								if (!inMonth(date))
									return (
										<span
											key={keyOf(date)}
											role="gridcell"
											className={styles.blank}
										/>
									);
								const key = keyOf(date);
								const disabled = Boolean(
									(minDate && dayDiff(date, minDate) < 0) ||
										(maxDate && dayDiff(date, maxDate) > 0),
								);
								const inRange = Boolean(
									lo && hi && dayDiff(date, lo) >= 0 && dayDiff(date, hi) <= 0,
								);
								const edge = sameDay(date, lo) || sameDay(date, hi);
								const isToday = sameDay(date, today);
								return (
									<span
										key={key}
										role="gridcell"
										aria-selected={inRange}
										className={styles.cell}
									>
										<button
											type="button"
											className={styles.day}
											data-date={key}
											data-edge={edge || undefined}
											data-range={inRange || undefined}
											data-today={isToday || undefined}
											tabIndex={key === tabbable ? 0 : -1}
											disabled={disabled}
											aria-current={isToday ? "date" : undefined}
											aria-label={formatters.day.format(date)}
											onClick={() => onPick(date)}
											onPointerEnter={() => {
												if (!disabled) onHover(date);
											}}
											onFocus={() => onFocusDay(date)}
											onKeyDown={(event) => onKey(event, date)}
										>
											<span>{date.getDate()}</span>
										</button>
									</span>
								);
							})}
						</div>
					))}
				</div>
			</div>
		</div>
	);
}

/** The visible months travel together; a leaving set turns inert so focus and queries only find the current one. */
function Months({
	children,
	direction,
	reduced,
	ref,
}: {
	children: ReactNode;
	direction: number;
	reduced: boolean;
	/**
	 * AnimatePresence's `mode="popLayout"` clones this child and injects
	 * its own ref so it can measure the leaving set and take it OUT of
	 * flow while it exits. A custom component that swallows that ref
	 * leaves the ref null, so nothing is popped: the leaving month keeps
	 * its place in the layout and stacks under/over the arriving one,
	 * and the viewport (and the panel) grow to hold both. Forwarding the
	 * ref to the motion element is what makes the pop work.
	 */
	ref?: React.Ref<HTMLDivElement>;
}) {
	const present = useIsPresent();
	return (
		<motion.div
			ref={ref}
			className={styles.months}
			data-current={present || undefined}
			inert={!present || undefined}
			custom={direction}
			variants={reduced ? fade : slide}
			initial="enter"
			animate="center"
			exit="exit"
		>
			{children}
		</motion.div>
	);
}

export function DateRangePicker({
	value,
	defaultValue = null,
	onChange,
	label = "Date range",
	placeholder = "Select dates",
	presets = defaultDateRangePresets,
	minDate,
	maxDate,
	weekStartsOn = 0,
	locale = "en-US",
	months = "auto",
	boundary,
	className,
	strings,
	open: controlledOpen,
	onOpenChange,
	hideTrigger = false,
	autoApply = false,
	align = "start",
	dismissGuard,
}: DateRangePickerProps) {
	const copy = {
		presetsLabel: "Presets",
		prevMonthLabel: "Previous month",
		nextMonthLabel: "Next month",
		cancelLabel: "Cancel",
		applyLabel: "Apply",
		pickEndDateHint: "Pick an end date",
		noDatesText: "No dates",
		formatDayCount: (days: number) => `${days} ${days === 1 ? "day" : "days"}`,
		formatStatusStart: (startText: string) =>
			`Start ${startText}. Choose an end date.`,
		formatStatusShown: (shownText: string, countText: string) =>
			`${shownText}, ${countText}`,
		...strings,
	};
	const reduced = useReducedFlag();
	const today = useToday();
	const uid = useId();
	const [inner, setInner] = useState<DateRange | null>(defaultValue);
	const committed = value !== undefined ? value : inner;

	const [innerOpen, setInnerOpen] = useState(false);
	// Controlled-open bridge: external drivers read `open`, every
	// internal transition funnels through `setOpen` so onOpenChange fires
	// exactly once per transition either way. `close` below is a stable
	// useCallback capturing this instance; callers must pass a STABLE
	// onOpenChange (or none) so it never goes stale.
	const open = controlledOpen ?? innerOpen;
	const setOpen = useCallback(
		(next: boolean) => {
			onOpenChange?.(next);
			if (controlledOpen === undefined) setInnerOpen(next);
		},
		[controlledOpen, onOpenChange],
	);
	// Controlled hideTrigger opens skip openPanel (the trigger's
	// click owns it), so seed single-month callers compact from the
	// start instead of flashing two months on the first open.
	const [compact, setCompact] = useState(months === 1);
	const [panelWidth, setPanelWidth] = useState(0);
	const [view, setView] = useState<Date>(() =>
		// No committed value yet: open on the current month, never 2000.
		monthStart(committed?.end ?? new Date()),
	);
	const [direction, setDirection] = useState(1);
	const [draft, setDraft] = useState<DateRange | null>(committed);
	const [anchor, setAnchor] = useState<Date | null>(null);
	const [hover, setHover] = useState<Date | null>(null);
	const [focusKey, setFocusKey] = useState("");

	const formatters = useMemo(
		() => ({
			label: new Intl.DateTimeFormat(locale, {
				month: "short",
				day: "numeric",
				year: "numeric",
			}),
			title: new Intl.DateTimeFormat(locale, {
				month: "long",
				year: "numeric",
			}),
			day: new Intl.DateTimeFormat(locale, {
				weekday: "long",
				month: "long",
				day: "numeric",
				year: "numeric",
			}),
			weekday: new Intl.DateTimeFormat(locale, { weekday: "short" }),
			weekdayLong: new Intl.DateTimeFormat(locale, { weekday: "long" }),
		}),
		[locale],
	);
	const format = useCallback(
		(range: DateRange | null) => {
			if (!range) return placeholder;
			if (sameDay(range.start, range.end))
				return formatters.label.format(range.start);
			// formatRange is ES2021 Intl; the repo tsconfig lib predates it,
			// so narrow structurally instead of widening the whole lib. The
			// runtime (modern Chromium/Node) implements it.
			const rangeFormatter = formatters.label as Intl.DateTimeFormat & {
				formatRange(a: Date, b: Date): string;
			};
			return rangeFormatter.formatRange(range.start, range.end);
		},
		[formatters, placeholder],
	);

	const count = compact ? 1 : 2;
	const visible = useMemo(
		() => Array.from({ length: count }, (_, index) => addMonths(view, index)),
		[count, view],
	);
	const shown = anchor ? ordered(anchor, hover ?? anchor) : draft;
	const activePreset =
		today && !anchor
			? presets.findIndex((preset) => sameRange(preset.range(today), draft))
			: -1;
	const days = shown ? dayDiff(shown.end, shown.start) + 1 : 0;
	const committedDirection = useDirection(committed);
	const shownDirection = useDirection(shown);

	const rootRef = useRef<HTMLDivElement>(null);
	const triggerRef = useRef<HTMLButtonElement>(null);
	const panelRef = useRef<HTMLDivElement>(null);
	const railRef = useRef<HTMLDivElement>(null);
	const pendingFocus = useRef<"trigger" | "day" | null>(null);

	/* ---------- One surface: the trigger's box springs to the panel's box and back ---------- */
	// Before the first measurement the surface simply fills the root and the root wraps the trigger, so server markup already looks right.
	const width = useMotionValue<number | string>("100%"),
		height = useMotionValue<number | string>("100%"),
		radius = useMotionValue(TRIGGER_RADIUS),
		x = useMotionValue(0),
		rootWidth = useMotionValue<number | string>("auto");
	const sizes = useRef<{ trigger: Size; panel: Size | null }>({
		trigger: { w: 0, h: 0 },
		panel: null,
	});
	const live = useRef({ open: false, reduced: false, ready: false });
	const queued = useRef(false);
	const boundaryRef = useRef(boundary);
	useLayoutEffect(() => {
		boundaryRef.current = boundary;
	}, [boundary]);

	const bounds = useCallback(() => {
		const element = boundaryRef.current?.();
		const viewport = { left: 0, right: document.documentElement.clientWidth };
		if (!element) return viewport;
		const rect = element.getBoundingClientRect();
		return {
			left: Math.max(viewport.left, rect.left),
			right: Math.min(viewport.right, rect.right),
		};
	}, []);

	const update = useCallback(() => {
		queued.current = false;
		const { trigger, panel } = sizes.current,
			state = live.current;
		// Trigger-less callers render the panel directly (no surface), so
		// there is nothing to size or slide: the panel's box IS the card and
		// its offset under the root origin is pure CSS (`left`/`right` on
		// `.panel[data-floating]`, keyed on `align`). Deliberately no
		// measurement here — a `x` offset would have no consumer without the
		// surface, and reading the panel's width on every pass is exactly the
		// stale-measurement path that made the old morph misplace the panel.
		if (hideTrigger) {
			state.ready = true;
			return;
		}
		// Trigger owners keep the plain early return: their mount jump
		// owns `ready` and the surface collapses on close.
		if (!trigger.w && !(state.open && panel)) return;
		const openPanel = state.open && panel ? panel : null;
		const target = openPanel ?? trigger;
		let offset = 0;
		if (openPanel && rootRef.current) {
			const rect = rootRef.current.getBoundingClientRect(),
				box = bounds();
			offset = Math.min(0, box.right - EDGE - (rect.left + openPanel.w));
			offset = Math.max(offset, box.left + EDGE - rect.left);
		}
		const r = openPanel ? PANEL_RADIUS : TRIGGER_RADIUS;
		if (!state.ready || state.reduced) {
			width.jump(target.w);
			height.jump(target.h);
			radius.jump(r);
			x.jump(offset);
			rootWidth.jump(trigger.w);
			state.ready = true;
			return;
		}
		const spring = openPanel ? GROW : state.open ? GROW : SHRINK;
		animate(width, target.w, spring);
		animate(height, target.h, spring);
		animate(radius, r, spring);
		animate(x, offset, spring);
		animate(rootWidth, trigger.w, motionTokens.spring.morph);
	}, [bounds, height, hideTrigger, radius, rootWidth, width, x]);

	const schedule = useCallback(() => {
		if (queued.current) return;
		queued.current = true;
		queueMicrotask(update);
	}, [update]);

	useLayoutEffect(() => {
		live.current.open = open;
		live.current.reduced = reduced;
		if (!open) sizes.current.panel = null;
		schedule();
	}, [open, reduced, schedule]);

	useLayoutEffect(() => {
		const node = triggerRef.current;
		if (!node) return;
		const read = () => {
			sizes.current.trigger = { w: node.offsetWidth, h: node.offsetHeight };
			schedule();
		};
		read();
		if (typeof ResizeObserver === "undefined") return;
		const observer = new ResizeObserver(read);
		observer.observe(node);
		return () => observer.disconnect();
	}, [schedule]);

	useLayoutEffect(() => {
		const node = panelRef.current;
		if (!open || !node) return;
		const read = () => {
			sizes.current.panel = { w: node.offsetWidth, h: node.offsetHeight };
			schedule();
		};
		read();
		if (typeof ResizeObserver === "undefined") return;
		const observer = new ResizeObserver(read);
		observer.observe(node);
		return () => observer.disconnect();
	}, [open, schedule]);

	/* ---------- Layout: two months when there is room, one compact month otherwise ---------- */
	const measureLayout = useCallback(() => {
		const box = bounds();
		const available = Math.max(0, box.right - box.left - EDGE * 2);
		const single = months === 1 || (months === "auto" && available < WIDE_MIN);
		return { single, width: Math.min(352, available) };
	}, [bounds, months]);

	useEffect(() => {
		if (!open) return;
		const onResize = () => {
			const next = measureLayout();
			setCompact(next.single);
			setPanelWidth(next.width);
			schedule();
		};
		window.addEventListener("resize", onResize);
		return () => window.removeEventListener("resize", onResize);
	}, [measureLayout, open, schedule]);

	const viewFor = (
		range: DateRange | null,
		single: boolean,
		fallback: Date,
	) => {
		const end = monthStart(range?.end ?? fallback);
		return single ? end : addMonths(end, -1);
	};

	const openPanel = () => {
		if (!today) return;
		const layout = measureLayout();
		setCompact(layout.single);
		setPanelWidth(layout.width);
		setDraft(committed);
		setAnchor(null);
		setHover(null);
		setView(viewFor(committed, layout.single, today));
		setDirection(0);
		setFocusKey(keyOf(committed?.start ?? today));
		pendingFocus.current = "day";
		setOpen(true);
	};

	const close = useCallback(
		(focus: boolean) => {
			if (focus) pendingFocus.current = "trigger";
			setDirection(0);
			setOpen(false);
			setAnchor(null);
			setHover(null);
		},
		[setOpen],
	);

	const apply = () => {
		const next = anchor ? { start: anchor, end: anchor } : draft;
		if (!next) return;
		if (value === undefined) setInner(next);
		onChange?.(next);
		close(true);
	};

	/* ---------- Focus management ---------- */
	useLayoutEffect(() => {
		const target = pendingFocus.current;
		if (!target) return;
		if (target === "trigger" && !open) {
			pendingFocus.current = null;
			triggerRef.current?.focus({ preventScroll: true });
			return;
		}
		if (target === "day" && open) {
			const node = panelRef.current?.querySelector<HTMLButtonElement>(
				`[data-current] [data-date="${focusKey}"]`,
			);
			if (node) {
				pendingFocus.current = null;
				node.focus({ preventScroll: true });
			}
		}
	}, [focusKey, open, view, compact]);

	useEffect(() => {
		if (!open) return;
		const down = (event: PointerEvent) => {
			const target = event.target as Node;
			if (rootRef.current?.contains(target)) return;
			// The caller's own opener is not "outside": dismissing on its
			// pointerdown would make the following click reopen the panel,
			// so the panel flickers and the caller's active indicator
			// bounces off the control and back.
			if (dismissGuard?.()?.contains(target)) return;
			close(false);
		};
		// Escape. `onRootKey` below only fires while focus is INSIDE the
		// root, which is always true for a trigger owner (opening moves
		// focus to the panel) but never for a trigger-less caller: focus
		// stays on the caller's own control, so the root never sees the
		// key and the panel becomes undismissable by keyboard. Listen on
		// the document instead, and skip when the root already handled it
		// so the key is not processed twice.
		const key = (event: KeyboardEvent) => {
			if (event.key !== "Escape") return;
			if (rootRef.current?.contains(event.target as Node)) return;
			event.preventDefault();
			// `false`: there is no trigger of ours to hand focus back to.
			// The caller owns the opener and keeps its focus.
			close(false);
		};
		document.addEventListener("pointerdown", down);
		document.addEventListener("keydown", key);
		return () => {
			document.removeEventListener("pointerdown", down);
			document.removeEventListener("keydown", key);
		};
	}, [close, dismissGuard, open]);

	const onRootKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
		if (event.key === "Escape" && open) {
			event.preventDefault();
			event.stopPropagation();
			close(true);
		}
	};
	const onRootBlur = (event: ReactFocusEvent<HTMLDivElement>) => {
		const next = event.relatedTarget as Node | null;
		if (open && next && !event.currentTarget.contains(next)) close(false);
	};

	/* ---------- Month navigation and picking ---------- */
	const goTo = (next: Date) => {
		const delta = monthDiff(next, view);
		if (!delta) return;
		setDirection(Math.sign(delta));
		setView(next);
	};

	/** Keeps a range on screen: nothing moves when it already fits, otherwise its last month lands on the right. */
	const reveal = (range: DateRange) => {
		const first = view,
			last = addMonths(view, count - 1);
		if (monthDiff(range.start, first) >= 0 && monthDiff(range.end, last) <= 0)
			return;
		goTo(viewFor(range, compact, range.end));
	};

	const pick = (date: Date) => {
		setFocusKey(keyOf(date));
		if (!anchor) {
			setAnchor(date);
			setHover(date);
			return;
		}
		const next = ordered(anchor, date);
		setDraft(next);
		setAnchor(null);
		setHover(null);
		// autoApply: the end-date pick commits at once through the same
		// path Apply uses (uncontrolled inner state + onChange).
		if (autoApply) {
			if (value === undefined) setInner(next);
			onChange?.(next);
			close(true);
		}
	};

	const choosePreset = (preset: DateRangePreset) => {
		if (!today) return;
		const range = preset.range(today);
		setDraft(range);
		setAnchor(null);
		setHover(null);
		setFocusKey(keyOf(range.start));
		reveal(range);
	};

	const onDayKey = (
		event: ReactKeyboardEvent<HTMLButtonElement>,
		date: Date,
	) => {
		const weekday = (date.getDay() - weekStartsOn + 7) % 7;
		const moves: Record<string, () => Date> = {
			ArrowLeft: () => addDays(date, -1),
			ArrowRight: () => addDays(date, 1),
			ArrowUp: () => addDays(date, -7),
			ArrowDown: () => addDays(date, 7),
			Home: () => addDays(date, -weekday),
			End: () => addDays(date, 6 - weekday),
			PageUp: () => shiftMonths(date, event.shiftKey ? -12 : -1),
			PageDown: () => shiftMonths(date, event.shiftKey ? 12 : 1),
		};
		const move = moves[event.key];
		if (!move) return;
		event.preventDefault();
		const next = clampDate(move(), minDate, maxDate);
		setFocusKey(keyOf(next));
		if (anchor) setHover(next);
		pendingFocus.current = "day";
		const first = view,
			last = addMonths(view, count - 1);
		if (monthDiff(next, first) < 0) goTo(monthStart(next));
		else if (monthDiff(next, last) > 0)
			goTo(addMonths(monthStart(next), -(count - 1)));
	};

	const onRailKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
		const keys = compact
			? ["ArrowLeft", "ArrowRight"]
			: ["ArrowUp", "ArrowDown"];
		if (!keys.includes(event.key)) return;
		const buttons = Array.from(
			event.currentTarget.querySelectorAll<HTMLButtonElement>("[data-preset]"),
		);
		const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
		if (at < 0) return;
		event.preventDefault();
		buttons[
			(at + (event.key === keys[1] ? 1 : -1) + buttons.length) % buttons.length
		]?.focus();
	};

	/** The day that owns the tab stop: the focused day when it is on screen, else the first visible day of the range, else the 1st. */
	const tabbable = useMemo(() => {
		const onScreen = (date: Date) =>
			visible.some((month) => monthDiff(date, month) === 0);
		if (focusKey && onScreen(fromKey(focusKey))) return focusKey;
		if (shown && onScreen(shown.start)) return keyOf(shown.start);
		// `visible` always holds ≥1 month in practice; without a fallback
		// key no day would own the tab stop, so degrade to "none match".
		const firstVisible = visible[0];
		return firstVisible === undefined ? focusKey : keyOf(firstVisible);
	}, [focusKey, shown, visible]);

	/* ---------- Preset highlight glides between presets ---------- */
	const gx = useMotionValue(0),
		gy = useMotionValue(0),
		gw = useMotionValue(0),
		gh = useMotionValue(0),
		go = useMotionValue(0);
	useLayoutEffect(() => {
		const rail = railRef.current;
		const node =
			activePreset < 0
				? null
				: rail?.querySelector<HTMLElement>(`[data-preset="${activePreset}"]`);
		if (!rail || !node) {
			animate(go, 0, { duration: reduced ? 0 : 0.14 });
			return;
		}
		const place = [
			node.offsetLeft,
			node.offsetTop,
			node.offsetWidth,
			node.offsetHeight,
		];
		// Destructure with defaults: noUncheckedIndexedAccess widens every
		// index access even on a fixed-length literal.
		const [px = 0, py = 0, pw = 0, ph = 0] = place;
		if (go.get() < 0.05 || reduced) {
			gx.jump(px);
			gy.jump(py);
			gw.jump(pw);
			gh.jump(ph);
		} else {
			animate(gx, px, GLIDE);
			animate(gy, py, GLIDE);
			animate(gw, pw, GLIDE);
			animate(gh, ph, GLIDE);
		}
		animate(go, 1, { duration: reduced ? 0 : 0.16 });
		if (
			compact &&
			(node.offsetLeft < rail.scrollLeft ||
				node.offsetLeft + node.offsetWidth > rail.scrollLeft + rail.clientWidth)
		) {
			rail.scrollTo({
				left: node.offsetLeft - 12,
				behavior: reduced ? "auto" : "smooth",
			});
		}
	}, [activePreset, compact, go, gh, gw, gx, gy, open, reduced]);

	/* ---------- Copy ---------- */
	const committedText = format(committed);
	const shownText = shown ? format(shown) : copy.noDatesText;
	const countText = shown ? copy.formatDayCount(days) : "";
	const status = !open
		? ""
		: anchor
			? copy.formatStatusStart(formatters.label.format(anchor))
			: shown
				? copy.formatStatusShown(shownText, countText)
				: "";
	const layoutId = reduced ? undefined : `${uid}-value`;

	const cellSize =
		compact && panelWidth
			? Math.max(32, Math.min(42, Math.floor((panelWidth - 24) / 7)))
			: WIDE_CELL;

	const quiet = open
		? reduced
			? { opacity: 0 }
			: { opacity: 0, filter: `blur(${blur.subtle}px)` }
		: { opacity: 1, filter: "blur(0px)" };
	const quietTransition = open
		? { duration: 0.12, ease: standardEase }
		: { duration: 0.22, ease: enterEase, delay: reduced ? 0 : 0.1 };

	/**
	 * Panel shell. A trigger owner gets the vendor's single morphing
	 * surface: the panel is a plain box inside it, clipped while the
	 * surface grows out of the trigger's box. A trigger-less caller has
	 * nothing to morph FROM, so the surface would only be a second
	 * wrapper painting the same material — the panel carries it instead
	 * and the root becomes a bare anchor.
	 */
	const shell = (children: ReactNode) =>
		hideTrigger ? (
			children
		) : (
			<motion.div
				className={styles.surface}
				style={{ width, height, borderRadius: radius, x }}
			>
				{children}
			</motion.div>
		);

	// Trigger owners stagger the panel's content in behind the growing
	// surface. A floating panel already reveals as one piece (opacity +
	// scale on the panel), so a second, competing animation on the
	// content would just muddy it.
	const faceReveal: HTMLMotionProps<"div"> = hideTrigger
		? {}
		: {
				variants: reduced ? faceFade : faceIn,
				initial: "hidden",
				animate: "shown",
				exit: "gone",
			};

	return (
		<motion.div
			ref={rootRef}
			className={[styles.root, className].filter(Boolean).join(" ")}
			style={{ width: rootWidth }}
			data-open={open || undefined}
			onKeyDown={onRootKey}
			onBlur={onRootBlur}
		>
			{/* The trigger stays in place under the panel. Its icons fade, while the value itself flies to the panel footer and back. Hidden-trigger callers (hideTrigger) open the panel themselves; the surface still anchors at the root origin. */}
			{!hideTrigger && (
				<button
					ref={triggerRef}
					type="button"
					className={styles.trigger}
					aria-haspopup="dialog"
					aria-expanded={open}
					aria-controls={open ? `${uid}-panel` : undefined}
					aria-label={`${label}: ${committedText}`}
					disabled={!today}
					inert={open || undefined}
					onClick={openPanel}
				>
					<motion.span
						className={styles.triggerIcon}
						initial={false}
						animate={quiet}
						transition={quietTransition}
					>
						<HugeiconsIcon
							icon={Calendar01Icon}
							strokeWidth={1.75}
							className="h-4 w-4"
							aria-hidden="true"
						/>
					</motion.span>
					{open || !layoutId ? (
						<motion.span
							key="resting"
							className={styles.value}
							data-empty={!committed || undefined}
							initial={false}
							animate={open ? { opacity: 0 } : { opacity: 1 }}
							transition={quietTransition}
						>
							<Rolling
								text={committedText}
								direction={committedDirection}
								reduced={reduced}
							/>
						</motion.span>
					) : (
						<motion.span
							key="shared"
							layoutId={layoutId}
							layout="position"
							className={styles.value}
							data-empty={!committed || undefined}
							transition={SHRINK}
						>
							<Rolling
								text={committedText}
								direction={committedDirection}
								reduced={reduced}
							/>
						</motion.span>
					)}
					<motion.span
						className={styles.chevron}
						initial={false}
						animate={quiet}
						transition={quietTransition}
					>
						<HugeiconsIcon
							icon={ChevronDownIcon}
							strokeWidth={1.75}
							className="h-4 w-4"
							aria-hidden="true"
						/>
					</motion.span>
				</button>
			)}

			{shell(
				<AnimatePresence>
					{open && today && (
						<motion.div
							key="panel"
							ref={panelRef}
							id={`${uid}-panel`}
							// `rounded-lg` is the app's single radius step for
							// panels, and — unlike a plain border-radius
							// declaration — it is also what picks up the
							// global squircle corner-shape (index.css shapes
							// it via :where(.rounded-lg)). Without the class
							// the panel's corners were a plain superellipse(1)
							// while every card in the app renders
							// superellipse(2), so the calendar read as a
							// different material. The radius itself is
							// declared here, not in the module, so the card
							// look has exactly one source.
							className={`${styles.panel} rounded-lg`}
							role="dialog"
							aria-label={label}
							data-compact={compact || undefined}
							data-floating={hideTrigger || undefined}
							// Which edge of the panel hangs under the root
							// origin; the floating CSS keys `left`/`right` on
							// it. Only meaningful without a trigger.
							data-align={hideTrigger ? align : undefined}
							style={
								{
									"--cell": `${cellSize}px`,
									width: compact && panelWidth ? panelWidth : undefined,
									// A floating panel grows out of the corner it
									// hangs from, so the reveal reads as coming
									// from the control that opened it.
									transformOrigin: hideTrigger
										? align === "end"
											? "top right"
											: "top left"
										: undefined,
								} as CSSProperties
							}
							// Floating reveal: opacity + scale only, no travel.
							initial={hideTrigger ? { opacity: 0, scale: 0.8 } : undefined}
							animate={hideTrigger ? { opacity: 1, scale: 1 } : undefined}
							transition={
								hideTrigger
									? { duration: reduced ? 0 : 0.16, ease: enterEase }
									: undefined
							}
							exit={
								hideTrigger
									? {
											opacity: 0,
											scale: 0.8,
											transition: {
												duration: reduced ? 0 : 0.12,
												ease: standardEase,
											},
										}
									: { opacity: 1, transition: { duration: 0.14 } }
							}
						>
							<motion.div className={styles.body} {...faceReveal}>
								{/* No presets: the caller owns preset shortcuts
								    elsewhere (e.g. a pill row), so the rail —
								    an empty bordered column — stays out. */}
								{presets.length > 0 && (
									<div
										ref={railRef}
										className={styles.rail}
										role="group"
										aria-label={copy.presetsLabel}
										onKeyDown={onRailKey}
									>
										<motion.span
											className={styles.railHighlight}
											style={{
												x: gx,
												y: gy,
												width: gw,
												height: gh,
												opacity: go,
											}}
											aria-hidden="true"
										/>
										{presets.map((preset, index) => (
											<button
												key={preset.label}
												type="button"
												className={styles.preset}
												data-preset={index}
												aria-pressed={index === activePreset}
												onClick={() => choosePreset(preset)}
											>
												{preset.label}
											</button>
										))}
									</div>
								)}

								<div
									className={styles.calendars}
									onPointerLeave={() => {
										if (anchor) setHover(null);
									}}
								>
									<button
										type="button"
										className={styles.navButton}
										data-side="prev"
										aria-label={copy.prevMonthLabel}
										onClick={() => goTo(addMonths(view, -1))}
										disabled={Boolean(minDate && monthDiff(view, minDate) <= 0)}
									>
										<HugeiconsIcon
											icon={ArrowLeft01Icon}
											strokeWidth={1.75}
											className="h-4 w-4 nav-directional-icon"
											aria-hidden="true"
										/>
									</button>
									<button
										type="button"
										className={styles.navButton}
										data-side="next"
										aria-label={copy.nextMonthLabel}
										onClick={() => goTo(addMonths(view, 1))}
										disabled={Boolean(
											maxDate &&
												monthDiff(addMonths(view, count - 1), maxDate) >= 0,
										)}
									>
										<HugeiconsIcon
											icon={ArrowRight01Icon}
											strokeWidth={1.75}
											className="h-4 w-4 nav-directional-icon"
											aria-hidden="true"
										/>
									</button>
									<div className={styles.viewport}>
										<AnimatePresence
											initial={false}
											mode="popLayout"
											custom={direction}
										>
											<Months
												key={`${keyOf(view)}-${count}`}
												direction={direction}
												reduced={reduced}
											>
												{visible.map((month) => (
													<Month
														key={keyOf(month)}
														month={month}
														range={shown}
														tabbable={tabbable}
														today={today}
														minDate={minDate}
														maxDate={maxDate}
														weekStartsOn={weekStartsOn}
														reduced={reduced}
														formatters={formatters}
														idBase={uid}
														onPick={pick}
														onHover={(date) => {
															if (anchor) setHover(date);
														}}
														onKey={onDayKey}
														onFocusDay={(date) => setFocusKey(keyOf(date))}
													/>
												))}
											</Months>
										</AnimatePresence>
									</div>
								</div>
							</motion.div>

							<div className={styles.footer}>
								<div className={styles.summary}>
									<motion.span
										layoutId={layoutId}
										layout={layoutId ? "position" : undefined}
										className={styles.summaryValue}
										transition={GROW}
										variants={layoutId ? undefined : faceFade}
										initial={layoutId ? undefined : "hidden"}
										animate={layoutId ? undefined : "shown"}
										exit={layoutId ? undefined : "gone"}
									>
										<Rolling
											text={shownText}
											direction={shownDirection}
											reduced={reduced}
										/>
									</motion.span>
									<motion.span className={styles.summaryCount} {...faceReveal}>
										{countText && (
											<Rolling
												text={
													anchor && sameDay(anchor, hover)
														? copy.pickEndDateHint
														: countText
												}
												direction={shownDirection}
												reduced={reduced}
											/>
										)}
									</motion.span>
								</div>
								{/* autoApply commits on pick: the footer keeps
								    the live summary, the Cancel/Apply row goes. */}
								{!autoApply && (
									<motion.div className={styles.actions} {...faceReveal}>
										<button
											type="button"
											className={styles.ghost}
											onClick={() => close(true)}
										>
											{copy.cancelLabel}
										</button>
										<button
											type="button"
											className={styles.primary}
											onClick={apply}
											disabled={!shown}
										>
											{copy.applyLabel}
										</button>
									</motion.div>
								)}
							</div>
							<p className={styles.srOnly} aria-live="polite">
								{status}
							</p>
						</motion.div>
					)}
				</AnimatePresence>,
			)}
		</motion.div>
	);
}

export default DateRangePicker;
