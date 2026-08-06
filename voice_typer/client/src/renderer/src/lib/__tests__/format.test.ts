/**
 * Unit tests for the shared locale-aware formatters in ``lib/format.ts``.
 *
 * ``format.ts`` previously had ZERO unit tests. The module
 * is consumed by ``Dashboard.tsx``, ``StatCards.tsx``,
 * ``DownloadProgressBar.tsx``, and ``pages/Models.tsx`` — every
 * stat-card label, every download-progress readout, and every VRAM
 * figure flows through these helpers. A silent regression in any of
 * them (e.g. a locale lookup breaking, a unit threshold off-by-one)
 * would surface only as a visual glitch in the dashboard with no
 * failing test to point at the cause.
 *
 * Coverage:
 *   - ``formatBytes`` — null/undefined/(-1) → ``"—"``; ``0`` → reasonable
 *     value (the ``en`` locale produces ``"0 B"``); kilobyte / megabyte
 *     thresholds.
 *   - ``formatDuration`` — ``0`` → ``"0m"`` (sub-minute rounds up to 1m
 *     per the legacy StatCards snapshot contract; 0 is a special-case
 *     that returns ``"0m"`` not ``"1m"``).
 *   - ``compactNumber`` — ``999`` → ``"999"``; ``1500`` → locale-dependent
 *     compact output (``"1.5K"`` for ``en``).
 *   - ``formatSpeed`` — ``0`` → ``"0 B/s"`` for ``en``.
 *   - ``formatVram`` — ``0`` → ``"0 MB"`` for ``en``.
 *
 * Mocking strategy: the i18n module is mocked so ``getLocale()``
 * returns ``"en"`` (deterministic — Intl output for compact/byte
 * units is stable across Node versions for the ``en`` locale) and
 * ``t()`` returns the raw key string (matching the "missing key"
 * fallback behavior). This decouples the tests from the locale JSON contents.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the i18n module so the formatters resolve against a deterministic
// ``en`` locale. The real ``getLocale()`` reads from a module-scoped
// mutable variable (see ``i18n/store.ts:50``) — its default is ``"en"``
// but tests that mount <App /> can flip it via ``setLocale()``. By
// pinning the mock here we guarantee the Intl output is stable across
// the suite regardless of test execution order.
vi.mock("@/i18n/i18n", () => ({
	getLocale: vi.fn(() => "en"),
	// ``t()`` mirrors the real "missing key" behavior: return the raw
	// key string verbatim. ``formatDuration`` uses this to detect when
	// a key is absent (``key === returned``) and falls back to the
	// ``format.duration.hourShort`` / ``minuteShort`` glyphs (which
	// also resolve to raw keys here — but the fallback path still
	// produces a non-key string because it builds ```${m}${glyph}``
	// where ``m`` is a number, so the result is e.g. ``"0hourShort"``
	// — wait, that's not right).
	//
	// Actually — to keep ``formatDuration`` readable in tests, we
	// provide minimal stubs for the glyphs it falls back to. This
	// avoids the raw-key leak while still exercising the fallback path.
	t: vi.fn((key: string) => {
		const stubs: Record<string, string> = {
			"format.duration.hourShort": "h",
			"format.duration.minuteShort": "m",
		};
		return stubs[key] ?? key;
	}),
}));

import { getLocale } from "@/i18n/i18n";
import {
	compactNumber,
	formatBytes,
	formatDuration,
	formatSpeed,
	formatVram,
} from "@/lib/format";

describe("formatBytes", () => {
	beforeEach(() => {
		vi.mocked(getLocale).mockClear();
	});

	it("returns '—' for null", () => {
		// The DownloadProgressBar's "size unknown" state — null comes
		// from the IPC envelope when the backend hasn't reported a
		// Content-Length yet.
		expect(formatBytes(null)).toBe("—");
	});

	it("returns '—' for undefined", () => {
		expect(formatBytes(undefined)).toBe("—");
	});

	it("returns '—' for negative values", () => {
		// Negative bytes are nonsensical (treat as invalid input —
		// previously this returned "0 B" which was misleading).
		expect(formatBytes(-1)).toBe("—");
		expect(formatBytes(-1024)).toBe("—");
	});

	it("returns '—' for non-finite values (NaN / Infinity)", () => {
		expect(formatBytes(Number.NaN)).toBe("—");
		expect(formatBytes(Number.POSITIVE_INFINITY)).toBe("—");
	});

	it("returns a reasonable value for 0 (en narrow-byte: '0B')", () => {
		// The exact narrow-unit output for ``en`` is ``"0B"`` (Intl
		// ``unitDisplay: 'narrow'`` drops the space). The test
		// pins this so a regression in the byte-unit threshold or
		// the cache key surfaces immediately. We assert both the
		// exact string AND the looser "reasonable value" contract
		// from the acceptance criteria (non-empty, starts
		// with the digit 0).
		const result = formatBytes(0);
		expect(result).toBe("0B");
		expect(result.startsWith("0")).toBe(true);
		expect(result.length).toBeGreaterThan(0);
	});

	it("formats kilobyte range correctly (en narrow-unit: '1 kB')", () => {
		// Intl.NumberFormat(en, { style: 'unit', unit: 'kilobyte' })
		// produces "1 kB" (lowercase "kB" — SI convention).
		// maximumFractionDigits for the >1 threshold is 1, so
		// 1536 → "1.5 kB".
		expect(formatBytes(1024)).toBe("1 kB");
		expect(formatBytes(1536)).toBe("1.5 kB");
	});

	it("formats megabyte range correctly (en)", () => {
		expect(formatBytes(1024 * 1024)).toBe("1 MB");
		expect(formatBytes(1024 * 1024 * 1.5)).toBe("1.5 MB");
	});
});

describe("formatDuration", () => {
	it("returns '0m' for 0 seconds (en fallback)", () => {
		// 0 is a special case — the function returns "0m" via the
		// ``analytics.durationZero`` key (missing → fallback to
		// ``"0" + minuteGlyph``). With the mocked ``t()``,
		// ``minuteGlyph`` is "m", so the result is "0m".
		expect(formatDuration(0)).toBe("0m");
	});

	it("returns '0m' for negative / non-finite values", () => {
		// Defensive: negative durations (which would arise from a
		// clock-skew bug in the backend) and NaN should fall into
		// the same "zero" branch as 0.
		expect(formatDuration(-5)).toBe("0m");
		expect(formatDuration(Number.NaN)).toBe("0m");
	});

	it("rounds up sub-minute values to '1m'", () => {
		// 5 seconds → "1m" (not "5s") — matches the legacy
		// StatCards snapshot contract.
		expect(formatDuration(5)).toBe("1m");
		expect(formatDuration(59)).toBe("1m");
	});

	it("formats whole minutes correctly (en)", () => {
		expect(formatDuration(120)).toBe("2m");
		expect(formatDuration(600)).toBe("10m");
	});

	it("formats hours correctly (en)", () => {
		expect(formatDuration(3600)).toBe("1h");
		expect(formatDuration(7200)).toBe("2h");
	});

	it("formats hours+minutes correctly (en)", () => {
		expect(formatDuration(3900)).toBe("1h 5m");
		expect(formatDuration(5235)).toBe("1h 27m");
	});
});

describe("compactNumber", () => {
	beforeEach(() => {
		vi.mocked(getLocale).mockClear();
	});

	it("returns '999' for sub-1000 values (default opts)", () => {
		// Default opts: sub-1000 values use ``String(n)`` — no
		// locale-aware digit grouping. This matches the legacy
		// Dashboard contract.
		expect(compactNumber(999)).toBe("999");
		expect(compactNumber(0)).toBe("0");
	});

	it("returns '1K' for exactly 1000 (en)", () => {
		// Intl.NumberFormat(en, { notation: "compact" }).format(1000)
		// === "1K" — matches the previous hardcoded shape exactly.
		expect(compactNumber(1000)).toBe("1K");
	});

	it("returns '1.5K' for 1500 (en locale-dependent compact)", () => {
		// 1500 / 1000 = 1.5; Intl compact notation with
		// maximumFractionDigits: 1 → "1.5K". This is the
		// locale-dependent compact output the previous fix introduces
		// (previously: hardcoded "1.5K" via Math.floor arithmetic).
		expect(compactNumber(1500)).toBe("1.5K");
	});

	it("returns '2K' for 2000 (en)", () => {
		expect(compactNumber(2000)).toBe("2K");
	});

	it("appends '+' with plusSuffix when there is a remainder", () => {
		// StatCards caller passes { plusSuffix: true }. The "+"
		// is appended after Intl formatting when n % 1000 > 0.
		expect(compactNumber(1234, { plusSuffix: true })).toBe("1.2K+");
		expect(compactNumber(2000, { plusSuffix: true })).toBe("2K");
	});

	it("uses locale-aware digit grouping for sub-1000 values with localeAware opt", () => {
		// The localeAware opt only affects sub-1000 values (the
		// >=1000 path is ALWAYS locale-aware now). For ``en``
		// locale, 999 → "999" (no digit grouping needed).
		expect(compactNumber(999, { localeAware: true })).toBe("999");
	});
});

describe("formatSpeed", () => {
	beforeEach(() => {
		vi.mocked(getLocale).mockClear();
	});

	it("returns '—' for null / negative / non-finite", () => {
		expect(formatSpeed(null)).toBe("—");
		expect(formatSpeed(-1)).toBe("—");
		expect(formatSpeed(Number.NaN)).toBe("—");
	});

	it("returns '0B/s' for 0 (en narrow-byte)", () => {
		// The fallback byte-unit path with ``unitDisplay: "narrow"``
		// produces "0B" (no space — Intl narrow-unit convention)
		// and the "/s" suffix is appended.
		const result = formatSpeed(0);
		expect(result).toBe("0B/s");
		// Looser contract: a reasonable value.
		expect(result.startsWith("0")).toBe(true);
		expect(result.endsWith("/s")).toBe(true);
	});

	it("formats kilobyte/s range correctly (en)", () => {
		// Intl uses lowercase "kB" (SI) for the kilobyte unit in
		// the ``en`` locale.
		expect(formatSpeed(1024)).toBe("1 kB/s");
		expect(formatSpeed(1536)).toBe("1.5 kB/s");
	});
});

describe("formatVram", () => {
	beforeEach(() => {
		vi.mocked(getLocale).mockClear();
	});

	it("returns '0 MB' for 0 (en)", () => {
		// 0 falls into the ``mb <= 0`` branch — formats 0 with
		// the megabyte unit.
		expect(formatVram(0)).toBe("0 MB");
	});

	it("returns '0 MB' for negative / non-finite", () => {
		expect(formatVram(-1)).toBe("0 MB");
		expect(formatVram(Number.NaN)).toBe("0 MB");
	});

	it("formats megabyte range correctly (en, with digit grouping)", () => {
		// Intl ``en`` locale applies digit grouping for 4+ digit
		// values: 1023 → "1,023 MB". 512 has no grouping needed.
		expect(formatVram(512)).toBe("512 MB");
		expect(formatVram(1023)).toBe("1,023 MB");
	});

	it("formats gigabyte range correctly (en)", () => {
		expect(formatVram(1024)).toBe("1 GB");
		expect(formatVram(1536)).toBe("1.5 GB");
	});
});
