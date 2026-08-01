/**
 * Unit tests for the ``_safeT`` i18n fallback helper in
 * ``globalErrorHandler.ts``.
 *
 * Background: ``_safeT(key, fallback)`` resolves the toast action-button
 * labels (e.g. "View logs" / "Copy error"). The underlying ``t()``
 * function (see ``i18n/translate.ts``) returns the RAW KEY STRING when
 * the key is missing from BOTH the active locale AND the English
 * fallback table — so a naive "is the string non-empty?" check would
 * let the raw dot-path (e.g. ``"errors.viewLogsAction"``) leak through
 * to the UI as the button label. ``_safeT`` is responsible for
 * detecting that case (``msg === key``) and substituting the provided
 * English fallback instead.
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

		expect(_safeT("errors.viewLogsAction", "View logs")).toBe("View logs");
		expect(_safeT("errors.copyErrorAction", "Copy error")).toBe("Copy error");
	});

	it("returns the English fallback (NOT the raw key) when the key is missing", () => {
		// Simulate the real ``t()`` behavior for a missing key: return the
		// raw key string verbatim (this is what ``translate.ts:147`` does
		// when neither the active locale nor English has the key).
		vi.mocked(t).mockImplementation((key: string) => key);

		expect(_safeT("errors.viewLogsAction", "View logs")).toBe("View logs");
		expect(_safeT("errors.copyErrorAction", "Copy error")).toBe("Copy error");
	});

	it("does NOT return the raw dot-path key when the key is missing", () => {
		// This is the regression guard: pre-fix, ``_safeT`` returned the
		// raw key string (e.g. "errors.viewLogsAction") as the button
		// label. The fix must guarantee that the raw key never escapes.
		vi.mocked(t).mockImplementation((key: string) => key);

		const viewLogsLabel = _safeT("errors.viewLogsAction", "View logs");
		const copyErrorLabel = _safeT("errors.copyErrorAction", "Copy error");

		expect(viewLogsLabel).not.toBe("errors.viewLogsAction");
		expect(copyErrorLabel).not.toBe("errors.copyErrorAction");
	});

	it("returns the fallback when t() returns an empty string", () => {
		// Defensive: even though the real ``t()`` returns the key (not "")
		// for a missing key, the ``msg.length > 0`` guard is still
		// meaningful for keys whose translation value IS an empty string.
		// ``_safeT`` must fall back rather than render an empty label.
		vi.mocked(t).mockImplementation(() => "");

		expect(_safeT("errors.viewLogsAction", "View logs")).toBe("View logs");
	});

	it("returns the fallback when t() throws", () => {
		// The try/catch inside ``_safeT`` must swallow a throwing ``t()``
		// and yield the fallback rather than propagate the error.
		vi.mocked(t).mockImplementation(() => {
			throw new Error("i18n unavailable");
		});

		expect(_safeT("errors.viewLogsAction", "View logs")).toBe("View logs");
	});
});
