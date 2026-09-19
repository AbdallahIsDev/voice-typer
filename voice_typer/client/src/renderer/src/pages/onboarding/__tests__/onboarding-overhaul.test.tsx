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

// Radix Checkbox → real input (consent switch / cloud consent drive
// checked state via fireEvent in sibling suites).
vi.mock("@/components/ui/checkbox", () => ({
	Checkbox: ({
		checked,
		onCheckedChange,
		...props
	}: {
		checked?: boolean | "indeterminate";
		onCheckedChange?: (checked: boolean | "indeterminate") => void;
	} & Omit<
		React.InputHTMLAttributes<HTMLInputElement>,
		"checked" | "onChange"
	>) => (
		<input
			type="checkbox"
			checked={checked === true}
			onChange={(e) => onCheckedChange?.(e.target.checked)}
			{...props}
		/>
	),
}));

import { TooltipProvider } from "@/components/ui/tooltip";
import OnboardingPage from "@/pages/Onboarding";
import ConsentStep from "@/pages/onboarding/components/ConsentStep";
import ModelStep from "@/pages/onboarding/components/ModelStep";

/** Render helper: the wizard's steps mount Radix Tooltips (InfoTooltip);
 * the real App shell wraps every page in a TooltipProvider (App.tsx),
 * so test mounts must too. */
function renderWithProviders(ui: React.ReactElement) {
	return render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);
}

/** The server's 4-step layout (voice_typer/server/onboarding.py). */
const STEP_NAMES = ["Welcome", "Consent", "Model", "Hotkey"] as const;

