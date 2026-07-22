/**
 * Shared locale-aware formatting utilities.
 *
 * PVT-089 / PVT-090 / PVT-091 / Task #20: previously the renderer had
 * three+ copies of byte / number / duration formatting (``About.tsx``,
 * ``Dashboard.tsx``, ``StatCards.tsx``, ``lib/utils/models.ts``), each
 * hardcoding English suffixes (``"MB"`` / ``"GB"`` / ``"K+"``) and
 * calling ``toFixed()`` / ``String(n)`` directly — so non-English
 * locales saw English unit labels and Latin digit grouping regardless
 * of their selected UI language. This module is the single source of
 * truth for those formatters, using the platform ``Intl`` APIs so
 * digit grouping, decimal separators, unit names, relative-time
 * phrases, and date formats all respect the user-selected UI locale.
 *
 * ── Backward compatibility ────────────────────────────────────────────
 *   - ``compactNumber`` (added by Task #17) is preserved as-is for the
 *     existing Dashboard / StatCards callers. Its legacy ``K`` / ``K+``
 *     suffix behaviour is preserved via the ``plusSuffix`` /
 *     ``localeAware`` options. New code should prefer
 *     {@link formatCompactNumber}, which uses ``Intl.NumberFormat``
 *     with ``notation: "compact"`` so the suffix localises
 *     (``"1.2K"`` in en, ``"1,2 K"`` in fr, ``"۱٫۲ هزار"`` in fa, …).
 *   - ``About.tsx`` still exports its own ``formatBytes`` /
 *     ``formatRelativeTime`` for the ``About.test.tsx`` unit tests.
 *     Those wrappers remain untouched; the new locale-aware helpers
 *     here are the canonical implementations future call sites should
 *     migrate to.
 *
 * ── Locale resolution ────────────────────────────────────────────────
 * Every helper accepts an optional ``locale`` parameter and falls back
 * to ``getLocale()`` (the user-selected UI locale) when omitted. This
 * keeps call sites terse (``formatBytes(n)``) while still allowing
 * tests to pin a specific locale (``formatBytes(n, "en")``).
 */
import { getLocale, type Locale, t } from "@/i18n/i18n";

// ── Legacy compactNumber (Dashboard / StatCards callers) ─────────────

export interface CompactNumberOptions {
	/**
	 * When true, appends ``"+"`` to the ``K`` suffix when the input
	 * has a non-zero remainder below 1000 (e.g. ``1234 → "1.2K+"``).
	 * Matches the legacy StatCards ``formatCompactNumber`` behaviour.
	 * Default: ``false`` (Dashboard's legacy behaviour — always ``"K"``).
	 */
	plusSuffix?: boolean;
	/**
	 * When true, formats sub-1000 values via
	 * ``n.toLocaleString(getLocale())`` so the digit grouping respects
	 * the user's selected UI locale (e.g. ``"١٢٣"`` in Arabic).
	 * Default: ``false`` (Dashboard's legacy behaviour — ``String(n)``).
	 */
	localeAware?: boolean;
}

/**
 * Format a number compactly using a ``K`` suffix for thousands.
 *
 * Examples (default options):
 *   - ``compactNumber(0)`` → ``"0"``
 *   - ``compactNumber(999)`` → ``"999"``
 *   - ``compactNumber(1000)`` → ``"1K"``
 *   - ``compactNumber(1234)`` → ``"1.2K"``
 *   - ``compactNumber(2000)`` → ``"2K"``
 *
 * With ``{ plusSuffix: true, localeAware: true }`` (StatCards legacy):
 *   - ``compactNumber(999, { localeAware: true })`` → locale-formatted ``"999"``
 *   - ``compactNumber(1234, { plusSuffix: true })`` → ``"1.2K+"``
 *   - ``compactNumber(2000, { plusSuffix: true })`` → ``"2K"`` (no remainder)
 *
 * @deprecated Prefer {@link formatCompactNumber} for new code — it uses
 *   ``Intl.NumberFormat`` with ``notation: "compact"`` so the suffix
 *   localises (``"K"`` / ``"k"`` / ``"천"`` / ``"ألف"`` …) and the
 *   digit grouping respects the user-selected UI locale. Kept here
 *   only to preserve the legacy Dashboard + StatCards call sites
 *   whose snapshot tests depend on the ``"K"`` / ``"K+"`` suffix.
 */
