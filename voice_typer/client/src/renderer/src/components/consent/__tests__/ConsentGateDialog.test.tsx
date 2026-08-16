/**
 * Unit tests for the unified point-of-use consent dialog
 * (`ConsentGateDialog`).
 *
 * Contract under test:
 *   - Allow → persists the consent via the allowlisted `set_config`
 *     IPC (SEC-002), then runs the request's `onAllow` retry, then
 *     closes.
 *   - set_config failure → the dialog stays open, `onAllow` is NOT
 *     called (the UI never claims a grant that wasn't persisted), and
 *     an error snackbar fires.
 *   - Cancel → closes without granting anything.
 *   - "Open Settings" → deep-links to the exact consent row via the
 *     `consentField` navigate option.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module.
import {
	pythonMock,
	resetStableMocks,
	snackbarMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall, mockNavigate, showSnack: mockShowSnack } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useNavigation", () => ({
	useNavigation: () => ({ navigate: mockNavigate }),
}));
vi.mock("@/hooks/useSnackbar", () => snackbarMock());

vi.mock("@/i18n/i18n", () => ({
	useT: () => (key: string, params?: Record<string, string>) =>
		params ? `${key}:${JSON.stringify(params)}` : key,
}));

import {
	type ConsentGateRequest,
	useConsentGateStore,
} from "@/lib/consentGate";
import ConsentGateDialog from "../ConsentGateDialog";

describe("ConsentGateDialog — unified point-of-use consent", () => {
	beforeEach(() => {
		useConsentGateStore.setState({ request: null });
		resetStableMocks();
		mockCall.mockResolvedValue({});
	});

	afterEach(() => {
		useConsentGateStore.setState({ request: null });
	});

	function openDialog(overrides: Partial<ConsentGateRequest> = {}) {
		useConsentGateStore.getState().open({
			consentField: "cloud_groq_consent",
			bodyKey: "consentDialog.field.cloud_groq_consent",
			...overrides,
		});
	}

	it("renders nothing when no consent request is pending", () => {
		const { container } = render(<ConsentGateDialog />);
		expect(container).toBeEmptyDOMElement();
	});

	it("Allow persists the consent, runs the retry, and closes", async () => {
		const onAllow = vi.fn(async () => {});
		openDialog({ onAllow });
		render(<ConsentGateDialog />);

		await screen.findByRole("alertdialog");
		// Body is the exact field's plain-language description.
		expect(
			screen.getByText("consentDialog.field.cloud_groq_consent"),
		).toBeTruthy();

		await userEvent.click(
			screen.getByRole("button", { name: "consentDialog.allow" }),
		);

		expect(mockCall).toHaveBeenCalledWith("set_config", {
			cloud_groq_consent: true,
		});
		await waitFor(() => expect(onAllow).toHaveBeenCalledTimes(1));
		// The dialog closes only AFTER the retry ran.
		expect(useConsentGateStore.getState().request).toBeNull();
	});

	it("does NOT run the retry or close when set_config fails — error snackbar instead", async () => {
		const onAllow = vi.fn(async () => {});
		mockCall.mockRejectedValue(new Error("persist failed"));
		openDialog({ onAllow });
		render(<ConsentGateDialog />);

		await screen.findByRole("alertdialog");
		await userEvent.click(
			screen.getByRole("button", { name: "consentDialog.allow" }),
		);

		expect(onAllow).not.toHaveBeenCalled();
		// The dialog stays open — the grant did not persist.
		expect(useConsentGateStore.getState().request).not.toBeNull();
		expect(mockShowSnack).toHaveBeenCalledWith(
			"consentDialog.persistFailed",
			"error",
		);
	});

	it("Cancel closes without granting anything", async () => {
		const onAllow = vi.fn();
		openDialog({ onAllow });
		render(<ConsentGateDialog />);

		await screen.findByRole("alertdialog");
		await userEvent.click(
			screen.getByRole("button", { name: "consentDialog.cancel" }),
		);

		expect(mockCall).not.toHaveBeenCalled();
		expect(onAllow).not.toHaveBeenCalled();
		expect(useConsentGateStore.getState().request).toBeNull();
	});

	it("Open Settings deep-links to the exact consent row and closes", async () => {
		openDialog();
		render(<ConsentGateDialog />);

		await screen.findByRole("alertdialog");
		await userEvent.click(
			screen.getByRole("button", { name: "consentDialog.openSettings" }),
		);

		expect(mockNavigate).toHaveBeenCalledWith("settings", {
			consentField: "cloud_groq_consent",
		});
		expect(useConsentGateStore.getState().request).toBeNull();
	});

	it("surfaces a warning snackbar when the retry itself fails (consent still granted)", async () => {
		const onAllow = vi.fn(async () => {
			throw new Error("toggle failed");
		});
		openDialog({ onAllow });
		render(<ConsentGateDialog />);

		await screen.findByRole("alertdialog");
		await userEvent.click(
			screen.getByRole("button", { name: "consentDialog.allow" }),
		);

		await waitFor(() => expect(mockShowSnack).toHaveBeenCalled());
		// The consent WAS persisted — the dialog closed regardless.
		expect(useConsentGateStore.getState().request).toBeNull();
		expect(mockShowSnack).toHaveBeenCalledWith(
			"consentDialog.retryFailed",
			"warning",
		);
	});
});
