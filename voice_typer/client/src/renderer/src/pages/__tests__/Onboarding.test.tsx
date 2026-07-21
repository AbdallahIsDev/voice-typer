/**
 * Tests for the Onboarding wizard.
 *
 * Two test groups:
 *
 * 1. F2 (b-review Finding 6): pre-select existing config values — the
 *    wizard should fetch the user's existing hotkey/model/microphone
 *    via `get_config` after `onboarding_start` resolves and pre-select
 *    them in the wizard UI, instead of overwriting with hardcoded
 *    defaults.
 *
 * 2. CR-6 (UX-4 / UX-27): the renderer must include a Permissions
 *    step at index 2 (between Microphone and Hotkey), matching the
 *    server's 6-step wizard declared in
 *    `voice_typer/server/onboarding.py:124-141`. The Permissions step
 *    must call `onboarding_check_permissions` on mount and render the
 *    platform-specific setup walkthrough returned by the IPC.
 *    Hotkey/Model/Done must shift to step indices 3/4/5.
 *
 * CR-6: server-side step order, mirrored from
 * `voice_typer/server/onboarding.py:131-138` so the renderer test
 * exercises the same step names the server actually emits. If the
 * server adds/reorders steps, this fixture must be updated in lock-
 * step — that's the whole point of branching on `step_name` instead
 * of numeric index.
 */

import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Hoisted mocks ────────────────────────────────────────────────────

const { mockCall, mockShowSnack } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockShowSnack: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: () => {},
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({ showSnack: mockShowSnack }),
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

import OnboardingPage from "@/pages/Onboarding";

const STEP_NAMES = [
	"Welcome",
	"Microphone",
	"Permissions",
	"Hotkey",
	"Model",
	"Done",
] as const;

// ── F2: pre-select existing config values ────────────────────────────

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
	 * and step navigation advances through to the summary screen.
	 *
	 * @param cfg The config object that get_config should return.
	 */
	function mockIpc(cfg: Record<string, unknown>) {
		let currentStep = 0;
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					return Promise.resolve({
						step: 0,
						total_steps: 6,
						step_name: STEP_NAMES[0],
					});
				case "onboarding_next_step":
					currentStep = Math.min(currentStep + 1, 5);
					return Promise.resolve({
						step: currentStep,
						total_steps: 6,
						step_name: STEP_NAMES[currentStep],
					});
				case "onboarding_prev_step":
					currentStep = Math.max(currentStep - 1, 0);
					return Promise.resolve({
						step: currentStep,
						total_steps: 6,
						step_name: STEP_NAMES[currentStep],
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
				case "onboarding_check_permissions":
					return Promise.resolve({
						platform: "linux",
						state: "granted",
						needed: false,
						instructions: null,
					});
				default:
					return Promise.resolve({});
			}
		});
	}

	it("mounts and pre-selects existing hotkey/model/microphone from get_config", async () => {
		mockIpc({
			hotkey: "<f4>",
			model_size: "tiny.en",
			microphone: "",
		});

		const { container } = render(<OnboardingPage onComplete={() => {}} />);

		// Advance through the wizard to the final Done step.
		// The wizard is a 6-step flow (Welcome 0, Mic 1, Permissions 2,
		// Hotkey 3, Model 4, Done 5). Click Continue five times to
		// reach the Done step (where the button label flips to
		// "Get Started"); a 6th click on "Get Started" would trigger
		// `onboarding_apply` + `onComplete`, but we stop at the
		// summary screen to assert the pre-selected values.
		for (let i = 0; i < 5; i++) {
			const continueBtn = await screen.findByRole("button", {
				name: "Continue",
			});
			fireEvent.click(continueBtn);
			// Give React a tick to flush the state update before
			// polling for the next render.
			await waitFor(() => {
				const hasContinue = screen.queryByRole("button", {
					name: "Continue",
				});
				const hasGetStarted = screen.queryByRole("button", {
					name: "Get Started",
				});
				expect(hasContinue !== null || hasGetStarted !== null).toBe(true);
			});
		}

		// At step 5 (Done / Summary), the wizard shows the
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
		let currentStep = 0;
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					return Promise.resolve({
						step: 0,
						total_steps: 6,
						step_name: STEP_NAMES[0],
					});
				case "onboarding_next_step":
					currentStep = Math.min(currentStep + 1, 5);
					return Promise.resolve({
						step: currentStep,
						total_steps: 6,
						step_name: STEP_NAMES[currentStep],
					});
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
				case "onboarding_check_permissions":
					return Promise.resolve({
						platform: "linux",
						state: "granted",
						needed: false,
						instructions: null,
					});
				default:
					return Promise.resolve({});
			}
		});

		const { container } = render(<OnboardingPage onComplete={() => {}} />);

		// Advance to the Done step.
		for (let i = 0; i < 5; i++) {
			const continueBtn = await screen.findByRole("button", {
				name: "Continue",
			});
			fireEvent.click(continueBtn);
			await waitFor(() => {
				const hasContinue = screen.queryByRole("button", {
					name: "Continue",
				});
				const hasGetStarted = screen.queryByRole("button", {
					name: "Get Started",
				});
				expect(hasContinue !== null || hasGetStarted !== null).toBe(true);
			});
		}

		await waitFor(() => {
			expect(screen.getByRole("button", { name: "Get started" })).toBeTruthy();
		});
		const summaryText = container.textContent ?? "";
		expect(summaryText).toContain("F2");
		expect(summaryText).toContain("small.en");
	});
});