const MODEL_OPTIONS = [
	{
		name: "tiny",
		size: "~75MB",
		speed: "Fastest",
		description: "Multilingual, best for quick notes",
		vram_gb: 0.5,
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
];

/** Jump the wizard straight to a given step index (4-step layout). */
function mockStartAtStep(stepIndex: number) {
	let current = stepIndex;
	mockCall.mockImplementation((type: string) => {
		switch (type) {
			case "onboarding_start":
				return Promise.resolve({
					step: stepIndex,
					total_steps: 4,
					step_name: STEP_NAMES[stepIndex],
				});
			case "onboarding_next_step":
				current = Math.min(current + 1, STEP_NAMES.length - 1);
				return Promise.resolve({
					step: current,
					total_steps: 4,
					step_name: STEP_NAMES[current],
				});
			case "onboarding_prev_step":
				current = Math.max(current - 1, 0);
				return Promise.resolve({
					step: current,
					total_steps: 4,
					step_name: STEP_NAMES[current],
				});
			case "get_config":
				// huggingface_consent granted: the per-item Download
				// buttons must fire download_model directly (the consent
				// gate opens only when the grant is missing, C-MIC-3).
				return Promise.resolve({
					hotkey: "<caps_lock>",
					model_size: "tiny",
					microphone: null,
					huggingface_consent: true,
				});
			case "onboarding_get_microphones":
				return Promise.resolve({ microphones: [] });
			case "onboarding_get_hotkey_presets":
				return Promise.resolve({ presets: ["<caps_lock>", "<f2>"] });
			case "onboarding_get_model_options":
				return Promise.resolve({ models: MODEL_OPTIONS });
			case "get_model_catalog":
				return Promise.resolve({ models: [] });
			case "onboarding_check_permissions":
				return Promise.resolve({
					platform: "windows",
					state: "unknown",
					needed: false,
					instructions: null,
				});
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
}

describe("Onboarding 4-step essentials flow", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockShowSnack.mockReset();
	});

	afterEach(() => cleanup());

	it("renders the 4-step wizard: total_steps 4 and step names Welcome/Consent/Model/Hotkey", async () => {
		mockStartAtStep(0);
		renderWithProviders(<OnboardingPage onComplete={() => {}} />);

		// The localized progress text "Step 1 of 4" renders in the
		// header (the removed right-side title would have been here
		// too — asserted separately below).
		await screen.findByText("Step 1 of 4");

		// Advance through the whole wizard; every step must render its
		// expected heading with the progress text matching its index.
		const expected: string[] = [
			"Welcome to Voice Typer",
			"Privacy & Consent",
			"Choose Your Model",
			"Choose Your Hotkey",
		];
		let i = 0;
		for (const heading of expected) {
			// eslint-disable-next-line no-await-in-loop
			await waitFor(() => {
				expect(screen.getAllByText(heading).length).toBeGreaterThan(0);
			});
			expect(screen.getByText(`Step ${i + 1} of 4`)).toBeTruthy();
			if (i < expected.length - 1) {
				// eslint-disable-next-line no-await-in-loop
				fireEvent.click(screen.getByRole("button", { name: "Continue" }));
			}
			i += 1;
		}
	});

	it("hides the Back button on step 1 and shows it from step 2 onward (ONB-1)", async () => {
		mockStartAtStep(0);
		renderWithProviders(<OnboardingPage onComplete={() => {}} />);

		await screen.findByText("Step 1 of 4");
		// The Back button renders with aria-label backAria ("Go back").
		expect(screen.queryByRole("button", { name: "Go back" })).toBeNull();

		fireEvent.click(screen.getByRole("button", { name: "Continue" }));
		await screen.findByText("Step 2 of 4");
		expect(screen.getByRole("button", { name: "Go back" })).toBeTruthy();
	});

	it("does NOT render the top-right step-title span above the progress bar (ONB-1)", async () => {
		mockStartAtStep(1);
		renderWithProviders(<OnboardingPage onComplete={() => {}} />);

		await screen.findByText("Step 2 of 4");
		// The card heading "Privacy & Consent" is rendered exactly
		// ONCE (the sr-only h1 carries the "Step 2 of 4: " prefix, and
		expect(screen.getAllByText("Privacy & Consent")).toHaveLength(1);
	});

	it("renders NO Skip button or skip-confirm dialog on any step (ONB-3)", async () => {
		mockStartAtStep(0);
		renderWithProviders(<OnboardingPage onComplete={() => {}} />);

		for (let i = 0; i < STEP_NAMES.length; i++) {
			// eslint-disable-next-line no-await-in-loop
			await waitFor(() => {
				expect(
					screen.queryByRole("button", { name: "Continue" }) ??
						screen.queryByRole("button", { name: "Get started" }),
				).toBeTruthy();
			});
			expect(
				screen.queryByRole("button", { name: "Skip onboarding" }),
			).toBeNull();
			expect(screen.queryByText("Skip setup?")).toBeNull();
			if (i < STEP_NAMES.length - 1) {
				// eslint-disable-next-line no-await-in-loop
				fireEvent.click(screen.getByRole("button", { name: "Continue" }));
			}
		}
	});

	it("the final step's Get started button calls onboarding_apply (no Done summary step)", async () => {
		mockStartAtStep(3);
		renderWithProviders(<OnboardingPage onComplete={() => {}} />);

		await screen.findByText("Step 4 of 4");
		const getStarted = screen.getByRole("button", { name: "Get started" });
		expect(getStarted).toBeTruthy();

		fireEvent.click(getStarted);
		await waitFor(() => {
			expect(
				mockCall.mock.calls.some((c: unknown[]) => c[0] === "onboarding_apply"),
			).toBe(true);
		});
		// The success toast + navigation fired (apply resolved).
		await waitFor(() => {
			expect(mockShowSnack).toHaveBeenCalledWith(
				expect.stringContaining("Setup complete"),
				"success",
			);
		});
	});

	it("the final step persists the hotkey via onboarding_set_hotkey before applying", async () => {
		mockStartAtStep(3);
		renderWithProviders(<OnboardingPage onComplete={() => {}} />);

		await screen.findByText("Step 4 of 4");
		fireEvent.click(screen.getByRole("button", { name: "Get started" }));

		await waitFor(() => {
			const hotkeyCalls = mockCall.mock.calls.filter(
				(c: unknown[]) => c[0] === "onboarding_set_hotkey",
			);
			expect(hotkeyCalls.length).toBe(1);
		});
	});
});

describe("ConsentStep divider layout (ONB-4)", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockShowSnack.mockReset();
	});
	afterEach(() => cleanup());
	it("renders rows without nested boxes and with per-row info tooltips", () => {
		renderWithProviders(
			<ConsentStep
				headingRef={{ current: null }}
				consents={{}}
				onToggleConsent={() => {}}
				onAgreeToAll={() => {}}
			/>,
		);

		// Full-width rows separated by dividing borders, no per-row
		// border boxes.
		expect(document.querySelector('[class*="divide-y"]')).toBeTruthy();
		expect(document.querySelector(".rounded-lg.border.p-4")).toBeNull();

		// Descriptions moved into per-row InfoTooltip triggers.
		const tooltips = document.querySelectorAll(
			'[aria-label^="More info about"]',
		);
		expect(tooltips.length).toBe(6);
	});
});

