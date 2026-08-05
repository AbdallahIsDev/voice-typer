/**
 * Unit tests for the ``_safeT`` i18n fallback helper in
 * ``globalErrorHandler.ts``.
 *
 * Background: ``_safeT(key)`` resolves the toast action-button labels
 * (e.g. "View logs" / "Copy error"). The underlying ``t()`` function
 * (see ``i18n/translate.ts``) walks the currentLocale → primary-subtag
 * → en → raw-key chain — it never throws and returns the raw dot-path
 * key as the last-resort fallback (e.g. ``t("errors.viewLogsAction")``
 * returns ``"errors.viewLogsAction"`` when the key is missing from
 * BOTH the active locale AND the English fallback table).
 *
 * WM-C5-F9 (this revision): the previous implementation accepted an
 * English ``fallback`` parameter and substituted it whenever ``t()``
 * returned the raw key (or threw, or returned an empty string). That
 * silently masked missing-key bugs — a translator who forgot to add
 * ``errors.viewLogsAction`` to a locale file would never see the gap
 * because the English fallback always won. The new implementation
 * drops the English fallback entirely and lets the i18n layer's own
 * raw-key fallback surface the gap. The defensive try/catch is
 * retained so a FUTURE ``t()`` implementation that throws can never
 * break the global error handler; in that case ``_safeT`` returns the
 * raw key (not a hardcoded English string).
 *
 * These tests mock ``@/i18n/i18n`` so we can deterministically control
 * what ``t()`` returns per case — without depending on which keys
 * happen to exist in the translation JSON files (which is owned by a
 * separate work stream and may change over time).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the i18n module so we can control what ``t()`` returns per test.
// The default implementation mirrors the real ``t()`` "missing key"
// behavior: return the raw key string verbatim.
vi.mock("@/i18n/i18n", () => ({
	t: vi.fn((key: string) => key),
}));

import { t } from "@/i18n/i18n";
import { _safeT } from "@/lib/globalErrorHandler";

describe("_safeT i18n fallback helper", () => {
	beforeEach(() => {
		vi.mocked(t).mockClear();
	});

	it("returns the translated value when the key exists", () => {
		// Simulate a successful translation lookup: the key resolves to a
		// localized (or English) string that is NOT the raw key.
		vi.mocked(t).mockImplementation((key: string) => {
			if (key === "errors.viewLogsAction") return "View logs";
			if (key === "errors.copyErrorAction") return "Copy error";
			return key;
		});

		expect(_safeT("errors.viewLogsAction")).toBe("View logs");
		expect(_safeT("errors.copyErrorAction")).toBe("Copy error");
	});

	it("returns the raw key (letting the i18n layer surface the gap) when the key is missing", () => {
		// WM-C5-F9: when the key is missing from BOTH the active locale AND
		// the English fallback table, ``t()`` returns the raw dot-path key
		// verbatim (see ``translate.ts``). ``_safeT`` must NOT mask this
		// with a hardcoded English string — the raw key unambiguously
		// signals broken i18n to the developer.
		vi.mocked(t).mockImplementation((key: string) => key);

		expect(_safeT("errors.viewLogsAction")).toBe("errors.viewLogsAction");
		expect(_safeT("errors.copyErrorAction")).toBe("errors.copyErrorAction");
	});

	it("returns the raw key when t() throws", () => {
		// The defensive try/catch inside ``_safeT`` must swallow a throwing
		// ``t()`` and yield the raw key (NOT a hardcoded English string)
		// so a future broken i18n implementation is visible rather than
		// silently masked.
		vi.mocked(t).mockImplementation(() => {
			throw new Error("i18n unavailable");
		});

		expect(_safeT("errors.viewLogsAction")).toBe("errors.viewLogsAction");
	});
});
