/**
 * usePointerFocusModality, behavior-preservation tests for the shared
 * pointer-vs-keyboard focus-modality contract (C-FOCUS-3).
 *
 * The state machine was previously duplicated verbatim in
 * `components/ui/input.tsx` and `components/ui/textarea.tsx`; it now
 * lives in ONE hook (`hooks/usePointerFocusModality.ts`) consumed by
 * both primitives. These tests pin the exact semantics so the
 * extraction cannot silently change them:
 *
 *   1. pointerdown sets pointer-modality → the heavy focus ring is
 *      suppressed (`focus:border-ring/60 focus-visible:ring-0`) and
 *      the full-opacity keyboard ring classes are NOT applied.
 *   2. a Tab or Arrow keydown returns the field to keyboard modality →
 *      the full-opacity `focus-visible:ring-3 focus-visible:ring-ring`
 *      classes return (WCAG 1.4.11 3:1, keyboard ring intact).
 *   3. blur resets the modality (a later keyboard focus is announced
 *      with the full ring even after a previous pointer interaction).
 *   4. non-navigation keys (e.g. typing "a") do NOT leave pointer
 *      modality, only Tab/Arrow switch back to keyboard modality.
 *   5. caller-supplied onPointerDown/onKeyDown/onBlur OVERRIDE the
 *      hook's handlers (the documented `{...props}`-spread clobber
 *      semantics pinned by SearchField's comment, the same contract
 *      C-FOCUS-4 describes for the shared inputs).
 *
 * jsdom has no CSS engine, so the assertions read className strings
 * (same technique as `focus-ring-contrast.test.tsx`).
 */
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

afterEach(() => {
	cleanup();
});

/** The pointer-modality branch: subtle border tint, ring suppressed. */
const POINTER_BRANCH = [
	"focus:border-ring/60",
	"focus-visible:ring-0",
] as const;
/** The keyboard/AT branch: the full-opacity WCAG 1.4.11 ring. */
const KEYBOARD_BRANCH = [
	"focus-visible:border-ring",
	"focus-visible:ring-3",
	"focus-visible:ring-ring",
] as const;

function classes(el: Element | null): string {
	return el?.className ?? "";
}

function expectPointerMode(cls: string) {
	for (const c of POINTER_BRANCH) expect(cls).toContain(c);
	for (const c of KEYBOARD_BRANCH) expect(cls).not.toContain(c);
}

function expectKeyboardMode(cls: string) {
	for (const c of KEYBOARD_BRANCH) expect(cls).toContain(c);
	for (const c of POINTER_BRANCH) expect(cls).not.toContain(c);
}

describe("usePointerFocusModality, Input", () => {
	it("starts in keyboard modality (full ring classes, no suppression)", () => {
		const { container } = render(<Input type="text" />);
		expectKeyboardMode(classes(container.querySelector("input")));
	});

	it("pointerdown suppresses the ring; Tab keydown restores it; blur resets", () => {
		const { container } = render(<Input type="text" />);
		const input = container.querySelector("input") as HTMLInputElement;

		fireEvent.pointerDown(input);
		expectPointerMode(classes(input));

		fireEvent.keyDown(input, { key: "Tab" });
		expectKeyboardMode(classes(input));

		fireEvent.pointerDown(input);
		expectPointerMode(classes(input));

		fireEvent.blur(input);
		expectKeyboardMode(classes(input));
	});

	it("Arrow keydown (all four arrows) restores keyboard modality", () => {
		const { container } = render(<Input type="text" />);
		const input = container.querySelector("input") as HTMLInputElement;

		for (const key of ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]) {
			fireEvent.pointerDown(input);
			expectPointerMode(classes(input));
			fireEvent.keyDown(input, { key });
			expectKeyboardMode(classes(input));
		}
	});

	it("non-navigation keys (typing) keep pointer modality", () => {
		const { container } = render(<Input type="text" />);
		const input = container.querySelector("input") as HTMLInputElement;

		fireEvent.pointerDown(input);
		fireEvent.keyDown(input, { key: "a" });
		fireEvent.keyDown(input, { key: "Enter" });
		expectPointerMode(classes(input));
	});

	it("caller-supplied onPointerDown overrides the modality handler (documented clobber semantics)", () => {
		const callerPointerDown = vi.fn();
		const { container } = render(
			<Input type="text" onPointerDown={callerPointerDown} />,
		);
		const input = container.querySelector("input") as HTMLInputElement;

		fireEvent.pointerDown(input);
		// The caller's handler ran, the internal one did NOT, so the
		// field stays in keyboard modality (suppression inactive).
		expect(callerPointerDown).toHaveBeenCalledTimes(1);
		expectKeyboardMode(classes(input));
	});
});

describe("usePointerFocusModality, Textarea", () => {
	it("starts in keyboard modality (full ring classes, no suppression)", () => {
		const { container } = render(<Textarea />);
		expectKeyboardMode(classes(container.querySelector("textarea")));
	});

	it("pointerdown suppresses the ring; Tab keydown restores it; blur resets", () => {
		const { container } = render(<Textarea />);
		const textarea = container.querySelector("textarea") as HTMLTextAreaElement;

		fireEvent.pointerDown(textarea);
		expectPointerMode(classes(textarea));

		fireEvent.keyDown(textarea, { key: "Tab" });
		expectKeyboardMode(classes(textarea));

		fireEvent.pointerDown(textarea);
		expectPointerMode(classes(textarea));

		fireEvent.blur(textarea);
		expectKeyboardMode(classes(textarea));
	});

	it("Arrow keydown restores keyboard modality", () => {
		const { container } = render(<Textarea />);
		const textarea = container.querySelector("textarea") as HTMLTextAreaElement;

		fireEvent.pointerDown(textarea);
		expectPointerMode(classes(textarea));
		fireEvent.keyDown(textarea, { key: "ArrowUp" });
		expectKeyboardMode(classes(textarea));
	});

	it("non-navigation keys keep pointer modality", () => {
		const { container } = render(<Textarea />);
		const textarea = container.querySelector("textarea") as HTMLTextAreaElement;

		fireEvent.pointerDown(textarea);
		fireEvent.keyDown(textarea, { key: "x" });
		expectPointerMode(classes(textarea));
	});
});
