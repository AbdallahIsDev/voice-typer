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
 *   - When recordingState is "error", the error is surfaced as red
 *     text inside the single dynamic status line below the mic button
 *     (not a separate error card above it).
 *   - The single dynamic status line swaps between the default
 *     "Press <hotkey> or click to dictate" hint, the
 *     "Preparing offline engine…" message, and red error text.
 *   - A live MM:SS recording timer is rendered above the mic button
 *     while recording.
 *   - LAST_TEXT_AUTO_CLEAR_MS is bumped to 30_000 in constants.ts.
 *   - No task-ID / session-prefix comments remain in the owned files.
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
// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module. The sonner toast
// fns are singletons (toast.warning === stableMocks.toastWarning), so
// the assertions below observe the page's calls via the destructured
// names instead of a static `import { toast } from "sonner"` (a static
// sonner import would be hoisted ABOVE the stableMocks import by the
// import sorter and break the mock-factory binding order).
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
import { useConsentGateStore } from "@/lib/consentGate";

const { mockCall, mockPythonEvent, mockNavigate, toastWarning } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useNavigation", () => navigationMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());

// File-level cleanup so renders from one `it` block don't leak into the
// next (each describe block below doesn't have to repeat the boilerplate).
afterEach(() => {
	cleanup();
});

// ── shared helpers ──────────────────────────────────────────────────

async function renderHome() {
	const { default: Home } = await import("@/pages/Home");
	return render(<TooltipProvider>{<Home />}</TooltipProvider>);
}

//transcription text wrapped in <output aria-live="polite"> ──