// ── CR-6: Permissions step at index 2 ────────────────────────────────

describe("Onboarding wizard — CR-6: Permissions step at index 2", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockShowSnack.mockReset();
	});

	afterEach(() => {
		cleanup();
	});

	/**
	 * Helper: jump the wizard straight to a given step index by
	 * making `onboarding_start` return that step. Used to test
	 * individual step renderers without clicking through the
	 * preceding steps.
	 */
	function mockStartAtStep(stepIndex: number) {
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					return Promise.resolve({
						step: stepIndex,
						total_steps: 6,
						step_name: STEP_NAMES[stepIndex],
					});
				case "onboarding_next_step":
				case "onboarding_prev_step":
					return Promise.resolve({
						step: stepIndex,
						total_steps: 6,
						step_name: STEP_NAMES[stepIndex],
					});
				case "get_config":
					return Promise.resolve({
						hotkey: "<f2>",
						model_size: "small.en",
						microphone: "",
					});
				case "onboarding_get_microphones":
					return Promise.resolve({
						microphones: [{ id: "mic-1", name: "Built-in Mic" }],
					});
				case "onboarding_get_hotkey_presets":
					return Promise.resolve({
						presets: ["<f2>", "<f4>", "<f6>"],
					});
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
				case "onboarding_check_permissions":
					return Promise.resolve({
						platform: "linux",
						state: "denied",
						needed: true,
						instructions: {
							title: "Input Group + udev Rule Required",
							steps: [
								"Add yourself to the 'input' group",
								"Install the udev rule",
								"Log out and back in",
							],
							commands: [
								"sudo usermod -aG input $USER",
								'# KERNEL=="event*", SUBSYSTEM=="input", GROUP="input", MODE="0640"',
							],
						},
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

	it("renders the Permissions step at step index 2 (between Microphone and Hotkey)", async () => {
		mockStartAtStep(2);

		render(<OnboardingPage onComplete={() => {}} />);

		// Wait for the Permissions heading to render. This
		// confirms (a) the renderer has a branch for
		// step_name === "Permissions", and (b) the server's
		// step=2 (Permissions) no longer falls through to the
		// Hotkey branch.
		await waitFor(() => {
			expect(screen.getByText("Keyboard Monitoring Permission")).toBeTruthy();
		});
		// The "Test hotkey" button should also be present.
		expect(screen.getByRole("button", { name: "Test hotkey" })).toBeTruthy();
	});

	it("calls onboarding_check_permissions on mount when the Permissions step is shown", async () => {
		mockStartAtStep(2);

		render(<OnboardingPage onComplete={() => {}} />);

		// The IPC call should fire as soon as the Permissions
		// step mounts (useEffect on step.step_name ===
		// "Permissions").
		await waitFor(() => {
			const calls = mockCall.mock.calls.map((c: unknown[]) => c[0] as string);
			expect(calls).toContain("onboarding_check_permissions");
		});
	});

	it("renders platform-specific instructions when permission is needed (Linux)", async () => {
		mockStartAtStep(2);

		render(<OnboardingPage onComplete={() => {}} />);

		// The mocked IPC returns `needed: true` with Linux
		// instructions including the `sudo usermod -aG input`
		// command. The renderer should surface this verbatim
		// so a power user can apply it manually.
		await waitFor(() => {
			expect(screen.getByText("Input Group + udev Rule Required")).toBeTruthy();
		});
		expect(screen.getByText(/sudo usermod/)).toBeTruthy();
		expect(screen.getByText("Permission still required")).toBeTruthy();
	});

	it("renders the 'Permission granted' message when state is granted", async () => {
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					return Promise.resolve({
						step: 2,
						total_steps: 6,
						step_name: "Permissions",
					});
				case "get_config":
					return Promise.resolve({
						hotkey: "<f2>",
						model_size: "small.en",
						microphone: "",
					});
				case "onboarding_get_microphones":
					return Promise.resolve({ microphones: [] });
				case "onboarding_get_hotkey_presets":
					return Promise.resolve({ presets: ["<f2>"] });
				case "onboarding_get_model_options":
					return Promise.resolve({ models: [] });
				case "onboarding_check_permissions":
					return Promise.resolve({
						platform: "macos",
						state: "granted",
						needed: false,
						instructions: null,
					});
				default:
					return Promise.resolve({});
			}
		});

		render(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			expect(
				screen.getByText("Permission granted — hotkeys will work."),
			).toBeTruthy();
		});
	});

	it("renders the 'no permission needed' message on Windows / unknown platforms", async () => {
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					return Promise.resolve({
						step: 2,
						total_steps: 6,
						step_name: "Permissions",
					});
				case "get_config":
					return Promise.resolve({
						hotkey: "<f2>",
						model_size: "small.en",
						microphone: "",
					});
				case "onboarding_get_microphones":
					return Promise.resolve({ microphones: [] });
				case "onboarding_get_hotkey_presets":
					return Promise.resolve({ presets: ["<f2>"] });
				case "onboarding_get_model_options":
					return Promise.resolve({ models: [] });
				case "onboarding_check_permissions":
					return Promise.resolve({
						platform: "windows",
						state: "unknown",
						needed: false,
						instructions: null,
					});
				default:
					return Promise.resolve({});
			}
		});

		render(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			expect(
				screen.getByText(/No extra permission needed on this platform/),
			).toBeTruthy();
		});
	});

	it("shifts Hotkey/Model/Done to step indices 3/4/5 (out of 6)", async () => {
		// CR-6 regression guard: server-side step order is
		// [Welcome(0), Microphone(1), Permissions(2), Hotkey(3),
		//  Model(4), Done(5)]. Verify each step renders the
		// expected content when jumped to directly.
		for (const [idx, expectedText] of [
			[3, "Choose Your Hotkey"],
			[4, "Choose Your Model"],
			[5, "You're All Set!"],
		] as const) {
			cleanup();
			mockCall.mockReset();
			mockStartAtStep(idx);

			render(<OnboardingPage onComplete={() => {}} />);

			await waitFor(() => {
				expect(screen.getByText(expectedText)).toBeTruthy();
			});

			// The progress indicator should show "Step N of 6"
			// where N is idx+1, confirming the wizard is in
			// 6-step mode (not the old 5-step mode where
			// Done would have been "Step 5 of 5").
			expect(screen.getByText(`Step ${idx + 1} of 6`)).toBeTruthy();
		}
	});

	it("shows 'Get Started' button (not 'Continue') only on the Done step (index 5)", async () => {
		// On every step except Done, the primary button is
		// "Continue"; on Done it's "Get Started" (aria-label is
		// "Get started" — lowercase 's' — matching the i18n key
		// `getStartedAria`).
		for (const idx of [0, 1, 2, 3, 4]) {
			cleanup();
			mockCall.mockReset();
			mockStartAtStep(idx);

			render(<OnboardingPage onComplete={() => {}} />);

			await waitFor(() => {
				expect(screen.getByRole("button", { name: "Continue" })).toBeTruthy();
			});
			// "Get Started" should NOT appear before the Done step.
			expect(screen.queryByRole("button", { name: /Get started/i })).toBeNull();
			// Skip should appear on every non-Done step
			// (CR-6: skip guard is now `!isDoneStep` rather
			// than `step < 4`).
			expect(
				screen.getByRole("button", { name: "Skip onboarding" }),
			).toBeTruthy();
		}

		// On the Done step, "Get Started" appears and Skip is hidden.
		cleanup();
		mockCall.mockReset();
		mockStartAtStep(5);

		render(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			expect(screen.getByRole("button", { name: "Get started" })).toBeTruthy();
		});
		expect(
			screen.queryByRole("button", { name: "Skip onboarding" }),
		).toBeNull();
	});

	it("Test hotkey button: shows success message when the selected hotkey is pressed", async () => {
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					return Promise.resolve({
						step: 2,
						total_steps: 6,
						step_name: "Permissions",
					});
				case "get_config":
					return Promise.resolve({
						hotkey: "<f2>",
						model_size: "small.en",
						microphone: "",
					});
				case "onboarding_get_microphones":
					return Promise.resolve({ microphones: [] });
				case "onboarding_get_hotkey_presets":
					return Promise.resolve({ presets: ["<f2>"] });
				case "onboarding_get_model_options":
					return Promise.resolve({ models: [] });
				case "onboarding_check_permissions":
					return Promise.resolve({
						platform: "macos",
						state: "granted",
						needed: false,
						instructions: null,
					});
				default:
					return Promise.resolve({});
			}
		});

		render(<OnboardingPage onComplete={() => {}} />);

		// Wait for the Permissions step + permission probe to finish.
		await waitFor(() => {
			expect(screen.getByRole("button", { name: "Test hotkey" })).toBeTruthy();
		});

		// Click the button — should enter listening state and
		// show the "Press your hotkey to test" label.
		fireEvent.click(screen.getByRole("button", { name: "Test hotkey" }));
		await waitFor(() => {
			expect(screen.getByText("Press your hotkey to test")).toBeTruthy();
		});

		// Simulate pressing F2 (the selected hotkey). The
		// browser fires KeyboardEvent with key="F2"; the
		// renderer's normalizer strips <> and lowercases both
		// sides, so "<f2>" → "f2" matches "F2" → "f2".
		window.dispatchEvent(new KeyboardEvent("keydown", { key: "F2" }));

		await waitFor(() => {
			expect(screen.getByText("Hotkey detected! It works.")).toBeTruthy();
		});
	});
});
