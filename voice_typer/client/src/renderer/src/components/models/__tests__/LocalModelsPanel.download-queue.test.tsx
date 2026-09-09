/**
 * LocalModelsPanel — download-queue integration tests.
 *
 * Verifies the panel-level wiring of the pending-download queue slice:
 * the co-located `useModelDownloadQueue` hook consumes the backend's
 * `download_progress` events (`queue_position` field) and forwards the
 * per-model position to the REAL `<ModelCardActions>`, whose Download
 * button swaps to the localized "Queued" state. The panel-level prop
 * plumbing (downloadingModel) stays untouched — the
 * queue state comes from the event stream, not from the page.
 *
 * Same capture technique as `__tests__/app-download-progress-gating.test.tsx`:
 * `usePythonEvent` is mocked and the `download_progress` handler is
 * captured so the test invokes it directly with synthetic payloads.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LocalModelsPanel } from "@/components/models/LocalModelsPanel";
import type { ModelFamily } from "@/lib/utils/models";

const { capturedHandlerRef } = vi.hoisted(() => ({
	capturedHandlerRef: {
		current: null as ((data?: Record<string, unknown>) => unknown) | null,
	},
}));

vi.mock("@/hooks/usePython", () => ({
	// `useModelDownloadQueue` hydrates via `call("get_download_queue")`
	// on mount — resolve an empty queue here (hydration has its own
	// tests in `useModelDownloadQueue.test.tsx`).
	usePython: vi.fn(() => ({ call: vi.fn(async () => ({ queue: [] })) })),
	usePythonEvent: vi.fn(
		(type: string, handler: (data?: Record<string, unknown>) => unknown) => {
			if (type === "download_progress") {
				capturedHandlerRef.current = handler;
			}
		},
	),
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

const noop = vi.fn();

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
];

const baseProps = {
	modelFamilies: families,
	modelCatalog: {},
	selectingModel: null,
	downloadingModel: "tiny", // an active download is in flight
	downloadProgress: 0,
	downloadStatus: "",
	isPaused: false,
	downloadedBytes: null,
	totalBytes: null,
	speedBps: null,
	etaSeconds: null,
	onSelectModel: noop,
	onDownloadModel: noop,
	onDeleteModel: noop,
	onTogglePause: noop,
	onCancelDownload: noop,
	diskInfo: null,
	modelsFolderSupported: false,
	onOpenModelsFolder: noop,
};

function fireQueueEvent(data: Record<string, unknown> | undefined) {
	const handler = capturedHandlerRef.current;
	expect(handler).toBeTruthy();
	act(() => {
		handler?.(data);
	});
}

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
});

describe("LocalModelsPanel — pending-download queue wiring", () => {
	it("a queued model's Download button swaps to the 'Queued' state", () => {
		render(<LocalModelsPanel {...baseProps} />);
		// At rest: the second model's button is the at-rest download button
		// (no queued state yet) — ENABLED even though another download is
		// in flight (the backend QUEUES the request; that is the queue's
		// primary flow).
		expect(
			screen.getByRole("button", { name: /Download large-v3-turbo/i }),
		).toBeEnabled();

		// The backend queues the second request (progress event with
		// queue_position).
		fireQueueEvent({ model: "large-v3-turbo", queue_position: 1 });

		const queuedBtn = screen.getByRole("button", {
			name: "Queued — position 1 in the download queue",
		});
		expect(queuedBtn).toHaveTextContent("Queued");
		expect(queuedBtn).toHaveAttribute(
			"aria-label",
			"Queued — position 1 in the download queue",
		);
		// The active download's card keeps its own state (in-flight).
		expect(screen.getByRole("button", { name: /Downloading/i })).toBeDisabled();
	});

	it("a queued model's Cancel affordance removes it from the queue (named-cancel IPC shape)", () => {
		const onCancelDownload = vi.fn();
		render(
			<LocalModelsPanel {...baseProps} onCancelDownload={onCancelDownload} />,
		);
		fireQueueEvent({ model: "large-v3-turbo", queue_position: 1 });

		screen
			.getByRole("button", {
				name: /Cancel queued download of large-v3-turbo/i,
			})
			.click();

		// The panel threads the queued model's NAME into the shared cancel
		// handler (the queued-card Cancel removes the model from the pending
		// queue — the active transfer's Cancel stays argumentless).
		expect(onCancelDownload).toHaveBeenCalledWith("large-v3-turbo");
	});

	it("queued state clears when the model's transfer starts (event without queue_position)", () => {
		render(<LocalModelsPanel {...baseProps} />);
		fireQueueEvent({ model: "large-v3-turbo", queue_position: 1 });
		expect(
			screen.getByRole("button", {
				name: "Queued — position 1 in the download queue",
			}),
		).toBeInTheDocument();

		// The active download finished and the queued one auto-started:
		// its events no longer carry queue_position.
		fireQueueEvent({
			model: "large-v3-turbo",
			progress: 5,
			status: "Starting",
		});

		expect(
			screen.queryByRole("button", {
				name: "Queued — position 1 in the download queue",
			}),
		).toBeNull();
	});
});
