/**
 * Focused contracts for the onboarding / focus-ring / i18n fix batch:
 *
 * - onboarding init fallback uses t("errorBoundary.unknownError")
 *   instead of a hardcoded English string (all 8 locales already ship
 *   the key).
 * - onboarding.backendAria is genuinely translated in de/es/fr/hi/ru/zh
 *   (ar already was).
 * - Welcome/Consent share the one HEADING_CLASS from
 *   pages/onboarding/lib/constants (same as Model/Hotkey).
 * - The onboarding init-error card carries a focus-visible ring
 *   (C-FOCUS-1: the programmatic focus target must stay visible).
 * - MicToggleButton / SearchField clear / CloudProvidersPanel reveal /
 *   ActivityList show-more use ring-1at full opacity (C-FOCUS-2/5),
 *   never the thinner focus-visible:ring-1.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SearchField } from "@/components/common/SearchField";
import ar from "@/i18n/translations/ar.json";
import de from "@/i18n/translations/de.json";
import en from "@/i18n/translations/en.json";
import es from "@/i18n/translations/es.json";
import fr from "@/i18n/translations/fr.json";
import hi from "@/i18n/translations/hi.json";
import ru from "@/i18n/translations/ru.json";
import zh from "@/i18n/translations/zh.json";
import { MicToggleButton } from "@/pages/home/components/MicToggleButton";
import { HEADING_CLASS } from "@/pages/onboarding/lib/constants";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));
vi.mock("@hugeicons/core-free-icons", () => ({
	Mic02Icon: { name: "Mic02Icon" },
	StopIcon: { name: "StopIcon" },
	AlertCircleIcon: { name: "AlertCircleIcon" },
	Search01Icon: { name: "Search01Icon" },
	Cancel01Icon: { name: "Cancel01Icon" },
	ViewIcon: { name: "ViewIcon" },
	ViewOffIcon: { name: "ViewOffIcon" },
}));

// renderer/src/pages/onboarding/__tests__ → renderer/src
const RENDERER_SRC = join(
	dirname(fileURLToPath(import.meta.url)),
	"..",
	"..",
	"..",
);

afterEach(() => {
	cleanup();
});

const ALL_LOCALES = [
	["en", en],
	["ar", ar],
	["de", de],
	["es", es],
	["fr", fr],
	["hi", hi],
	["ru", ru],
	["zh", zh],
] as const;

describe("onboarding.backendAria is genuinely translated (C-I18N-1/2)", () => {
	it("every locale ships a non-empty backendAria under onboarding", () => {
		for (const [code, dict] of ALL_LOCALES) {
			const value = (dict as { onboarding?: { backendAria?: string } })
				.onboarding?.backendAria;
			expect(value, `${code}.onboarding.backendAria`).toBeTruthy();
			expect(typeof value).toBe("string");
		}
	});

	it("non-English locales are NOT English copies of 'Transcription backend'", () => {
		const english = en.onboarding.backendAria;
		expect(english).toBe("Transcription backend");
		for (const [code, dict] of ALL_LOCALES) {
			if (code === "en") continue;
			const value = (dict as typeof en).onboarding.backendAria;
			expect(
				value,
				`${code}.onboarding.backendAria must be a genuine translation, not the English copy`,
			).not.toBe(english);
			expect(value?.trim().length ?? 0).toBeGreaterThan(0);
		}
	});
});

describe("errorBoundary.unknownError exists in all 8 locales (onboarding fallback)", () => {
	it("every locale ships errorBoundary.unknownError", () => {
		for (const [code, dict] of ALL_LOCALES) {
			const value = (dict as { errorBoundary?: { unknownError?: string } })
				.errorBoundary?.unknownError;
			expect(value, `${code}.errorBoundary.unknownError`).toBeTruthy();
		}
	});
});

describe("onboarding heading class is shared (MO-44)", () => {
	it("exports one HEADING_CLASS; Welcome/Consent import it (no local redefinition)", () => {
		expect(HEADING_CLASS).toBe(
			"text-lg font-semibold text-(--text-primary) outline-none",
		);
		const componentsDir = join(
			RENDERER_SRC,
			"pages",
			"onboarding",
			"components",
		);
		for (const file of ["WelcomeStep.tsx", "ConsentStep.tsx"]) {
			const src = readFileSync(join(componentsDir, file), "utf8");
			expect(src, `${file} must import shared HEADING_CLASS`).toContain(
				'import { HEADING_CLASS } from "../lib/constants"',
			);
			expect(
				src,
				`${file} must not redefine a local HEADING_CLASS`,
			).not.toContain("text-2xl font-bold");
		}
	});
});

describe("focus-ring migration ring-1 → ring-1(C-FOCUS-2/5)", () => {
	it("MicToggleButton focus-visible ring is ring-1at full opacity", () => {
		render(
			<MicToggleButton
				isRecording={false}
				toggling={false}
				disabled={false}
				onClick={() => {}}
				label="Start dictation"
			/>,
		);
		const btn = screen.getByRole("button", { name: "Start dictation" });
		const cls = btn.className;
		expect(cls).toContain("focus-visible:ring-1");
		expect(cls).toContain("focus-visible:ring-ring");
		expect(cls).not.toMatch(/focus-visible:ring-3\b/);
		expect(cls).not.toMatch(/focus-visible:ring-ring\/\d+/);
	});

	it("MicToggleButton error-state decorative ring stays ring-1inset (not focus)", () => {
		render(
			<MicToggleButton
				isRecording={false}
				toggling={false}
				disabled={false}
				onClick={() => {}}
				label="Start dictation"
				error
			/>,
		);
		const btn = screen.getByRole("button", { name: "Start dictation" });
		// The hollow-destructive ERROR treatment is a static inset ring,
		// not a focus indicator — it must keep ring-1ring-inset.
		expect(btn.className).toContain("ring-1 ring-inset ring-destructive");
		expect(btn.className).toContain("focus-visible:ring-1");
	});

	it("SearchField clear button uses focus-visible:ring-1(not ring-1)", () => {
		render(<SearchField value="hello" onChange={() => {}} />);
		const clearBtn = screen.getByRole("button", { name: /clear search/i });
		const cls = clearBtn.className;
		expect(cls).toContain("focus-visible:ring-1");
		expect(cls).toContain("focus-visible:ring-ring");
		expect(cls).not.toMatch(/focus-visible:ring-3\b/);
		expect(cls).not.toMatch(/focus-visible:ring-ring\/\d+/);
	});
});

describe("source-level contracts for the remaining fix-batch files", () => {
	const read = (...parts: string[]) =>
		readFileSync(join(RENDERER_SRC, ...parts), "utf8");

	it("CloudProvidersPanel reveal button: ring-1, no focus-visible:ring-1", () => {
		const src = read("components", "models", "CloudProvidersPanel.tsx");
		expect(src).toContain("focus-visible:ring-1");
		expect(src).not.toMatch(/focus-visible:ring-3\b/);
	});

	it("ActivityList show-more button: ring-1, no focus-visible:ring-1", () => {
		const src = read("components", "dashboard", "ActivityList.tsx");
		expect(src).toContain("focus-visible:ring-1");
		expect(src).not.toMatch(/focus-visible:ring-3\b/);
	});

	it("PrewarmAndUpdates uses logical me-auto, never physical mr-auto (MO-43)", () => {
		const src = read("components", "settings", "PrewarmAndUpdates.tsx");
		expect(src).toContain("me-auto");
		expect(src).not.toMatch(/\bmr-auto\b/);
	});

	it("onboarding init-error card carries a focus-visible ring (MO-46)", () => {
		const src = read("pages", "Onboarding.tsx");
		expect(src).toMatch(/focus-visible:ring-1focus-visible:ring-ring/);
	});

	it("Home force-cancel button carries a focus-visible ring (MO-47)", () => {
		const src = read("pages", "Home.tsx");
		// Narrow to the force-cancel block (aria-label home.forceCancelHint).
		const idx = src.indexOf("home.forceCancelHint");
		expect(idx).toBeGreaterThan(-1);
		const window = src.slice(Math.max(0, idx - 400), idx + 200);
		expect(window).toMatch(/focus-visible:ring-1focus-visible:ring-ring/);
	});

	it("useOnboardingWizard uses t(errorBoundary.unknownError), no hardcoded English (MO-40)", () => {
		const src = read("pages", "onboarding", "hooks", "useOnboardingWizard.ts");
		expect(src).not.toContain('"Unknown error"');
		expect(src).toContain('t("errorBoundary.unknownError")');
	});

	it("HotkeyStep JSDoc is not duplicated (MO-48)", () => {
		const src = read("pages", "onboarding", "components", "HotkeyStep.tsx");
		const occurrences = src.split("Optional test-hotkey handler").length - 1;
		expect(occurrences).toBe(1);
	});
});
