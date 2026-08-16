import deepgram from "@/assets/models/deepgram.svg";
import nvidia from "@/assets/models/nvidia.svg";
import openai from "@/assets/models/openai.svg";
import qwen from "@/assets/models/qwen.svg";

/**
 * FamilyLogo — brand logo for a model family or cloud provider, shown
 * in the Models page family header (accordion trigger), the onboarding
 * family strip, and the cloud provider cards (Models page Cloud tab).
 *
 * Each family maps to ONE logo file (whisper → openai, qwen → qwen,
 * parakeet → nvidia). Cloud provider keys are supported too:
 * deepgram → deepgram, and `openai` is an alias for the same logo as
 * `whisper` (the cloud provider key vs the local family id). `groq`
 * has no logo asset and renders nothing.
 *
 * Color model (per the user's rule: only black/white logos adapt to
 * the theme — colored ones keep their brand color in both themes):
 *   - qwen.svg and nvidia.svg bake in their brand colors (#082DFF
 *     Qwen blue, #80bc00 NVIDIA green), so they render identically in
 *     light and dark mode.
 *   - openai.svg and deepgram.svg are pure black (light theme); in
 *     dark mode the `dark:invert` class flips them to white. This is
 *     a plain CSS filter on the <img> — deliberately NOT
 *     `currentColor`, which cannot work here: an SVG loaded through
 *     <img> is a separate document, so `currentColor` resolves to the
 *     SVG's own default (black) and never sees the host page's `color`
 *     property (that bug made every logo render black in both themes).
 *
 * The logo is decorative (the name text sits right next to it), so it
 * carries `alt=""` / `aria-hidden` to avoid double-announcing the
 * brand to screen readers.
 */
const FAMILY_LOGO: Record<string, string> = {
	whisper: openai,
	openai, // cloud provider key → same logo as the whisper family
	qwen,
	parakeet: nvidia,
	deepgram,
};

// Black/white logos that must flip to white under the `.dark` theme.
// Everything else bakes in its brand color and stays untouched.
const BLACK_LOGO_FAMILIES = new Set(["whisper", "openai", "deepgram"]);

export function FamilyLogo({ family }: { family: string }) {
	const src = FAMILY_LOGO[family];
	if (!src) {
		return null;
	}
	// Black/white logos (whisper/openai, deepgram) must invert to
	// white under the `.dark` theme. qwen/nvidia are fixed brand
	// colors and stay untouched.
	const needsInvert = BLACK_LOGO_FAMILIES.has(family);
	return (
		<span aria-hidden="true" className="inline-flex shrink-0 items-center">
			<img
				src={src}
				alt=""
				className={
					needsInvert
						? "h-4 w-auto object-contain dark:invert"
						: "h-4 w-auto object-contain"
				}
			/>
		</span>
	);
}
