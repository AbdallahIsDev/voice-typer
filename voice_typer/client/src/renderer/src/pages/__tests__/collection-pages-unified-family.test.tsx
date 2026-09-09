/**
 * Wiring tests for the shared collection-page family (the Vocabulary +
 * Templates pages' migration onto the Wave-3 shells).
 *
 * Both collection pages render their Toolbar / BulkBar / ListHeader through
 * the shared components (components/common/Collection*.tsx) — the per-page
 * mirrors were byte-identical except for i18n keys and a handful of drift
 * points, so the family is one component and each page injects its keys.
 * These tests pin the MIGRATION contracts:
 *
 *   - structural: both page roots import the shared shells; the per-page
 *     mirror files stay deleted (a regression that reintroduces a forked
 *     toolbar fails here)
 *   - drift: the Add button carries an accessible aria-label on BOTH pages
 *     (new key `vocabulary.addNewAria`, all 8 locales); the row action
 *     buttons use the unified compact size (`icon-xs` — 24×24, the WCAG
 *     2.5.8 AA minimum) and NO native tooltips on either page; each page's
 *     hidden import input keeps its domain-specific file-picker filter
 *   - behavior: a REJECTED export (IPC `success: false`) surfaces an error
 *     toast on both pages — a failed export must not be silent
 *
 * The pages are rendered through their REAL component trees (mocks only at
 * the boundaries: python bridge, snackbar/sonner, hugeicons, next-themes),
 * so these also act as page-level wiring tests for the shell migration.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
	within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	nextThemesMock,
	pythonMock,
	snackbarMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";
import { TooltipProvider } from "@/components/ui/tooltip";

import ar from "@/i18n/translations/ar.json";
import de from "@/i18n/translations/de.json";
import en from "@/i18n/translations/en.json";
import es from "@/i18n/translations/es.json";
import fr from "@/i18n/translations/fr.json";
import hi from "@/i18n/translations/hi.json";
import ru from "@/i18n/translations/ru.json";
import zh from "@/i18n/translations/zh.json";

const { mockCall, showSnack, toastError, toastSuccess } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

const RENDERER_SRC_ROOT = path.join(__dirname, "..", "..");

/** The deleted per-page mirror components (a fork would resurrect these). */
const DELETED_MIRRORS = [
	"pages/vocabulary/components/VocabToolbar.tsx",
	"pages/vocabulary/components/VocabBulkBar.tsx",
	"pages/vocabulary/components/VocabListHeader.tsx",
	"pages/templates/components/TemplateToolbar.tsx",
	"pages/templates/components/TemplateBulkBar.tsx",
	"pages/templates/components/TemplateListHeader.tsx",
];

const seedVocabularyData = {
	misspellings: { recieve: "receive" },
	phrase_corrections: [["i am going to", "I'm going to"]] as Array<
		[string, string]
	>,
};

const seedTemplatesData = {
	templates: [
		{ trigger: "hello", output: "Hello, world!", match_mode: "exact" },
		{ trigger: "brb", output: "be right back", match_mode: "contains" },
	],
};

/** Wire mockCall for the Vocabulary page's load path. */
function seedVocabulary() {
	mockCall.mockImplementation((type: unknown) => {
		const cmd = typeof type === "string" ? type : "";
		if (cmd === "get_vocabulary") return Promise.resolve(seedVocabularyData);
		if (cmd === "save_vocabulary") return Promise.resolve({ success: true });
		return Promise.resolve({});
	});
}

/** Wire mockCall for the Templates page's load path. */
function seedTemplates() {
	mockCall.mockImplementation((type: unknown) => {
		const cmd = typeof type === "string" ? type : "";
		if (cmd === "get_templates") return Promise.resolve(seedTemplatesData);
		if (cmd === "save_templates") return Promise.resolve({});
		return Promise.resolve({});
	});
}

async function renderVocabularyPage() {
	const { default: VocabularyPage } = await import("@/pages/Vocabulary");
	renderWithProviders(<VocabularyPage />);
	await waitFor(() => {
		expect(screen.getByText("recieve")).toBeTruthy();
	});
}

async function renderTemplatesPage() {
	const { default: TemplatesPage } = await import("@/pages/Templates");
	renderWithProviders(<TemplatesPage />);
	await waitFor(() => {
		expect(screen.getByText("hello")).toBeTruthy();
	});
}

beforeEach(() => {
	mockCall.mockReset();
	showSnack.mockReset();
	toastError.mockClear();
	toastSuccess.mockClear();
	sessionStorage.clear();
	localStorage.clear();
	vi.resetModules();
});

