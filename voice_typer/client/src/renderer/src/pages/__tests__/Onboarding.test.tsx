/**
 * Tests for the Onboarding wizard (4-step essentials flow, 2026-09-14).
 *
 * Contracts under test:
 *
 * 1. The renderer mirrors the server's 4-step wizard
 *    (voice_typer/server/onboarding.py): Welcome → Consent → Model →
 *    Hotkey. Step rendering branches on `step_name` so the tests
 *    exercise the same step names the server actually emits.
 *
 * 2. F2 regression: selections are seeded from the saved config on
 *    every start (get_config runs on fresh start AND resume).
 *
 * 3. Model step: Continue is blocked until the user explicitly picks
 *    a model (no default model exists since the 2026-08-28 sentinel
 *    change), and the accordion rows fire onboarding_set_model.
 *
 * 4. Final step: "Get started" persists the hotkey (onboarding_set_hotkey)
 *    BEFORE onboarding_apply, then shows the success snack + navigates.
 *    Apply failure surfaces an inline alert + error snack and does NOT
 *    complete.
 *
 * All IPC is mocked via the shared stableMocks preamble.
 */

import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	pythonMock,
	snackbarMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall, showSnack: mockShowSnack } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock({ noopEvent: true }));
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());

/** Render helper: the wizard's steps mount Radix Tooltips (InfoTooltip);
 * the real App shell wraps every page in a TooltipProvider (App.tsx),
 * so test mounts must too. */
function renderWithProviders(ui: React.ReactElement) {
	return render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);
}

import { TooltipProvider } from "@/components/ui/tooltip";
import OnboardingPage from "@/pages/Onboarding";

/** The server's 4-step layout (voice_typer/server/onboarding.py). */
const STEP_NAMES = ["Welcome", "Consent", "Model", "Hotkey"] as const;

/** Localized step headings (en.json), used as step-render gates. */
const STEP_HEADINGS: string[] = [
	"Welcome to Voice Typer",
	"Privacy & Consent",
	"Choose Your Model",
	"Choose Your Hotkey",
];

/**
 * Mock the IPC handlers with a controllable wizard state machine.
 *
 * `get_config` returns `cfg` (defaults to a user with saved F4 +
 * large-v3-turbo so the "no default model" guard doesn't block).
 */
function mockWizard(
	options: {
		startStep?: number;
		cfg?: Record<string, unknown>;
		models?: Array<Record<string, unknown>>;
	} = {},
) {
	let current = options.startStep ?? 0;
	const cfg = {
		hotkey: "<f4>",
		model_size: "large-v3-turbo",
		microphone: null,
		...options.cfg,
	};
	mockCall.mockImplementation((type: string) => {
		switch (type) {
			case "onboarding_start":
			case "onboarding_next_step":
			case "onboarding_prev_step":
				return Promise.resolve({
					step: current,
					total_steps: STEP_NAMES.length,
					step_name: STEP_NAMES[current],
				});
			case "get_config":
				return Promise.resolve(cfg);
			case "onboarding_get_hotkey_presets":
				return Promise.resolve({ presets: ["<caps_lock>", "<f2>", "<f4>"] });
			case "onboarding_get_model_options":
				return Promise.resolve({
					models:
						options.models ??
						([
							{
								name: "tiny",
								size: "~75MB",
								speed: "Fastest",
								description: "Multilingual, best for quick notes",
								vram_gb: 0.5,
								languages: null,
							},
							{
								name: "large-v3",
								size: "~3GB",
								speed: "Slow",
								description: "Multilingual, highest accuracy",
								vram_gb: 4.0,
								languages: null,
							},
							{
								name: "large-v3-turbo",
								size: "~809MB",
								speed: "Fast",
								description: "Multilingual, near-large-v3 accuracy",
								vram_gb: 2.0,
								languages: null,
							},
							{
								name: "parakeet",
								size: "~1.2GB",
								speed: "Fast",
								description: "NVIDIA Parakeet",
								vram_gb: 2.0,
								languages: null,
							},
						] as Array<Record<string, unknown>>),
				});
			case "get_model_catalog":
				return Promise.resolve({ models: [] });
			case "onboarding_set_model":
			case "onboarding_set_backend":
			case "onboarding_set_hotkey":
			case "onboarding_apply":
			case "set_config":
				return Promise.resolve({});
			default:
				return Promise.resolve({});
		}
	});
	return {
		advance: () => {
			current = Math.min(current + 1, STEP_NAMES.length - 1);
		},
	};
}

/** Click Continue and wait for the given step heading to render. */
async function advanceToHeading(expectedHeading: string) {
	fireEvent.click(screen.getByRole("button", { name: "Continue" }));
	await waitFor(() => {
		expect(screen.getAllByText(expectedHeading).length).toBeGreaterThan(0);
	});
}

beforeEach(() => {
	mockCall.mockReset();
	mockShowSnack.mockReset();
});

afterEach(() => cleanup());

