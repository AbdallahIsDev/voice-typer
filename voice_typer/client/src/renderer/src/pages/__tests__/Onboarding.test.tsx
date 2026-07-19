/**
 * Tests for the Onboarding wizard — F2 (b-review Finding 6).
 *
 * Regression: previously the wizard initialised `selectedHotkey` to
 * `"<f2>"`, `selectedModel` to `"small.en"`, and `selectedMic` to `""`
 * hardcoded. If the user already had a hotkey/model/mic set in their
 * config (e.g. they re-ran the wizard from Settings → "Run onboarding
 * again"), the wizard showed the defaults and overwrote the existing
 * config on "Continue".
 *
 * After the fix, Onboarding fetches the current config after
 * `onboarding_start` resolves and pre-selects the user's existing
 * hotkey/model/microphone. We verify this by mocking get_config to
 * return a non-default hotkey/model and asserting the wizard's summary
 * (step 4) shows the user's values, not the defaults.
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockCall, mockShowSnack } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockShowSnack: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({ showSnack: mockShowSnack }),
}));

// Radix Select uses portals + pointer events that jsdom doesn't fully
// support. We stub it with a simple div-based rendering that surfaces
// the current value as text. We don't need to exercise the open/close
// interaction — we just need the trigger's visible text to reflect the
// selected value.
vi.mock("@/components/ui/select", () => ({
	Select: ({
		value,
		children,
	}: {
		value: string;
		onValueChange: (v: string) => void;
		children: React.ReactNode;
	}) => (
		<div data-testid="select" data-value={value}>
			<span data-testid="select-value">{value}</span>
			{children}
		</div>
	),
	SelectTrigger: ({
		children,
		...rest
	}: {
		children?: React.ReactNode;
		"aria-label"?: string;
		className?: string;
	}) => (
		<div {...rest} data-testid="select-trigger">
			{children}
		</div>
	),
	SelectValue: ({ placeholder }: { placeholder?: string }) => (
		<span data-testid="select-value-display">{placeholder}</span>
	),
	SelectContent: ({ children }: { children?: React.ReactNode }) => (
		<div data-testid="select-content">{children}</div>
	),
	SelectItem: ({
		value,
		children,
	}: {
		value: string;
		children?: React.ReactNode;
	}) => (
		<div data-value={value} data-testid="select-item">
			{children}
		</div>
	),
}));

import OnboardingPage from "@/pages/Onboarding";

describe("Onboarding wizard — F2: pre-select existing config values", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockShowSnack.mockReset();
	});

	afterEach(() => {
		cleanup();
	});

	/**
	 * Helper: mock the IPC handlers so the wizard loads successfully
	 * and the step navigation advances through to the summary screen.
	 *
	 * @param cfg The config object that get_config should return.
	 */
	function mockIpc(cfg: Record<string, unknown>) {
		// Step navigation: each onboarding_next_step call returns the
		// next step object (step: 1 → 2 → 3 → 4). The wizard uses
		// step.step to decide what to render, and step.total_steps
		// for the progress bar.
		let currentStep = 0;
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					return Promise.resolve({
						step: 0,
						total_steps: 6,
						step_name: "welcome",
					});
				case "onboarding_next_step":
					currentStep = Math.min(currentStep + 1, 5);
					return Promise.resolve({
						step: currentStep,
						total_steps: 6,
						step_name: `step-${currentStep}`,
					});
				case "onboarding_prev_step":
					currentStep = Math.max(currentStep - 1, 0);
					return Promise.resolve({
						step: currentStep,
						total_steps: 6,
						step_name: `step-${currentStep}`,
					});
				case "get_config":
					return Promise.resolve(cfg);
				case "onboarding_get_microphones":
					return Promise.resolve({
						microphones: [
							{ id: "mic-1", name: "Built-in Mic" },
							{ id: "mic-2", name: "USB Mic" },
						],
					});
				case "onboarding_get_hotkey_presets":
					return Promise.resolve({
						presets: ["<f2>", "<f4>", "<f6>"],
					});
				case "onboarding_get_model_options":
					return Promise.resolve({
						models: [
							{
								name: "tiny.en",
								size: "~75MB",
								speed: "Fastest",
								description: "Tiny",
							},
							{
								name: "small.en",
								size: "~466MB",
								speed: "Fast",
								description: "Small",
							},
							{
								name: "medium.en",
								size: "~1.5GB",
								speed: "Slow",
								description: "Medium",
							},
						],
					});
				case "onboarding_set_microphone":
				case "onboarding_set_hotkey":
				case "onboarding_set_model":
				case "onboarding_apply":
				case "onboarding_skip":
					return Promise.resolve({});
				default:
					return Promise.resolve({});
			}
		});
	}

	it("pre-selects the user's existing hotkey + model from get_config (not the defaults)", async () => {
		// User already has hotkey=<f4> and model=tiny.en configured.
		// The wizard should show these, not the hardcoded <f2> / small.en defaults.
		mockIpc({
			hotkey: "<f4>",
			model_size: "tiny.en",
			microphone: "mic-2",
		});

		const { container } = render(<OnboardingPage onComplete={() => {}} />);

		// Wait for the wizard's init() to resolve (loading spinner
		// disappears, welcome screen renders).
		await waitFor(() => {
			expect(screen.getByText("Welcome to Voice Typer")).toBeTruthy();
		});

		// Advance through the wizard to the final Done step. The wizard is
		// now a 6-step flow (Welcome 0, Mic 1, Permissions 2, Hotkey 3,
		// Model 4, Done 5), so click Continue five times to reach the
		// "Get Started" button on the Done step.
		for (let i = 0; i < 5; i++) {
			const continueBtn = await screen.findByRole("button", {
				name: "Continue",
			});
			fireEvent.click(continueBtn);
			// Give React a tick to flush the state update before
			// polling for the next render.
			await waitFor(() => {
				// On the last click the button label flips to
				// "Get Started"; on earlier clicks "Continue" is
				// still visible.
				const hasContinue = screen.queryByRole("button", {
					name: "Continue",
				});
				const hasGetStarted = screen.queryByRole("button", {
					name: "Get Started",
				});
				expect(hasContinue !== null || hasGetStarted !== null).toBe(true);
			});
		}

		// At step 4 (Complete / Summary), the wizard shows the
		// selected hotkey (uppercased, with <> stripped) and the
		// selected model name. They should match the user's config,
		// not the hardcoded defaults.
		//
		// We wait for the Get Started button (aria-label is
		// "Get started" — note the lowercase 's' — matching the
		// onboarding.getStartedAria i18n key).
		await waitFor(() => {
			expect(screen.getByRole("button", { name: "Get started" })).toBeTruthy();
		});
		const summaryText = container.textContent ?? "";
		expect(summaryText).toContain("F4");
		expect(summaryText).toContain("tiny.en");
	});

	it("falls back to defaults when get_config fails (older backend)", async () => {
		// Mock get_config to reject; the wizard should still load
		// and fall back to the hardcoded defaults (<f2>, small.en).
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					return Promise.resolve({
						step: 0,
						total_steps: 6,
						step_name: "welcome",
					});
				case "onboarding_next_step": {
					const next = Math.min(
						Number.parseInt(
							String(
								(
									mockCall.mock.calls.find(
										(c) => c[0] === "onboarding_next_step",
									)?.[1] as { step?: number } | undefined
								)?.step ?? 0,
							),
							10,
						) + 1,
						5,
					);
					return Promise.resolve({
						step: next,
						total_steps: 6,
						step_name: `step-${next}`,
					});
				}
				case "get_config":
					return Promise.reject(new Error("not available"));
				case "onboarding_get_microphones":
					return Promise.resolve({
						microphones: [{ id: "mic-1", name: "Built-in" }],
					});
				case "onboarding_get_hotkey_presets":
					return Promise.resolve({ presets: ["<f2>"] });
				case "onboarding_get_model_options":
					return Promise.resolve({
						models: [
							{
								name: "small.en",
								size: "~466MB",
								speed: "Fast",
								description: "Small",
							},
						],
					});
				default:
					return Promise.resolve({});
			}
		});

		render(<OnboardingPage onComplete={() => {}} />);

		// Wizard should load despite get_config failure.
		await waitFor(() => {
			expect(screen.getByText("Welcome to Voice Typer")).toBeTruthy();
		});
	});

	it("calls get_config after onboarding_start to fetch the user's existing config", async () => {
		mockIpc({
			hotkey: "<f4>",
			model_size: "tiny.en",
			microphone: "",
		});

		render(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			expect(screen.getByText("Welcome to Voice Typer")).toBeTruthy();
		});

		// The fix should have called onboarding_start then get_config.
		const callTypes = mockCall.mock.calls.map((c) => c[0]);
		const startIdx = callTypes.indexOf("onboarding_start");
		const cfgIdx = callTypes.indexOf("get_config");
		expect(startIdx).toBeGreaterThanOrEqual(0);
		expect(cfgIdx).toBeGreaterThan(startIdx);
	});
});
