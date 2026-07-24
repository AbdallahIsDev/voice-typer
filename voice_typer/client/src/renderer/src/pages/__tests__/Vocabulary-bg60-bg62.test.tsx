/**
 * Tests for the Vocabulary page — BG-60 (load-error variant) and
 * BG-62 (localised load-failed description).
 *
 * BG-60: the load-error EmptyState in Vocabulary.tsx previously used
 * the default ``"info"`` variant, which made a backend-load failure
 * look identical to "you haven't added any words yet". BG-60 switches
 * the load-error EmptyState to ``variant="error"`` so the failure is
 * visually distinct (destructive ring + Alert02Icon + role="alert").
 *
 * BG-62: the loadError string previously fell back to a hardcoded
 * English "Failed to load vocabulary" when the caught error wasn't an
 * Error instance. BG-62 replaces that fallback with the localised
 * ``vocabulary.loadFailedDescription`` i18n key.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockCall } = vi.hoisted(() => ({
	mockCall: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
}));

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({
		children,
		icon,
	}: {
		children?: React.ReactNode;
		icon?: { name?: string };
	}) => (
		<span data-testid="hugeicon" data-name={icon?.name}>
			{children}
		</span>
	),
}));

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		Add01Icon: make("Add01Icon"),
		Alert01Icon: make("Alert01Icon"),
		Alert02Icon: make("Alert02Icon"),
		AlertCircleIcon: make("AlertCircleIcon"),
		ArrowDown01Icon: make("ArrowDown01Icon"),
		ArrowUp01Icon: make("ArrowUp01Icon"),
		BookOpen02Icon: make("BookOpen02Icon"),
		Cancel01Icon: make("Cancel01Icon"),
		Delete01Icon: make("Delete01Icon"),
		Download01Icon: make("Download01Icon"),
		PencilEdit02Icon: make("PencilEdit02Icon"),
		Search01Icon: make("Search01Icon"),
		Tick02Icon: make("Tick02Icon"),
		UnfoldMoreIcon: make("UnfoldMoreIcon"),
	};
});

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock("sonner", () => ({
	toast: {
		success: (...args: unknown[]) => toastSuccess(...args),
		error: (...args: unknown[]) => toastError(...args),
		warning: vi.fn(),
		info: vi.fn(),
		dismiss: vi.fn(),
	},
	Toaster: () => null,
}));

vi.mock("next-themes", () => ({
	useTheme: () => ({ theme: "light" as const }),
}));

describe("Vocabulary page — BG-60 load-error variant + BG-62 localised description", () => {
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

	it("renders the localised loadFailedDescription (BG-62)", async () => {
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		render(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("Failed to load vocabulary")).toBeTruthy();
		});

		// BG-62: the loadError description is the localised string
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
