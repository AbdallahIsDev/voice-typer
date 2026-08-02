/**
 * Tests for the Onboarding wizard fixes:
 *
 * - Microphone step "Refresh microphones" button re-fetches the
 *          mic list (was mis-wired to keyboard-permission re-probe).
 * - Done step "Get Started" kicks off onboarding_apply without
 *          awaiting; onComplete is called immediately so the user is
 *          navigated to Home (where DownloadProgressBar surfaces real
 *          progress). The wizard no longer blocks the UI for 10+ min.
 * - Welcome step renders a compact language picker with the 8
 *          supported locales; pre-selects the detected/saved locale;
 *          changing it calls setLocale().
 * - Done step reveals the Skip escape hatch when applyError is
 *          set (e.g. onboarding_apply threw synchronously because the
 *          IPC bridge is missing).
 * - ModelStep aria-label/placeholder interpolates the {name}
 *          placeholder in onboarding.modelSelectAria (was literal
 *          "{name}" — screen readers announced template tokens).
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

// Mock the Radix Select wrapper so SelectItem children render inline
// in the DOM without needing to drive the dropdown's pointer-capture /
// Portal machinery in jsdom. The mock renders the SelectTrigger as a
// <button> so we can interact with it, and SelectItem as a <div
// role="option"> so we can assert on option content.
vi.mock("@/components/ui/select", () => ({
	Select: ({
		children,
		value,
		onValueChange,
	}: {
		children: React.ReactNode;
		value?: string;
		onValueChange?: (v: string) => void;
	}) => (
		<div
			data-testid="select-root"
			data-value={value}
			role="listbox"
			onClick={(e) => {
				// Bubble option clicks up to the Select so
				// tests can drive selection via fireEvent.click
				// on the option div.
				const target = e.target as HTMLElement;
				const opt = target.closest('[role="option"]');
				if (opt && onValueChange) {
					onValueChange(opt.getAttribute("data-value") ?? "");
				}
			}}
			onKeyDown={(e) => {
				if (e.key === "Enter" || e.key === " ") {
					const target = e.target as HTMLElement;
					const opt = target.closest('[role="option"]');
					if (opt && onValueChange) {
						onValueChange(opt.getAttribute("data-value") ?? "");
					}
				}
			}}
		>
			{children}
		</div>
	),
	SelectTrigger: ({
		children,
		...props
	}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
		children?: React.ReactNode;
	}) => (
		<button type="button" {...props}>
			{children}
		</button>
	),
	SelectValue: ({ placeholder }: { placeholder?: string }) => (
		<span>{placeholder ?? ""}</span>
	),
	SelectContent: ({ children }: { children: React.ReactNode }) => (
		<div data-testid="select-content">{children}</div>
	),
	SelectItem: ({
		children,
		value,
		textValue,
	}: {
		children?: React.ReactNode;
		value: string;
		textValue?: string;
	}) => (
		<div
			data-value={value}
			data-text-value={textValue}
			role="option"
			tabIndex={-1}
		>
			{children}
		</div>
	),
}));

import { getLocale, SUPPORTED_LOCALES, setLocale } from "@/i18n/i18n";
import OnboardingPage from "@/pages/Onboarding";
import ModelStep from "@/pages/onboarding/components/ModelStep";
import WelcomeStep from "@/pages/onboarding/components/WelcomeStep";

const STEP_NAMES = [
	"Welcome",
	"Microphone",
	"Permissions",
	"Hotkey",
	"Model",
	"Done",
] as const;

// Radix Select's pointerDown handler calls
// `target.hasPointerCapture(pointerId)` which jsdom doesn't implement.
// Stub the three methods Radix touches so the mocked Select doesn't
// crash inside its event handlers.
if (
	typeof Element !== "undefined" &&
	typeof Element.prototype.hasPointerCapture !== "function"
) {
	Element.prototype.hasPointerCapture = function hasPointerCapture() {
		return false;
	};
	Element.prototype.setPointerCapture = function setPointerCapture() {};
	Element.prototype.releasePointerCapture = function releasePointerCapture() {};
}

// ── Shared helpers ───────────────────────────────────────────────────

function mockStartAtStep(
	stepIndex: number,
	opts: {
		microphones?: Array<Record<string, unknown>>;
		models?: Array<Record<string, unknown>>;
	} = {},
) {
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
					hotkey: "<caps_lock>",
					model_size: "small.en",
					microphone: "",
				});
			case "onboarding_get_microphones":
				return Promise.resolve({
					microphones: opts.microphones ?? [
						{ id: "mic-1", name: "Built-in Mic" },
					],
				});
			case "onboarding_get_hotkey_presets":
				return Promise.resolve({
					presets: ["<caps_lock>", "<f2>", "<f4>"],
				});
			case "onboarding_get_model_options":
				return Promise.resolve({
					models: opts.models ?? [
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

// ── Refresh microphones re-fetches the mic list ───────────────────

describe("Microphone step Refresh button re-fetches mic list", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockShowSnack.mockReset();
	});
	afterEach(() => cleanup());

	it("clicking Refresh microphones calls onboarding_get_microphones again and updates the list", async () => {
		// First call returns no mics; the second call (after Refresh)
		// returns a freshly-plugged-in mic.
		let getMicsCallCount = 0;
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					return Promise.resolve({
						step: 1,
						total_steps: 6,
						step_name: "Microphone",
					});
				case "get_config":
					return Promise.resolve({
						hotkey: "<caps_lock>",
						model_size: "small.en",
						microphone: "",
					});
				case "onboarding_get_microphones":
					getMicsCallCount += 1;
					if (getMicsCallCount === 1) {
						return Promise.resolve({ microphones: [] });
					}
					return Promise.resolve({
						microphones: [{ id: "mic-1", name: "USB Headset", default: true }],
					});
				case "onboarding_get_hotkey_presets":
					return Promise.resolve({ presets: ["<caps_lock>"] });
				case "onboarding_get_model_options":
					return Promise.resolve({ models: [] });
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

		render(<OnboardingPage onComplete={() => {}} />);

		// Wait for the Microphone step to mount with the empty mic list.
		await waitFor(() => {
			expect(
				screen.getAllByText("Choose Your Microphone").length,
			).toBeGreaterThan(0);
		});
		expect(screen.getByText("Refresh microphones")).toBeTruthy();

		// Sanity: the init effect already called get_microphones once.
		expect(getMicsCallCount).toBe(1);

		// Click Refresh microphones. This should call
		// onboarding_get_microphones again and update the mic list
		// so the user can proceed.
		fireEvent.click(screen.getByText("Refresh microphones"));

		await waitFor(() => {
			expect(getMicsCallCount).toBe(2);
		});

		// After refresh, the mic dropdown should show the new mic.
		// The mocked Select renders SelectItem children inline, so
		// the option text is in the DOM.
		await waitFor(() => {
			expect(screen.getByText("USB Headset")).toBeTruthy();
		});
	});

	it("Refresh microphones does NOT call onboarding_check_permissions (keyboard probe)", async () => {
		// Regression guard: the previous wiring passed
		// `reprobePermissions` (which calls
		// onboarding_check_permissions) as onRefreshMics. Verify
		// the new wiring calls onboarding_get_microphones instead.
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					return Promise.resolve({
						step: 1,
						total_steps: 6,
						step_name: "Microphone",
					});
				case "get_config":
					return Promise.resolve({
						hotkey: "<caps_lock>",
						model_size: "small.en",
						microphone: "",
					});
				case "onboarding_get_microphones":
					return Promise.resolve({ microphones: [] });
				case "onboarding_get_hotkey_presets":
					return Promise.resolve({ presets: ["<caps_lock>"] });
				case "onboarding_get_model_options":
					return Promise.resolve({ models: [] });
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

		render(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			expect(
				screen.getAllByText("Choose Your Microphone").length,
			).toBeGreaterThan(0);
		});

		// The init effect fires onboarding_check_permissions once
		// for the Permissions step (not for Microphone). Capture
		// the count BEFORE clicking Refresh.
		const permsBefore = mockCall.mock.calls.filter(
			(c: unknown[]) => c[0] === "onboarding_check_permissions",
		).length;

		fireEvent.click(screen.getByText("Refresh microphones"));

		await waitFor(() => {
			const getMicsCalls = mockCall.mock.calls.filter(
				(c: unknown[]) => c[0] === "onboarding_get_microphones",
			).length;
			expect(getMicsCalls).toBe(2);
		});

		// No additional onboarding_check_permissions call should
		// have been made as a result of clicking Refresh mics.
		const permsAfter = mockCall.mock.calls.filter(
			(c: unknown[]) => c[0] === "onboarding_check_permissions",
		).length;
		expect(permsAfter).toBe(permsBefore);
	});
});

// ── handleApply is fire-and-forget ─────────────────────────────────

describe("Done step Get Started does not block on onboarding_apply", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockShowSnack.mockReset();
	});
	afterEach(() => cleanup());

	it("calls onComplete immediately even when onboarding_apply has not resolved", async () => {
		// Make onboarding_apply never resolve — simulating a
		// backend that blocks for many minutes while downloading
		// the model. The wizard must NOT wait for it; onComplete
		// must fire immediately.
		let applyCallCount = 0;
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					return Promise.resolve({
						step: 5,
						total_steps: 6,
						step_name: "Done",
					});
				case "get_config":
					return Promise.resolve({
						hotkey: "<caps_lock>",
						model_size: "small.en",
						microphone: "",
						voice_biometric_consent: true,
					});
				case "onboarding_get_microphones":
					return Promise.resolve({
						microphones: [{ id: "mic-1", name: "Built-in Mic" }],
					});
				case "onboarding_get_hotkey_presets":
					return Promise.resolve({ presets: ["<caps_lock>"] });
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
				case "onboarding_apply":
					applyCallCount += 1;
					// Never resolve — simulates a long-running download.
					return new Promise(() => {});
				default:
					return Promise.resolve({});
			}
		});

		let onCompleteCalled = false;
		let onCompleteCallCount = 0;
		render(
			<OnboardingPage
				onComplete={() => {
					onCompleteCalled = true;
					onCompleteCallCount += 1;
				}}
			/>,
		);

		// Wait for the Done step to render and the consent probe to settle.
		await waitFor(() => {
			expect(screen.getByRole("button", { name: "Get started" })).toBeTruthy();
		});

		// Click "Get Started" — this calls handleApply.
		fireEvent.click(screen.getByRole("button", { name: "Get started" }));

		// The apply call must have been kicked off.
		await waitFor(() => {
			expect(applyCallCount).toBe(1);
		});

		// onComplete must be called immediately, even though
		// onboarding_apply has not resolved. This is the
		// fire-and-forget pattern: the user is navigated to Home
		// right away, and Home's DownloadProgressBar surfaces real
		// progress.
		await waitFor(() => {
			expect(onCompleteCalled).toBe(true);
		});
		expect(onCompleteCallCount).toBe(1);
	});
});

// ── Welcome step language picker ───────────────────────────────────

describe("Welcome step renders a language picker", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockShowSnack.mockReset();
	});
	afterEach(() => cleanup());

	it("renders a Select with the 8 supported locales", async () => {
		mockStartAtStep(0);

		render(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			expect(
				screen.getAllByText("Welcome to Voice Typer").length,
			).toBeGreaterThan(0);
		});

		// The language picker should be present.
		const picker = screen.getByTestId("onboarding-language-picker");
		expect(picker).toBeTruthy();

		// All 8 supported locales should be rendered as SelectItem options.
		// The mocked Select renders SelectItem children inline.
		for (const locale of SUPPORTED_LOCALES) {
			// Each option renders the locale's label (e.g. "English",
			// "Deutsch", "العربية"). We assert the option exists by
			// looking for the data-value attribute on the option div.
			const option = picker.querySelector(`[data-value="${locale}"]`);
			expect(option).not.toBeNull();
		}
	});

	it("pre-selects the current locale (getLocale())", async () => {
		// Force a known locale so the assertion is deterministic.
		setLocale("de");

		mockStartAtStep(0);

		render(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			expect(
				screen.getAllByText("Welcome to Voice Typer").length,
			).toBeGreaterThan(0);
		});

		const picker = screen.getByTestId("onboarding-language-picker");
		const selectRoot = picker.querySelector('[data-testid="select-root"]');
		expect(selectRoot?.getAttribute("data-value")).toBe("de");

		// Reset locale to en so other tests aren't affected.
		setLocale("en");
	});

	it("renders the language picker when WelcomeStep is rendered directly", () => {
		// Unit-test the WelcomeStep component in isolation so the
		// assertion doesn't depend on the full wizard's IPC flow.
		setLocale("en");
		render(<WelcomeStep headingRef={() => {}} />);

		const picker = screen.getByTestId("onboarding-language-picker");
		expect(picker).toBeTruthy();

		// The Select root's data-value should match getLocale().
		const selectRoot = picker.querySelector('[data-testid="select-root"]');
		expect(selectRoot?.getAttribute("data-value")).toBe(getLocale());
	});

	it("renders all 8 supported locales as options when rendered directly", () => {
		setLocale("en");
		render(<WelcomeStep headingRef={() => {}} />);

		const picker = screen.getByTestId("onboarding-language-picker");
		for (const locale of SUPPORTED_LOCALES) {
			const option = picker.querySelector(`[data-value="${locale}"]`);
			expect(option).not.toBeNull();
		}
	});
});

// ── Skip escape hatch on Done step when apply fails ────────────────

describe("Done step reveals Skip button when apply fails", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockShowSnack.mockReset();
	});
	afterEach(() => cleanup());

	it("does NOT render Skip on Done step when no apply error has occurred", async () => {
		// Regression guard: the existing behaviour (Skip hidden on
		// Done step) must be preserved when applyError is false.
		mockStartAtStep(5);

		render(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			expect(screen.getByRole("button", { name: "Get started" })).toBeTruthy();
		});

		expect(
			screen.queryByRole("button", { name: "Skip onboarding" }),
		).toBeNull();
	});

	it("renders Skip on Done step when onboarding_apply throws synchronously", async () => {
		// Simulate a broken IPC bridge: call("onboarding_apply")
		// throws synchronously. The wizard must NOT navigate away
		// (onComplete is not called) and must reveal the Skip
		// escape hatch so the user is not trapped on the Done step.
		let onCompleteCalled = false;
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					return Promise.resolve({
						step: 5,
						total_steps: 6,
						step_name: "Done",
					});
				case "get_config":
					return Promise.resolve({
						hotkey: "<caps_lock>",
						model_size: "small.en",
						microphone: "",
						voice_biometric_consent: true,
					});
				case "onboarding_get_microphones":
					return Promise.resolve({
						microphones: [{ id: "mic-1", name: "Built-in Mic" }],
					});
				case "onboarding_get_hotkey_presets":
					return Promise.resolve({ presets: ["<caps_lock>"] });
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
				case "onboarding_apply":
					// Synchronous throw — simulates a missing
					// IPC bridge or a broken call() implementation.
					throw new Error("IPC bridge missing");
				case "onboarding_skip":
					return Promise.resolve({});
				default:
					return Promise.resolve({});
			}
		});

		render(
			<OnboardingPage
				onComplete={() => {
					onCompleteCalled = true;
				}}
			/>,
		);

		await waitFor(() => {
			expect(screen.getByRole("button", { name: "Get started" })).toBeTruthy();
		});

		// Click Get Started — this triggers handleApply which throws
		// synchronously. The wizard must NOT navigate away.
		fireEvent.click(screen.getByRole("button", { name: "Get started" }));

		// The Skip escape hatch should now be visible on the Done step.
		await waitFor(() => {
			expect(
				screen.getByRole("button", { name: "Skip onboarding" }),
			).toBeTruthy();
		});

		// The Skip button should be wired to the skip-confirm dialog
		// (same as on every other step). Clicking it opens the dialog.
		fireEvent.click(screen.getByRole("button", { name: "Skip onboarding" }));
		await waitFor(() => {
			expect(screen.getByText("Skip setup?")).toBeTruthy();
		});

		// onComplete must NOT have been called via handleApply (the
		// synchronous throw prevented navigation to Home).
		expect(onCompleteCalled).toBe(false);
	});
});

// ── modelSelectAria interpolates {name} ────────────────────────────

describe("ModelStep aria-label interpolates the model name", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockShowSnack.mockReset();
	});
	afterEach(() => cleanup());

	it("aria-label contains the selected model name (not literal '{name}')", () => {
		// Render ModelStep directly so we can assert on the
		// SelectTrigger's aria-label without driving the full
		// wizard IPC flow.
		render(
			<ModelStep
				headingRef={() => {}}
				modelOptions={[
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
				]}
				selectedModel="small.en"
				setSelectedModel={() => {}}
			/>,
		);

		// The SelectTrigger is rendered as a <button> by the mock.
		// Its aria-label should be the interpolated string
		// "Select model: small.en" — NOT "Select model: {name}".
		const trigger = screen.getByRole("button");
		const label = trigger.getAttribute("aria-label") ?? "";
		expect(label).toContain("small.en");
		expect(label).not.toContain("{name}");
	});

	it("aria-label updates when the selected model changes", () => {
		const { rerender } = render(
			<ModelStep
				headingRef={() => {}}
				modelOptions={[
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
				]}
				selectedModel="small.en"
				setSelectedModel={() => {}}
			/>,
		);

		let trigger = screen.getByRole("button");
		expect(trigger.getAttribute("aria-label")).toContain("small.en");

		// Re-render with a different selectedModel.
		rerender(
			<ModelStep
				headingRef={() => {}}
				modelOptions={[
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
				]}
				selectedModel="medium.en"
				setSelectedModel={() => {}}
			/>,
		);

		trigger = screen.getByRole("button");
		expect(trigger.getAttribute("aria-label")).toContain("medium.en");
		expect(trigger.getAttribute("aria-label")).not.toContain("{name}");
	});

	it("placeholder also interpolates the model name", () => {
		render(
			<ModelStep
				headingRef={() => {}}
				modelOptions={[
					{
						name: "tiny.en",
						size: "~75MB",
						speed: "Fastest",
						description: "Tiny",
					},
				]}
				selectedModel="tiny.en"
				setSelectedModel={() => {}}
			/>,
		);

		// The mocked SelectValue renders the placeholder as text.
		// The placeholder should contain the interpolated model name.
		const placeholderText = screen.getByText(/Select model:/);
		expect(placeholderText.textContent).toContain("tiny.en");
		expect(placeholderText.textContent).not.toContain("{name}");
	});
});
// ── Resume selections preserved (skip get_config override) ────────

describe("useOnboardingWizard: resume does not clobber selections with disk config", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockShowSnack.mockReset();
	});
	afterEach(() => cleanup());

	it("on a fresh start (step=0), get_config IS called to seed selections", async () => {
		// Sanity guard: the resume-skip logic must NOT suppress the
		// get_config probe on a genuine fresh start. The renderer needs
		// the disk config so a user who already configured the app via
		// Settings (then re-ran onboarding) sees their saved hotkey/model.
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					return Promise.resolve({
						step: 0,
						total_steps: 6,
						step_name: "Welcome",
					});
				case "get_config":
					return Promise.resolve({
						hotkey: "<f5>",
						model_size: "medium.en",
						microphone: "saved-mic-id",
					});
				case "onboarding_get_microphones":
					return Promise.resolve({
						microphones: [
							{ id: "saved-mic-id", name: "Saved Mic", default: true },
						],
					});
				case "onboarding_get_hotkey_presets":
					return Promise.resolve({ presets: ["<caps_lock>", "<f5>"] });
				case "onboarding_get_model_options":
					return Promise.resolve({ models: [] });
				default:
					return Promise.resolve({});
			}
		});

		render(<OnboardingPage onComplete={() => {}} />);

		// Wait for the Welcome step to render.
		await waitFor(() => {
			expect(
				screen.getAllByText("Welcome to Voice Typer").length,
			).toBeGreaterThan(0);
		});

		// get_config must have been called on a fresh start.
		const getConfigCalls = mockCall.mock.calls.filter(
			(c: unknown[]) => c[0] === "get_config",
		).length;
		expect(getConfigCalls).toBeGreaterThanOrEqual(1);
	});

	it("on a resume (step>0), get_config is NOT called for selection override", async () => {
		// Regression guard for the resume-clobber bug: previously the
		// init() effect unconditionally called get_config and overwrote
		// the React selections with the disk config's pre-wizard
		// defaults (since onboarding_apply was never called). On a
		// resume, that silently threw away the controller's in-memory
		// restored selections. Now init() detects step>0 and skips the
		// get_config override entirely.
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					// Resume at the Hotkey step (step index 3) — the
					// user picked a mic + advanced through Permissions
					// before closing the app.
					return Promise.resolve({
						step: 3,
						total_steps: 6,
						step_name: "Hotkey",
					});
				case "get_config":
					// If called, returns values that DIFFER from the
					// renderer defaults — so the test fails if init()
					// forgets to skip the override.
					return Promise.resolve({
						hotkey: "<f11>",
						model_size: "medium.en",
						microphone: "should-not-appear",
					});
				case "onboarding_get_microphones":
					return Promise.resolve({
						microphones: [
							{ id: "saved-mic-id", name: "Saved Mic", default: true },
						],
					});
				case "onboarding_get_hotkey_presets":
					return Promise.resolve({ presets: ["<caps_lock>", "<f11>"] });
				case "onboarding_get_model_options":
					return Promise.resolve({ models: [] });
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

		render(<OnboardingPage onComplete={() => {}} />);

		// Wait for the Hotkey step to render.
		await waitFor(() => {
			expect(screen.getAllByText("Choose Your Hotkey").length).toBeGreaterThan(
				0,
			);
		});

		// The init effect's get_config probe must NOT have been called
		// (the consent probe on Onboarding.tsx only fires on the Done
		// step, which we are not on). This is the core regression guard.
		const getConfigCalls = mockCall.mock.calls.filter(
			(c: unknown[]) => c[0] === "get_config",
		).length;
		expect(getConfigCalls).toBe(0);

		// The renderer's default hotkey (HOTKEY_DEFAULT = <caps_lock>)
		// must be shown — NOT the disk config's "<f11>". The mocked
		// Select renders the trigger value as the SelectValue's
		// placeholder, so we assert the trigger does not contain
		// "<f11>" (the disk config value).
		const selectRoot = document.querySelector('[data-testid="select-root"]');
		expect(selectRoot).not.toBeNull();
		const selectValue = selectRoot?.getAttribute("data-value") ?? "";
		expect(selectValue).toBe("<caps_lock>");
		expect(selectValue).not.toBe("<f11>");
	});
});
