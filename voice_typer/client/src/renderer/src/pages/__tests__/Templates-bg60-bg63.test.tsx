/**
 * Tests for the Templates page —  (load-error variant) and
 * (export format forwarding).
 *
 * : the load-error EmptyState in Templates.tsx previously used the
 * default ``"info"`` variant, which made a backend-load failure look
 * identical to "you haven't added anything yet".  switches the
 * load-error EmptyState to ``variant="error"`` so the failure is
 * visually distinct (destructive ring + Alert02Icon + role="alert").
 *
 * : the ExportFormatMenu picks "json" or "csv" and calls
 * onExport(format). Templates.tsx previously had ``onExport={() =>
 * doExport()}`` — the arrow function dropped the format arg, so CSV
 * export silently behaved like JSON export.  forwards the format
 * through to ``doExport`` and ultimately to the IPC bridge.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";

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
		Cancel01Icon: make("Cancel01Icon"),
		Delete01Icon: make("Delete01Icon"),
		Download01Icon: make("Download01Icon"),
		File02Icon: make("File02Icon"),
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

/** TemplateListRow renders an InfoTooltip (Radix Tooltip) which throws
 *  without a TooltipProvider ancestor — the real App shell provides
 *  one, so tests mounting the page directly must too. */
const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

describe("Templates page — BG-60 load-error variant", () => {
	beforeEach(() => {
		mockCall.mockReset();
		// get_templates throws → loadRows sets loadError AND
		// localStorage is empty (cleared below) → Templates renders
		// the load-error EmptyState.
		mockCall.mockRejectedValue(new Error("backend down"));
		toastError.mockClear();
		toastSuccess.mockClear();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it('renders the load-error EmptyState with role="alert" (error variant)', async () => {
		const { default: TemplatesPage } = await import("@/pages/Templates");
		renderWithProviders(<TemplatesPage />);

		// Wait for the load-error EmptyState to appear.
		await waitFor(() => {
			expect(screen.getByText("Failed to load templates")).toBeTruthy();
		});

		// The error variant of EmptyState wraps the card in a div with
		// role="alert" (the info variant uses role="status"). This is
		// the assertion that fails if variant="error" is removed.
		const alertRegion = screen.getByRole("alert");
		expect(alertRegion).toBeTruthy();
		// The destructive ring + soft wash should be applied.
		expect(alertRegion.className).toContain("destructive");
	});

	it("renders the load-error EmptyState with the localised description (BG-62)", async () => {
		const { default: TemplatesPage } = await import("@/pages/Templates");
		renderWithProviders(<TemplatesPage />);

		await waitFor(() => {
			expect(screen.getByText("Failed to load templates")).toBeTruthy();
		});

		//the loadError description is the localised string from
		// templates.loadFailedDescription (not a hardcoded English
		// fallback).
		expect(
			screen.getByText(
				/Failed to load templates from the backend\. Check your connection and try again\./,
			),
		).toBeTruthy();
	});
});

describe("Templates page — BG-63 export format forwarding", () => {
	// Captured args from the bridge.exportTemplates mock so the test can
	// assert that the format arg reaches the IPC layer.
	const exportTemplatesMock = vi.fn();
	let originalWindow_: typeof window.window_ | undefined;

	beforeEach(() => {
		mockCall.mockReset();
		// get_templates returns one template so the page renders the
		// Export button (exportDisabled={templates.length === 0}).
		mockCall.mockImplementation((type: string) => {
			if (type === "get_templates") {
				return Promise.resolve({
					templates: [
						{
							trigger: "hi",
							output: "hello",
							match_mode: "exact",
						},
					],
				});
			}
			if (type === "save_templates") return Promise.resolve({});
			return Promise.resolve({});
		});
		toastError.mockClear();
		toastSuccess.mockClear();
		localStorage.clear();
		vi.resetModules();

		// Install a window_.exportTemplates mock so doExport can
		// reach the bridge path. The cast mirrors the production code's
		//ExportTemplatesWithFormat alias ().
		originalWindow_ = window.window_;
		exportTemplatesMock.mockResolvedValue({
			success: true,
			path: "/tmp/templates-export.json",
		});
		window.window_ = {
			...(window.window_ ?? ({} as object)),
			exportTemplates: exportTemplatesMock,
		} as unknown as typeof window.window_;
	});

	afterEach(() => {
		cleanup();
		// Restore the original window_ so other tests aren't affected.
		if (originalWindow_ !== undefined) {
			window.window_ = originalWindow_;
		} else {
			delete (window as { window_?: unknown }).window_;
		}
	});

	it("forwards the chosen format ('json' | 'csv') to the IPC bridge", async () => {
		const user = userEvent.setup();
		const { default: TemplatesPage } = await import("@/pages/Templates");
		renderWithProviders(<TemplatesPage />);

		// Wait for the seeded template to render.
		await waitFor(() => {
			expect(screen.getByText("hi")).toBeTruthy();
		});

		// Click the Export button (exportFormat.export → "Export") to
		// open the dropdown menu. userEvent is required (not
		// fireEvent.click) because Radix DropdownMenu listens for
		// pointer events that fireEvent doesn't simulate.
		const exportButton = screen.getByRole("button", { name: /^Export$/i });
		await user.click(exportButton);

		// Click the "Export as CSV" menuitem (exportFormat.csv).
		const csvItem = await screen.findByRole("menuitem", {
			name: /export as csv/i,
		});
		await user.click(csvItem);

		// The bridge.exportTemplates mock should have been called with
		//the templates payload AND format="csv". Before , the
		// format arg was dropped at the call site (onExport={() =>
		// doExport()}) so the bridge received only the data arg.
		await waitFor(() => {
			expect(exportTemplatesMock).toHaveBeenCalledTimes(1);
		});
		const callArgs = exportTemplatesMock.mock.calls[0];
		expect(callArgs?.[0]).toEqual({
			templates: [{ trigger: "hi", output: "hello", match_mode: "exact" }],
		});
		expect(callArgs?.[1]).toBe("csv");
	});
});
