/**
 * About page — offline-pack consent gate (point-of-use contract).
 *
 * When `check_offline_pack_update` returns `{consent_required: true}`
 * (update found but the download refused because
 * `offline_pack_consent` is off), the page must open the SHARED
 * point-of-use consent dialog (`openConsentGate`) instead of showing
 * a persistent "enable in Settings" instruction:
 *
 *   • the dialog request carries the offline_pack_consent field +
 *     its consentDialog body key;
 *   • Allow persists the consent (persistence itself is covered by
 *     ConsentGateDialog.test.tsx) and re-runs the check, which then
 *     triggers the download;
 *   • Cancel leaves everything untouched — no download, no nag;
 *   • a successful check never opens the dialog.
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
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	nextThemesMock,
	pythonMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

import { useConsentGateStore } from "@/lib/consentGate";

/** Shape of the `check_offline_pack_update` response (mirrors About). */
function packResult(overrides: Record<string, unknown> = {}) {
	return {
		success: false,
		...overrides,
	};
}

describe("About & Privacy page — offline-pack consent gate (point-of-use)", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockCall.mockImplementation(() => Promise.resolve({}));
		useConsentGateStore.setState({ request: null });
	});

	afterEach(() => {
		cleanup();
		useConsentGateStore.setState({ request: null });
	});

	const renderAbout = async () => {
		const { default: AboutPage } = await import("@/pages/AboutAndPrivacy");
		render(<AboutPage />);
		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "About & Privacy" }),
			).toBeTruthy();
		});
	};

	const clickCheck = () =>
		fireEvent.click(screen.getByRole("button", { name: "Check for Updates" }));

	it("opens the shared consent dialog when the check is refused for missing consent", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "check_offline_pack_update") {
				return Promise.resolve(
					packResult({
						consent_required: true,
						remote_version: "2.0.0",
					}),
				);
			}
			return Promise.resolve({});
		});

		await renderAbout();
		clickCheck();

		await waitFor(() => {
			const req = useConsentGateStore.getState().request;
			expect(req).not.toBeNull();
			expect(req?.consentField).toBe("offline_pack_consent");
			expect(req?.bodyKey).toBe("consentDialog.field.offline_pack_consent");
		});

		// No persistent "go enable it in Settings" instruction — the
		// row stays informational ("update available").
		expect(screen.queryByText(/enable them in Settings/i)).toBeNull();
		expect(screen.getByText(/2\.0\.0/)).toBeTruthy();
	});

	it("re-runs the pack check after Allow (the retry that triggers the download)", async () => {
		let checkCount = 0;
		mockCall.mockImplementation((type: string) => {
			if (type === "check_offline_pack_update") {
				checkCount += 1;
				if (checkCount === 1) {
					return Promise.resolve(packResult({ consent_required: true }));
				}
				return Promise.resolve(
					packResult({
						success: true,
						update_available: true,
						remote_version: "2.0.0",
						download_triggered: true,
					}),
				);
			}
			return Promise.resolve({});
		});

		await renderAbout();
		clickCheck();

		await waitFor(() => {
			expect(useConsentGateStore.getState().request).not.toBeNull();
		});
		expect(checkCount).toBe(1);

		// Allow → (the dialog persisted the consent before this in the
		// real flow) → the blocked action is retried.
		const onAllow = useConsentGateStore.getState().request?.onAllow;
		expect(onAllow).toBeDefined();
		await act(async () => {
			await onAllow?.();
		});

		await waitFor(() => {
			expect(checkCount).toBe(2);
		});
		await waitFor(() => {
			// The retried check succeeded and triggered the download.
			expect(screen.getByText(/Downloading the update/i)).toBeTruthy();
		});
	});

	it("keeps everything untouched after Cancel — no retry, no download", async () => {
		let checkCount = 0;
		mockCall.mockImplementation((type: string) => {
			if (type === "check_offline_pack_update") {
				checkCount += 1;
				return Promise.resolve(packResult({ consent_required: true }));
			}
			return Promise.resolve({});
		});

		await renderAbout();
		clickCheck();

		await waitFor(() => {
			expect(useConsentGateStore.getState().request).not.toBeNull();
		});

		// Cancel = close without granting.
		useConsentGateStore.getState().close();

		await waitFor(() => {
			expect(screen.queryByText(/Checking…/)).toBeNull();
		});
		expect(checkCount).toBe(1);
		expect(mockCall).not.toHaveBeenCalledWith(
			"download_offline_pack",
			expect.anything(),
		);
	});

	it("never opens the consent dialog when the check succeeds without refusal", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "check_offline_pack_update") {
				return Promise.resolve(
					packResult({
						success: true,
						update_available: false,
						local_version: "1.0.0",
					}),
				);
			}
			return Promise.resolve({});
		});

		await renderAbout();
		clickCheck();

		await waitFor(() => {
			expect(screen.getByText(/Up to date/)).toBeTruthy();
		});
		expect(useConsentGateStore.getState().request).toBeNull();
	});
});
