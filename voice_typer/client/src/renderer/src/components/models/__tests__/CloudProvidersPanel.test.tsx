/**
 * CloudProvidersPanel unit tests —  /
 *
 * (UI/UX overhaul 2026-08-20): the panel was rebuilt to match the
 * Local Models tab's collapsible-group pattern:
 *   • each provider is a collapsible accordion group header;
 *   • expanding a group reveals the API model row + a "Configure"
 *     action that reveals the API-key form (input + Save Key + Test
 *     Connection + consent Switch).
 * Tests that exercise the form therefore expand the group AND click
 * Configure first (`openApiKeyForm` helper).
 *
 * Coverage:
 *   1. : the test-result <span> exposes role=status + aria-live=polite
 *      so SR users hear the test-connection outcome as it arrives.
 *   2. : the "info" branch uses the canonical `text-(--text-muted)`
 *      Tailwind class (NOT the invalid `text-[(--text-muted)]` form).
 *   3. Three test-result color branches (success/failure/info) render the
 *      right text color class.
 *   4. Consent progressive disclosure: the consent card is hidden when no
 *      API key is set AND no consent has been granted; it appears when
 *      EITHER condition is true.
 *   5. Consent granted / not-granted status strings render appropriately.
 *   6. : the Save Key button is disabled when the input is empty.
 *   7. : the Test Connection button shows a spinner + is disabled
 *      while a test is in flight; stale results are cleared via
 *      onClearTestResult when the API-key Input changes.
 *   8. (overhaul point 11): the provider group is collapsible and the
 *      API-key form is hidden until the Configure action is clicked.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CloudProvidersPanel } from "@/components/models/CloudProvidersPanel";
import type { ApiTestResult } from "@/hooks/useModelLifecycle";
import type { CloudProvider } from "@/lib/utils/models";
import type { VoiceTyperConfig } from "@/types/config";

// Mock the HugeiconsIcon wrapper so the test doesn't depend on the SVG
// renderer.
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

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

// Stub KeyringStatusBadge so we don't have to construct the keyring state.
vi.mock("@/components/common/KeyringStatusBadge", () => ({
	KeyringStatusBadge: () => <span data-testid="keyring-badge" />,
}));

// Stub Input + Switch to keep the markup lean.
vi.mock("@/components/ui/input", () => ({
	Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => (
		<input data-testid="api-key-input" {...props} />
	),
}));

vi.mock("@/components/ui/switch", () => ({
	Switch: (props: {
		checked: boolean;
		onCheckedChange: (granted: boolean) => void;
		"aria-label"?: string;
	}) => (
		<button
			type="button"
			data-testid="consent-switch"
			role="switch"
			aria-checked={props.checked}
			aria-label={props["aria-label"]}
			onClick={() => props.onCheckedChange(!props.checked)}
		/>
	),
}));

const noop = vi.fn();

const openaiProvider: CloudProvider = {
	key: "openai",
	url: "https://api.openai.com/v1/audio/transcriptions",
	model: "whisper-1",
};

const baseConfig = {
	cloud_openai_consent: false,
	cloud_groq_consent: false,
	cloud_deepgram_consent: false,
	keyring_status: undefined,
} as unknown as VoiceTyperConfig;

const baseProps = {
	config: baseConfig,
	cloudProviders: [openaiProvider],
	apiKeys: {},
	testResults: {},
	onApiKeyChange: noop,
	onSaveApiKey: noop,
	onTestConnection: noop,
	onConsentChange: noop,
};

// ── Helpers ───────────────────────────────────────────────────────────
//
// The API-key form lives behind the provider group (accordion trigger)
// + the "Configure" action (overhaul point 11). These helpers expand
// the group and reveal the form before the test interacts with it.

function expandProviderGroup(providerLabel: string) {
	fireEvent.click(screen.getByRole("button", { name: providerLabel }));
}

function openApiKeyForm(providerLabel = "OpenAI Whisper API") {
	expandProviderGroup(providerLabel);
	fireEvent.click(
		screen.getByRole("button", {
			name: new RegExp(`Configure ${providerLabel}`, "i"),
		}),
	);
}

describe("CloudProvidersPanel — provider brand logos + collapsible groups", () => {
	afterEach(() => cleanup());

	it("renders the brand logo for openai and deepgram; groq keeps the shield fallback", () => {
		const groqProvider: CloudProvider = {
			key: "groq",
			url: "https://api.groq.com/openai/v1/audio/transcriptions",
			model: "whisper-large-v3",
		};
		const deepgramProvider: CloudProvider = {
			key: "deepgram",
			url: "https://api.deepgram.com/v1/listen",
			model: "nova-2",
		};
		const { container } = render(
			<CloudProvidersPanel
				{...baseProps}
				cloudProviders={[openaiProvider, groqProvider, deepgramProvider]}
			/>,
		);

		// openai + deepgram render an <img> brand logo in their group
		// headers; groq has none.
		const imgs = container.querySelectorAll("img");
		expect(imgs).toHaveLength(2);
		const srcs = Array.from(imgs).map((i) => i.getAttribute("src") ?? "");
		expect(srcs.some((s) => s.includes("OpenAI%20icon"))).toBe(true);
		expect(srcs.some((s) => s.includes("Deepgram%20icon"))).toBe(true);

		// groq keeps the generic shield glyph in its group header.
		const shields = screen
			.getAllByTestId("hugeicon")
			.filter((el) => el.getAttribute("data-name") === "Shield01Icon");
		expect(shields).toHaveLength(1);
	});

	it("renders each provider as a collapsible group with a Configure action (not an always-open card)", () => {
		render(<CloudProvidersPanel {...baseProps} />);

		// Group header exists for the provider.
		expect(
			screen.getByRole("button", { name: "OpenAI Whisper API" }),
		).toBeInTheDocument();

		// The API-key form is NOT visible until Configure is clicked
		// (point 11 — no permanently-visible API-key card).
		expect(screen.queryByTestId("api-key-input")).toBeNull();
		expect(
			screen.queryByRole("button", {
				name: /Save OpenAI Whisper API API key/i,
			}),
		).toBeNull();

		// Expanding the group reveals the model row + Configure action.
		expandProviderGroup("OpenAI Whisper API");
		expect(
			screen.getByRole("button", {
				name: /Configure OpenAI Whisper API/i,
			}),
		).toBeInTheDocument();
		// The API model name renders as a display-formatted heading.
		expect(
			screen.getByRole("heading", { name: "Whisper 1" }),
		).toBeInTheDocument();

		// Configure reveals the form; the button flips to "Hide".
		fireEvent.click(
			screen.getByRole("button", { name: /Configure OpenAI Whisper API/i }),
		);
		expect(screen.getByTestId("api-key-input")).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /Hide OpenAI Whisper API setup/i }),
		).toBeInTheDocument();
	});
});

describe("CloudProvidersPanel — BG-74 (test-result span is a live region)", () => {
	afterEach(() => cleanup());

	it("test-result span exposes role=status + aria-live=polite (so SR users hear the outcome)", () => {
		const testResult: ApiTestResult = {
			message: "Connection successful — API key is valid.",
			status: "success",
		};
		render(
			<CloudProvidersPanel
				{...baseProps}
				testResults={{ openai: testResult }}
			/>,
		);
		openApiKeyForm();
		const resultSpan = screen.getByText(
			"Connection successful — API key is valid.",
		);
		// The result renders as an <output> element, whose IMPLICIT ARIA
		// role is status (so SR users hear the outcome). Assert the
		// computed role via toHaveRole rather than the literal role
		// attribute, which <output> doesn't carry.
		expect(resultSpan).toHaveRole("status");
		expect(resultSpan).toHaveAttribute("aria-live", "polite");
	});

	it("test-result span is absent when there is no testResult for the provider", () => {
		render(<CloudProvidersPanel {...baseProps} testResults={{}} />);
		openApiKeyForm();
		expect(screen.queryByRole("status")).toBeNull();
	});
});

describe("CloudProvidersPanel — three test-result color branches (BG-77 invalid syntax fixed)", () => {
	afterEach(() => cleanup());

	it("success branch uses text-primary", () => {
		const testResult: ApiTestResult = {
			message: "ok",
			status: "success",
		};
		render(
			<CloudProvidersPanel
				{...baseProps}
				testResults={{ openai: testResult }}
			/>,
		);
		openApiKeyForm();
		const span = screen.getByText("ok");
		expect(span.className).toContain("text-primary");
		expect(span.className).not.toContain("text-[(--text-muted)]");
	});

	it("failure branch uses text-destructive", () => {
		const testResult: ApiTestResult = {
			message: "bad key",
			status: "failure",
		};
		render(
			<CloudProvidersPanel
				{...baseProps}
				testResults={{ openai: testResult }}
			/>,
		);
		openApiKeyForm();
		const span = screen.getByText("bad key");
		expect(span.className).toContain("text-destructive");
	});

	it("info branch uses the canonical text-(--text-muted) class (NOT the invalid text-[(--text-muted)] form)", () => {
		const testResult: ApiTestResult = {
			message: "testing…",
			status: "info",
		};
		render(
			<CloudProvidersPanel
				{...baseProps}
				testResults={{ openai: testResult }}
			/>,
		);
		openApiKeyForm();
		const span = screen.getByText("testing…");
		//`text-[(--text-muted)]` is invalid Tailwind v4 syntax.
		// The canonical form is `text-(--text-muted)` — matches every other
		// call site in the codebase.
		expect(span.className).toContain("text-(--text-muted)");
		expect(span.className).not.toContain("text-[(--text-muted)]");
	});
});

describe("CloudProvidersPanel — consent progressive disclosure", () => {
	afterEach(() => cleanup());

	it("hides the consent card when no API key is set AND consent is not granted", () => {
		render(
			<CloudProvidersPanel
				{...baseProps}
				apiKeys={{}}
				config={
					{ ...baseConfig, cloud_openai_consent: false } as VoiceTyperConfig
				}
			/>,
		);
		openApiKeyForm();
		expect(screen.queryByTestId("consent-switch")).toBeNull();
	});

	it("shows the consent card when an API key is present (even if consent not granted)", () => {
		render(
			<CloudProvidersPanel
				{...baseProps}
				apiKeys={{ openai: "sk-test" }}
				config={
					{ ...baseConfig, cloud_openai_consent: false } as VoiceTyperConfig
				}
			/>,
		);
		openApiKeyForm();
		expect(screen.getByTestId("consent-switch")).toBeInTheDocument();
		expect(screen.getByText(/Consent not granted/i)).toBeInTheDocument();
	});

	it("shows the consent card when consent is already granted (even without an API key)", () => {
		render(
			<CloudProvidersPanel
				{...baseProps}
				apiKeys={{}}
				config={
					{ ...baseConfig, cloud_openai_consent: true } as VoiceTyperConfig
				}
			/>,
		);
		openApiKeyForm();
		expect(screen.getByTestId("consent-switch")).toBeInTheDocument();
		expect(screen.getByText(/Consent granted/i)).toBeInTheDocument();
	});

	it("toggling the consent switch invokes onConsentChange with the new value", () => {
		const onConsentChange = vi.fn();
		render(
			<CloudProvidersPanel
				{...baseProps}
				apiKeys={{ openai: "sk-test" }}
				config={
					{ ...baseConfig, cloud_openai_consent: false } as VoiceTyperConfig
				}
				onConsentChange={onConsentChange}
			/>,
		);
		openApiKeyForm();
		screen.getByTestId("consent-switch").click();
		expect(onConsentChange).toHaveBeenCalledWith("openai", true);
	});
});

describe("CloudProvidersPanel — Save / Test buttons", () => {
	afterEach(() => cleanup());

	it("Save Key button invokes onSaveApiKey with the provider key", () => {
		// Provider label for "openai" is "OpenAI Whisper API" — so the aria-label
		// resolves to "Save OpenAI Whisper API API key".
		//the Save Key button is disabled when the input is empty,
		// so the test must pass a non-empty key to click it.
		const onSaveApiKey = vi.fn();
		render(
			<CloudProvidersPanel
				{...baseProps}
				apiKeys={{ openai: "sk-test" }}
				onSaveApiKey={onSaveApiKey}
			/>,
		);
		openApiKeyForm();
		screen
			.getByRole("button", { name: /Save OpenAI Whisper API API key/i })
			.click();
		expect(onSaveApiKey).toHaveBeenCalledWith("openai");
	});

	it("Test Connection button invokes onTestConnection with the provider key", () => {
		const onTestConnection = vi.fn();
		render(
			<CloudProvidersPanel
				{...baseProps}
				onTestConnection={onTestConnection}
			/>,
		);
		openApiKeyForm();
		screen
			.getByRole("button", { name: /Test OpenAI Whisper API connection/i })
			.click();
		expect(onTestConnection).toHaveBeenCalledWith("openai");
	});
});

// ─────────────────────────────────────────────────────────────────────
//the "Save Key" button is disabled when the input is empty
// (prevents silently clobbering a stored secret with the empty string
// that `safeApiKey` substitutes for the `<redacted>` sentinel on
// every config fetch).
// ─────────────────────────────────────────────────────────────────────
describe("CloudProvidersPanel — ZU-6 (Save Key button disabled guard)", () => {
	afterEach(() => cleanup());

	it("disables the Save Key button when the API key input is empty", () => {
		render(<CloudProvidersPanel {...baseProps} apiKeys={{ openai: "" }} />);
		openApiKeyForm();
		const saveBtn = screen.getByRole("button", {
			name: /Save OpenAI Whisper API API key/i,
		});
		expect(saveBtn).toBeDisabled();
	});

	it("enables the Save Key button when the API key input has a non-whitespace value", () => {
		render(
			<CloudProvidersPanel
				{...baseProps}
				apiKeys={{ openai: "sk-test-key" }}
			/>,
		);
		openApiKeyForm();
		const saveBtn = screen.getByRole("button", {
			name: /Save OpenAI Whisper API API key/i,
		});
		expect(saveBtn).not.toBeDisabled();
	});

	it("disables the Save Key button when the input is only whitespace", () => {
		render(<CloudProvidersPanel {...baseProps} apiKeys={{ openai: "   " }} />);
		openApiKeyForm();
		const saveBtn = screen.getByRole("button", {
			name: /Save OpenAI Whisper API API key/i,
		});
		expect(saveBtn).toBeDisabled();
	});
});

// ─────────────────────────────────────────────────────────────────────
//the "Test Connection" button shows a spinner + is disabled
// while a test is in flight (`testResult?.status === "pending"`).
// Stale results are cleared via `onClearTestResult` whenever the
// API-key Input changes.
// ─────────────────────────────────────────────────────────────────────
describe("CloudProvidersPanel — ZU-23 (Test Connection pending state + clear-on-key-change)", () => {
	afterEach(() => cleanup());

	it("disables the Test Connection button + sets aria-busy when testResult.status is 'pending'", () => {
		render(
			<CloudProvidersPanel
				{...baseProps}
				apiKeys={{ openai: "sk-test" }}
				testResults={{
					openai: { message: "Testing…", status: "pending" },
				}}
			/>,
		);
		openApiKeyForm();
		const testBtn = screen.getByRole("button", {
			name: /Test OpenAI Whisper API connection/i,
		});
		expect(testBtn).toBeDisabled();
		expect(testBtn).toHaveAttribute("aria-busy", "true");
	});

	it("renders the spinning Loading03Icon (NOT SparklesIcon) when testResult.status is 'pending'", () => {
		render(
			<CloudProvidersPanel
				{...baseProps}
				apiKeys={{ openai: "sk-test" }}
				testResults={{
					openai: { message: "Testing…", status: "pending" },
				}}
			/>,
		);
		openApiKeyForm();
		const icons = screen.getAllByTestId("hugeicon");
		const iconNames = icons.map((el) => el.getAttribute("data-name"));
		expect(iconNames).toContain("Loading03Icon");
		expect(iconNames).not.toContain("SparklesIcon");
	});

	it("enables the Test Connection button + uses SparklesIcon when no test is in flight", () => {
		render(
			<CloudProvidersPanel {...baseProps} apiKeys={{ openai: "sk-test" }} />,
		);
		openApiKeyForm();
		const testBtn = screen.getByRole("button", {
			name: /Test OpenAI Whisper API connection/i,
		});
		expect(testBtn).not.toBeDisabled();
		expect(testBtn).toHaveAttribute("aria-busy", "false");
		const icons = screen.getAllByTestId("hugeicon");
		const iconNames = icons.map((el) => el.getAttribute("data-name"));
		expect(iconNames).toContain("SparklesIcon");
	});

	it("clears the stale test result via onClearTestResult when the API key Input changes", () => {
		const onApiKeyChange = vi.fn();
		const onClearTestResult = vi.fn();
		render(
			<CloudProvidersPanel
				{...baseProps}
				apiKeys={{ openai: "sk-test" }}
				testResults={{
					openai: {
						message: "Connection successful — API key is valid.",
						status: "success",
					},
				}}
				onApiKeyChange={onApiKeyChange}
				onClearTestResult={onClearTestResult}
			/>,
		);
		openApiKeyForm();
		const input = screen.getByTestId("api-key-input");
		fireEvent.change(input, { target: { value: "sk-new-key" } });
		expect(onApiKeyChange).toHaveBeenCalledWith("openai", "sk-new-key");
		expect(onClearTestResult).toHaveBeenCalledWith("openai");
	});
});
