/**
 * LocalModelsPanel / ModelCardActions, React.memo re-render gating
 * during download-progress ticks.
 *
 * Every `download_progress` event updates the consolidated download
 * state in `useModelDownload` (progress / bytes / speed / ETA), which
 * re-renders the Models page and this panel ~2-10× per second. The
 * panel MUST re-render (the active row's DownloadProgressBar needs the
 * fresh progress), but the per-model action rows that did not change
 * must NOT re-render, previously every one of them re-rendered on
 * every tick (ModelCardActions had no memo() while every settings
 * section did).
 *
 * The memo() + stable-handler contract under test:
 *   1. A progress-only prop change re-renders ONLY the active
 *      download's progress bar (its Pause/Cancel buttons re-render);
 *      unrelated model rows' buttons do NOT re-render.
 *      (Counting mocked `<Button>` renders is a faithful proxy —
 *      ModelCardActions and DownloadProgressBar render exclusively
 *      through the shared Button, and Button is not memo'd. Same
 *      technique as `components/layout/__tests__/sidebar-memo.test.tsx`.)
 *   2. A real prop change (model data changes) still re-renders the
 *      row, guards against an over-aggressive memo freezing stale UI.
 *
 * Stable handler refs matter: the panel forwards the page-level
 * callbacks (onSelectModel / onDownloadModel / ...) straight through to
 * the memo'd rows, so a shallow-equal prop comparison succeeds across
 * renders only because the callbacks keep their identity (the
 * useModelLifecycle handlers are useCallback'd). The test passes the
 * SAME function refs across rerenders, mirroring the page.
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ModelFamily } from "@/lib/utils/models";

// Render-counting Button mock. Every button ModelCardActions /
// DownloadProgressBar render goes through the shared Button; counting
// renders per aria-label counts row renders per model.
const buttonRenders: Record<string, number> = {};
vi.mock("@/components/ui/button", () => ({
	Button: ({
		asChild: _asChild,
		children,
		...rest
	}: {
		asChild?: boolean;
		children?: React.ReactNode;
	} & React.ButtonHTMLAttributes<HTMLButtonElement>) => {
		const key =
			rest["aria-label"] ??
			(typeof children === "string" ? children : "unlabeled");
		buttonRenders[key] = (buttonRenders[key] ?? 0) + 1;
		return (
			<button type="button" {...rest}>
				{children}
			</button>
		);
	},
}));

vi.mock("@/hooks/usePython", () => ({
	// `useModelDownloadQueue` hydrates via `call("get_download_queue")`
	// on mount, resolve an empty queue (same stub as the sibling
	// download-queue tests).
	usePython: vi.fn(() => ({ call: vi.fn(async () => ({ queue: [] })) })),
	usePythonEvent: vi.fn(),
}));

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

// Stub the Accordion so the model cards render without driving Radix
// open state (same stub as the sibling LocalModelsPanel tests).
vi.mock("@/components/ui/accordion", () => ({
	Accordion: ({ children }: { children: React.ReactNode }) => (
		<div data-testid="accordion">{children}</div>
	),
	AccordionItem: ({ children }: { children: React.ReactNode }) => (
		<div data-testid="accordion-item">{children}</div>
	),
	AccordionTrigger: ({ children }: { children: React.ReactNode }) => (
		<div data-testid="accordion-trigger">{children}</div>
	),
	AccordionContent: ({ children }: { children: React.ReactNode }) => (
		<div data-testid="accordion-content">{children}</div>
	),
}));

import { LocalModelsPanel } from "@/components/models/LocalModelsPanel";

const families: ModelFamily[] = [
	{
		id: "whisper",
		name: "Whisper",
		description: null,
		variants: [
			{
				name: "tiny",
				size: "~75MB",
				speed: "Fastest",
				backend: "whisper",
				downloaded: false,
				depsOk: true,
				isActive: false,
			},
			{
				name: "large-v3-turbo",
				size: "~1.5GB",
				speed: "Slow",
				backend: "whisper",
				downloaded: false,
				depsOk: true,
				isActive: false,
			},
		],
	},
	{
		id: "parakeet",
		name: "Parakeet",
		description: null,
		variants: [
			{
				name: "parakeet-mlx",
				size: "~1GB",
				speed: "Fast",
				backend: "parakeet",
				downloaded: false,
				depsOk: true,
				isActive: false,
			},
		],
	},
];

// Stable handler refs, the page passes useCallback'd handlers, so the
// same references flow through every render. Re-created refs would
// defeat the memo for the wrong reason (identity churn, not value
// change).
const handlers = {
	onSelectModel: vi.fn(),
	onDownloadModel: vi.fn(),
	onDeleteModel: vi.fn(),
	onTogglePause: vi.fn(),
	onCancelDownload: vi.fn(),
	onOpenModelsFolder: vi.fn(),
};

const baseProps = {
	modelFamilies: families,
	modelCatalog: {},
	selectingModel: null,
	downloadingModel: "tiny" as string | null,
	downloadProgress: 0,
	downloadStatus: "",
	isPaused: false,
	downloadedBytes: null,
	totalBytes: null,
	speedBps: null,
	etaSeconds: null,
	diskInfo: null,
	modelsFolderSupported: false,
	...handlers,
};

function rendersOf(label: string): number {
	return buttonRenders[label] ?? 0;
}

beforeEach(() => {
	for (const key of Object.keys(buttonRenders)) delete buttonRenders[key];
});

afterEach(() => {
	cleanup();
});

describe("LocalModelsPanel, download-progress tick re-render gating", () => {
	it("a progress tick does NOT re-render unrelated model rows (memo'd ModelCardActions)", () => {
		const { rerender } = render(<LocalModelsPanel {...baseProps} />);

		// Baseline: each not-downloaded model renders one "Download <name>"
		// button (branch 2 of ModelCardActions; the ACTIVE download's button
		// swaps its aria-label to "Downloading…"), and the active download
		// renders the DownloadProgressBar with Pause + Cancel buttons.
		expect(rendersOf("Downloading…")).toBe(1);
		expect(rendersOf("Download large-v3-turbo")).toBe(1);
		expect(rendersOf("Download parakeet-mlx")).toBe(1);
		expect(rendersOf("Pause download")).toBe(1);
		expect(rendersOf("Cancel model download")).toBe(1);

		// Simulate a download_progress tick: the page re-renders the panel
		// with a new progress value and byte/speed/ETA updates, keeping
		// every other prop reference identical.
		rerender(
			<LocalModelsPanel
				{...baseProps}
				downloadProgress={42}
				downloadStatus="Downloading…"
				downloadedBytes={123}
				totalBytes={1000}
				speedBps={456}
				etaSeconds={12}
			/>,
		);

		// The active download's progress bar re-rendered (its controls
		// count again), the progress MUST flow through.
		expect(rendersOf("Pause download")).toBe(2);
		expect(rendersOf("Cancel model download")).toBe(2);
		// The active row's action button did NOT re-render either, its
		// props (isDownloadingThis=true, …) are unchanged by the tick.
		expect(rendersOf("Downloading…")).toBe(1);

		// Unrelated rows did NOT re-render, their buttons still rendered
		// exactly once.
		expect(rendersOf("Download large-v3-turbo")).toBe(1);
		expect(rendersOf("Download parakeet-mlx")).toBe(1);
	});

	it("a row's own data change still re-renders it (memo does not freeze stale UI)", () => {
		// fresh copies so the baseline counts are stable
		const { rerender } = render(<LocalModelsPanel {...baseProps} />);
		expect(rendersOf("Select large-v3-turbo")).toBe(0);

		// The "large-v3-turbo" model becomes downloaded → its row must
		// re-render and show the Select/Delete branch instead of Download.
		const updatedFamilies: ModelFamily[] = families.map((family) => ({
			...family,
			variants: family.variants.map((model) =>
				model.name === "large-v3-turbo"
					? { ...model, downloaded: true }
					: model,
			),
		}));
		rerender(
			<LocalModelsPanel {...baseProps} modelFamilies={updatedFamilies} />,
		);

		// Branch 3 rendered for the changed model: its Select button and
		// Delete icon button are now in the DOM (aria-labels carry the
		// model name).
		expect(rendersOf("Select large-v3-turbo")).toBe(1);
		expect(rendersOf("Delete large-v3-turbo")).toBe(1);
	});
});
