/**
 * Tests for the reworked onboarding Model step.
 *
 * The core product decision under test: the app NEVER downloads a model
 * automatically. The Model step is the user's explicit choice:
 *   • "Local model" → pick a model + grant HuggingFace consent + click
 *     Download (the ONLY download trigger in the wizard).
 *   • "Cloud API" → pick a provider + API key + consent, persisted via
 *     the allowlisted set_config fields (mirrors the Models page).
 *
 * Covered here:
 *   1. Local is the default; Download is disabled until HF consent.
 *   2. Clicking Download (with consent) fires download_model + persists
 *      huggingface_consent — and nothing is downloaded on a normal pass.
 *   3. download_progress push events drive the in-wizard progress bar.
 *   4. Cloud branch renders provider/API-key/consent; Continue persists
 *      them via set_config + onboarding_set_backend("cloud").
 *   5. The Done step no longer promises a background download and shows
 *      the chosen backend in the summary.
 */

import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
	within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module. Variants here:
// usePythonEvent captures handlers into the pythonEventHandlers map
// (tests drive download_progress etc. directly), and the Radix Select
// mock below is file-specific and stays inline.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	pythonMock,
	snackbarMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const {
	mockCall,
	showSnack: mockShowSnack,
	pythonEventHandlers: eventHandlers,
} = stableMocks;

vi.mock("@/hooks/usePython", () =>
	pythonMock({ captureEvents: stableMocks.pythonEventHandlers }),
);
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());

