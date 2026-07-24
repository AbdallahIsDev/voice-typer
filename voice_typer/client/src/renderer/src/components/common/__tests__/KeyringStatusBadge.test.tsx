/**
 * BG-R11 (KeyringStatusBadge branching test).
 *
 * KeyringStatusBadge has four code paths:
 *
 *   1. `status?.available === true` (with a real OS keyring backend) →
 *      green LockKey icon + "Secure" text (full) or icon-only (compact)
 *      + tooltip text localized via `settings.keyring.available` /
 *      `settings.keyring.availableWithBackend`.
 *   2. otherwise (fallback / legacy / unknown status object) →
 *      amber Alert02 icon + "Plaintext" text (full) or icon-only (compact)
 *      + tooltip text localized via `settings.keyring.fallback` /
 *      `settings.keyring.fallbackWithReason`.
 *
 * Additional contract:
 *   - In compact mode the trigger button carries an aria-label so the
 *     icon-only button still has an accessible name.
 *   - In full mode the visible "Secure"/"Plaintext" span provides the
 *     accessible name and aria-label is omitted (no double announcement).
 *   - BG-R11: cursor-help → cursor-default. The trigger button must
 *     NOT carry the misleading `cursor-help` class.
 *
 * The tooltip text is asserted via the Tooltip content; Radix Tooltip
 * requires the trigger to be focused (or hovered) before the content
 * is portaled into the DOM, so we focus the trigger then findByText
 * the tooltip string.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KeyringStatusBadge } from "@/components/common/KeyringStatusBadge";
import type { KeyringStatus } from "@/types/config";

// Stub the hugeicons wrapper so we don't pull in the real SVG renderer.
vi.mock("@hugeicons/react", () => ({
        HugeiconsIcon: ({ icon }: { icon?: { name?: string } }) => (
                <span data-testid="hugeicon" data-name={icon?.name} />
        ),
}));

vi.mock("@hugeicons/core-free-icons", () => {
        const make = (name: string) => ({ name });
        return {
                Alert02Icon: make("Alert02Icon"),
                LockKeyIcon: make("LockKeyIcon"),
        };
});

const availableStatus: KeyringStatus = {
        available: true,
        backend: "SecretServiceKeyring",
        fallback: false,
        reason: null,
};

const fallbackStatus: KeyringStatus = {
        available: false,
        backend: null,
        fallback: true,
        reason: "keyring.backend.missing",
};

describe("KeyringStatusBadge — BG-R11 (branching + cursor-default)", () => {
        afterEach(() => {
                cleanup();
        });

        it("available + full: shows 'Secure' text with green LockKey icon", () => {
                render(<KeyringStatusBadge status={availableStatus} />);
                // en.json: settings.keyring.secure → "Secure"
                expect(screen.getByText("Secure")).toBeInTheDocument();
                // LockKeyIcon used in the available branch.
                expect(screen.getByTestId("hugeicon")).toHaveAttribute(
                        "data-name",
                        "LockKeyIcon",
                );
        });

        it("available + compact: icon-only, no visible text, with aria-label", () => {
                render(<KeyringStatusBadge status={availableStatus} compact />);
                // No visible "Secure" text in compact mode.
                expect(screen.queryByText("Secure")).toBeNull();
                // Button still has an accessible name via aria-label.
                const btn = screen.getByRole("button");
                expect(btn).toHaveAttribute("aria-label");
        });

        it("available + full: aria-label is omitted (visible text provides accessible name)", () => {
                render(<KeyringStatusBadge status={availableStatus} />);
                const btn = screen.getByRole("button");
                expect(btn).not.toHaveAttribute("aria-label");
        });

        it("fallback + full: shows 'Plaintext' text with amber Alert02 icon", () => {
                render(<KeyringStatusBadge status={fallbackStatus} />);
                // en.json: settings.keyring.plaintext → "Plaintext"
                expect(screen.getByText("Plaintext")).toBeInTheDocument();
                expect(screen.getByTestId("hugeicon")).toHaveAttribute(
                        "data-name",
                        "Alert02Icon",
                );
        });

        it("fallback + compact: icon-only, with aria-label", () => {
                render(<KeyringStatusBadge status={fallbackStatus} compact />);
                expect(screen.queryByText("Plaintext")).toBeNull();
                const btn = screen.getByRole("button");
                expect(btn).toHaveAttribute("aria-label");
        });

        it("undefined status → fallback branch (legacy responses treated as fallback)", () => {
                // KeyringStatusBadge is called without `status` for legacy
                // responses that don't carry the keyring_status field.
                // We must never claim keyring is available when we don't
                // know — the badge should render the fallback UI.
                render(<KeyringStatusBadge />);
                expect(screen.getByText("Plaintext")).toBeInTheDocument();
                expect(screen.getByTestId("hugeicon")).toHaveAttribute(
                        "data-name",
                        "Alert02Icon",
                );
        });

        it("available + tooltip with backend name appears when trigger is focused", async () => {
                render(<KeyringStatusBadge status={availableStatus} />);
                const btn = screen.getByRole("button");
                // Radix Tooltip opens on focus / pointer enter (not on click).
                // Fire both events to be robust against Radix version changes.
                btn.focus();
                fireEvent.mouseEnter(btn);
                fireEvent.pointerEnter(btn);
                // The tooltip text is rendered into a portal at document.body.
                // The availableWithBackend template includes the backend
                // name ("SecretServiceKeyring"). Radix renders the tooltip
                // text twice (once visible + once in an SR-only duplicate)
                // so we use getAllByText and assert >=1 match.
                await waitFor(() => {
                        expect(
                                screen.getAllByText(/SecretServiceKeyring/).length,
                        ).toBeGreaterThan(0);
                });
        });

        it("fallback + tooltip with reason appears when trigger is focused", async () => {
                render(<KeyringStatusBadge status={fallbackStatus} />);
                const btn = screen.getByRole("button");
                btn.focus();
                fireEvent.mouseEnter(btn);
                fireEvent.pointerEnter(btn);
                await waitFor(() => {
                        expect(
                                screen.getAllByText(/keyring\.backend\.missing/).length,
                        ).toBeGreaterThan(0);
                });
        });

        it("BG-R11: trigger button does NOT carry cursor-help (misleading affordance)", () => {
                const { rerender } = render(<KeyringStatusBadge status={availableStatus} />);
                const btn = screen.getByRole("button");
                expect(btn.className).not.toContain("cursor-help");
                expect(btn.className).toContain("cursor-default");

                // Re-render with compact + fallback variant and check the
                // same invariant — cursor-help removal applies to ALL branches.
                rerender(<KeyringStatusBadge status={fallbackStatus} compact />);
                const btn2 = screen.getByRole("button");
                expect(btn2.className).not.toContain("cursor-help");
                expect(btn2.className).toContain("cursor-default");
        });
});
