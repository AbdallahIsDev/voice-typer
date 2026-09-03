/**
 * Tests for the Vocabulary page —  (load-error variant) and
 *  (localised load-failed description).
 *
 * : the load-error EmptyState in Vocabulary.tsx previously used
 * the default ``"info"`` variant, which made a backend-load failure
 * look identical to "you haven't added any words yet".  switches
 * the load-error EmptyState to ``variant="error"`` so the failure is
 * visually distinct (destructive ring + Alert02Icon + role="alert").
 *
 * : the loadError string previously fell back to a hardcoded
 * English "Failed to load vocabulary" when the caught error wasn't an
 * Error instance.  replaces that fallback with the localised
 * ``vocabulary.loadFailedDescription`` i18n key.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	nextThemesMock,
	pythonMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall, toastSuccess, toastError } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

describe("Vocabulary page — load-error variant + localised description", () => {
	beforeEach(() => {
		mockCall.mockReset();
		// get_vocabulary throws → loadVocabulary sets loadError AND
		// entries is empty → Vocabulary renders the load-error
		// EmptyState. We throw a non-Error (string) so the catch
		// block's fallback (`t("vocabulary.loadFailedDescription")`)
		// is exercised — if we threw an Error, err.message would be
		// used instead and the localised description wouldn't show.
		mockCall.mockRejectedValue("backend exploded");
		toastError.mockClear();
		toastSuccess.mockClear();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it('renders the load-error EmptyState with role="alert" (error variant)', async () => {
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		render(<VocabularyPage />);

		// Wait for the load-error EmptyState to appear.
		await waitFor(() => {
			expect(screen.getByText("Failed to load vocabulary")).toBeTruthy();
		});

		// The error variant of EmptyState wraps the card in a div with
		// role="alert" (the info variant uses role="status"). This is
		// the assertion that fails if variant="error" is removed.
		const alertRegion = screen.getByRole("alert");
		expect(alertRegion).toBeTruthy();
		// The destructive ring + soft wash should be applied.
		expect(alertRegion.className).toContain("destructive");
	});

	it("renders the localised loadFailedDescription", async () => {
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		render(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("Failed to load vocabulary")).toBeTruthy();
		});

		//the loadError description is the localised string
		// from vocabulary.loadFailedDescription (not a hardcoded
		// English "Failed to load vocabulary" fallback). We threw a
		// non-Error rejection above so the fallback path is taken.
		expect(
			screen.getByText(
				/Failed to load vocabulary from the backend\. Check your connection and try again\./,
			),
		).toBeTruthy();
	});
});
