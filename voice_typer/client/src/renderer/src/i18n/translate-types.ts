// Shared translate-function type.
//
// The renderer has ~16 call sites that accept the i18n ``t`` function
// as a hook/module argument (toast hooks, error-message helpers, …).
// Each previously re-declared its own local alias, ``type TFn = (key:
// string, params?: Record<string, string>) => string``, 16 drifting
// copies of the same shape. This module is the single authoritative
// declaration (the runtime implementations live in ``./translate.ts`` /
// ``useT()`` in ``./hooks.ts``, importing those here would pull the
// whole i18n runtime into type-only consumers, so the shape is stated
// once, structurally, and both sides stay decoupled).
//
// The shape is deliberately the LOOSE signature: keys are plain
// ``string`` (several call sites build keys dynamically, e.g. from a
// reason→key map), and ``t()``'s strict catalog-key overloads accept
// every ``string``-typed argument, so the real ``t`` is assignable to
// this type while static literals keep their compile-time catalog
// checking at the ``t()`` call sites that use them directly.

/**
 * Translate-function type matching the runtime ``t`` / ``useT`` shape:
 * takes a dot-separated catalog key plus optional interpolation params
 * and returns the localized string.
 */
export type TranslateFn = (
	key: string,
	params?: Record<string, string>,
) => string;
