import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { APP_NAME } from "@/branding";
import { getBubbleAriaLabel } from "@/bubble/helpers";
import { t } from "@/i18n/i18n";
import {
	_setCurrentLocale,
	_translations,
	registerTranslations,
} from "@/i18n/store";
import { _invalidateResolvedCache } from "@/i18n/translate";

const helpersSource = readFileSync(
	join(dirname(fileURLToPath(import.meta.url)), "../helpers.ts"),
	"utf8",
);

describe("getBubbleAriaLabel mid-flow modes route through the locale catalog", () => {
	it("returns the catalog value with the {appName} placeholder substituted for each mid-flow mode", () => {
		// The English catalog values use the {appName} placeholder; the
		// loader substitutes APP_NAME at registration time, so the output
		// the only change is the SOURCE of the string (catalog, not a
		// hardcoded literal in the helper).
		expect(getBubbleAriaLabel("blocked")).toBe(`${APP_NAME} blocked indicator`);
		expect(getBubbleAriaLabel("cancelling")).toBe(
			`${APP_NAME} cancelling indicator`,
		);
		expect(getBubbleAriaLabel("permission_revoked")).toBe(
			`${APP_NAME} microphone permission revoked indicator`,
		);
		expect(getBubbleAriaLabel("paste_failed")).toBe(
			`${APP_NAME} paste failed indicator`,
		);
	});

	it("matches t() for the same key (single source of truth, no duplicate fallback copy)", () => {
		expect(getBubbleAriaLabel("blocked")).toBe(
			t("bubble.blockedIndicatorAria"),
		);
		expect(getBubbleAriaLabel("cancelling")).toBe(
			t("bubble.cancellingIndicatorAria"),
		);
		expect(getBubbleAriaLabel("permission_revoked")).toBe(
			t("bubble.permissionRevokedIndicatorAria"),
		);
		expect(getBubbleAriaLabel("paste_failed")).toBe(
			t("bubble.pasteFailedIndicatorAria"),
		);
	});

	it("localizes the mid-flow aria labels for a non-English locale (no English fallback)", async () => {
		// Sentinel locale tag so the real locale tables stay untouched.
		const TEST_LOCALE = "__ariaLocaleTest__" as unknown as Parameters<
			typeof registerTranslations
		>[0];
		registerTranslations(TEST_LOCALE, {
			bubble: {
				blockedIndicatorAria: "BLOCKIERT {appName}",
				cancellingIndicatorAria: "ABBRUCH {appName}",
				permissionRevokedIndicatorAria: "MIKRO {appName}",
				pasteFailedIndicatorAria: "EINFÜGEN {appName}",
			},
		});
		_setCurrentLocale(TEST_LOCALE);
		_invalidateResolvedCache(TEST_LOCALE);
		try {
			expect(getBubbleAriaLabel("blocked")).toBe(`BLOCKIERT ${APP_NAME}`);
			expect(getBubbleAriaLabel("cancelling")).toBe(`ABBRUCH ${APP_NAME}`);
			expect(getBubbleAriaLabel("permission_revoked")).toBe(
				`MIKRO ${APP_NAME}`,
			);
			expect(getBubbleAriaLabel("paste_failed")).toBe(`EINFÜGEN ${APP_NAME}`);
		} finally {
			_translations.delete(TEST_LOCALE);
			_invalidateResolvedCache(TEST_LOCALE);
			_setCurrentLocale("en");
		}
	});
});

describe("helpers.ts source hygiene (no hardcoded brand literals)", () => {
	it("contains no hardcoded brand string (the brand flows through the {appName} placeholder)", () => {
		expect(helpersSource).not.toContain(APP_NAME);
	});

	it("contains no tf() fallback literal for the mid-flow indicator aria keys", () => {
		// The four mid-flow modes must not carry an English fallback
		// literal anymore: the keys exist in every locale (see the
		// locale-key parity test), so t() alone suffices and a fallback
		// would be a second source of truth for the copy.
		for (const key of [
			"bubble.blockedIndicatorAria",
			"bubble.cancellingIndicatorAria",
			"bubble.permissionRevokedIndicatorAria",
			"bubble.pasteFailedIndicatorAria",
		]) {
			expect(helpersSource).not.toContain(`tf(\n\t\t\t\t"${key}"`);
			expect(helpersSource).not.toContain(`tf("${key}"`);
		}
	});
});
