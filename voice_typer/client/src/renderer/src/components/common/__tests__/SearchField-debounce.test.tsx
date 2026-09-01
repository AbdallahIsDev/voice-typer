/**
 * Optional debounce on the shared SearchField.
 *
 * `debounceMs` is opt-in: undefined keeps the current immediate
 * onChange behavior (existing consumers unchanged). With a delay, the
 * input stays FULLY CONTROLLED (the value prop renders instantly —
 * no typing lag), while onChange notifications are batched. A pending
 * timer is cancelled on unmount and when the external value changes
 * (external change = someone else reset the field, e.g. clearQuery —
 * no trailing notification for a value the consumer didn't originate).
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SearchField } from "@/components/common/SearchField";

describe("SearchField — optional onChange debounce", () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
		cleanup();
	});

	it("immediate by default (no debounceMs): onChange fires per keystroke", () => {
		const onChange = vi.fn();
		render(<SearchField value="" onChange={onChange} />);
		const input = screen.getByRole("textbox");
		fireEvent.change(input, { target: { value: "a" } });
		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenLastCalledWith("a");
	});

	it("with debounceMs, onChange fires once after the delay (trailing edge)", () => {
		const onChange = vi.fn();
		render(<SearchField value="" onChange={onChange} debounceMs={150} />);
		const input = screen.getByRole("textbox");
		fireEvent.change(input, { target: { value: "q" } });
		fireEvent.change(input, { target: { value: "qu" } });
		fireEvent.change(input, { target: { value: "que" } });
		expect(onChange).not.toHaveBeenCalled();
		vi.advanceTimersByTime(149);
		expect(onChange).not.toHaveBeenCalled();
		vi.advanceTimersByTime(1);
		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenLastCalledWith("que");
	});

	it("the input stays fully controlled — displayed value tracks the value prop immediately", () => {
		const onChange = vi.fn();
		const { rerender } = render(
			<SearchField value="" onChange={onChange} debounceMs={150} />,
		);
		const input = screen.getByRole("textbox") as HTMLInputElement;
		fireEvent.change(input, { target: { value: "typ" } });
		// The draft renders instantly (no typing lag) even though the
		// debounced notification hasn't fired yet.
		expect(input.value).toBe("typ");
		expect(onChange).not.toHaveBeenCalled();
		// Parent echoes the committed value.
		rerender(<SearchField value="typ" onChange={onChange} debounceMs={150} />);
		expect(input.value).toBe("typ");
		// External reset re-syncs the displayed value.
		rerender(<SearchField value="" onChange={onChange} debounceMs={150} />);
		expect(input.value).toBe("");
	});

	it("external value change cancels the pending trailing notification", () => {
		const onChange = vi.fn();
		const { rerender } = render(
			<SearchField value="" onChange={onChange} debounceMs={150} />,
		);
		fireEvent.change(screen.getByRole("textbox"), {
			target: { value: "draft" },
		});
		// External reset (e.g. clearQuery writing a different value)
		// before the timer settles.
		rerender(
			<SearchField value="external" onChange={onChange} debounceMs={150} />,
		);
		vi.advanceTimersByTime(300);
		expect(onChange).not.toHaveBeenCalled();
		expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe(
			"external",
		);
	});

	it("clear button fires immediately (no debounce)", () => {
		const onChange = vi.fn();
		render(<SearchField value="abc" onChange={onChange} debounceMs={150} />);
		fireEvent.click(screen.getByRole("button"));
		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenLastCalledWith("");
	});

	it("no stray timer after unmount", () => {
		const onChange = vi.fn();
		const { unmount } = render(
			<SearchField value="" onChange={onChange} debounceMs={150} />,
		);
		fireEvent.change(screen.getByRole("textbox"), { target: { value: "x" } });
		unmount();
		vi.advanceTimersByTime(300);
		expect(onChange).not.toHaveBeenCalled();
	});
});
