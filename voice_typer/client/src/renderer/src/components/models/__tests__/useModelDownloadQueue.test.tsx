/**
 * useModelDownloadQueue unit tests, renderer-side download-queue state.
 *
 * The hook derives the pending-download queue from the backend's
 * `download_progress` events:
 *   • an event WITH `queue_position` marks the model as queued at that
 *     1-based FIFO position (the backend is the single source of truth
 *    , the queue survives renderer navigation/reload),
 *   • an event WITHOUT the field means "not queued" (active transfer or
 *     terminal state) and clears the entry,
 *   • position-only updates (drain / queue-advance) re-render without
 *     state churn when the value is unchanged.
 *
 * The `@/hooks/usePython` module is mocked via the shared stable-mocks
 * harness (`__tests__/helpers/stableMocks`): `pythonMock({ captureEvents })`
 * wires `usePython().call` to the assertable `stableMocks.mockCall`
 * singleton and stores the `usePythonEvent` handler in the hoisted event
 * map, so the tests invoke the captured `download_progress` handler
 * directly with synthetic payloads.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
	pythonMock,
	resetStableMocks,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall } = stableMocks;

// Hoisted (NOT a standard singleton, it is this file's event capture
// map) because the vi.mock factory below is hoisted above module-body
// declarations and closes over it.
const { eventHandlers } = vi.hoisted(() => ({
	eventHandlers: {} as Record<string, (data: unknown) => void>,
}));

vi.mock("@/hooks/usePython", () =>
	pythonMock({ captureEvents: eventHandlers }),
);

import { useModelDownloadQueue } from "@/components/models/useModelDownloadQueue";

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
	const handler = eventHandlers.download_progress;
	expect(handler).toBeTruthy();
	act(() => {
		handler?.(data);
	});
}

beforeEach(() => {
	resetStableMocks();
	// Default snapshot response, an empty queue (the pre-stableMocks
	// preamble's mock resolved `{ queue: [] }`): the event tests below
	// don't care about hydration. The hydration describe overrides per
	// call via mockResolvedValueOnce / mockRejectedValueOnce.
	mockCall.mockResolvedValue({ queue: [] });
});

afterEach(() => {
	cleanup();
});

describe("useModelDownloadQueue, event-derived queue state", () => {
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

describe("useModelDownloadQueue, mount hydration", () => {
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
		// Let the rejected promise settle, no chip, no crash.
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