export function compactNumber(n: number, opts?: CompactNumberOptions): string {
	if (n >= 1000) {
		const k = n / 1000;
		const display = Math.floor(k * 10) / 10;
		const usePlus = opts?.plusSuffix ?? false;
		const suffix = usePlus && n % 1000 > 0 ? "K+" : "K";
		if (display === Math.floor(display)) {
			return `${Math.floor(display)}${suffix}`;
		}
		return `${display}${suffix}`;
	}
	if (opts?.localeAware) {
		return n.toLocaleString(getLocale());
	}
	return String(n);
}

// ── Locale resolution helper ─────────────────────────────────────────

/**
 * Resolve a locale string, falling back to the renderer's current
 * UI locale (``getLocale()``) when the caller doesn't supply one.
 * Kept private — every exported formatter takes a ``locale?: Locale``
 * parameter so call sites stay terse.
 */
function resolveLocale(locale?: Locale): Locale {
	return locale ?? getLocale();
}

// ── formatBytes ──────────────────────────────────────────────────────

/**
 * Format a byte count using locale-aware unit formatting.
 *
 * Uses ``Intl.NumberFormat`` with ``style: "unit"`` so digit grouping
 * and the ``"MB"`` / ``"GB"`` (or translated) label respect the
 * user-selected UI locale. Returns the locale-formatted ``"0 MB"``
 * for non-positive or non-finite values to match the legacy About.tsx
 * behaviour (the ``"0 MB"`` placeholder was the previous sentinel
 * for "no bytes / not yet measured").
 *
 * Examples (``en``):
 *   - ``formatBytes(0)`` → ``"0 MB"``
 *   - ``formatBytes(1024)`` → ``"1 KB"``
 *   - ``formatBytes(1024 * 1024)`` → ``"1 MB"``
 *   - ``formatBytes(500 * 1024 * 1024)`` → ``"500 MB"``
 *   - ``formatBytes(1024 ** 3)`` → ``"1 GB"``
 *
 * Examples (``de``): ``"1 MB"`` → ``"1 MB"`` (same glyphs, but the
 * thousands separator differs at higher magnitudes, e.g.
 * ``formatBytes(1_500_000_000, "de")`` → ``"1,5 GB"``).
 *
 * PVT-089: replaces the hardcoded ``"MB"`` / ``"GB"`` suffixes in
 * ``About.tsx``'s local ``formatBytes`` copy. (The local copy is kept
 * for now to avoid touching ``About.test.tsx``; future tasks should
 * migrate About.tsx to import from here.)
 */
export function formatBytes(bytes: number, locale?: Locale): string {
	if (!Number.isFinite(bytes) || bytes <= 0) {
		return new Intl.NumberFormat(resolveLocale(locale), {
			style: "unit",
			unit: "megabyte",
			maximumFractionDigits: 0,
		}).format(0);
	}
	const GB = 1024 ** 3;
	const MB = 1024 ** 2;
	const KB = 1024;
	const loc = resolveLocale(locale);
	if (bytes >= GB) {
		return new Intl.NumberFormat(loc, {
			style: "unit",
			unit: "gigabyte",
			maximumFractionDigits: 1,
		}).format(bytes / GB);
	}
	if (bytes >= MB) {
		return new Intl.NumberFormat(loc, {
			style: "unit",
			unit: "megabyte",
			maximumFractionDigits: 0,
		}).format(bytes / MB);
	}
	if (bytes >= KB) {
		return new Intl.NumberFormat(loc, {
			style: "unit",
			unit: "kilobyte",
			maximumFractionDigits: 1,
		}).format(bytes / KB);
	}
	return new Intl.NumberFormat(loc, {
		style: "unit",
		unit: "byte",
		maximumFractionDigits: 0,
	}).format(bytes);
}

// ── formatSpeed ──────────────────────────────────────────────────────