afterEach(() => {
	cleanup();
});

describe("both collection pages render through the shared family", () => {
	it("Vocabulary.tsx imports the shared Collection shells", () => {
		const src = fs.readFileSync(
			path.join(RENDERER_SRC_ROOT, "pages", "Vocabulary.tsx"),
			"utf8",
		);
		expect(src).toContain("@/components/common/CollectionToolbar");
		expect(src).toContain("@/components/common/CollectionBulkBar");
		expect(src).toContain("@/components/common/CollectionListHeader");
	});

	it("Templates.tsx imports the shared Collection shells", () => {
		const src = fs.readFileSync(
			path.join(RENDERER_SRC_ROOT, "pages", "Templates.tsx"),
			"utf8",
		);
		expect(src).toContain("@/components/common/CollectionToolbar");
		expect(src).toContain("@/components/common/CollectionBulkBar");
		expect(src).toContain("@/components/common/CollectionListHeader");
	});

	it.each(DELETED_MIRRORS)("the per-page mirror %s stays deleted", (rel) => {
		const abs = path.join(RENDERER_SRC_ROOT, rel);
		expect(fs.existsSync(abs), `${abs} must not be re-created`).toBe(false);
	});
});

describe("Add button accessible name — both pages (a11y)", () => {
	it("Vocabulary's Add button carries an aria-label matching its visible label", async () => {
		seedVocabulary();
		await renderVocabularyPage();
		// Visible label stays (speech-input / getByText contracts).
		expect(screen.getByText("Add Word")).toBeTruthy();
		// The accessible name resolves from the aria-label key, whose
		// text equals the visible label (WCAG 2.5.3 Label in Name: the
		// accessible name must contain the visible label, so a
		// speech-input "click Add Word" keeps matching).
		const add = screen.getByRole("button", { name: "Add Word" });
		expect(add).toHaveAttribute("aria-label", "Add Word");
	});

	it("Templates's Add button keeps its aria-label next to its visible label", async () => {
		seedTemplates();
		await renderTemplatesPage();
		expect(screen.getByText("Add Template")).toBeTruthy();
		expect(
			screen.getByRole("button", { name: "Add new template" }),
		).toBeTruthy();
	});

	it("vocabulary.addNewAria exists in ALL 8 locale catalogues", () => {
		const LOCALES: Record<string, typeof en> = {
			en,
			ar,
			de,
			es,
			fr,
			hi,
			ru,
			zh,
		};
		const missing: string[] = [];
		for (const [locale, catalogue] of Object.entries(LOCALES)) {
			if (
				!(
					catalogue.vocabulary &&
					typeof catalogue.vocabulary.addNewAria === "string"
				)
			) {
				missing.push(locale);
			}
		}
		expect(missing).toEqual([]);
	});
});

describe("row action buttons — unified compact language", () => {
	it("Vocabulary rows: icon-xs (24px) buttons, aria-labels, no tooltips", async () => {
		seedVocabulary();
		await renderVocabularyPage();

		const row = screen
			.getByText("recieve")
			.closest('[data-testid="vocab-list-row"]') as HTMLElement;
		const actions = within(row)
			.getAllByRole("button")
			.filter((b) =>
				/^(Edit:|Test this entry:|Delete:)/.test(
					b.getAttribute("aria-label") ?? "",
				),
			);
		expect(actions.length).toBe(3);
		for (const b of actions) {
			// Unified compact size (icon-xs = the size-6 token).
			expect(b.className).toContain("size-6");
			expect(b.className).not.toContain("size-8");
			// Accessible name present (never a bare icon button).
			expect(b.getAttribute("aria-label")).toBeTruthy();
			// No native tooltips on any action icon.
			expect(b.getAttribute("title")).toBeNull();
		}
	});

	it("Templates rows: icon-xs (24px) buttons, aria-labels, no tooltips", async () => {
		seedTemplates();
		await renderTemplatesPage();

		const row = screen
			.getByText("hello")
			.closest('[data-testid="template-list-row"]') as HTMLElement;
		const actions = within(row)
			.getAllByRole("button")
			.filter((b) =>
				/^(Edit template:|Delete template:)/.test(
					b.getAttribute("aria-label") ?? "",
				),
			);
		expect(actions.length).toBe(2);
		for (const b of actions) {
			expect(b.className).toContain("size-6");
			expect(b.className).not.toContain("size-8");
			expect(b.getAttribute("aria-label")).toBeTruthy();
			// Drift resolution: titles were Template-only; the unified
			// family has none (the Vocabulary page's pinned contract).
			expect(b.getAttribute("title")).toBeNull();
		}
	});
});

