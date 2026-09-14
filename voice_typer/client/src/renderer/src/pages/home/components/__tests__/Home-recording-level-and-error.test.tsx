/**
 * Home mic-button error state (page-level wiring): the MicToggleButton
 * receives `error` when the store's recordingState is "error", otherwise
 * the normal idle treatment renders.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	navigationMock,
	pythonMock,
	resetStableMocks,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useAppStore } from "@/stores/appStore";

const eventHandlers: Record<string, (data: unknown) => void> = {};

vi.mock("@/hooks/usePython", () =>
	pythonMock({ captureEvents: eventHandlers }),
);
vi.mock("@/hooks/useNavigation", () => navigationMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());

// Stub heavy leaf components (kept as testids so Home's wiring stays
// observable without their internals).
vi.mock("@/components/dashboard/ActivityList", () => ({
	default: () => <div data-testid="activity-list" />,
}));
vi.mock("@/components/dashboard/StatCards", () => ({
	default: () => <div data-testid="stat-cards" />,
}));
vi.mock("@/components/dashboard/ShareStatsDialog", () => ({
	ShareStatsDialog: () => <div data-testid="share-stats-dialog" />,
}));
vi.mock("@/components/dashboard/StatsShareImage", () => ({
	StatsShareImage: () => <div data-testid="stats-share-image" />,
}));
vi.mock("@/components/common/LastUpdatedIndicator", () => ({
	LastUpdatedIndicator: () => <div data-testid="last-updated" />,
}));
vi.mock("@/components/hotkey/HotkeyChips", () => ({
	HotkeyChips: () => <span data-testid="hotkey-chips" />,
}));
vi.mock("@/hooks/useOfflinePackDownload", () => ({
	useOfflinePackDownload: () => ({ isReady: true }),
}));
vi.mock("@/hooks/useStatsShare", () => ({
	useStatsShare: () => ({
		imageRef: { current: null },
		downloadImage: vi.fn(),
		saveImageAs: vi.fn(),
		copyImageToClipboard: vi.fn(),
		revealInFolder: vi.fn(),
	}),
	canShareStats: () => true,
	computeShareStats: () => null,
}));

async function renderHome() {
	const { default: Home } = await import("@/pages/Home");
	return render(
		<TooltipProvider delayDuration={200}>
			<Home />
		</TooltipProvider>,
	);
}

beforeEach(() => {
	resetStableMocks();
	for (const key of Object.keys(eventHandlers)) {
		delete eventHandlers[key];
	}
	localStorage.clear();
	useAppStore.setState({
		recordingState: "idle",
		lastError: null,
		connectionStatus: "connected",
	});
	// Keep initial-load IPC calls pending so effects don't race.
	stableMocks.mockCall.mockImplementation(() => new Promise(() => {}));
});

afterEach(() => {
	cleanup();
	useAppStore.setState({ recordingState: "idle", lastError: null });
});

describe("Home mic-button error state wiring", () => {
	it("exposes aria-live=polite on the mic button when recordingState is error", async () => {
		await renderHome();
		act(() => {
			useAppStore.setState({ recordingState: "error", lastError: "boom" });
		});
		const btn = screen.getByTestId("mic-toggle-button");
		expect(btn.getAttribute("aria-live")).toBe("polite");
	});

	it("keeps the mic button in the normal idle state when idle", async () => {
		await renderHome();
		const btn = screen.getByTestId("mic-toggle-button");
		expect(btn.getAttribute("aria-live")).toBeNull();
	});
});
