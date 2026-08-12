// Test-audio playback hook for the Microphone page.
//
//Extracted from the former ``useMicrophoneTest`` monolith ().
// Owns the ``playingEnhanced`` / ``playingOriginal`` UI state plus the
// underlying ``HTMLAudioElement`` ref (``audioRef``) and the
// "is-playing" ref (``playingRef``) that the level-monitor hook reads
// to suppress level updates during playback.
//
//(1-C Finding 8): ``playAudio`` / ``stopPlayback`` are wrapped
// in ``useCallback`` with their actual deps so the session hook (and
// the page) can capture them directly without ref indirection.
//
// The ``playingRef`` is exposed (alongside the public state) so the
// composition hook can hand it to ``useMicrophoneLevelMonitor``. The
// public ``useMicrophoneTest`` API does NOT re-export ``playingRef``
// — only the three sibling hooks coordinate via it.

import {
	type MutableRefObject,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";

export interface UseMicrophonePlaybackResult {
	playingEnhanced: boolean;
	playingOriginal: boolean;
	/** Ref-to-latest "is audio playing" flag — read by ``useMicrophoneLevelMonitor``. */
	playingRef: MutableRefObject<boolean>;
	playAudio: (base64: string, isEnhanced: boolean) => void;
	stopPlayback: () => void;
}

export function useMicrophonePlayback(): UseMicrophonePlaybackResult {
	const { showSnack } = useSnackbar();
	const [playingEnhanced, setPlayingEnhanced] = useState(false);
	const [playingOriginal, setPlayingOriginal] = useState(false);
	const audioRef = useRef<HTMLAudioElement | null>(null);
	const playingRef = useRef(false);

	const playAudio = useCallback(
		(base64: string, isEnhanced: boolean) => {
			if (!base64) return;
			if (audioRef.current) {
				audioRef.current.pause();
				audioRef.current = null;
			}

			if (isEnhanced) {
				setPlayingEnhanced(true);
				setPlayingOriginal(false);
			} else {
				setPlayingEnhanced(false);
				setPlayingOriginal(true);
			}
			playingRef.current = true;

			try {
				const audioDataUri = `data:audio/wav;base64,${base64}`;
				const audio = new Audio(audioDataUri);
				audioRef.current = audio;

				audio.onended = () => {
					setPlayingEnhanced(false);
					setPlayingOriginal(false);
					playingRef.current = false;
					audioRef.current = null;
				};

				audio.onerror = () => {
					setPlayingEnhanced(false);
					setPlayingOriginal(false);
					playingRef.current = false;
					audioRef.current = null;
					showSnack(t("microphone.playbackFailed"), "error");
				};

				audio.play().catch(() => {
					setPlayingEnhanced(false);
					setPlayingOriginal(false);
					playingRef.current = false;
					audioRef.current = null;
					showSnack(t("microphone.playbackRetryFailed"), "error");
				});
			} catch {
				setPlayingEnhanced(false);
				setPlayingOriginal(false);
				playingRef.current = false;
				showSnack(t("microphone.startPlaybackFailed"), "error");
			}
		},
		[showSnack],
	);

	const stopPlayback = useCallback(() => {
		if (audioRef.current) {
			audioRef.current.pause();
			audioRef.current = null;
		}
		setPlayingEnhanced(false);
		setPlayingOriginal(false);
		playingRef.current = false;
	}, []);

	// Unmount cleanup: pause any playing test audio to prevent
	// background playback after navigation. Also clears the audioRef so
	// onended/onerror don't fire setState on an unmounted component.
	// (Previously this cleanup also lived inside the test-session
	// hook's unmount effect; moved here so the playback hook owns its
	// own resource lifecycle.)
	useEffect(() => {
		return () => {
			if (audioRef.current) {
				try {
					audioRef.current.pause();
				} catch (e) {
					/* noop — audio element may already be in a
					   closed/stopped state */
					console.warn(
						"[renderer:useMicrophonePlayback] cleanup pause failed:",
						e,
					);
				}
				audioRef.current = null;
			}
		};
	}, []);

	return {
		playingEnhanced,
		playingOriginal,
		playingRef,
		playAudio,
		stopPlayback,
	};
}