describe("Model step rebuild (ONB-5)", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockShowSnack.mockReset();
	});
	afterEach(() => cleanup());

	it("selecting a model row fires onboarding_set_model and advancing persists the backend choice", async () => {
		mockStartAtStep(2);
		renderWithProviders(<OnboardingPage onComplete={() => {}} />);

		await screen.findByText("Step 3 of 4");

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

	it("per-item Download fires download_model for that model; no standalone Download button or HF checkbox exists", async () => {
		mockStartAtStep(2);
		renderWithProviders(<OnboardingPage onComplete={() => {}} />);

		await screen.findByText("Step 3 of 4");

		// No duplicate consent / standalone-download affordances.
		expect(screen.queryByTestId("onboarding-hf-consent")).toBeNull();
		expect(screen.queryByTestId("onboarding-download-button")).toBeNull();

		fireEvent.click(screen.getByTestId("onboarding-download-button-tiny"));
		await waitFor(() => {
			const dl = mockCall.mock.calls.find(
				(c: unknown[]) => c[0] === "download_model",
			);
			expect(dl?.[1]).toEqual({ model: "tiny" });
		});
	});

	it("direct ModelStep render shows the family accordion + SegmentedControl (Models-page parity)", () => {
		renderWithProviders(
			<ModelStep
				headingRef={{ current: null }}
				modelOptions={MODEL_OPTIONS}
				selectedModel="tiny"
				setSelectedModel={() => {}}
				selectedBackend="local"
				setSelectedBackend={() => {}}
				downloadingModel={null}
				downloadProgress={0}
				downloadFailed={false}
				onDownload={() => {}}
				cloudProvider="openai"
				setCloudProvider={() => {}}
				cloudApiKey=""
				setCloudApiKey={() => {}}
				cloudConsent={false}
				setCloudConsent={() => {}}
			/>,
		);

		// SegmentedControl (Local / Cloud tabs).
		expect(screen.getByRole("tab", { name: "Local model" })).toBeTruthy();
		expect(screen.getByRole("tab", { name: "Cloud API" })).toBeTruthy();

		// Family accordion rows render with per-item download buttons.
		expect(screen.getByTestId("onboarding-model-select-tiny")).toBeTruthy();
		expect(screen.getByTestId("onboarding-download-button-tiny")).toBeTruthy();

		// No "Powered by" strip (removed; multi-provider).
		expect(screen.queryByText("Powered by")).toBeNull();
	});

	it("ERR-1: raw catalog-only entry without speed/size does NOT crash the wizard", async () => {
		// Real backend `get_model_catalog` returns ModelMetadata dicts
		// (speed_rating/download_size_mb/supported_languages, NO
		// speed/size). The qwen entry is catalog-only (not in curated
		// MODEL_OPTIONS). Before the fix, merge passed it through raw
		// and ModelStep's formatModelSpeed(m.speed) threw
		// "Cannot read properties of undefined (reading 'length')".
		mockCall.mockImplementation((type: string) => {
			switch (type) {
				case "onboarding_start":
					return Promise.resolve({
						step: 2,
						total_steps: 4,
						step_name: "Model",
					});
				case "get_config":
					return Promise.resolve({
						hotkey: "<caps_lock>",
						model_size: "tiny",
						huggingface_consent: true,
					});
				case "onboarding_get_hotkey_presets":
					return Promise.resolve({ presets: ["<caps_lock>"] });
				case "onboarding_get_model_options":
					return Promise.resolve({ models: MODEL_OPTIONS });
				case "get_model_catalog":
					return Promise.resolve({
						models: [
							{
								name: "qwen",
								download_size_mb: 0,
								required_vram_mb: 4096,
								backend: "qwen",
								description: "Qwen",
								supported_languages: null,
								speed_rating: "medium",
							},
						],
					});
				default:
					return Promise.resolve({});
			}
		});
		renderWithProviders(<OnboardingPage onComplete={() => {}} />);

		await screen.findByText("Step 3 of 4");
		// Qwen family rendered via normalized catalog entry, no crash.
		expect(screen.getByTestId("onboarding-model-select-qwen")).toBeTruthy();
		expect(screen.getByTestId("onboarding-download-button-qwen")).toBeTruthy();
	});
});
