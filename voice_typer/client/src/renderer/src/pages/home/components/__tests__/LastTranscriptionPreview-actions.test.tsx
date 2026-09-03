/**
 * Tests for the show-more/show-less toggle, the Copy action, and the
 * Discard action on the Home last-transcription preview
 * (pages/home/components/LastTranscriptionPreview.tsx).
 *
 * Pins:
 *  - Long transcriptions (text.length > threshold) clamp to two lines
 *    with an expand toggle (aria-expanded reflects state); short texts
 *    never render the toggle.
 *  - Copy always writes the FULL text to the clipboard (even while the
 *    collapsed display is active) and toasts via the reused history
 *    copy keys; a clipboard failure toasts the shared failure key.
 *  - Discard renders only when onDiscard is provided and invokes it.
 */
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/__tests__/helpers/render";
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";
import { LastTranscriptionPreview } from "@/pages/home/components/LastTranscriptionPreview";

const { toastSuccess, toastError } = stableMocks;

vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => {
		const catalog: Record<string, string> = {
			"home.showMore": "Show more",
			"home.showLess": "Show less",
			"home.copy": "Copy",
			"home.copyAria": "Copy transcription to clipboard",
			"home.discard": "Discard",
			"home.discardAria": "Discard this transcription from the preview",
			"home.undo": "Undo",
			"home.undoAria": "Undo last transcription",
			"home.repaste": "Re-paste",
			"home.repasteAria": "Re-paste last transcription",
			"history.copiedToClipboard": "Copied to clipboard",
			"activityList.failedToCopy": "Failed to copy",
		};
		return catalog[key] ?? key;
	},
}));

beforeEach(() => {
	stableMocks.toastSuccess.mockReset();
	stableMocks.toastError.mockReset();
});

afterEach(() => {
	cleanup();
});

function writeTextImpl() {
	const writeText = vi.fn<(text: string) => Promise<void>>();
	Object.assign(navigator, { clipboard: { writeText } });
	return writeText;
}

describe("LastTranscriptionPreview show-more toggle", () => {
	const longText = `word `.repeat(60);

	it("does not render the toggle for short text", () => {
		renderWithProviders(
			<LastTranscriptionPreview
				text="short text"
				onUndo={() => {}}
				onRepaste={() => {}}
			/>,
		);
		expect(screen.queryByTestId("last-transcription-show-toggle")).toBeNull();
	});

	it("renders the toggle clamped (aria-expanded=false) for long text", () => {
		renderWithProviders(
			<LastTranscriptionPreview
				text={longText}
				onUndo={() => {}}
				onRepaste={() => {}}
			/>,
		);
		const toggle = screen.getByTestId("last-transcription-show-toggle");
		expect(toggle.getAttribute("aria-expanded")).toBe("false");
		expect(toggle.textContent).toBe("Show more");
	});

	it("expands (aria-expanded=true, Show less) and collapses on click", () => {
		renderWithProviders(
			<LastTranscriptionPreview
				text={longText}
				onUndo={() => {}}
				onRepaste={() => {}}
			/>,
		);
		const toggle = screen.getByTestId("last-transcription-show-toggle");
		fireEvent.click(toggle);
		expect(toggle.getAttribute("aria-expanded")).toBe("true");
		expect(toggle.textContent).toBe("Show less");
		fireEvent.click(toggle);
		expect(toggle.getAttribute("aria-expanded")).toBe("false");
		expect(toggle.textContent).toBe("Show more");
	});
});

describe("LastTranscriptionPreview Copy action", () => {
	it("copies the FULL text to the clipboard and toasts success", async () => {
		const longText = "A".repeat(300);
		const writeText = writeTextImpl();
		renderWithProviders(
			<LastTranscriptionPreview
				text={longText}
				onUndo={() => {}}
				onRepaste={() => {}}
			/>,
		);
		fireEvent.click(screen.getByTestId("last-transcription-copy"));
		await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
		// Full text — never the clamped preview slice.
		expect(writeText.mock.calls[0]?.[0]).toBe(longText);
		expect(toastSuccess).toHaveBeenCalledTimes(1);
	});

	it("toasts the shared failure key when the clipboard write rejects", async () => {
		const writeText = writeTextImpl();
		writeText.mockRejectedValueOnce(new Error("denied"));
		renderWithProviders(
			<LastTranscriptionPreview
				text="some text"
				onUndo={() => {}}
				onRepaste={() => {}}
			/>,
		);
		fireEvent.click(screen.getByTestId("last-transcription-copy"));
		await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
		expect(toastSuccess).not.toHaveBeenCalled();
	});
});

describe("LastTranscriptionPreview Discard action", () => {
	it("renders only when onDiscard is provided and invokes it on click", () => {
		const onDiscard = vi.fn();
		const { rerender } = renderWithProviders(
			<LastTranscriptionPreview
				text="some text"
				onUndo={() => {}}
				onRepaste={() => {}}
			/>,
		);
		expect(screen.queryByTestId("last-transcription-discard")).toBeNull();

		rerender(
			<LastTranscriptionPreview
				text="some text"
				onUndo={() => {}}
				onRepaste={() => {}}
				onDiscard={onDiscard}
			/>,
		);
		fireEvent.click(screen.getByTestId("last-transcription-discard"));
		expect(onDiscard).toHaveBeenCalledTimes(1);
	});
});
