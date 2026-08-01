/**
 * Bubble overlay subcomponents and hooks — thin re-export shim.
 *
 * The former 823-line monolith has been split into the `./bubble/`
 * package (constants, helpers, hooks, and one file per component).
 * This file remains only so existing consumers that import from
 * `"./bubble-components"` (notably `Bubble.tsx`) keep working without
 * churn. New code should import directly from `./bubble`.
 *
 * NOTE: the leaky `HugeiconsIcon` / `Mic02Icon` re-export that used to
 * live at the bottom of this module has been dropped. Consumers should
 * import the icon components directly from `@hugeicons/react` and
 * `@hugeicons/core-free-icons` respectively — see `Bubble.tsx` for the
 * canonical pattern.
 */
export * from "./bubble/index";
