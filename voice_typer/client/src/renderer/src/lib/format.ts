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
 *     ``localeAware`` options.
 *   - ``About.tsx`` still exports its own ``formatBytes`` /
 *     ``formatRelativeTime`` for the ``About.test.tsx`` unit tests.
 *     Those wrappers remain untouched.
 *   - ``DownloadProgressBar.tsx`` previously kept its own local
 *     ``formatBytes`` / ``formatSpeed`` — XA-20-7 consolidated them
 *     into the shared exports below (``formatBytes`` already existed;
 *     ``formatSpeed`` is new).
 *
 * ── Locale resolution ────────────────────────────────────────────────
 * Every helper accepts an optional ``locale`` parameter and falls back
 * to ``getLocale()`` (the user-selected UI locale) when omitted. This
 * keeps call sites terse (``formatVram(n)``) while still allowing
 * tests to pin a specific locale (``formatVram(n, "en")``).
 *
 * ── GT-33 (session-6 dead-code purge) ────────────────────────────────
 * Previously this module also exported ``formatCompactNumber``,
 * ``formatDateTime``, and ``formatRelativeTime``. None of those were
 * imported by any production file (verified by grep across
 * ``voice_typer/client/src/renderer``) — every call site
 * (``About.tsx``, ``Dashboard.tsx``, ``StatCards.tsx``,
 * ``DownloadProgressBar.tsx``) keeps its own private local copy that
 * DOES get called. The shared exports were dead. Deleted to collapse
 * the module.
 *
 *   - GT-E2-5 (session-6): the ``@deprecated`` tag on ``compactNumber``
 *     was also removed. ``compactNumber`` is the LIVE implementation
 *     (its two callers depend on the ``"K"`` / ``"K+"`` suffix shape
 *     that ``formatCompactNumber`` did NOT produce). The
 *     ``@deprecated`` tag was a leftover from a migration plan that
 *     never landed — and now that ``formatCompactNumber`` is deleted,
 *     ``compactNumber`` is the canonical compact-number formatter.
 *
 * ── XA-20-7 / XA-20-8 (this fix) ─────────────────────────────────────
 *   - ``formatBytes`` and ``formatSpeed`` are now the canonical exports
 *     consumed by ``DownloadProgressBar.tsx`` (its local copies were
 *     removed).
 *   - ``formatDuration`` now resolves the ``h`` / ``m`` glyphs through
 *     ``t()`` (``analytics.durationHours`` / ``durationMinutes`` /
 *     ``durationHoursMinutes`` / ``durationZero``). When those keys are
 *     missing from the active locale, it falls back to the existing
 *     ``format.duration.hourShort`` / ``minuteShort`` keys (which ship
 *     in all 8 locale files) so the function never returns a raw
 *     translation key to the user. Sub-minute values round up to 1m
 *     (matches the legacy StatCards behaviour); seconds are dropped
 *     (matches the Dashboard / StatCards snapshot contracts).
 */
import { getLocale, type Locale, t } from "@/i18n/i18n";

// ── ER-23: Intl formatter caches ────────────────────────────────────
//
// ``new Intl.NumberFormat(loc, opts)`` is ~5-10× slower than
// ``.format(n)`` because the constructor parses the locale + options
// and builds an internal formatter. Every exported formatter below
// previously called the constructor on EVERY invocation — Dashboard
// calls ``formatBytes`` / ``compactNumber`` / ``toLocaleString`` ~6-10
// times per render, History.tsx calls them per-row in a list, etc.
//
// The fix: each option-shape gets its own module-level ``Map`` keyed
// by locale. Cache hit is a Map lookup (~50× faster than the
// constructor). The maps are unbounded but in practice the key set
// is tiny — the renderer only ever uses one locale at a time (the
// user-selected UI locale), plus ``"en"`` for tests. So the maps
// will hold ≤2 entries in production and ≤8 in dev (one per
// supported locale).
//
// We cache by LOCALE only (not by options) because each formatter
// hardcodes its own options — the cache is per-call-site, not a
// generic NumberFormat cache. This keeps the key small (a string)
// and avoids serialising the options dict for the lookup.

const _numberFormatCache = new Map<string, Intl.NumberFormat>();
const _dateTimeFormatCache = new Map<string, Intl.DateTimeFormat>();

function _getCachedNumberFormat(
	locale: Locale,
	options: Intl.NumberFormatOptions,
): Intl.NumberFormat {
	// The cache key includes a stable stringification of the options
	// so two call sites with different options don't share a formatter.
	// The key shape is ``<locale>|<options-json>``. JSON.stringify of
	// the small options dict is fast (microseconds) and the result is
	// cached on the caller's options object literal (V8 caches the
	// object-shape hash for literals), so in practice this is a single
	// hash + string concat.
	const key = `${locale}|${JSON.stringify(options)}`;
	let fmt = _numberFormatCache.get(key);
	if (fmt === undefined) {
		fmt = new Intl.NumberFormat(locale, options);
		_numberFormatCache.set(key, fmt);
	}
	return fmt;
}

