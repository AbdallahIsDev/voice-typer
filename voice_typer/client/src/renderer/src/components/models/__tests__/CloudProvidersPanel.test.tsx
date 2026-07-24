/**
 * CloudProvidersPanel unit tests — BG-74 / BG-77.
 *
 * Coverage:
 *   1. BG-74: the test-result <span> exposes role=status + aria-live=polite
 *      so SR users hear the test-connection outcome as it arrives.
 *   2. BG-77: the "info" branch uses the canonical `text-(--text-muted)`
 *      Tailwind class (NOT the invalid `text-[(--text-muted)]` form).
 *   3. Three test-result color branches (success/failure/info) render the
 *      right text color class.
 *   4. Consent progressive disclosure: the consent card is hidden when no
 *      API key is set AND no consent has been granted; it appears when
 *      EITHER condition is true.
 *   5. Consent granted / not-granted status strings render appropriately.
 */
import { cleanup, render, screen } from "@testing-library/react";
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

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		Shield01Icon: make("Shield01Icon"),
		SparklesIcon: make("SparklesIcon"),
	};
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
		const resultSpan = screen.getByText(
			"Connection successful — API key is valid.",
		);
		expect(resultSpan).toHaveAttribute("role", "status");
		expect(resultSpan).toHaveAttribute("aria-live", "polite");
	});

	it("test-result span is absent when there is no testResult for the provider", () => {
		render(<CloudProvidersPanel {...baseProps} testResults={{}} />);
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
		const span = screen.getByText("testing…");
		// BG-77: `text-[(--text-muted)]` is invalid Tailwind v4 syntax.
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
		screen.getByTestId("consent-switch").click();
		expect(onConsentChange).toHaveBeenCalledWith("openai", true);
	});
});

describe("CloudProvidersPanel — Save / Test buttons", () => {
	afterEach(() => cleanup());

	it("Save Key button invokes onSaveApiKey with the provider key", () => {
		// Provider label for "openai" is "OpenAI Whisper API" — so the aria-label
		// resolves to "Save OpenAI Whisper API API key".
		const onSaveApiKey = vi.fn();
		render(<CloudProvidersPanel {...baseProps} onSaveApiKey={onSaveApiKey} />);
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
		screen
			.getByRole("button", { name: /Test OpenAI Whisper API connection/i })
			.click();
		expect(onTestConnection).toHaveBeenCalledWith("openai");
	});
});
