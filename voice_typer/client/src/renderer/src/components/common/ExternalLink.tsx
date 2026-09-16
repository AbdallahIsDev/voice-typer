// components/common/ExternalLink.tsx
//
// MO-118: the ONE anchor every in-app link to an EXTERNAL https page
// renders through.
//
// Why an anchor instead of a `Button onClick={() => open(url)}`:
//
// - Semantics/a11y: the element IS a link, so it keeps `role="link"`,
//   the accessible href, keyboard activation, middle-click, and
//   right-click "copy link address". Turning it into a button loses all
//   of that (and, in the Resources grid, made "Report a Bug" collide
//   with the Troubleshooting section's own "Report a Bug" button).
// - Regression-safe: the pre-existing pages/tests assert on
//   `a[href*="SECURITY.md"]` and `getByRole("link", …)`, so the element
//   type must stay an anchor.
//
// Why intercept the click anyway:
//
// - Under Electron the host intercepts `target="_blank"` /
//   `window.open` (`input-nav-guard.ts`) and `shell.openExternal`s the
//   https URL, so the native behavior is already correct there.
// - Under Tauri there is no host interception (CSP `default-src 'self'`
//   + `plugins.shell.open = false`, C-TAURI-2): a bare anchor would
//   either be blocked or trap the page inside the webview. The click is
//   therefore routed through the shared `openExternalUrl` helper
//   (`window.window_.openExternalUrl` → Rust `open_external_url_command`,
//   same https-only policy), which itself falls back to the classic
//   `window.open` when the bridge is absent.
//
// Callers keep the app's normal look by wrapping it in
// `<Button asChild>`; the Slot merges the Button classes onto this
// anchor.

import type { ComponentPropsWithoutRef, MouseEvent } from "react";

import { openExternalUrl } from "@/lib/external-links";

type ExternalLinkProps = Omit<ComponentPropsWithoutRef<"a">, "href"> & {
	href: string;
};

export function ExternalLink({
	href,
	onClick,
	target = "_blank",
	rel = "noreferrer noopener",
	...rest
}: ExternalLinkProps) {
	const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
		onClick?.(event);
		if (event.defaultPrevented) return;
		// Route the activation to the OS browser. `preventDefault` keeps
		// the webview from navigating itself (which would replace the app
		// UI under Tauri); the anchor's href stays for copy-link and for
		// the no-bridge fallback inside `openExternalUrl`.
		event.preventDefault();
		void openExternalUrl(href);
	};

	return (
		<a href={href} target={target} rel={rel} {...rest} onClick={handleClick} />
	);
}