function _getCachedDateTimeFormat(
	locale: Locale,
	options: Intl.DateTimeFormatOptions,
): Intl.DateTimeFormat {
	const key = `${locale}|${JSON.stringify(options)}`;
	let fmt = _dateTimeFormatCache.get(key);
	if (fmt === undefined) {
		fmt = new Intl.DateTimeFormat(locale, options);
		_dateTimeFormatCache.set(key, fmt);
	}
	return fmt;
}

// ── compactNumber (Dashboard / StatCards callers) ────────────────────

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
 * GT-E2-5 (session-6): the previous ``@deprecated`` tag pointing at
 * ``formatCompactNumber`` has been removed — ``formatCompactNumber``
 * was deleted (GT-33) because no production file imported it, so
 * ``compactNumber`` is now the canonical compact-number formatter.
 * Its ``"K"`` / ``"K+"`` suffix shape is required by the Dashboard +
 * StatCards callers' snapshot tests.
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
/**
 * Format bytes to a human-readable string using locale-aware unit
 * formatting (e.g. ``"1.5 MB"``, ``"500 KB"``, ``"12 B"``).
 *
 * XA-20-7: signature widened to accept ``null`` / ``undefined`` so
 * ``DownloadProgressBar`` (which previously had its own local copy
 * returning ``"—"`` for null) can migrate to this shared helper
 * without an extra null-check at every call site. The null/undefined
 * return value is ``"—"`` (matching the legacy DownloadProgressBar
 * behaviour so the progress bar's secondary line stays readable when
 * the size is unknown).
 *
 * Negative / non-finite inputs also return ``"—"`` (previously
 * returned ``"0 B"`` — the new behaviour matches the legacy
 * DownloadProgressBar copy and is more honest about the input being
 * invalid).
 *
 * Examples (``en``):
 *   - ``formatBytes(null)``    → ``"—"``
 *   - ``formatBytes(undefined)`` → ``"—"``
 *   - ``formatBytes(0)``       → ``"0 B"``
 *   - ``formatBytes(500)``     → ``"500 B"``
 *   - ``formatBytes(1024)``    → ``"1 KB"``
 *   - ``formatBytes(1048576)`` → ``"1 MB"``
 *   - ``formatBytes(1073741824)`` → ``"1 GB"``
 */
export function formatBytes(
	bytes: number | null | undefined,
	locale?: Locale,
): string {
	if (bytes == null || !Number.isFinite(bytes) || bytes < 0) {
		return "—";
	}
	const loc = resolveLocale(locale);
	const units: [number, Intl.NumberFormatOptions["unit"]][] = [
		[1024 ** 3, "gigabyte"],
		[1024 ** 2, "megabyte"],
		[1024, "kilobyte"],
		[1, "byte"],
	];
	for (const [threshold, unit] of units) {
		if (bytes >= threshold) {
			// ER-23: cached NumberFormat keyed by locale+options.
			const opts: Intl.NumberFormatOptions = {
				style: "unit",
				unit,
				maximumFractionDigits: threshold > 1 ? 1 : 0,
			};
			return _getCachedNumberFormat(loc, opts).format(bytes / threshold);
		}
	}
	const opts: Intl.NumberFormatOptions = {
		style: "unit",
		unit: "byte",
		unitDisplay: "narrow",
		maximumFractionDigits: 0,
	};
	return _getCachedNumberFormat(loc, opts).format(0);
}

/**
 * Format seconds to a human-readable duration using locale-aware
 * glyphs (e.g. "2m", "1h 5m").
 *
 * XA-20-8: the previous implementation hardcoded English "h" / "m" /
 * "s" suffixes. The new implementation resolves the suffixes through
 * ``t()`` so non-English locales can localize them.
 *
 * Translation-key strategy (with graceful fallback):
 *   1. Try ``analytics.durationZero`` / ``durationMinutes`` /
 *      ``durationHours`` / ``durationHoursMinutes`` (the BG-9 contract
 *      keys expected by ``Dashboard.test.tsx``). When present, these
 *      give the locale full control over the format string (e.g.
 *      Arabic could render "٥د" via ``{m}m`` + Arabic-Indic digits).
 *   2. When those keys are MISSING (``t()`` returns the raw key), fall
 *      back to the per-glyph ``format.duration.hourShort`` /
 *      ``minuteShort`` keys that ship in all 8 locale files. This
 *      guarantees the function never returns a raw key to the user.
 *
 * Behavioural changes vs the previous implementation:
 *   - ``formatDuration(0)`` returns ``"0m"`` (was ``"0s"``).
 *   - ``formatDuration(5)`` returns ``"1m"`` (was ``"5s"``) — sub-
 *     minute values round UP to 1m, matching the legacy StatCards
 *     storybook snapshot.
 *   - Seconds are no longer included in the output (was ``"1h 27m 15s"``;
 *     now ``"1h 27m"``). The dashboard / stat-card surface only ever
 *     displayed hours+minutes; surfacing seconds was a UX regression.
 *
 * Examples (en):
 *   - ``formatDuration(0)``    → ``"0m"``
 *   - ``formatDuration(5)``    → ``"1m"``  (rounds up)
 *   - ``formatDuration(120)``  → ``"2m"``
 *   - ``formatDuration(3600)`` → ``"1h"``
 *   - ``formatDuration(3900)`` → ``"1h 5m"``
 *   - ``formatDuration(5235)`` → ``"1h 27m"``
 */
