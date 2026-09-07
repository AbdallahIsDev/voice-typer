/**
 * useModelDownloadQueue unit tests — renderer-side download-queue state.
 *
 * The hook derives the pending-download queue from the backend's
 * `download_progress` events:
 *   • an event WITH `queue_position` marks the model as queued at that
 *     1-based FIFO position (the backend is the single source of truth
 *     — the queue survives renderer navigation/reload),
 *   • an event WITHOUT the field means "not queued" (active transfer or
 *     terminal state) and clears the entry,
 *   • position-only updates (drain / queue-advance) re-render without
 *     state churn when the value is unchanged.
 *
 * The `usePythonEvent` hook is mocked (same capture technique as
 * `__tests__/app-download-progress-gating.test.tsx`) so the test invokes
 * the captured `download_progress` handler directly with synthetic
 * payloads.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useModelDownloadQueue } from "@/components/models/useModelDownloadQueue";

const { capturedHandlerRef, mockCall } = vi.hoisted(() => ({
	capturedHandlerRef: {
		current: null as ((data?: Record<string, unknown>) => unknown) | null,
	},
	mockCall: vi.fn(async (): Promise<{ queue?: unknown }> => ({ queue: [] })),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: vi.fn(() => ({ call: mockCall })),
	usePythonEvent: vi.fn(
		(type: string, handler: (data?: Record<string, unknown>) => unknown) => {
			if (type === "download_progress") {
				capturedHandlerRef.current = handler;
			}
		},
	),
}));

/** Probe component: renders one row per queued model + its position. */
function QueueProbe() {
	const queued = useModelDownloadQueue();
	return (
		<ul>
			{Object.entries(queued).map(([model, position]) => (
				<li key={model} data-testid={`queued-${model}`}>
					{model}:{position}
				</li>
			))}
		</ul>
	);
}

function fireEvent(data: Record<string, unknown> | undefined) {
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

describe("useModelDownloadQueue — event-derived queue state", () => {
	it("marks a model queued when an event carries queue_position", () => {
		render(<QueueProbe />);
		fireEvent({
			model: "tiny",
			progress: 0,
			status: "queued",
			queue_position: 1,
		});
		expect(screen.getByTestId("queued-tiny").textContent).toBe("tiny:1");
	});

	it("tracks multiple queued models with their FIFO positions", () => {
		render(<QueueProbe />);
		fireEvent({ model: "tiny", queue_position: 1 });
		fireEvent({ model: "base", queue_position: 2 });
		expect(screen.getByTestId("queued-tiny").textContent).toBe("tiny:1");
		expect(screen.getByTestId("queued-base").textContent).toBe("base:2");
	});

	it("updates the position when the queue advances (drain re-push)", () => {
		render(<QueueProbe />);
		fireEvent({ model: "base", queue_position: 2 });
		fireEvent({ model: "base", queue_position: 1 });
		expect(screen.getByTestId("queued-base").textContent).toBe("base:1");
	});

	it("clears the entry when an event for the model has NO queue_position", () => {
		render(<QueueProbe />);
		fireEvent({ model: "tiny", queue_position: 1 });
		expect(screen.getByTestId("queued-tiny")).toBeTruthy();
		// The queued model's transfer starts (active download events carry
		// no queue_position).
		fireEvent({ model: "tiny", progress: 10, status: "Downloading tiny" });
		expect(screen.queryByTestId("queued-tiny")).toBeNull();
	});

	it("ignores events without a model field", () => {
		render(<QueueProbe />);
		fireEvent({ progress: 10 });
		fireEvent(undefined);
		expect(screen.queryByTestId(/queued-/)).toBeNull();
	});

	it("ignores non-positive / invalid queue_position values", () => {
		render(<QueueProbe />);
		fireEvent({ model: "tiny", queue_position: 0 });
		expect(screen.queryByTestId("queued-tiny")).toBeNull();
		fireEvent({ model: "tiny", queue_position: "2" });
		expect(screen.queryByTestId("queued-tiny")).toBeNull();
	});
});

describe("useModelDownloadQueue — mount hydration", () => {
	it("restores chips from the get_download_queue snapshot on mount", async () => {
		mockCall.mockResolvedValueOnce({ queue: ["tiny", "base"] });
		render(<QueueProbe />);
		expect(await screen.findByTestId("queued-tiny")).toBeTruthy();
		expect(screen.getByTestId("queued-tiny").textContent).toBe("tiny:1");
		expect(screen.getByTestId("queued-base").textContent).toBe("base:2");
		expect(mockCall).toHaveBeenCalledWith("get_download_queue");
	});

	it("stays empty and tracks events when the snapshot rejects", async () => {
		mockCall.mockRejectedValueOnce(new Error("bridge down"));
		render(<QueueProbe />);
		// Let the rejected promise settle — no chip, no crash.
		await act(async () => {});
		expect(screen.queryByTestId(/queued-/)).toBeNull();
		// The live event path still works after a failed snapshot.
		fireEvent({ model: "tiny", queue_position: 1 });
		expect(screen.getByTestId("queued-tiny").textContent).toBe("tiny:1");
	});

	it("ignores a malformed snapshot shape", async () => {
		mockCall.mockResolvedValueOnce({ queue: "tiny" });
		render(<QueueProbe />);
		await act(async () => {});
		expect(screen.queryByTestId(/queued-/)).toBeNull();
	});

	it("filters non-string entries out of the snapshot", async () => {
		mockCall.mockResolvedValueOnce({ queue: ["tiny", 42, null] });
		render(<QueueProbe />);
		expect(await screen.findByTestId("queued-tiny")).toBeTruthy();
		expect(screen.queryByTestId("queued-42")).toBeNull();
	});
});