describe("import file-picker filters — domain capability wiring", () => {
	it("Vocabulary accepts JSON + CSV (its parser reads both)", async () => {
		seedVocabulary();
		await renderVocabularyPage();
		const input = document.querySelector(
			'input[type="file"]',
		) as HTMLInputElement;
		expect(input).not.toBeNull();
		expect(input.getAttribute("accept")).toBe(
			"application/json,.json,.csv,text/csv",
		);
	});

	it("Templates accepts JSON only (its parser reads JSON only)", async () => {
		seedTemplates();
		await renderTemplatesPage();
		const input = document.querySelector(
			'input[type="file"]',
		) as HTMLInputElement;
		expect(input).not.toBeNull();
		expect(input.getAttribute("accept")).toBe("application/json,.json");
	});
});

describe("a rejected export is never silent", () => {
	it("Vocabulary surfaces the backend's rejection error as a toast", async () => {
		seedVocabulary();
		const exportVocabulary = vi
			.fn()
			.mockResolvedValue({ success: false, error: "disk full" });
		(window as unknown as { window_?: unknown }).window_ = {
			exportVocabulary,
		};
		await renderVocabularyPage();

		// Open the toolbar's Export menu (Radix DropdownMenu needs
		// userEvent's pointer events, not fireEvent.click) and pick JSON.
		const user = userEvent.setup();
		await user.click(screen.getByRole("button", { name: /^Export$/i }));
		const jsonItem = await screen.findByRole("menuitem", {
			name: /export as json/i,
		});
		await user.click(jsonItem);

		await waitFor(() => {
			expect(exportVocabulary).toHaveBeenCalledTimes(1);
		});
		// The rejected outcome surfaces the backend error.
		await waitFor(() => {
			expect(toastError).toHaveBeenCalledWith("disk full");
		});
	});

	it("Templates surfaces a rejected export with the backend error", async () => {
		seedTemplates();
		const exportTemplates = vi
			.fn()
			.mockResolvedValue({ success: false, error: "no disk" });
		const original = window.window_;
		window.window_ = {
			...(window.window_ ?? {}),
			exportTemplates,
		} as unknown as typeof window.window_;
		try {
			await renderTemplatesPage();

			const user = userEvent.setup();
			await user.click(screen.getByRole("button", { name: /^Export$/i }));
			const jsonItem = await screen.findByRole("menuitem", {
				name: /export as json/i,
			});
			await user.click(jsonItem);

			await waitFor(() => {
				expect(exportTemplates).toHaveBeenCalledTimes(1);
			});
			await waitFor(() => {
				expect(toastError).toHaveBeenCalledWith("no disk");
			});
		} finally {
			if (original !== undefined) {
				window.window_ = original;
			} else {
				delete (window as { window_?: unknown }).window_;
			}
		}
	});
});

describe("bulk bar + list header render through the shells (page wiring)", () => {
	it("Vocabulary's bulk bar appears with the selected count via the shared shell", async () => {
		seedVocabulary();
		await renderVocabularyPage();
		fireEvent.click(screen.getByLabelText("Select recieve"));
		const bulkBar = screen.getByTestId("vocab-bulk-bar");
		expect(within(bulkBar).getByText("1 selected")).toBeTruthy();
	});

	it("Templates's bulk bar appears with the selected count via the shared shell", async () => {
		seedTemplates();
		await renderTemplatesPage();
		fireEvent.click(screen.getByLabelText("Select hello"));
		const bulkBar = screen.getByTestId("template-bulk-bar");
		expect(within(bulkBar).getByText("1 selected")).toBeTruthy();
	});

	it("both pages' column headers render through the shared shell with their own labels", async () => {
		seedVocabulary();
		await renderVocabularyPage();
		const vocabHeader = screen.getByTestId("vocab-list-header");
		expect(within(vocabHeader).getByText("Heard as")).toBeTruthy();
		expect(within(vocabHeader).getByText("Corrected to")).toBeTruthy();

		cleanup();
		mockCall.mockReset();
		toastError.mockClear();

		seedTemplates();
		await renderTemplatesPage();
		const templateHeader = screen.getByTestId("template-list-header");
		expect(within(templateHeader).getByText("Trigger")).toBeTruthy();
		expect(within(templateHeader).getByText("Output")).toBeTruthy();
	});
});
