/**
 * Canonical mock for `@hugeicons/core-free-icons` — the SINGLE source of
 * truth for icon stubs across every renderer test file.
 *
 * WHY THIS FILE EXISTS (drift guard):
 *   Each test file used to enumerate its own `vi.mock("@hugeicons/core-free-icons",
 *   () => ({ ... }))` stub list by hand. The lists drifted: a component that
 *   imported an icon missing from a file's list crashed the file's tests with
 *   "No '<Icon>' export is defined on the mock" at module-load time (vitest
 *   validates named imports against the mock factory upfront). The union below
 *   is the complete set of every icon imported anywhere in the renderer source
 *   tree. `hugeicons-mock-guard.test.ts` enforces two invariants so the set
 *   can never drift again:
 *     1. every icon imported from `@hugeicons/core-free-icons` in `src/`
 *        MUST be a key of this mock (new component icon → guard fails until
 *        the icon is added here, with a message pointing at this file);
 *     2. no test file may hand-roll its own `...: make("...")` stub list —
 *        they must all delegate to `createHugeiconsMock()`.
 *
 * Each icon is a `{ name }`-tagged object (NOT the real icon data — the real
 * exports are raw SVG path arrays with no `.name` property). The test-side
 * `HugeiconsIcon` mock (from `@hugeicons/react`) renders
 * `<span data-name={icon?.name} />`, so the tag is what the ~76
 * `data-name` assertions across the suite key on.
 */
const make = (name: string) => ({ name });

/** Complete, alphabetized union of every icon imported in the renderer. */
const hugeiconsMock = {
	Activity03Icon: make("Activity03Icon"),
	Add01Icon: make("Add01Icon"),
	AiBrain03Icon: make("AiBrain03Icon"),
	Alert01Icon: make("Alert01Icon"),
	Alert02Icon: make("Alert02Icon"),
	AlertCircleIcon: make("AlertCircleIcon"),
	Analytics01Icon: make("Analytics01Icon"),
	ArrowDown01Icon: make("ArrowDown01Icon"),
	ArrowRight01Icon: make("ArrowRight01Icon"),
	ArrowTurnBackwardIcon: make("ArrowTurnBackwardIcon"),
	ArrowUp01Icon: make("ArrowUp01Icon"),
	Book02Icon: make("Book02Icon"),
	BookOpen02Icon: make("BookOpen02Icon"),
	Bug02Icon: make("Bug02Icon"),
	Calendar01Icon: make("Calendar01Icon"),
	Cancel01Icon: make("Cancel01Icon"),
	CheckmarkCircle01Icon: make("CheckmarkCircle01Icon"),
	CheckmarkCircle02Icon: make("CheckmarkCircle02Icon"),
	ClipboardPasteIcon: make("ClipboardPasteIcon"),
	Copy01Icon: make("Copy01Icon"),
	CustomActionIcon: make("CustomActionIcon"),
	Delete01Icon: make("Delete01Icon"),
	Delete02Icon: make("Delete02Icon"),
	Download01Icon: make("Download01Icon"),
	File02Icon: make("File02Icon"),
	FilterIcon: make("FilterIcon"),
	Folder02Icon: make("Folder02Icon"),
	HistoryIcon: make("HistoryIcon"),
	Home04Icon: make("Home04Icon"),
	InformationCircleIcon: make("InformationCircleIcon"),
	KeyboardIcon: make("KeyboardIcon"),
	LayoutGridIcon: make("LayoutGridIcon"),
	Loading03Icon: make("Loading03Icon"),
	LockKeyIcon: make("LockKeyIcon"),
	Mic02Icon: make("Mic02Icon"),
	MicOff01Icon: make("MicOff01Icon"),
	ModernTvIcon: make("ModernTvIcon"),
	Moon02Icon: make("Moon02Icon"),
	MultiplicationSignCircleIcon: make("MultiplicationSignCircleIcon"),
	PanelLeftIcon: make("PanelLeftIcon"),
	PauseIcon: make("PauseIcon"),
	PencilEdit02Icon: make("PencilEdit02Icon"),
	PlayIcon: make("PlayIcon"),
	RefreshIcon: make("RefreshIcon"),
	Search01Icon: make("Search01Icon"),
	Settings03Icon: make("Settings03Icon"),
	Share08Icon: make("Share08Icon"),
	Shield01Icon: make("Shield01Icon"),
	ShieldBanIcon: make("ShieldBanIcon"),
	SparklesIcon: make("SparklesIcon"),
	SpeechToTextIcon: make("SpeechToTextIcon"),
	StarIcon: make("StarIcon"),
	StopIcon: make("StopIcon"),
	Sun01Icon: make("Sun01Icon"),
	TextIcon: make("TextIcon"),
	Tick02Icon: make("Tick02Icon"),
	Time02Icon: make("Time02Icon"),
	Undo02Icon: make("Undo02Icon"),
	UnfoldMoreIcon: make("UnfoldMoreIcon"),
	ZapIcon: make("ZapIcon"),
};

/** Factory passed to `vi.mock("@hugeicons/core-free-icons", ...)`. */
export const createHugeiconsMock = () => hugeiconsMock;

/** Keys exposed for the drift-guard test. */
export const hugeiconsMockKeys = Object.keys(hugeiconsMock);
