import { Cancel01Icon, Search01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

interface SearchFieldProps {
	value: string;
	onChange: (value: string) => void;
	placeholder?: string;
	/**
	 * Delay in milliseconds before the debounced ``onChange``
	 * notification fires. Undefined (the default) keeps the historical
	 * immediate behavior — every keystroke notifies synchronously. When
	 * set, typing notifications are batched on the trailing edge while
	 * the input itself stays fully controlled: an internal draft (kept
	 * in sync with the ``value`` prop) renders instantly, so typing
	 * never lags. A pending timer is cancelled on unmount and whenever
	 * the external ``value`` changes to a value other than the pending
	 * draft (an external reset must not deliver a stale notification).
	 */
	debounceMs?: number;
	/**
	 * Accessible label for the search input. Falls back to
	 * ``t("common.search")`` so the field is always announced as a
	 * search field by screen readers even when no explicit label is
	 * supplied. Callers SHOULD pass a context-specific label such as
	 * ``t("settings.searchPlaceholder")`` (the existing translation
	 * "Search settings…") to disambiguate multiple SearchFields on
	 * different pages.
	 */
	ariaLabel?: string;
	/** Extra classes merged onto the inner ``<Input>`` — lets callers
	 *  compact the field (e.g. the title-bar global search). */
	className?: string;
	/** Ref forwarded to the inner ``<Input>``, used by the title bar
	 *  to programmatically focus the global search (Ctrl+K). */
	inputRef?: React.RefObject<HTMLInputElement | null>;
}

export function SearchField({
	value,
	onChange,
	placeholder = t("common.search"),
	debounceMs,
	ariaLabel,
	className,
	inputRef,
}: SearchFieldProps) {
	// Always provide an accessible name. The magnifier icon is
	// decorative (the placeholder + aria-label convey the same meaning),
	// so it is marked aria-hidden below. Screen readers therefore
	// announce e.g. "Search settings, edit field" rather than the
	// generic "edit field" — a WCAG 2.1 SC 1.3.1 / 4.1.2 requirement.
	const resolvedAriaLabel = ariaLabel ?? t("common.search");

	// Latest-callback ref so the timer bookkeeping never needs to
	// re-bind on identity changes of ``onChange``.
	const onChangeRef = useRef(onChange);
	onChangeRef.current = onChange;

	// Internal draft: what the input renders. Synced from the ``value``
	// prop whenever the prop changes to something other than the
	// pending draft (parent echoed our edit, or reset the field).
	const [draft, setDraft] = useState(value);
	const draftRef = useRef(value);
	const prevValueRef = useRef(value);
	const timerRef = useRef<number | null>(null);

	const cancelPending = useCallback(() => {
		if (timerRef.current !== null) {
			window.clearTimeout(timerRef.current);
			timerRef.current = null;
		}
	}, []);

	useEffect(
		() => () => {
			cancelPending();
		},
		[cancelPending],
	);

	useEffect(() => {
		if (value === prevValueRef.current) return;
		prevValueRef.current = value;
		if (value !== draftRef.current) {
			cancelPending();
			draftRef.current = value;
			setDraft(value);
		}
	}, [value, cancelPending]);

	const notify = (next: string) => {
		draftRef.current = next;
		setDraft(next);
		if (debounceMs === undefined) {
			onChangeRef.current(next);
			return;
		}
		cancelPending();
		timerRef.current = window.setTimeout(() => {
			timerRef.current = null;
			onChangeRef.current(next);
		}, debounceMs);
	};

	const handleChange = (e: React.ChangeEvent<HTMLInputElement>) =>
		notify(e.target.value);

	const handleClear = () => {
		cancelPending();
		draftRef.current = "";
		setDraft("");
		onChangeRef.current("");
	};

	return (
		// Wrap the field in a `role="search"` landmark so SR users
		// can navigate to it via the "search" landmark shortcut. biome
		// suggests the `<search>` element instead; we keep `<div role="search">`
		// for explicit compatibility with older AT that doesn't recognize `<search>`.
		// biome-ignore lint/a11y/useSemanticElements: see comment above — keeping role="search" for AT compatibility.
		<div role="search" className="relative">
			<HugeiconsIcon
				icon={Search01Icon}
				strokeWidth={1.625}
				// decorative — the input's aria-label
				// provides the accessible name.
				aria-hidden="true"
				className="absolute inset-s-3 top-1/2 -translate-y-1/2 h-4 w-4 text-(--text-muted) pointer-events-none"
			/>
			{/* Pointer-vs-keyboard focus modality lives in the shared
			    Input primitive (components/ui/input.tsx) — SearchField
			    must NOT pass its own onPointerDown/onKeyDown/onBlur
			    (they'd clobber Input's internal handlers via the
			    {...props} spread). Only presentation classes here. */}
			<Input
				ref={inputRef}
				value={draft}
				onChange={handleChange}
				placeholder={placeholder}
				aria-label={resolvedAriaLabel}
				// Muted 10% frame (same as every other border in the app) —
				// a bare `border-border` would override the Input's
				// transparent border with a full-opacity line.
				className={cn(
					"ps-9 pe-9 rounded-xl bg-(--bg-subtle) border-border/5",
					className,
				)}
			/>
			{draft && (
				<button
					type="button"
					onClick={handleClear}
					aria-label={t("a11y.clearSearch")}
					className="absolute inset-e-3 top-1/2 -translate-y-1/2 text-(--text-muted) hover:text-(--text-primary) focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
				>
					<HugeiconsIcon
						icon={Cancel01Icon}
						strokeWidth={1.625}
						aria-hidden="true"
						className="h-4 w-4"
					/>
				</button>
			)}
		</div>
	);
}
