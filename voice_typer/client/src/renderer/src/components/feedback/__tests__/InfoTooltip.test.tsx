/**
 * InfoTooltip unit tests.
 *
 * BG-R13 (sub-agent 21 follow-up): the ``contextLabel`` prop was
 * introduced so screen-reader users can distinguish multiple
 * InfoTooltips on the same page (e.g. a Settings tab may have a dozen
 * rows each with an InfoTooltip — without ``contextLabel`` they all
 * announce as "More info", which is useless when tabbing through them).
 *
 * When ``contextLabel`` is provided, the trigger button's accessible
 * name is composed as ``t("a11y.moreInfoAbout", { label: contextLabel })``
 * — e.g. "More info about VAD aggressiveness". When omitted, the
 * trigger falls back to ``t("a11y.moreInfo")`` ("More info").
 *
 * Production callers (SettingRow.tsx, TemplateListRow.tsx) do NOT yet
 * pass ``contextLabel``. F12 owns SettingRow.tsx and is responsible
 * for wiring ``contextLabel={label}`` through to ``<InfoTooltip>`` so
 * each Settings row's tooltip is disambiguated. These tests verify the
 * InfoTooltip-side contract so F12's wiring change can be verified in
 * isolation.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { InfoTooltip } from "@/components/feedback/InfoTooltip";

describe("InfoTooltip — contextLabel disambiguation (BG-R13)", () => {
        afterEach(() => {
                cleanup();
        });

        it("renders the trigger button with the generic 'More info' aria-label when contextLabel is omitted", () => {
                render(<InfoTooltip text="Some help text" />);
                // en.json: a11y.moreInfo = "More info"
                const trigger = screen.getByRole("button", { name: "More info" });
                expect(trigger).toBeInTheDocument();
        });

        it("renders the trigger button with the specific 'More info about {label}' aria-label when contextLabel is provided", () => {
                render(
                        <InfoTooltip
                                text="Adjusts how aggressively the voice activity detector filters out background noise."
                                contextLabel="VAD aggressiveness"
                        />,
                );
                // en.json: a11y.moreInfoAbout = "More info about {label}"
                // Interpolated: "More info about VAD aggressiveness"
                const trigger = screen.getByRole("button", {
                        name: "More info about VAD aggressiveness",
                });
                expect(trigger).toBeInTheDocument();
        });

        it("renders distinct accessible names for multiple InfoTooltips on the same page when contextLabel is provided", () => {
                render(
                        <div>
                                <InfoTooltip text="Help for setting A" contextLabel="VAD aggressiveness" />
                                <InfoTooltip text="Help for setting B" contextLabel="Noise gate threshold" />
                                <InfoTooltip text="Help for setting C" contextLabel="Compressor ratio" />
                        </div>,
                );
                // All three triggers are distinguishable by their accessible names —
                // a screen-reader user tabbing through them hears each field name
                // instead of "More info, More info, More info".
                expect(
                        screen.getByRole("button", { name: "More info about VAD aggressiveness" }),
                ).toBeInTheDocument();
                expect(
                        screen.getByRole("button", { name: "More info about Noise gate threshold" }),
                ).toBeInTheDocument();
                expect(
                        screen.getByRole("button", { name: "More info about Compressor ratio" }),
                ).toBeInTheDocument();
        });

        it("renders the help text inside the tooltip content when the trigger is focused", async () => {
                // The Radix Tooltip content is portaled to document.body and only
                // mounts when the trigger is focused/hovered. Focusing the trigger
                // opens the tooltip, which exposes the text via the portaled
                // role="tooltip" element. We query by role instead of by text
                // because Radix renders the text in two places (the visible
                // tooltip bubble and a visually-hidden twin for SR) which makes
                // exact-string text matching flaky.
                render(<InfoTooltip text="Detailed help text" contextLabel="Some field" />);
                const trigger = screen.getByRole("button", {
                        name: "More info about Some field",
                });
                trigger.focus();
                // Wait for the portaled tooltip (role="tooltip") to mount.
                const tooltip = await screen.findByRole("tooltip");
                expect(tooltip).toBeInTheDocument();
                expect(tooltip.textContent).toContain("Detailed help text");
        });
});
