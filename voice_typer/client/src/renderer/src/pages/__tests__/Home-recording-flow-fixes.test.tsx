/**
 * Regression tests for the Home recording-flow fixes ().
 *
 * Each describe block pins one finding so a future regression points at
 * the exact contract that broke:
 *
 *   - The transcription preview text is wrapped in an
 *     `<output aria-live="polite">` region so screen readers announce
 *     freshly arrived transcriptions.
 *   - The LastTranscriptionPreview container carries
 *     `aria-live="polite"` so the card remains accessible when rendered
 *     outside Home's `<output>` wrapper.
 *   - The MicToggleButton exposes `aria-pressed` so screen readers
 *     announce the toggle state ("pressed" / "not pressed") rather than
 *     just the label.
 *   - When recordingState is "error", the RecordingErrorCard retry
 *     button label is `t("home.startDictation")` (matching the actual
 *     action of `handleToggle`) rather than the misleading "Retry".
 *   - A live MM:SS timer is rendered next to the RecordingStatusPill
 *     while recording.
 *   - LAST_TEXT_AUTO_CLEAR_MS is bumped to 30_000 in constants.ts.
 *   - No task-ID / session-prefix comments remain in the owned files.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockCall, mockPythonEvent } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

vi.mock("@/hooks/useNavigation", () => ({
	useNavigation: () => ({ navigate: vi.fn() }),
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

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		ClipboardPasteIcon: make("ClipboardPasteIcon"),
		Copy01Icon: make("Copy01Icon"),
		Delete01Icon: make("Delete01Icon"),
		Mic02Icon: make("Mic02Icon"),
		RefreshIcon: make("RefreshIcon"),
		Share08Icon: make("Share08Icon"),
		StarIcon: make("StarIcon"),
		StopIcon: make("StopIcon"),
		TextIcon: make("TextIcon"),
		Tick02Icon: make("Tick02Icon"),
		Time02Icon: make("Time02Icon"),
		Undo02Icon: make("Undo02Icon"),
	};
});

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
		warning: vi.fn(),
		info: vi.fn(),
		dismiss: vi.fn(),
	},
	Toaster: () => null,
}));

// File-level cleanup so renders from one `it` block don't leak into the
// next (each describe block below doesn't have to repeat the boilerplate).
afterEach(() => {
	cleanup();
});

// ── shared helpers ──────────────────────────────────────────────────

async function renderHome() {
	const { default: Home } = await import("@/pages/Home");
	return render(<Home />);
}

//transcription text wrapped in <output aria-live="polite"> ──

describe("QV-9: lastText is rendered inside an <output aria-live='polite'> region", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		vi.resetModules();
		// Keep the backend calls pending so initial-load effects don't
		// race with the assertion.
		mockCall.mockImplementation(() => new Promise(() => {}));
	});

	afterEach(() => {
		cleanup();
	});

	it("uses an <output> element (semantic live region) with aria-live='polite'", async () => {
		await renderHome();

		// Find the transcription_final handler captured by the mock and
		// invoke it with a fake result so lastText populates.
		const transcriptionFinalCall = mockPythonEvent.mock.calls.find(
			(c) => c[0] === "transcription_final",
		);
		expect(transcriptionFinalCall).toBeDefined();
		const handler = transcriptionFinalCall?.[1] as (data: {
			text?: string;
		}) => unknown;

		await act(async () => {
			handler({ text: "hello world" });
		});

		// The transcription text must be rendered.
		const textEl = screen.getByText("hello world");
		// There must be at least one ancestor with aria-live="polite".
		const liveRegion = textEl.closest('[aria-live="polite"]');
		expect(liveRegion).not.toBeNull();
		expect(liveRegion?.getAttribute("aria-live")).toBe("polite");
		// The Home page wraps the preview in an <output> element (the
		// semantic HTML5 live region). Walk up to confirm an <output>
		// ancestor exists with aria-live="polite" — the closest
		// aria-live ancestor may be the inner LastTranscriptionPreview
		//container (also aria-live="polite" per ), but the outer
		// <output> must still be present.
		let node: Element | null = textEl.parentElement;
		let outputAncestor: Element | null = null;
		while (node) {
			if (
				node.tagName.toLowerCase() === "output" &&
				node.getAttribute("aria-live") === "polite"
			) {
				outputAncestor = node;
				break;
			}
			node = node.parentElement;
		}
		expect(outputAncestor).not.toBeNull();
	});
});

//LastTranscriptionPreview container exposes aria-live ──

describe("QV-96: LastTranscriptionPreview container exposes aria-live='polite'", () => {
	it("renders the outer container with aria-live='polite'", async () => {
		const { LastTranscriptionPreview } = await import(
			"@/pages/home/components/LastTranscriptionPreview"
		);
		const { container } = render(
			<LastTranscriptionPreview
				text="sample text"
				onUndo={() => {}}
				onRepaste={() => {}}
			/>,
		);
		// The outermost element must carry aria-live="polite".
		const outer = container.firstElementChild;
		expect(outer).not.toBeNull();
		expect(outer?.getAttribute("aria-live")).toBe("polite");
	});
});

//MicToggleButton exposes aria-pressed ──

describe("QV-16: MicToggleButton exposes aria-pressed matching isRecording", () => {
	it("sets aria-pressed='false' when not recording", async () => {
		const { MicToggleButton } = await import(
			"@/pages/home/components/MicToggleButton"
		);
		render(
			<MicToggleButton
				isRecording={false}
				toggling={false}
				disabled={false}
				onClick={() => {}}
				label="Start dictation"
			/>,
		);
		const btn = screen.getByRole("button", { name: "Start dictation" });
		expect(btn.getAttribute("aria-pressed")).toBe("false");
	});

	it("sets aria-pressed='true' when recording", async () => {
		const { MicToggleButton } = await import(
			"@/pages/home/components/MicToggleButton"
		);
		render(
			<MicToggleButton
				isRecording={true}
				toggling={false}
				disabled={false}
				onClick={() => {}}
				label="Stop dictation"
			/>,
		);
		const btn = screen.getByRole("button", { name: "Stop dictation" });
		expect(btn.getAttribute("aria-pressed")).toBe("true");
	});
});

//RecordingErrorCard retry label matches the action ──
//
// The shared `<Button>` from `@/components/ui/button` is mocked here so
// the RecordingErrorCard can be tested in isolation. The shared Button
// has a dev-mode useEffect (owned by another sub-agent) that is
// orthogonal to the retry-label contract under test.

vi.mock("@/components/ui/button", () => ({
	Button: ({
		children,
		onClick,
		disabled,
		...rest
	}: {
		children?: React.ReactNode;
		onClick?: () => void;
		disabled?: boolean;
		[key: string]: unknown;
	}) => (
		<button type="button" onClick={onClick} disabled={disabled} {...rest}>
			{children}
		</button>
	),
}));

describe("QV-11: RecordingErrorCard retry button label defaults to t('home.retry') and can be overridden", () => {
	it("renders the default 'Retry' label when retryLabel is not provided", async () => {
		const { RecordingErrorCard } = await import(
			"@/pages/home/components/RecordingErrorCard"
		);
		render(
			<RecordingErrorCard message="boom" onRetry={() => {}} retrying={false} />,
		);
		// en.json value for home.retry is "Retry".
		expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
	});

	it("renders the provided retryLabel so the action matches the label", async () => {
		const { RecordingErrorCard } = await import(
			"@/pages/home/components/RecordingErrorCard"
		);
		render(
			<RecordingErrorCard
				message="boom"
				onRetry={() => {}}
				retrying={false}
				retryLabel="Start dictation"
			/>,
		);
		// The override must replace the default label.
		expect(
			screen.getByRole("button", { name: "Start dictation" }),
		).toBeTruthy();
		// The default label must NOT appear when the override is set.
		expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
	});
});

//(a): live MM:SS timer renders while recording ──

describe("QV-49(a): Home renders a live MM:SS timer while recording", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		vi.resetModules();
		mockCall.mockImplementation(() => new Promise(() => {}));
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the timer with aria-label when recordingState is 'recording'", async () => {
		// Pre-populate the appStore with recordingState="recording" so the
		// Home page mounts already in the recording state.
		const { useAppStore } = await import("@/stores/appStore");
		useAppStore.setState({ recordingState: "recording" });

		await renderHome();

		// The timer's accessible name follows the timerAria template
		// "Recording duration: {duration}". Initially "00:00".
		const timer = screen.getByLabelText(/Recording duration:/i);
		expect(timer).toBeTruthy();
		// The visible text must be in MM:SS format.
		expect(timer.textContent ?? "").toMatch(/^\d{2}:\d{2}$/);
	});

	it("does NOT render the timer when recordingState is 'idle'", async () => {
		const { useAppStore } = await import("@/stores/appStore");
		useAppStore.setState({ recordingState: "idle" });

		await renderHome();

		// No element should expose the timer aria-label.
		expect(screen.queryByLabelText(/Recording duration:/i)).toBeNull();
	});
});

//(b): LAST_TEXT_AUTO_CLEAR_MS bumped to 30_000 ──

describe("QV-49(b): LAST_TEXT_AUTO_CLEAR_MS is 30000ms", () => {
	it("exports the bumped value (30s) so the preview stays visible long enough to act on", async () => {
		const mod = await import("@/pages/home/lib/constants");
		expect(mod.LAST_TEXT_AUTO_CLEAR_MS).toBe(30_000);
	});
});

//no task-ID / session-prefix comments in owned files ──

describe("QV-25: owned files contain no task-ID / session-prefix comments", () => {
	const OWNED_FILES = [
		"src/renderer/src/pages/Home.tsx",
		"src/renderer/src/pages/home/components/RecordingStatusPill.tsx",
		"src/renderer/src/pages/home/components/RecordingErrorCard.tsx",
		"src/renderer/src/pages/home/components/LastTranscriptionPreview.tsx",
		"src/renderer/src/pages/home/components/MicToggleButton.tsx",
		"src/renderer/src/pages/home/lib/constants.ts",
		"src/renderer/src/pages/home/lib/status.ts",
		"src/renderer/src/pages/home/lib/cache.ts",
		"src/renderer/src/pages/home/hooks/useFirstRecordingCelebration.ts",
	];

	// Known task-ID / session-prefix tokens that MUST NOT appear in source
	// comments (per CONSTRAINTS.md C-STYLE-1). The list is intentionally
	// non-exhaustive — it covers the prefixes that were previously
	// scattered across these files.
	const FORBIDDEN_TOKENS = [
		"EC-FIX-12",
		"EC-FIX-14",
		"EC-12",
		"PVT-053",
		"BACKLOG-004",
		"FIX-15",
		"CR-14",
		"UX-016",
		"UX-025",
		"UX-009",
		"UX-9",
		"NEW-TS-006",
		"F11-FIX",
		"R7-F13",
	];

	it.each(
		OWNED_FILES,
	)("%s contains no forbidden task-ID tokens", async (path) => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(path, "utf8");
		for (const token of FORBIDDEN_TOKENS) {
			expect(src).not.toContain(token);
		}
	});
});