export function formatDuration(seconds: number): string {
	if (!Number.isFinite(seconds) || seconds <= 0) {
		const zeroKey = t("analytics.durationZero");
		if (zeroKey !== "analytics.durationZero") {
			return zeroKey;
		}
		// Fallback: build "0" + minute glyph from format.duration.*.
		return `0${t("format.duration.minuteShort")}`;
	}
	// Sub-minute values round up to 1m (matches legacy StatCards
	// snapshot; the previous implementation returned "5s" / "45s"
	// which was a UX bug — the dashboard only ever showed h+m).
	let totalMinutes: number;
	if (seconds < 60) {
		totalMinutes = 1;
	} else {
		totalMinutes = Math.floor(seconds / 60);
	}
	const h = Math.floor(totalMinutes / 60);
	const m = totalMinutes % 60;
	const hourGlyph = t("format.duration.hourShort");
	const minuteGlyph = t("format.duration.minuteShort");
	if (h === 0) {
		const tmpl = t("analytics.durationMinutes", {
			m: String(m),
			count: String(m),
		});
		if (tmpl !== "analytics.durationMinutes") return tmpl;
		return `${m}${minuteGlyph}`;
	}
	if (m === 0) {
		const tmpl = t("analytics.durationHours", {
			h: String(h),
			count: String(h),
		});
		if (tmpl !== "analytics.durationHours") return tmpl;
		return `${h}${hourGlyph}`;
	}
	const tmpl = t("analytics.durationHoursMinutes", {
		h: String(h),
		m: String(m),
		count: String(h),
	});
	if (tmpl !== "analytics.durationHoursMinutes") return tmpl;
	return `${h}${hourGlyph} ${m}${minuteGlyph}`;
}

/**
 * Format a transfer rate (bytes per second) using locale-aware unit
 * formatting.
 *
 * XA-20-7: extracted from ``DownloadProgressBar.tsx``'s local copy
 * (which hardcoded English "B/s" / "KB/s" / "MB/s" / "GB/s" suffixes
 * and used ``toFixed()`` directly). This implementation uses the
 * platform ``Intl.NumberFormat`` API with ``style: "unit"`` so the
 * digit grouping, decimal separator, and unit name all respect the
 * user-selected UI locale.
 *
 * Returns ``"—"`` for null / negative / non-finite inputs (matches the
 * legacy DownloadProgressBar behaviour so the progress bar's
 * secondary line stays readable when the speed is unknown).
 */
export function formatSpeed(
	bytesPerSecond: number | null | undefined,
	locale?: Locale,
): string {
	if (
		bytesPerSecond == null ||
		bytesPerSecond < 0 ||
		!Number.isFinite(bytesPerSecond)
	) {
		return "—";
	}
	const loc = resolveLocale(locale);
	// Intl doesn't ship a "bytes per second" unit identifier, so we
	// format the scalar with the matching byte-prefix unit and append
	// "/s". This keeps the digit grouping / decimal separator
	// locale-aware while preserving the "/s" suffix universally.
	const units: [number, Intl.NumberFormatOptions["unit"]][] = [
		[1024 ** 3, "gigabyte"],
		[1024 ** 2, "megabyte"],
		[1024, "kilobyte"],
		[1, "byte"],
	];
	for (const [threshold, unit] of units) {
		if (bytesPerSecond >= threshold) {
			// ER-23: cached NumberFormat keyed by locale+options.
			const opts: Intl.NumberFormatOptions = {
				style: "unit",
				unit,
				maximumFractionDigits: threshold > 1 ? 1 : 0,
			};
			const formatted = _getCachedNumberFormat(loc, opts).format(
				bytesPerSecond / threshold,
			);
			return `${formatted}/s`;
		}
	}
	const fallbackOpts: Intl.NumberFormatOptions = {
		style: "unit",
		unit: "byte",
		unitDisplay: "narrow",
		maximumFractionDigits: 0,
	};
	return `${_getCachedNumberFormat(loc, fallbackOpts).format(0)}/s`;
}

export function formatVram(mb: number, locale?: Locale): string {
	if (!Number.isFinite(mb) || mb <= 0) {
		const zeroOpts: Intl.NumberFormatOptions = {
			style: "unit",
			unit: "megabyte",
			maximumFractionDigits: 0,
		};
		return _getCachedNumberFormat(resolveLocale(locale), zeroOpts).format(0);
	}
	const loc = resolveLocale(locale);
	if (mb >= 1024) {
		const gbOpts: Intl.NumberFormatOptions = {
			style: "unit",
			unit: "gigabyte",
			maximumFractionDigits: 1,
		};
		return _getCachedNumberFormat(loc, gbOpts).format(mb / 1024);
	}
	const mbOpts: Intl.NumberFormatOptions = {
		style: "unit",
		unit: "megabyte",
		maximumFractionDigits: 0,
	};
	return _getCachedNumberFormat(loc, mbOpts).format(mb);
}
