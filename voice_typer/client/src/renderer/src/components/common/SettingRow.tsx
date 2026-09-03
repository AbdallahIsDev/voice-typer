// src/renderer/src/components/common/SettingRow.tsx

import { type ReactNode, useEffect, useId, useRef } from "react";
import { InfoTooltip } from "@/components/feedback/InfoTooltip";
import { cn } from "@/lib/utils";

interface SettingRowProps {
	label: string;
	info?: string;
	children: ReactNode;
	align?: "start" | "center";
	/**
	 * Optional `htmlFor` to associate this row's visible label with a
	 * specific form control rendered as a child. When provided, the
	 * label is rendered as a real `<label htmlFor={htmlFor}>` element
	 * (clicking the label focuses the control — WCAG 2.4.13 + SC 1.3.1
	 * + SC 4.1.2). When omitted, the label is rendered as a `<span>`
	 * (existing behavior — the child must provide its own accessible
	 * name via `aria-label` / `aria-labelledby` / a wrapping `<label>`).
	 *
	 * Callers that pass a child without its own accessible name AND
	 * without `htmlFor` will get a dev-mode console warning (see the
	 * useEffect below) so the omission is caught during development.
	 */
	htmlFor?: string;
}

export function SettingRow({
	label,
	info,
	children,
	align = "center",
	htmlFor,
}: SettingRowProps) {
	// useId() generates a stable id we put on the visible label
	// element. Children can grab it via aria-labelledby if they want
	// to point at the visible label text without duplicating the string.
	const labelId = useId();

	// Dev-mode audit. Inspect the rendered children for form controls
	// (input, select, textarea, button[role=switch], [role=checkbox],
	// [role=radio]) and verify each one has an accessible name
	// (aria-label, aria-labelledby, title, or — when `htmlFor` is set
	// on the row — is referenced by the row's label). If any control
	// lacks an accessible name, log a one-time warning so the omission
	// surfaces during development without breaking production builds.
	const childrenRef = useRef<HTMLDivElement | null>(null);
	useEffect(() => {
		if (!import.meta.env.DEV) return;
		const node = childrenRef.current;
		if (!node) return;
		const controls = node.querySelectorAll<HTMLElement>(
			'input, select, textarea, button[role="switch"], [role="checkbox"], [role="radio"]',
		);
		if (controls.length === 0) return;
		controls.forEach((ctrl) => {
			const hasOwnName =
				ctrl.hasAttribute("aria-label") ||
				ctrl.hasAttribute("aria-labelledby") ||
				ctrl.hasAttribute("title");
			const isWrappedInLabel = ctrl.closest("label") !== null;
			// Skip controls hidden from the accessibility tree. Radix
			// primitives (Switch/Checkbox/Radio) mount an invisible
			// <input aria-hidden="true"> purely for native form
			// semantics — it is never announced, so it legitimately has
			// no accessible name and must not trip the warning. The
			// same applies to `type="hidden"` inputs and any control
			// under an aria-hidden ancestor.
			//
			// Radix Slider's bubble input (SliderBubbleInput) is
			// hidden via `style: { display: "none" }` — no aria-hidden,
			// no type="hidden" — so a computed-style check is required
			// to keep the audit from false-positiving on a correctly
			// labelled slider (the thumb itself carries the forwarded
			// aria-label).
			const isHiddenFromAT =
				ctrl.getAttribute("type") === "hidden" ||
				ctrl.getAttribute("aria-hidden") === "true" ||
				ctrl.closest('[aria-hidden="true"]') !== null ||
				ctrl.hasAttribute("hidden") ||
				getComputedStyle(ctrl).display === "none" ||
				getComputedStyle(ctrl).visibility === "hidden";
			if (hasOwnName || isWrappedInLabel || isHiddenFromAT) return;
			console.warn(
				`[renderer:SettingRow] The visible label "${label}" has no programmatic association with its child form control. ` +
					"Pass `htmlFor` on SettingRow (and `id` on the control) OR pass `aria-label` / `aria-labelledby` on the control. " +
					"Without an association, screen-reader users hear the control announced without its name.",
			);
		});
	}, [label]);

	// The label is rendered as `<label>` only when the caller has
	// opted in via `htmlFor`. Without `htmlFor`, a `<label>` would
	// either (a) wrap the child (changing layout — many children are
	// flex / Switch / Select and don't tolerate being wrapped) or
	// (b) carry a dangling `htmlFor` pointing at a non-existent id (the
	// bug that caused the original `<label>` → `<span>` downgrade).
	const LabelTag = htmlFor ? "label" : "span";
	return (
		<div
			className={cn(
				"flex items-start justify-between gap-6 px-4 py-2",
				align === "center" && "items-center",
			)}
		>
			<div className="flex min-w-0 items-center gap-2">
				<LabelTag
					id={labelId}
					htmlFor={htmlFor}
					data-settings-row-label={label}
					className="text-sm font-medium text-(--text-primary) cursor-default"
				>
					{label}
				</LabelTag>
				{info && <InfoTooltip text={info} contextLabel={label} />}
			</div>
			<div ref={childrenRef} className="shrink-0">
				{children}
			</div>
		</div>
	);
}
