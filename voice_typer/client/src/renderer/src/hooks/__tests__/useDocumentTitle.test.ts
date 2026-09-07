/**
 * Tests for useDocumentTitle (extracted from App.tsx).
 *
 * Contract: compose ``document.title`` as
 * ``<localised page title> — <APP_NAME>`` on mount and on every
 * route/locale change; Settings surfaces pull their key from the
 * section registry instead of ``nav.*``.
 */
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import type { Page } from "@/types/ipc";

/** Locale-reactive t stub — returns the key so assertions are exact. */
const t = vi.fn((key: string) => key);

let currentPage: Page = "home";

beforeEach(() => {
	t.mockClear();
	document.title = "";
	currentPage = "home";
});

afterEach(() => {
	document.title = "";
	vi.clearAllMocks();
});

describe("useDocumentTitle", () => {
	it("titles the initial mount from the nav key", () => {
		renderHook(() => useDocumentTitle({ currentPage, t }));
		expect(document.title).toBe("nav.home — Voice Typer");
	});

	it("uses the settings hub key for the settings surface", () => {
		currentPage = "settings";
		renderHook(() => useDocumentTitle({ currentPage, t }));
		expect(document.title).toBe("settings.title — Voice Typer");
	});

	it("uses the section registry key for a settings sub-page", () => {
		currentPage = "settingsPrivacy";
		renderHook(() => useDocumentTitle({ currentPage, t }));
		// The registry key (NOT a nav.* duplicate) — hub row, card
		// heading, and window title all read the same source.
		expect(t).toHaveBeenCalledWith("settings.privacy.privacyTitle");
		expect(document.title).toBe("settings.privacy.privacyTitle — Voice Typer");
	});

	it("re-titles when the route changes", () => {
		const { rerender } = renderHook(() => useDocumentTitle({ currentPage, t }));
		expect(document.title).toBe("nav.home — Voice Typer");

		currentPage = "history";
		rerender();
		expect(document.title).toBe("nav.history — Voice Typer");
	});

	it("re-titles when the locale (t identity) changes", () => {
		type Translate = (key: string, params?: Record<string, string>) => string;
		const { rerender } = renderHook(
			({ tt }: { tt: Translate }) => useDocumentTitle({ currentPage, t: tt }),
			{ initialProps: { tt: t } },
		);
		expect(document.title).toBe("nav.home — Voice Typer");
		// A locale switch hands out a NEW t closure resolving the same
		// key differently — the effect must re-fire and re-title.
		const otherLocale = vi.fn((key: string) => `de:${key}`);
		rerender({ tt: otherLocale });
		expect(document.title).toBe("de:nav.home — Voice Typer");
	});
});
