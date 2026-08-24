/**
 * Microphone page interaction contracts:
 *
 *   1. GDPR point-of-use consent gate on Start Test (mirrors the Home
 *      dictation gate): pressing Start Test while
 *      ``voice_biometric_consent`` is off opens the unified consent
 *      dialog INSTEAD of firing a doomed ``microphone_test_start`` IPC;
 *      Allow retries the start; a granted config starts directly and
 *      never prompts.
 *   2. Unified radio mic list: picking a device (radio click or row
 *      click) routes through onSelectMicrophone → ``set_config``.
 *   3. Selecting a microphone while a device-lost flag is active clears
 *      the flag AND refreshes the device list (stale-banner fix).
 *   4. Fixed test duration: the duration RangeSlider is gone from the
 *      card (no slider role anywhere on the page).
 */

import {
	act,
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeConfig } from "@/__tests__/helpers/fixtures";
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	pythonMock,
	resetStableMocks,
	snackbarMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";
import { TooltipProvider } from "@/components/ui/tooltip";

const { mockCall } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock({ noopEvent: true }));
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("@/components/audio/AudioFilterChain", () => ({
	AudioFilterChain: () => <div data-testid="audio-filter-chain" />,
}));
vi.mock("@/hooks/useOfflinePackDownload", () => ({
	useOfflinePackDownload: () => ({ status: "ready", isReady: true }),
}));

const MIC_A = {
	index: 0,
	id: "mic-a",
	name: "Blue Yeti X",
	host_api: "MME",
	default: false,
	channels: 2,
	rate: 48000,
};
const MIC_B = {
	index: 1,
	id: "mic-b",
	name: "Rode NT-USB",
	host_api: "MME",
	default: true,
	channels: 1,
	rate: 44100,
};

async function renderMicrophonePage(configOverrides = {}) {
	mockCall.mockImplementation((cmd: string) => {
		if (cmd === "get_microphones") return Promise.resolve([MIC_A, MIC_B]);
		if (cmd === "get_config")
			return Promise.resolve(makeConfig(configOverrides));
		return Promise.resolve({});
	});
	const { default: MicrophonePage } = await import("@/pages/Microphone");
	render(
		<TooltipProvider delayDuration={200}>
			<MicrophonePage />
		</TooltipProvider>,
	);
	await waitFor(
		() => {
			expect(screen.getByText("Start Test")).toBeTruthy();
		},
		{ timeout: 3000 },
	);
}

describe("Microphone page — Start Test consent gate (GDPR Art. 9)", () => {
	beforeEach(() => {
		resetStableMocks();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("opens the consent gate instead of calling microphone_test_start when consent is off", async () => {
		await renderMicrophonePage({ voice_biometric_consent: false });

		fireEvent.click(screen.getByText("Start Test"));

		const startCalls = mockCall.mock.calls.filter(
			(c) => c[0] === "microphone_test_start",
		);
		expect(startCalls.length).toBe(0);

		// The store must be imported dynamically — vi.resetModules() above
		// means the page holds a FRESH lib/consentGate instance; a
		// top-level import would be a different singleton.
		const { useConsentGateStore } = await import("@/lib/consentGate");
		const req = useConsentGateStore.getState().request;
		expect(req).toEqual(
			expect.objectContaining({
				consentField: "voice_biometric_consent",
				bodyKey: "consentDialog.field.voice_biometric_consent",
				onAllow: expect.any(Function),
			}),
		);
	});

	it("retries the blocked test start when the consent dialog's Allow fires", async () => {
		await renderMicrophonePage({ voice_biometric_consent: false });

		fireEvent.click(screen.getByText("Start Test"));
		const { useConsentGateStore } = await import("@/lib/consentGate");
		const req = useConsentGateStore.getState().request;
		expect(req).toBeTruthy();

		req?.onAllow?.();
		await waitFor(() => {
			const startCalls = mockCall.mock.calls.filter(
				(c) => c[0] === "microphone_test_start",
			);
			expect(startCalls.length).toBe(1);
		});
	});

	it("starts directly and never prompts when consent IS granted", async () => {
		await renderMicrophonePage({ voice_biometric_consent: true });

		fireEvent.click(screen.getByText("Start Test"));

		await waitFor(() => {
			const startCalls = mockCall.mock.calls.filter(
				(c) => c[0] === "microphone_test_start",
			);
			expect(startCalls.length).toBe(1);
		});
		const { useConsentGateStore } = await import("@/lib/consentGate");
		expect(useConsentGateStore.getState().request).toBeNull();
	});
});

describe("Microphone page — unified radio mic list", () => {
	beforeEach(() => {
		resetStableMocks();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("selects a device via its radio (set_config microphone=<id>)", async () => {
		await renderMicrophonePage({
			voice_biometric_consent: true,
			microphone: null,
		});

		fireEvent.click(screen.getByRole("radio", { name: "Blue Yeti X" }));

		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("set_config", {
				microphone: "mic-a",
			});
		});
	});

	it("selects System Default via a row click (set_config microphone=null)", async () => {
		await renderMicrophonePage({
			voice_biometric_consent: true,
			microphone: "mic-a",
		});

		// Row click (NOT on the radio control) must route through the same
		// handler.
		fireEvent.click(screen.getByTestId("system-default-row"));

		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("set_config", {
				microphone: null,
			});
		});
	});
});

describe("Microphone page — device-lost recovery via mic switch", () => {
	beforeEach(() => {
		resetStableMocks();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("clears the lost flag and refreshes data when selecting a mic while lost", async () => {
		await renderMicrophonePage({
			voice_biometric_consent: true,
			microphone: null,
		});

		const { useDeviceLostStore } = await import("@/stores/deviceLostStore");
		// act(): the flag must be COMMITTED to the subscribed page (and its
		// callbacks re-created with lostSource set) BEFORE the click — an
		// unflushed update leaves the page's stale closure unable to see it.
		await act(async () => {
			useDeviceLostStore.getState().markLost("alsa:hw:1");
		});
		expect(useDeviceLostStore.getState().lostSource).toBe("alsa:hw:1");

		fireEvent.click(screen.getByRole("radio", { name: "Rode NT-USB" }));

		await waitFor(() => {
			expect(useDeviceLostStore.getState().lostSource).toBeNull();
		});
		// The refresh re-queries the backend for the device list.
		await waitFor(() => {
			const loads = mockCall.mock.calls.filter(
				(c) => c[0] === "get_microphones",
			);
			expect(loads.length).toBeGreaterThanOrEqual(2);
		});
	});
});

describe("Microphone page — fixed test duration (slider removed)", () => {
	beforeEach(() => {
		resetStableMocks();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders no slider role anywhere on the settled page", async () => {
		await renderMicrophonePage({
			voice_biometric_consent: true,
			microphone: null,
		});

		expect(screen.queryByRole("slider")).toBeNull();
	});
});