describe("QV-9: lastText is rendered inside an <output aria-live='polite'> region", () => {
	beforeEach(() => {
		// Reset the shared singletons (mockCall, mockPythonEvent, mockNavigate, …).
		resetStableMocks();
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
		// ancestor exists with aria-live="polite" — the
		// LastTranscriptionPreview container itself no longer carries
		// aria-live (the ancestor <output> is the single live region
		// so the same text isn't announced twice by screen readers),
		// so the closest aria-live ancestor should be the <output>.
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

//LastTranscriptionPreview container relies on the ancestor <output> ──

describe("QV-96: LastTranscriptionPreview container does NOT carry its own aria-live (ancestor <output> provides it)", () => {
	it("renders the outer container with no aria-live attribute", async () => {
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
		// The outermost element must NOT carry aria-live — the
		// ancestor `<output aria-live="polite">` wrapper in Home.tsx
		// is the single live region. A second aria-live here would
		// cause screen readers to announce the same text twice.
		const outer = container.firstElementChild;
		expect(outer).not.toBeNull();
		expect(outer?.hasAttribute("aria-live")).toBe(false);
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

// The shared `<Button>` from `@/components/ui/button` is mocked so
// components rendered through Home (e.g. MicToggleButton) can be tested
// without the shared Button's dev-mode useEffect. File-level mock —
// kept even though RecordingErrorCard (its original reason for existing)
// has been removed.

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

//(a): live MM:SS timer renders while recording ──

describe("QV-49(a): Home renders a live MM:SS timer while recording", () => {
	beforeEach(() => {
		// Reset the shared singletons (mockCall, mockPythonEvent, mockNavigate, …).
		resetStableMocks();
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

// Single dynamic status line below the mic button ──
//
// All state-driven copy (default hotkey hint, "Preparing offline
// engine…", red errors) lives in ONE <output aria-live="polite">
// element under the button. Nothing else renders above the button as a
// separate status line (only the recording timer while recording).

describe("Home renders the single dynamic status line below the mic button", () => {
	beforeEach(() => {
		// Reset the shared singletons (mockCall, mockPythonEvent, mockNavigate, …).
		resetStableMocks();
		localStorage.clear();
		vi.resetModules();
		mockCall.mockImplementation(() => new Promise(() => {}));
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the default 'Press <hotkey> or click to dictate' hint when idle", async () => {
		const { useAppStore } = await import("@/stores/appStore");
		useAppStore.setState({ recordingState: "idle", lastError: null });

		await renderHome();

		// en.json values for home.press / home.pressOrClick.
		expect(screen.getByText("Press")).toBeTruthy();
		expect(screen.getByText("or click to dictate")).toBeTruthy();
		// No separate preparing/status line is rendered alongside.
		expect(screen.queryByText("Preparing offline engine…")).toBeNull();
	});

	it("renders the recording error in red inside the single status line", async () => {
		const { useAppStore } = await import("@/stores/appStore");
		useAppStore.setState({
			recordingState: "error",
			lastError: "No model selected",
		});

		await renderHome();

		const line = screen.getByText("No model selected");
		// The single dynamic line is an <output aria-live="polite">.
		const output = line.closest("output");
		expect(output).not.toBeNull();
		expect(output?.getAttribute("aria-live")).toBe("polite");
		// Error state renders with the destructive (red) token and is
		// announced as an alert.
		expect(output?.className).toContain("text-destructive");
		expect(output?.getAttribute("role")).toBe("alert");
		// The default hotkey hint is replaced, not shown alongside.
		expect(screen.queryByText("or click to dictate")).toBeNull();
	});

	it("renders the 'No model selected' hint in red when config has no model", async () => {
		// Resolve get_config with the NO_MODEL_SIZE sentinel ("") so
		// the no-model branch of the status line activates.
		mockCall.mockImplementation((cmd: string) => {
			if (cmd === "get_config") {
				return Promise.resolve({ hotkey: "<f2>", model_size: "" });
			}
			return new Promise(() => {});
		});
		const { useAppStore } = await import("@/stores/appStore");
		useAppStore.setState({ recordingState: "idle", lastError: null });

		await renderHome();

		// en.json value for home.noModelSelectedHint.
		const line = await screen.findByText(
			"No model selected. Go to the models page to select a model.",
		);
		const output = line.closest("output");
		expect(output?.className).toContain("text-destructive");
		expect(screen.queryByText("or click to dictate")).toBeNull();
	});

	it("navigates to the Models page when the 'No model selected' hint is clicked", async () => {
		// Resolve get_config with the NO_MODEL_SIZE sentinel ("") so the
		// no-model branch renders the clickable hint.
		mockCall.mockImplementation((cmd: string) => {
			if (cmd === "get_config") {
				return Promise.resolve({ hotkey: "<f2>", model_size: "" });
			}
			return new Promise(() => {});
		});
		const { useAppStore } = await import("@/stores/appStore");
		useAppStore.setState({ recordingState: "idle", lastError: null });

		await renderHome();

		const hint = await screen.findByRole("button", {
			name: "No model selected. Go to the models page to select a model.",
		});
		fireEvent.click(hint);
		expect(mockNavigate).toHaveBeenCalledWith("models");
	});

	it("flips the status pill to the error state when the dynamic line shows an error (no model selected)", async () => {
		// Resolve get_config with the NO_MODEL_SIZE sentinel ("") so
		// the no-model branch of the status line activates — the pill
		// must agree with the red error line below the button instead of
		// staying in the underlying idle state.
		mockCall.mockImplementation((cmd: string) => {
			if (cmd === "get_config") {
				return Promise.resolve({ hotkey: "<f2>", model_size: "" });
			}
			return new Promise(() => {});
		});
		const { useAppStore } = await import("@/stores/appStore");
		useAppStore.setState({ recordingState: "idle", lastError: null });

		await renderHome();

		// Wait for the error line to render (config loaded).
		await screen.findByText(
			"No model selected. Go to the models page to select a model.",
		);

		// The pill shows the error label + red dot (en.json
		// home.error = "ERROR", STATUS_COLORS.error = #E74C3C).
		const pillLabel = screen.getByText("ERROR");
		expect(pillLabel).toBeTruthy();
		const dot = pillLabel.previousElementSibling as HTMLElement | null;
		expect(dot?.style.backgroundColor).toBe("rgb(231, 76, 60)");
		// The underlying state is NOT shown — "READY" is gone.
		expect(screen.queryByText("READY")).toBeNull();
	});

	it("keeps the status pill in the underlying state when the dynamic line is not an error", async () => {
		// Model selected (tiny) + idle → the pill stays "Ready" because
		// the dynamic line below is the default hotkey hint, not an
		// error.
		mockCall.mockImplementation((cmd: string) => {
			if (cmd === "get_config") {
				return Promise.resolve({ hotkey: "<f2>", model_size: "tiny" });
			}
			return new Promise(() => {});
		});
		const { useAppStore } = await import("@/stores/appStore");
		useAppStore.setState({ recordingState: "idle", lastError: null });

		await renderHome();

		// en.json home.ready = "READY".
		await screen.findByText("READY");
		expect(screen.queryByText("ERROR")).toBeNull();
	});

	it("renders 'Preparing offline engine…' in the status line after an attempted dictation with the pack not ready", async () => {
		const { useAppStore } = await import("@/stores/appStore");
		useAppStore.setState({ recordingState: "idle", lastError: null });

		await renderHome();

		// Fire offline_pack_missing so the pack is not ready, then
		// simulate a dictation attempt (the same path handleToggle
		// uses) so the preparing state activates.
		const missingCall = mockPythonEvent.mock.calls.find(
			(c) => c[0] === "offline_pack_missing",
		);
		expect(missingCall).toBeDefined();
		await act(async () => {
			missingCall?.[1]?.({});
		});

		const micButton = screen.getByRole("button", {
			name: /start dictation/i,
		});
		fireEvent.click(micButton);

		// The single status line now shows the preparing message instead
		// of the default hotkey hint. en.json value for
		// pack.preparingOfflineEngine.
		const line = await screen.findByText("Preparing offline engine…");
		const output = line.closest("output");
		expect(output?.className).toContain("text-(--text-muted)");
		expect(screen.queryByText("or click to dictate")).toBeNull();
	});
});

// Exactly ONE live region across the three status surfaces ──
//
// The pill, the recording timer, and the dynamic status line all
// derive from the same recordingState store. Only the dynamic line
// may be a live region: the pill is a plain <div> (no implicit
// `status` role) and the timer is role="timer" with EXPLICIT
// aria-live="off" (per WAI-ARIA the `timer` role only carries the
// value implicitly, and some screen readers announce role="timer"
// content anyway — the explicit attribute is the hardening). This
// guard asserts the invariant so a future <output> swap or stray
// aria-live can't silently double-announce every state change.

describe("Home keeps exactly ONE live region across pill / timer / dynamic line", () => {
	beforeEach(() => {
		// Reset the shared singletons (mockCall, mockPythonEvent, mockNavigate, …).
		resetStableMocks();
		localStorage.clear();
		vi.resetModules();
		mockCall.mockImplementation(() => new Promise(() => {}));
	});

	afterEach(() => {
		cleanup();
	});

	it("renders all three surfaces with exactly one [aria-live] element — the dynamic line", async () => {
		// Recording state puts ALL THREE surfaces in the DOM at once:
		// the pill (RECORDING label), the MM:SS timer, and the dynamic
		// status line.
		const { useAppStore } = await import("@/stores/appStore");
		useAppStore.setState({ recordingState: "recording", lastError: null });

		await renderHome();

		// The pill must be present but NON-live — a plain <div>, never
		// an <output> (whose implicit `status` role is a live region).
		const pillLabel = screen.getByText("RECORDING");
		expect(pillLabel.closest("output")).toBeNull();
		expect(pillLabel.closest("div")).not.toBeNull();

		// The timer is role="timer" with EXPLICIT aria-live="off" — the
		// tick is never announced by any screen reader (the implicit
		// off value is not reliably honored by all of them).
		const timer = screen.getByLabelText(/Recording duration:/i);
		expect(timer.getAttribute("role")).toBe("timer");
		expect(timer.getAttribute("aria-live")).toBe("off");

		// Exactly ONE ANNOUNCEABLE live region in the whole Home tree,
		// and it is the dynamic status line below the mic button. The
		// bare `[aria-live]` selector would also match the timer's
		// explicit aria-live="off" (present but non-announcing), so
		// count only regions that can actually announce: polite /
		// assertive live regions plus the implicit-live status/alert
		// roles.
		const liveRegions = document.querySelectorAll(
			'[aria-live="polite"], [aria-live="assertive"], [role="status"], [role="alert"]',
		);
		expect(liveRegions.length).toBe(1);
		expect(liveRegions[0]?.getAttribute("aria-live")).toBe("polite");
		expect(liveRegions[0]?.tagName).toBe("OUTPUT");
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
		"src/renderer/src/pages/home/components/LastTranscriptionPreview.tsx",
		"src/renderer/src/pages/home/components/MicToggleButton.tsx",
		"src/renderer/src/pages/home/lib/constants.ts",
		"src/renderer/src/pages/home/lib/status.ts",
		"src/renderer/src/pages/home/lib/cache.ts",
		"src/renderer/src/pages/home/hooks/useFirstRecordingCelebration.ts",
	];

	// Known task-ID / session-prefix tokens that MUST NOT appear in source
	// comments (per AGENTS.md C-STYLE-1). The list is intentionally
	// non-exhaustive — it covers the prefixes that were previously
	// scattered across these files.
	const FORBIDDEN_TOKENS = [
		"EC-",
		"EC-",
		"EC-",
		"PVT-",
		"BACKLOG-",
		"FIX-",
		"CR-",
		"UX-",
		"UX-",
		"UX-",
		"UX-",
		"NEW-TS-",
		"F11-",
		"R7-",
		"GG-",
		"GG-",
		"GG-",
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

// ── inline transcribing / downloading-model status hint ──
//
// The mic button is disabled during `transcribing` and `loading`. Without
// an inline textual hint the user has no visible explanation for why the
// button is unresponsive. Home.tsx renders an inline `<p role="status">`
// between the mic button and the hotkey hint, and passes the same text
// as `disabledReason` to MicToggleButton so the accessible name on the
// disabled button explains why it can't be clicked.

describe("Home renders an inline status hint while transcribing or loading", () => {
	beforeEach(() => {
		// Reset the shared singletons (mockCall, mockPythonEvent, mockNavigate, …).
		resetStableMocks();
		localStorage.clear();
		vi.resetModules();
		mockCall.mockImplementation(() => new Promise(() => {}));
	});

	afterEach(() => {
		cleanup();
	});

	it("renders t('home.transcribingHint') when recordingState is 'transcribing'", async () => {
		const { useAppStore } = await import("@/stores/appStore");
		useAppStore.setState({ recordingState: "transcribing" });

		await renderHome();

		// en.json value for home.transcribingHint. The inline hint is an
		// <output> element (biome useSemanticElements) — the semantic
		// HTML5 live region — carrying the explicit aria-live="polite"
		// that the previous <p role="status"> provided.
		const hint = screen.getByText("Transcribing… please wait");
		expect(hint).toBeTruthy();
		expect(hint.tagName.toLowerCase()).toBe("output");
		expect(hint.getAttribute("aria-live")).toBe("polite");
	});

	it("renders t('home.downloadingModel') when recordingState is 'loading' and no downloadPct has arrived yet", async () => {
		const { useAppStore } = await import("@/stores/appStore");
		useAppStore.setState({ recordingState: "loading" });

		await renderHome();

		// en.json value for home.downloadingModel. The inline hint
		// is suppressed once `downloadPct` arrives (the progressbar
		// takes over) — verified in a separate test below.
		const hint = screen.getByText("Downloading model…");
		expect(hint).toBeTruthy();
		expect(hint.getAttribute("aria-live")).toBe("polite");
	});

	it("does NOT render the inline hint when recordingState is 'idle'", async () => {
		const { useAppStore } = await import("@/stores/appStore");
		useAppStore.setState({ recordingState: "idle" });

		await renderHome();

		expect(screen.queryByText("Transcribing… please wait")).toBeNull();
		expect(screen.queryByText("Downloading model…")).toBeNull();
	});
});

// ── MicToggleButton disabledReason surfaces as aria-label / title ──

describe("MicToggleButton surfaces disabledReason as the accessible name when disabled", () => {
	it("uses disabledReason for aria-label and title when disabled", async () => {
		const { MicToggleButton } = await import(
			"@/pages/home/components/MicToggleButton"
		);
		render(
			<MicToggleButton
				isRecording={false}
				toggling={false}
				disabled={true}
				onClick={() => {}}
				label="Start dictation"
				disabledReason="Transcribing… please wait"
			/>,
		);
		// The accessible name must explain why the button is disabled,
		// not just repeat the (now-unusable) action label.
		const btn = screen.getByRole("button", {
			name: "Transcribing… please wait",
		});
		expect(btn.getAttribute("title")).toBe("Transcribing… please wait");
		expect(btn.getAttribute("aria-pressed")).toBe("false");
	});

	it("falls back to `label` when disabled but no disabledReason is provided", async () => {
		const { MicToggleButton } = await import(
			"@/pages/home/components/MicToggleButton"
		);
		render(
			<MicToggleButton
				isRecording={false}
				toggling={false}
				disabled={true}
				onClick={() => {}}
				label="Start dictation"
			/>,
		);
		const btn = screen.getByRole("button", { name: "Start dictation" });
		expect(btn.getAttribute("title")).toBe("Start dictation");
	});

	it("uses `label` (not disabledReason) when not disabled", async () => {
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
				disabledReason="Transcribing… please wait"
			/>,
		);
		// When the button is actionable the accessible name must
		// describe the action, not the dormant disabled reason.
		const btn = screen.getByRole("button", { name: "Start dictation" });
		expect(btn.getAttribute("title")).toBe("Start dictation");
	});
});

// ── GDPR gate: Home refuses dictation start without consent ──

/**
 * Render Home with a controlled ``get_config`` promise. The test
 * resolves the config AFTER mount so ``cfg`` is populated
 * deterministically BEFORE the mic button is clicked (the gate reads
 * ``cfg.voice_biometric_consent``). The other mount fetches stay
 * pending — with ``stats``/``recent`` null the StatCards/ActivityList
 * never render with empty data (``compactNumber`` would crash on
 * ``undefined``).
 */
async function renderHomeWithDeferredConfig() {
	let resolveConfig: (cfg: unknown) => void = () => {};
	const configPromise = new Promise<unknown>((res) => {
		resolveConfig = res;
	});
	mockCall.mockImplementation((cmd: string) => {
		if (cmd === "get_config") return configPromise;
		return new Promise(() => {});
	});
	const { useAppStore } = await import("@/stores/appStore");
	useAppStore.setState({ recordingState: "idle", lastError: null });
	await renderHome();
	return { resolveConfig };
}
describe("Home gates dictation on voice_biometric_consent (GDPR Art. 9)", () => {
	beforeEach(() => {
		// Reset the shared singletons (mockCall, mockPythonEvent, mockNavigate, …).
		resetStableMocks();
		toastWarning.mockClear();
		useConsentGateStore.setState({ request: null });
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("opens the point-of-use consent gate (Allow → starts dictation) instead of calling toggle_dictation when consent is off", async () => {
		const { resolveConfig } = await renderHomeWithDeferredConfig();

		await act(async () => {
			resolveConfig({ voice_biometric_consent: false, hotkey: "<f2>" });
		});

		// Click the mic button with consent OFF.
		const micButton = await screen.findByRole("button", {
			name: /start dictation/i,
		});
		fireEvent.click(micButton);

		// The IPC must NOT be called — the client-side gate short-circuits
		// into the unified consent dialog.
		const toggleCalls = mockCall.mock.calls.filter(
			(c) => c[0] === "toggle_dictation",
		);
		expect(toggleCalls.length).toBe(0);

		// The consent gate opened with the exact field + a retry that
		// starts dictation (Allow in the dialog). NOTE: the store must
		// be imported dynamically — the beforeEach calls
		// ``vi.resetModules()`` so Home (imported after the reset) holds
		// a FRESH module instance of lib/consentGate; the top-level
		// import would be a different singleton.
		const { useConsentGateStore: gateStore } = await import(
			"@/lib/consentGate"
		);
		const req = gateStore.getState().request;
		expect(req).toEqual(
			expect.objectContaining({
				consentField: "voice_biometric_consent",
				bodyKey: "consentDialog.field.voice_biometric_consent",
				onAllow: expect.any(Function),
			}),
		);
		// Fire the retry WITHOUT awaiting it — the deferred-config mock
		// leaves ``toggle_dictation`` pending forever, so awaiting would
		// hang the test. The assertion is on the IPC call itself.
		await act(async () => {
			void req?.onAllow?.();
			await new Promise((r) => setTimeout(r, 0));
		});
		expect(mockCall).toHaveBeenCalledWith("toggle_dictation");
	});

	it("calls toggle_dictation normally when consent IS granted", async () => {
		const { resolveConfig } = await renderHomeWithDeferredConfig();

		await act(async () => {
			resolveConfig({ voice_biometric_consent: true, hotkey: "<f2>" });
		});

		const micButton = await screen.findByRole("button", {
			name: /start dictation/i,
		});
		fireEvent.click(micButton);

		// The IPC fires; no consent prompt.
		await waitFor(() => {
			const toggleCalls = mockCall.mock.calls.filter(
				(c) => c[0] === "toggle_dictation",
			);
			expect(toggleCalls.length).toBe(1);
		});
		expect(toastWarning).not.toHaveBeenCalled();
	});
});

// ── Home shows recording errors as red status-line text ──
//
// Recording errors are surfaced as red text inside the single dynamic
// status line (see "Home renders the single dynamic status line below
// the mic button" above). The old error card + its secondary CTA were
// removed entirely (RecordingErrorCard deleted).

describe("Home shows recording errors as red status-line text, not a card", () => {
	beforeEach(() => {
		// Reset the shared singletons (mockCall, mockPythonEvent, mockNavigate, …).
		resetStableMocks();
		localStorage.clear();
		vi.resetModules();
		mockCall.mockImplementation(() => new Promise(() => {}));
	});

	afterEach(() => {
		cleanup();
	});

	it("does not render the 'Open Microphone settings' CTA in the error state", async () => {
		const { useAppStore } = await import("@/stores/appStore");
		useAppStore.setState({
			recordingState: "error",
			lastError: "Device unavailable",
		});

		await renderHome();

		// The error is shown as red text in the status line — no card,
		// no secondary CTA.
		expect(
			screen.queryByRole("button", { name: /Open Microphone settings/i }),
		).toBeNull();
		expect(screen.getByText("Device unavailable")).toBeTruthy();
	});
});