//mock the Radix Select wrapper so options render inline (same as the
// sibling Onboarding tests — these tests don't drive pointer-capture
// machinery, they assert on wiring).
vi.mock("@/components/ui/select", () => ({
	Select: ({ children }: { children: React.ReactNode }) => (
		<div data-testid="select-root">{children}</div>
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

//mock the Radix Checkbox wrapper so the HuggingFace + cloud consent
// checkboxes render as a real <input type="checkbox"> in jsdom — the
// Radix primitive is a <button role="checkbox"> whose pointer + keyboard
// events jsdom does not simulate uniformly, and these tests drive it via
// fireEvent.click + assert on `.checked`. Same shape as the Onboarding
// Select mock above (forwards checked + onCheckedChange to a real input).
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

import OnboardingPage from "@/pages/Onboarding";

const STEP_NAMES = [
	"Welcome",
	"Microphone",
	"Permissions",
	"Hotkey",
	"Model",
	"Done",
] as const;

const MODEL_OPTIONS = [
	{
		name: "tiny",
		size: "~466MB",
		speed: "Fast",
		description: "Small",
	},
	{
		name: "large-v3-turbo",
		size: "~1.5GB",
		speed: "Slow",
		description: "Medium",
	},
	{
		name: "parakeet",
		size: "~1.2GB",
		speed: "Fast",
		description: "NVIDIA Parakeet",
	},
];

/** Jump the wizard straight to a given step index. */
function mockStartAtStep(stepIndex: number, cfg: Record<string, unknown> = {}) {
	mockCall.mockImplementation((type: string) => {
		switch (type) {
			case "onboarding_start":
				return Promise.resolve({
					step: stepIndex,
					total_steps: 6,
					step_name: STEP_NAMES[stepIndex],
				});
			case "onboarding_next_step":
				return Promise.resolve({
					step: Math.min(stepIndex + 1, 5),
					total_steps: 6,
					step_name: STEP_NAMES[Math.min(stepIndex + 1, 5)],
				});
			case "onboarding_prev_step":
				return Promise.resolve({
					step: Math.max(stepIndex - 1, 0),
					total_steps: 6,
					step_name: STEP_NAMES[Math.max(stepIndex - 1, 0)],
				});
			case "get_config":
				return Promise.resolve({
					hotkey: "<caps_lock>",
					model_size: "tiny",
					microphone: "",
					huggingface_consent: false,
					cloud_openai_consent: false,
					...cfg,
				});
			case "onboarding_get_microphones":
				return Promise.resolve({
					microphones: [{ id: "mic-1", name: "Built-in Mic" }],
				});
			case "onboarding_get_hotkey_presets":
				return Promise.resolve({ presets: ["<caps_lock>"] });
			case "onboarding_get_model_options":
				return Promise.resolve({ models: MODEL_OPTIONS });
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

describe("Onboarding Model step — explicit backend choice, no auto-download", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockShowSnack.mockReset();
		Object.keys(eventHandlers).forEach((k) => {
			delete eventHandlers[k];
		});
	});

	afterEach(() => {
		cleanup();
	});

	it("defaults to the Local branch and disables Download until HuggingFace consent is granted", async () => {
		mockStartAtStep(4);
		render(<OnboardingPage onComplete={() => {}} />);

		// The Local backend card is pre-selected.
		await waitFor(() => {
			expect(screen.getByTestId("onboarding-backend-local")).toBeTruthy();
		});
		const localCard = screen.getByTestId("onboarding-backend-local");
		expect(localCard.getAttribute("aria-checked")).toBe("true");
		expect(
			screen
				.getByTestId("onboarding-backend-cloud")
				.getAttribute("aria-checked"),
		).toBe("false");

		// The model select + HF consent + Download button render.
		expect(screen.getByRole("button", { name: /Select model/ })).toBeTruthy();
		const consent = screen.getByTestId(
			"onboarding-hf-consent",
		) as HTMLInputElement;
		expect(consent.checked).toBe(false);

		// Download is disabled without consent.
		const download = screen.getByTestId(
			"onboarding-download-button",
		) as HTMLButtonElement;
		expect(download.disabled).toBe(true);

		// Granting consent enables it.
		fireEvent.click(consent);
		await waitFor(() => {
			expect(
				(screen.getByTestId("onboarding-download-button") as HTMLButtonElement)
					.disabled,
			).toBe(false);
		});
	});

	it("Download fires download_model ONLY after consent, and persists huggingface_consent", async () => {
		mockStartAtStep(4);
		render(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			expect(screen.getByTestId("onboarding-hf-consent")).toBeTruthy();
		});

		// Click Download WITHOUT consent → the button is disabled, so
		// no download_model call can happen.
		fireEvent.click(screen.getByTestId("onboarding-download-button"));
		await new Promise((r) => setTimeout(r, 0));
		expect(
			mockCall.mock.calls.filter((c: unknown[]) => c[0] === "download_model"),
		).toHaveLength(0);

		// Grant consent, then download. Keep download_model pending so
		// the progress UI stays mounted while we assert the call args.
		fireEvent.click(screen.getByTestId("onboarding-hf-consent"));
		mockCall.mockImplementation((type: string) => {
			if (type === "download_model") return new Promise(() => {});
			return Promise.resolve({});
		});

		fireEvent.click(await screen.findByTestId("onboarding-download-button"));

		await waitFor(() => {
			expect(
				mockCall.mock.calls.some((c: unknown[]) => c[0] === "download_model"),
			).toBe(true);
		});
		// huggingface_consent is persisted before the download.
		expect(
			mockCall.mock.calls.some(
				(c: unknown[]) =>
					c[0] === "set_config" &&
					(c[1] as Record<string, unknown>).huggingface_consent === true,
			),
		).toBe(true);
		const downloadArgs = mockCall.mock.calls.find(
			(c: unknown[]) => c[0] === "download_model",
		);
		expect(downloadArgs?.[1]).toEqual({ model: "tiny" });
	});

	it("download_progress push events update the in-wizard progress bar", async () => {
		mockStartAtStep(4);
		render(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			expect(screen.getByTestId("onboarding-hf-consent")).toBeTruthy();
		});

		// Grant consent and stall download_model so progress renders.
		fireEvent.click(screen.getByTestId("onboarding-hf-consent"));
		mockCall.mockImplementation((type: string) => {
			if (type === "download_model") return new Promise(() => {});
			return Promise.resolve({});
		});

		fireEvent.click(await screen.findByTestId("onboarding-download-button"));
		await waitFor(() => {
			expect(screen.getByTestId("onboarding-download-progress")).toBeTruthy();
		});

		// Fire a progress push event (the hook subscribes via
		// usePythonEvent("download_progress", ...)).
		eventHandlers.download_progress?.({ progress: 42 });

		await waitFor(() => {
			const bar = screen.getByTestId("onboarding-download-progress");
			expect(bar.getAttribute("aria-valuenow")).toBe("42");
			expect(bar.textContent).toContain("42%");
		});
	});

	it("shows a family-brand strip above the local model picker (and hides it in the cloud branch)", async () => {
		mockStartAtStep(4);
		render(<OnboardingPage onComplete={() => {}} />);

		// The strip appears in the Local branch, listing the brands of
		// the offered families (Whisper → OpenAI, Parakeet → NVIDIA).
		await waitFor(() => {
			expect(screen.getByTestId("onboarding-family-strip")).toBeTruthy();
		});
		const strip = screen.getByTestId("onboarding-family-strip");

		// Vitest inlines the SVGs as data URIs — assert on each file's
		// unique content (same convention as the FamilyLogo tests).
		const srcs = Array.from(strip.querySelectorAll("img")).map(
			(i) => i.getAttribute("src") ?? "",
		);
		expect(srcs.some((s) => s.includes("OpenAI%20icon"))).toBe(true);
		expect(srcs.some((s) => s.includes("Nvidia%20icon"))).toBe(true);
		// No Qwen logo — qwen is not offered in the onboarding catalog.
		expect(srcs.some((s) => s.includes("Qwen%20icon"))).toBe(false);

		// Brand names + the localized strip label render next to the
		// logos (the label resolves to "Powered by" in en). The whisper
		// family shows the COMPANY name (OpenAI) — UI/UX overhaul
		// point 5a, matching the Models page group headers.
		expect(within(strip).getByText("OpenAI")).toBeTruthy();
		expect(within(strip).getByText("Nvidia")).toBeTruthy();
		expect(within(strip).getByText("Powered by")).toBeTruthy();

		// The strip is a local-model affordance — switching to Cloud
		// removes it.
		fireEvent.click(screen.getByTestId("onboarding-backend-cloud"));
		await waitFor(() => {
			expect(screen.queryByTestId("onboarding-family-strip")).toBeNull();
		});
	});

	it("switching to Cloud renders provider/API-key/consent and Continue persists them via set_config", async () => {
		mockStartAtStep(4);
		render(<OnboardingPage onComplete={() => {}} />);

		await waitFor(() => {
			expect(screen.getByTestId("onboarding-backend-cloud")).toBeTruthy();
		});
		fireEvent.click(screen.getByTestId("onboarding-backend-cloud"));

		// Cloud panel appears: API key input + consent checkbox.
		const apiKey = (await screen.findByTestId(
			"onboarding-cloud-api-key",
		)) as HTMLInputElement;
		fireEvent.change(apiKey, { target: { value: "sk-test-123" } });
		fireEvent.click(screen.getByTestId("onboarding-cloud-consent"));

		// Continue persists the cloud choice + API key + consent.
		fireEvent.click(screen.getByRole("button", { name: "Continue" }));

		await waitFor(() => {
			const calls = mockCall.mock.calls.map((c: unknown[]) => c[0] as string);
			expect(calls).toContain("onboarding_set_backend");
		});
		const backendCall = mockCall.mock.calls.find(
			(c: unknown[]) => c[0] === "onboarding_set_backend",
		);
		expect(backendCall?.[1]).toEqual({ backend: "cloud" });

		const configCalls = mockCall.mock.calls.filter(
			(c: unknown[]) => c[0] === "set_config",
		);
		expect(configCalls.some((c) => c[1]?.cloud_openai_consent === true)).toBe(
			true,
		);
		expect(
			configCalls.some((c) => c[1]?.openai_api_key === "sk-test-123"),
		).toBe(true);

		// Nothing was downloaded.
		expect(
			mockCall.mock.calls.some((c: unknown[]) => c[0] === "download_model"),
		).toBe(false);
	});

	it("a normal wizard pass (Local default) never calls download_model", async () => {
		mockStartAtStep(4);
		render(<OnboardingPage onComplete={() => {}} />);

		// Continue through the Model step with the Local default.
		await waitFor(() => {
			expect(screen.getByTestId("onboarding-backend-local")).toBeTruthy();
		});
		fireEvent.click(screen.getByRole("button", { name: "Continue" }));

		await waitFor(() => {
			expect(screen.getByTestId("onboarding-consent-checkbox")).toBeTruthy();
		});
		expect(
			mockCall.mock.calls.some((c: unknown[]) => c[0] === "download_model"),
		).toBe(false);
	});

	it("the Done step no longer promises a background download and shows the backend summary", async () => {
		mockStartAtStep(5);
		render(<OnboardingPage onComplete={() => {}} />);

		// The heading appears twice (sr-only h1 + visible h2 — the
		// standard wizard pattern), so use getAllByText.
		await waitFor(() => {
			expect(screen.getAllByText("You're All Set!").length).toBeGreaterThan(0);
		});

		// The old background-download feedback element is gone.
		expect(screen.queryByTestId("onboarding-download-feedback")).toBeNull();
		// No "downloads in the background" promise anywhere on the step.
		expect(
			screen.queryByText(/will start downloading in the background/i),
		).toBeNull();
		// The summary includes the backend choice.
		expect(screen.getByText("Local model")).toBeTruthy();
		expect(screen.getByText("tiny")).toBeTruthy();
	});
});
