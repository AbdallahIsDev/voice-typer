import deepgram from "@/assets/models/deepgram.svg";
import nvidia from "@/assets/models/nvidia.svg";
import openai from "@/assets/models/openai.svg";
import qwen from "@/assets/models/qwen.svg";

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