describe("Onboarding 4-step flow (server lockstep)", () => {
	it("renders each step's heading with matching progress text", async () => {
		const wizard = mockWizard({ startStep: 0 });

		renderWithProviders(<OnboardingPage onComplete={() => {}} />);

		let i = 0;
		for (const heading of STEP_HEADINGS) {
			// eslint-disable-next-line no-await-in-loop
			await waitFor(() => {
				expect(screen.getAllByText(heading).length).toBeGreaterThan(0);
			});
			// eslint-disable-next-line no-await-in-loop
			expect(screen.getByText(`Step ${i + 1} of 4`)).toBeTruthy();
			const next = STEP_HEADINGS[i + 1];
			if (next) {
				// eslint-disable-next-line no-await-in-loop
				wizard.advance();
				// eslint-disable-next-line no-await-in-loop
				await advanceToHeading(next);
			}
			i += 1;
		}
	});

	it("seeds selections from the saved config on every start (F2)", async () => {
		// get_config is probed on EVERY start (fresh + resume); a saved
		// F4 hotkey must be preselected instead of the renderer default.
		mockWizard({ startStep: 3, cfg: { hotkey: "<f4>" } });

		renderWithProviders(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			const calls = mockCall.mock.calls.filter(
				(c: unknown[]) => c[0] === "get_config",
			);
			expect(calls.length).toBeGreaterThanOrEqual(1);
		});
	});
});

describe("Model step gating + selection", () => {
	it("Continue is DISABLED when no model is selected (no default model)", async () => {
		mockWizard({ startStep: 2, cfg: { model_size: "" } });

		renderWithProviders(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			expect(screen.getAllByText("Choose Your Model").length).toBeGreaterThan(
				0,
			);
		});
		const continueBtn = await screen.findByRole("button", {
			name: "Continue",
		});
		expect(continueBtn.hasAttribute("disabled")).toBe(true);
	});

	it("Continue is ENABLED when a model is selected", async () => {
		mockWizard({ startStep: 2, cfg: { model_size: "large-v3-turbo" } });

		renderWithProviders(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			expect(screen.getAllByText("Choose Your Model").length).toBeGreaterThan(
				0,
			);
		});
		const continueBtn = await screen.findByRole("button", {
			name: "Continue",
		});
		expect(continueBtn.hasAttribute("disabled")).toBe(false);
	});

	it("selecting an accordion row fires onboarding_set_model + onboarding_set_backend on Continue", async () => {
		mockWizard({ startStep: 2, cfg: { model_size: "" } });

		renderWithProviders(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			expect(screen.getAllByText("Choose Your Model").length).toBeGreaterThan(
				0,
			);
		});

		// Pick large-v3-turbo via its accordion row.
		fireEvent.click(
			screen.getByTestId("onboarding-model-select-large-v3-turbo"),
		);
		fireEvent.click(screen.getByRole("button", { name: "Continue" }));

		await waitFor(() => {
			const setModel = mockCall.mock.calls.find(
				(c: unknown[]) => c[0] === "onboarding_set_model",
			);
			expect(setModel?.[1]).toEqual({ model: "large-v3-turbo" });
		});
		const backendCall = mockCall.mock.calls.find(
			(c: unknown[]) => c[0] === "onboarding_set_backend",
		);
		expect(backendCall?.[1]).toEqual({ backend: "local" });
	});
});

describe("Final step (Hotkey) apply flow", () => {
	it("Get started persists the hotkey BEFORE onboarding_apply", async () => {
		mockWizard({ startStep: 3 });

		renderWithProviders(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			expect(screen.getAllByText("Choose Your Hotkey").length).toBeGreaterThan(
				0,
			);
		});

		fireEvent.click(screen.getByRole("button", { name: "Get started" }));

		await waitFor(() => {
			const applyIdx = mockCall.mock.calls.findIndex(
				(c: unknown[]) => c[0] === "onboarding_apply",
			);
			expect(applyIdx).toBeGreaterThanOrEqual(0);
			const hotkeyIdx = mockCall.mock.calls.findIndex(
				(c: unknown[]) => c[0] === "onboarding_set_hotkey",
			);
			expect(hotkeyIdx).toBeGreaterThanOrEqual(0);
			expect(hotkeyIdx).toBeLessThan(applyIdx);
		});
		// Success toast fired (apply resolved).
		await waitFor(() => {
			expect(mockShowSnack).toHaveBeenCalledWith(
				expect.stringContaining("Setup complete"),
				"success",
			);
		});
	});

	it("apply failure surfaces the error snack + inline alert and does NOT complete", async () => {
		let applyShouldFail = true;
		mockWizard({ startStep: 3 });
		{
			const base = mockCall.getMockImplementation();
			if (!base) throw new Error("wizard mock not installed");
			mockCall.mockImplementation((type: string) => {
				if (type === "onboarding_apply") {
					return applyShouldFail
						? Promise.reject(new Error("disk full"))
						: Promise.resolve({});
				}
				return base(type);
			});
		}

		const onComplete = vi.fn();
		renderWithProviders(<OnboardingPage onComplete={onComplete} />);

		await waitFor(() => {
			expect(screen.getAllByText("Choose Your Hotkey").length).toBeGreaterThan(
				0,
			);
		});

		fireEvent.click(screen.getByRole("button", { name: "Get started" }));

		await waitFor(() => {
			expect(mockShowSnack).toHaveBeenCalledWith(
				"Failed to save selection",
				"error",
			);
		});
		expect(onComplete).not.toHaveBeenCalled();
		expect(screen.getByRole("alert")).toBeTruthy();
		expect(screen.getByText("Couldn't finish setup")).toBeTruthy();

		// Retry succeeds once the backend recovers.
		applyShouldFail = false;
		fireEvent.click(screen.getByRole("button", { name: "Get started" }));
		await waitFor(() => {
			expect(mockShowSnack).toHaveBeenCalledWith(
				expect.stringContaining("Setup complete"),
				"success",
			);
		});
		expect(onComplete).toHaveBeenCalledTimes(1);
		// The inline alert resets at the start of every apply attempt.
		expect(screen.queryByRole("alert")).toBeNull();
	});
});