/**
 * Format a transfer speed (bytes per second) using locale-aware unit
 * formatting. Returns ``"—"`` for non-finite or negative input,
 * matching the legacy ``DownloadProgressBar.formatSpeed`` behaviour
 * (the ``"—"`` sentinel indicated "no measurement yet").
 *
 * Examples (``en``):
 *   - ``formatSpeed(0)`` → ``"0 B/s"``
 *   - ``formatSpeed(512)`` → ``"512 B/s"``
 *   - ``formatSpeed(2048)`` → ``"2 KB/s"``
 *   - ``formatSpeed(2_500_000)`` → ``"2.5 MB/s"``
 *   - ``formatSpeed(null)`` → ``"—"``
 *
 * Uses ``Intl.NumberFormat`` with ``style: "unit"`` and the
 * ``"{unit}-per-second"`` form so the locale's preferred separator
 * (``"/"`` in en, ``"⁄"`` in fr, …) is used.
 */
export function formatSpeed(
	bps: number | null | undefined,
	locale?: Locale,
): string {
	if (bps == null || !Number.isFinite(bps) || bps < 0) return "—";
	const GB = 1024 ** 3;
	const MB = 1024 ** 2;
	const KB = 1024;
	const loc = resolveLocale(locale);
	const fmt = (unit: string, value: number, maxFrac: number): string =>
		new Intl.NumberFormat(loc, {
			style: "unit",
			unit,
			unitDisplay: "short",
			maximumFractionDigits: maxFrac,
		}).format(value);
	if (bps >= GB) return fmt("gigabyte-per-second", bps / GB, 2);
	if (bps >= MB) return fmt("megabyte-per-second", bps / MB, 1);
	if (bps >= KB) return fmt("kilobyte-per-second", bps / KB, 0);
	return fmt("byte-per-second", bps, 0);
}

// ── formatDuration ───────────────────────────────────────────────────

/**
 * ``Intl.DurationFormat`` is part of ES2024.Intl — not yet in the
 * ``lib.es2020.intl`` shipped with the project's tsconfig. Feature-detect
 * via a narrowed cast so the runtime check is type-safe and we don't
 * have to wait for a TS lib upgrade to use the API when it's present.
 */
interface DurationFormatLike {
	format(parts: Record<string, number>): string;
}
type DurationFormatCtor = new (
	locale: string,
	options?: { style?: string },
) => DurationFormatLike;

function getDurationFormatCtor(): DurationFormatCtor | null {
	const ctor = (Intl as unknown as { DurationFormat?: DurationFormatCtor })
		.DurationFormat;
	return typeof ctor === "function" ? ctor : null;
}

/**
 * Format a duration (in seconds) as a locale-aware short string.
 *
 * Primary path: ``Intl.DurationFormat`` (Chromium ≥ 129, Node ≥ 22).
 * Falls back to a minimal ASCII composite (``"1h 5m"`` / ``"45m"`` /
 * ``"30s"``) when ``Intl.DurationFormat`` isn't available — this
 * matches the legacy ``Dashboard.formatDuration`` /
 * ``StatCards.formatDuration`` outputs so older runtimes don't regress.
 *
 * Examples (``en``):
 *   - ``formatDuration(0)`` → ``"0m"``
 *   - ``formatDuration(45)`` → ``"45s"``
 *   - ``formatDuration(120)`` → ``"2m"``
 *   - ``formatDuration(3900)`` → ``"1h 5m"``
 *
 * The seconds band is only shown when the duration is under a minute
 * — longer durations round to minutes/hours to keep stat-card layout
 * compact (matching the legacy Dashboard behaviour).
 *
 * PVT-090: replaces the hardcoded ``"h"`` / ``"m"`` suffixes in the
 * Dashboard / StatCards local ``formatDuration`` copies.
 */
export function formatDuration(seconds: number, locale?: Locale): string {
	const minuteLabel = "m";
	const hourLabel = "h";
	const secondLabel = "s";
	if (!Number.isFinite(seconds) || seconds <= 0) {
		return `0${minuteLabel}`;
	}
	const totalSeconds = Math.round(seconds);
	const hours = Math.floor(totalSeconds / 3600);
	const minutes = Math.floor((totalSeconds % 3600) / 60);
	const secs = totalSeconds % 60;
	const loc = resolveLocale(locale);

	// Preferred path: Intl.DurationFormat (Chromium ≥ 129).
	const Ctor = getDurationFormatCtor();
	if (Ctor) {
		try {
			const parts: Record<string, number> = {};
			if (hours > 0) parts.hours = hours;
			if (minutes > 0) parts.minutes = minutes;
			if (hours === 0 && minutes === 0) parts.seconds = secs;
			const fmt = new Ctor(loc, { style: "short" });
			return fmt.format(parts);
		} catch {
			// fall through to ASCII composite below
		}
	}

	// Fallback: ASCII short labels (matches legacy Dashboard/StatCards
	// outputs so older runtimes don't regress).
	if (hours > 0) {
		return minutes > 0
			? `${hours}${hourLabel} ${minutes}${minuteLabel}`
			: `${hours}${hourLabel}`;
	}
	if (minutes > 0) return `${minutes}${minuteLabel}`;
	return `${secs}${secondLabel}`;
}

