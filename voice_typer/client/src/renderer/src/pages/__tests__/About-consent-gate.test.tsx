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

describe("About & Privacy page, pack update check (always-on, no consent gate)", () => {
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

	it("calls check_offline_pack_update and never opens a consent dialog", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "check_offline_pack_update") {
				return Promise.resolve({
					success: true,
					update_available: true,
					remote_version: "2.0.0",
					download_triggered: true,
				});
			}
			return Promise.resolve({});
		});

		await renderAbout();
		clickCheck();

		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith(
				"check_offline_pack_update",
				expect.anything(),
			);
		});
		expect(useConsentGateStore.getState().request).toBeNull();
	});

	it("never opens the consent dialog even if backend returns consent_required (legacy shape)", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "check_offline_pack_update") {
				return Promise.resolve({
					success: false,
					consent_required: true,
					remote_version: "2.0.0",
				});
			}
			return Promise.resolve({});
		});

		await renderAbout();
		clickCheck();

		await waitFor(() => {
			expect(
				(
					screen.getByRole("button", {
						name: "Check for Updates",
					}) as HTMLButtonElement
				).disabled,
			).toBe(false);
		});
		expect(useConsentGateStore.getState().request).toBeNull();
	});

	it("button re-enables after the check completes", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "check_offline_pack_update") {
				return Promise.resolve({
					success: true,
					update_available: false,
					local_version: "1.0.0",
				});
			}
			return Promise.resolve({});
		});

		await renderAbout();
		clickCheck();

		await waitFor(() => {
			expect(
				(
					screen.getByRole("button", {
						name: "Check for Updates",
					}) as HTMLButtonElement
				).disabled,
			).toBe(false);
		});
	});
});
