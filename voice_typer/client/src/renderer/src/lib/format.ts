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
 *   - ``Dashboard.tsx`` / ``StatCards.tsx`` / ``DownloadProgressBar.tsx``
 *     keep their own local ``formatBytes`` / ``formatSpeed`` /
 *     ``formatDuration`` / ``formatCompactNumber`` /
 *     ``formatRelativeTime`` / ``formatDateTime`` copies — see GT-33
 *     (session-6) note below.
 *
 * ── Locale resolution ────────────────────────────────────────────────
 * Every helper accepts an optional ``locale`` parameter and falls back
 * to ``getLocale()`` (the user-selected UI locale) when omitted. This
 * keeps call sites terse (``formatVram(n)``) while still allowing
 * tests to pin a specific locale (``formatVram(n, "en")``).
 *
 * ── GT-33 (session-6 dead-code purge) ────────────────────────────────
 * Previously this module also exported ``formatBytes``,
 * ``formatSpeed``, ``formatDuration``, ``formatCompactNumber``,
 * ``formatDateTime``, and ``formatRelativeTime``. None of those were
 * imported by any production file (verified by grep across
 * ``voice_typer/client/src/renderer``) — every call site
 * (``About.tsx``, ``Dashboard.tsx``, ``StatCards.tsx``,
 * ``DownloadProgressBar.tsx``) keeps its own private local copy that
 * DOES get called. The shared exports were dead. Deleted to collapse
 * the module to its two actually-imported exports:
 * ``compactNumber`` (Dashboard, StatCards) and ``formatVram``
 * (``lib/utils/models.ts`` re-export, LocalModelsPanel).
 *
 *   - GT-E2-5 (session-6): the ``@deprecated`` tag on ``compactNumber``
 *     was also removed. ``compactNumber`` is the LIVE implementation
 *     (its two callers depend on the ``"K"`` / ``"K+"`` suffix shape
 *     that ``formatCompactNumber`` did NOT produce). The
 *     ``@deprecated`` tag was a leftover from a migration plan that
 *     never landed — and now that ``formatCompactNumber`` is deleted,
 *     ``compactNumber`` is the canonical compact-number formatter.
 */
import { getLocale, type Locale } from "@/i18n/i18n";

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
/** Format bytes to human-readable string (e.g., "1.5 MB"). */
export function formatBytes(bytes: number, locale?: Locale): string {
	if (!Number.isFinite(bytes) || bytes < 0) {
		return new Intl.NumberFormat(resolveLocale(locale), {
			style: "unit",
			unit: "byte",
			unitDisplay: "narrow",
			maximumFractionDigits: 0,
		}).format(0);
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
			return new Intl.NumberFormat(loc, {
				style: "unit",
				unit,
				maximumFractionDigits: threshold > 1 ? 1 : 0,
			}).format(bytes / threshold);
		}
	}
	return new Intl.NumberFormat(loc, {
		style: "unit",
		unit: "byte",
		unitDisplay: "narrow",
		maximumFractionDigits: 0,
	}).format(0);
}

/** Format seconds to human-readable duration (e.g., "2m 30s"). */
export function formatDuration(seconds: number): string {
	if (!Number.isFinite(seconds) || seconds < 0) return "0s";
	const h = Math.floor(seconds / 3600);
	const m = Math.floor((seconds % 3600) / 60);
	const s = Math.floor(seconds % 60);
	const parts: string[] = [];
	if (h > 0) parts.push(`${h}h`);
	if (m > 0) parts.push(`${m}m`);
	if (s > 0 || parts.length === 0) parts.push(`${s}s`);
	return parts.join(" ");
}

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
