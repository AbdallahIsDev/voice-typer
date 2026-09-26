// useMediaJob lifecycle: start ack / consent-gate retry / error mapping /
// push-event transitions / status hydration (ADR-0023).

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useConsentGateStore } from "@/lib/consentGate";
import { type UseMediaJobResult, useMediaJob } from "../useMediaJob";

const registered = new Map<string, (data?: unknown) => unknown>();
const callMock = vi.fn();

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: callMock }),
	usePythonEvent: (type: string, handler: (data?: unknown) => unknown) => {
		registered.set(type, handler);
	},
}));

function fire(type: string, data?: unknown): void {
	const handler = registered.get(type);
	if (!handler) throw new Error(`${type} handler not registered`);
	act(() => {
		handler(data);
	});
}

async function actStart(result: { current: UseMediaJobResult }): Promise<void> {
	await result.current.start("https://example.com/v", false);
	await waitFor(() => expect(result.current.job.phase).toBe("loading_model"));
}

beforeEach(() => {
	registered.clear();
	vi.clearAllMocks();
	useConsentGateStore.setState({ request: null });
	// Default: status probe sees an idle backend; start acks cleanly.
	callMock.mockImplementation((cmd: string) => {
		if (cmd === "media_transcribe_status") {
			return Promise.resolve({ job: null });
		}
		return Promise.resolve({});
	});
});

describe("useMediaJob", () => {
	it("registers handlers for the three media push events", () => {
		renderHook(() => useMediaJob());
		expect(registered.has("media_transcribe_progress")).toBe(true);
		expect(registered.has("media_transcribe_complete")).toBe(true);
		expect(registered.has("media_transcribe_error")).toBe(true);
	});

	it("start acks into the loading_model phase and reports running", async () => {
		const { result } = renderHook(() => useMediaJob());
		await actStart(result);
		expect(result.current.job.phase).toBe("loading_model");
		expect(result.current.running).toBe(true);
		expect(callMock).toHaveBeenCalledWith("media_transcribe_start", {
			source: "https://example.com/v",
			use_subtitles: false,
		});
	});

	it("opens the consent gate on client.consent_required and Allow retries", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "media_transcribe_status")
				return Promise.resolve({ job: null });
			const err = new Error("consent required") as Error & {
				code?: string;
				consent_field?: string;
			};
			err.code = "client.consent_required";
			err.consent_field = "media_url_consent";
			return Promise.reject(err);
		});
		const { result } = renderHook(() => useMediaJob());
		await result.current.start("https://example.com/v", true);

		const req = useConsentGateStore.getState().request;
		expect(req).toEqual(
			expect.objectContaining({
				consentField: "media_url_consent",
				bodyKey: "consentDialog.field.media_url_consent",
				onAllow: expect.any(Function),
			}),
		);
		expect(result.current.job.phase).toBe("idle");

		callMock.mockImplementation((cmd: string) =>
			cmd === "media_transcribe_status"
				? Promise.resolve({ job: null })
				: Promise.resolve({}),
		);
		await req?.onAllow?.();
		await waitFor(() => expect(result.current.job.phase).toBe("loading_model"));
		expect(callMock).toHaveBeenLastCalledWith("media_transcribe_start", {
			source: "https://example.com/v",
			use_subtitles: true,
		});
	});

	it("maps start-time server.no_model to the no-model error copy", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "media_transcribe_status")
				return Promise.resolve({ job: null });
			const err = new Error("no model") as Error & { code?: string };
			err.code = "server.no_model";
			return Promise.reject(err);
		});
		const { result } = renderHook(() => useMediaJob());
		await act(async () => {
			await result.current.start("/tmp/clip.wav", false);
		});
		expect(result.current.job.phase).toBe("error");
		expect(result.current.job.errorKey).toBe("media.errorNoModel");
	});

	it("applies progress pushes (phase, fraction, ETA, duration)", async () => {
		const { result } = renderHook(() => useMediaJob());
		await actStart(result);
		fire("media_transcribe_progress", {
			job_id: "j1",
			progress: 0.25,
			phase: "transcribing",
			eta_seconds: 42.5,
			duration_seconds: 180,
		});
		expect(result.current.job.phase).toBe("transcribing");
		expect(result.current.job.progress).toBe(0.25);
		expect(result.current.job.etaSeconds).toBe(42.5);
		expect(result.current.job.durationSeconds).toBe(180);
	});

	it("completion carries row_id / chars / partial into the done state", async () => {
		const { result } = renderHook(() => useMediaJob());
		await actStart(result);
		fire("media_transcribe_complete", {
			job_id: "j1",
			row_id: 12,
			chars: 345,
			partial: true,
		});
		expect(result.current.job.phase).toBe("done");
		expect(result.current.job.rowId).toBe(12);
		expect(result.current.job.chars).toBe(345);
		expect(result.current.job.partial).toBe(true);
	});

	it("maps media error codes to localized copy (drm_refused)", async () => {
		const { result } = renderHook(() => useMediaJob());
		await actStart(result);
		fire("media_transcribe_error", {
			job_id: "j1",
			code: "drm_refused",
			message: "DRM content refused",
		});
		expect(result.current.job.phase).toBe("error");
		expect(result.current.job.errorKey).toBe("media.errorDrm");
		// The raw backend message never reaches the UI surface.
		expect(result.current.job.errorCode).toBe("drm_refused");
	});

	it("hydrates a running job from the status probe after navigation", async () => {
		callMock.mockImplementation((cmd: string) =>
			cmd === "media_transcribe_status"
				? Promise.resolve({
						job: { job_id: "j9", status: "running", progress: 0.4 },
					})
				: Promise.resolve({}),
		);
		const { result } = renderHook(() => useMediaJob());
		await waitFor(() => expect(result.current.job.phase).toBe("transcribing"));
		expect(result.current.job.progress).toBe(0.4);
		expect(result.current.job.jobId).toBe("j9");
	});

	it("cancel dispatches media_transcribe_cancel", async () => {
		const { result } = renderHook(() => useMediaJob());
		await result.current.cancel();
		expect(callMock).toHaveBeenCalledWith("media_transcribe_cancel");
	});
});