// ── formatCompactNumber ──────────────────────────────────────────────

/**
 * Format a number compactly using ``Intl.NumberFormat`` with
 * ``notation: "compact"``. Suffix localises automatically
 * (``"K"`` / ``"M"`` / ``"B"`` in en, ``"mil"`` / ``"mm"`` in es,
 * ``"এক হাজার"`` in bn, …) and digit grouping respects the locale.
 *
 * Examples (``en``):
 *   - ``formatCompactNumber(0)`` → ``"0"``
 *   - ``formatCompactNumber(999)`` → ``"999"``
 *   - ``formatCompactNumber(1234)`` → ``"1.2K"``
 *   - ``formatCompactNumber(1_000_000)`` → ``"1M"``
 *
 * For the legacy ``"K"`` / ``"K+"`` suffix behaviour used by
 * Dashboard / StatCards, see {@link compactNumber}. New call sites
 * should prefer this helper.
 */
export function formatCompactNumber(n: number, locale?: Locale): string {
	if (!Number.isFinite(n)) return "0";
	return new Intl.NumberFormat(resolveLocale(locale), {
		notation: "compact",
		maximumFractionDigits: 1,
	}).format(n);
}

// ── formatRelativeTime ───────────────────────────────────────────────

/**
 * Format an ISO timestamp as a relative time string using
 * ``Intl.RelativeTimeFormat``. Returns ``t("about.neverRun")``
 * (``"Never"`` in en) for null / empty input, and the raw input
 * string when it can't be parsed as a date.
 *
 * Walks the standard relative-time divisions (seconds → minutes →
 * hours → days → weeks → months → years) and picks the smallest one
 * whose threshold the delta fits inside, then formats it with
 * ``Intl.RelativeTimeFormat({ numeric: "auto" })`` so the output is
 * naturally phrased (``"yesterday"`` instead of ``"1 day ago"``,
 * ``"now"`` instead of ``"in 0 seconds"``).
 *
 * For deltas beyond one year, falls back to ``Intl.DateTimeFormat``
 * with a medium date style — this matches the legacy About.tsx
 * behaviour of returning a real date string once "5 years ago" stops
 * being useful.
 *
 * Examples (``en``):
 *   - ``formatRelativeTime(null)`` → ``"Never"``
 *   - ``formatRelativeTime("not-a-date")`` → ``"not-a-date"``
 *   - ``formatRelativeTime(iso30sAgo)`` → ``"30 seconds ago"``
 *   - ``formatRelativeTime(iso5MinAgo)`` → ``"5 minutes ago"``
 *   - ``formatRelativeTime(iso3HrAgo)`` → ``"3 hours ago"``
 *   - ``formatRelativeTime(iso3DayAgo)`` → ``"3 days ago"``
 *   - ``formatRelativeTime(iso3MonthAgo)`` → ``"3 months ago"``
 *   - ``formatRelativeTime(iso3YearAgo)`` → medium-formatted date
 *
 * PVT-089: replaces the hardcoded ``"min ago"`` / ``"h ago"`` /
 * ``"d ago"`` suffixes in the About.tsx ``formatRelativeTime``
 * copy. About.tsx's local copy is kept for now (its tests depend on
 * the ``"min ago"`` / ``"h ago"`` / ``"d ago"`` shape); new code
 * should import from here.
 */
export function formatRelativeTime(
	iso: string | null,
	locale?: Locale,
): string {
	if (!iso) return t("about.neverRun");
	let then: number;
	try {
		then = new Date(iso).getTime();
		if (Number.isNaN(then)) return iso;
	} catch {
		return iso;
	}
	const now = Date.now();
	const diffMs = then - now;
	const loc = resolveLocale(locale);
	const rtf = new Intl.RelativeTimeFormat(loc, { numeric: "auto" });

	// Standard relative-time thresholds. The 4.34524 factor is the
	// ISO 8601 average weeks-per-month (365.25 / 12 / 7); using the
	// mean instead of a 28/29/30/31 switch keeps the helper pure
	// (no calendar lookup) while staying accurate to within a few
	// hours — well below the granularity of "last week" / "last
	// month" UX.
	const divisions: {
		amount: number;
		unit: Intl.RelativeTimeFormatUnit;
	}[] = [
		{ amount: 60, unit: "second" },
		{ amount: 60, unit: "minute" },
		{ amount: 24, unit: "hour" },
		{ amount: 7, unit: "day" },
		{ amount: 4.34524, unit: "week" },
		{ amount: 12, unit: "month" },
		{ amount: Number.POSITIVE_INFINITY, unit: "year" },
	];

	let duration = diffMs / 1000; // seconds
	for (const division of divisions) {
		if (Math.abs(duration) < division.amount) {
			return rtf.format(Math.round(duration), division.unit);
		}
		duration /= division.amount;
	}
	// >1y: fall back to a medium date format so users see an absolute
	// date instead of "2 years ago" (which is rarely actionable).
	return new Intl.DateTimeFormat(loc, { dateStyle: "medium" }).format(
		new Date(then),
	);
}

// ── formatDateTime ───────────────────────────────────────────────────

/**
 * Format an ISO timestamp as a locale-aware absolute date / time
 * string using ``Intl.DateTimeFormat``. Returns an empty string for
 * null / empty input and the raw input when unparseable.
 *
 * Uses ``dateStyle: "medium"`` + ``timeStyle: "short"`` so the output
 * is concise enough for stat-card sublabels (``"Jul 22, 2026, 3:04 PM"``
 * in en) while still being locale-aware (``"22 juil. 2026, 15:04"`` in
 * fr, ``"٢٢ يوليو ٢٠٢٦، ٣:٠٤ م"`` in ar with Arabic digits).
 *
 * Examples (``en``):
 *   - ``formatDateTime(null)`` → ``""``
 *   - ``formatDateTime("not-a-date")`` → ``"not-a-date"``
 *   - ``formatDateTime("2026-07-22T15:04:00Z")`` → locale-formatted date/time
 */
export function formatDateTime(iso: string | null, locale?: Locale): string {
	if (!iso) return "";
	let date: Date;
	try {
		date = new Date(iso);
		if (Number.isNaN(date.getTime())) return iso;
	} catch {
		return iso;
	}
	return new Intl.DateTimeFormat(resolveLocale(locale), {
		dateStyle: "medium",
		timeStyle: "short",
	}).format(date);
}

// ── formatVram ───────────────────────────────────────────────────────

/**
 * Format a VRAM amount (in megabytes) using locale-aware unit
 * formatting.
 *
 * PVT-091: previously ``pages/Models.tsx`` had its own inline copy
 * AND ``lib/utils/models.ts`` had a second copy — both hardcoded
 * ``"MB"`` / ``"GB"`` suffixes and used ``toFixed(1)`` for the GB
 * path. This single implementation replaces both. The Models module
 * re-exports this function so existing imports
 * (``import { formatVram } from "@/lib/utils/models"``) keep working
 * — see ``lib/utils/models.ts`` for the re-export.
 *
 * Examples (``en``):
 *   - ``formatVram(0)`` → ``"0 MB"``
 *   - ``formatVram(512)`` → ``"512 MB"``
 *   - ``formatVram(2048)`` → ``"2 GB"``
 *   - ``formatVram(1536)`` → ``"1.5 GB"``
 */
export function formatVram(mb: number, locale?: Locale): string {
	if (!Number.isFinite(mb) || mb <= 0) {
		return new Intl.NumberFormat(resolveLocale(locale), {
			style: "unit",
			unit: "megabyte",
			maximumFractionDigits: 0,
		}).format(0);
	}
	const loc = resolveLocale(locale);
	if (mb >= 1024) {
		return new Intl.NumberFormat(loc, {
			style: "unit",
			unit: "gigabyte",
			maximumFractionDigits: 1,
		}).format(mb / 1024);
	}
	return new Intl.NumberFormat(loc, {
		style: "unit",
		unit: "megabyte",
		maximumFractionDigits: 0,
	}).format(mb);
}
