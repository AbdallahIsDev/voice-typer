# ADR-0023: Universal Media-to-Text Transcription

## Status

Accepted

## Context

Users want to turn any video or audio into text: a pasted link (YouTube,
Vimeo, TikTok, X, Instagram, podcast feeds, direct `.mp3`/`.mp4` URLs, ...)
or a dropped local file, in any container/codec. The transcript is produced
by the existing local transcription engine and saved like any other
dictation (History + optional file export).

Constraints shaping this decision:

- **Reliability bar (product):** the feature must work the same for every
  user, every day. A "works today, broken tomorrow" runtime is rejected.
- **Small installer (XS-28):** the user-downloaded installer stays slim
  (~40 MB max). The heavy offline pack downloads silently after install.
  Rarely-used weight must NOT ride along with every app update.
- **Zero-reinvent (E13):** extraction logic comes from the community
  (yt-dlp, ~1000 supported sites), not a home-grown scraper.
- **User-initiated network only (C-DATA-1):** fetching a pasted URL is
  user-initiated and allowed; unsolicited egress stays forbidden.
- **Just-in-time consent (C-MIC-3):** first URL ingestion goes through the
  existing `openConsentGate()`; local files need no consent.
- **Dual allowlist lockstep (CONTRIBUTING §6.4):** new IPC commands land in
  both `voice_typer/server/ipc/registry.py` and
  `src-tauri/src/commands/sidecar_cmds/allowlist.rs`.

Key verified facts (web-checked, not assumed):

- `faster-whisper` already pulls **PyAV 18.1.0** (locked). Its wheels bundle
  full FFmpeg: any-format decode with zero new heavy deps and zero external
  `ffmpeg` binary. `av.open()` reads local files AND remote HTTP(S) URLs
  directly, with `open_timeout`/`read_timeout` options.
- Since yt-dlp 2025.11.12 (issue #15012), full YouTube support requires an
  external JS runtime. Supported: Deno >= 2.0 (recommended, default-enabled),
  Node >= 20 (explicit opt-in), QuickJS, Bun (deprecated). Without one,
  YouTube degrades over time; this is by upstream design, not a bug.
- YouTube's protection is a **program to run, not a question to answer**:
  solver scripts need a complete runtime *plus* browser/network abilities
  (fetch, crypto, on-the-fly npm). Bare language engines (MiniRacer,
  pythonmonkey, js2py) cannot satisfy this — verified in the EJS wiki.
  The old pure-Python solver is exactly what YouTube defeated in 2025.
- A second layer (PO token / BotGuard attestation, per-video badge) is
  handled by yt-dlp plugins maintained by the yt-dlp team, on the same
  runtime. Phase 2 of this feature.
- `yt-dlp` itself is Unlicense; the `yt-dlp-ejs` solver package bundles
  ISC/MIT code and needs third-party attribution.

## Decision

### Ingestion ladder (cheapest path wins; video bytes are never fetched)

1. **Tier 0 — zero-download streaming (default for URLs).** yt-dlp runs in
   resolve-only mode (`download=False`) and returns the direct **audio-only**
   CDN URL. PyAV opens that URL and stream-decodes over HTTPS range reads;
   16 kHz mono PCM chunks flow straight into the engine in ~30 s windows.
   A 2 GB video moves ~40-70 MB of streamed audio; **nothing is written to
   disk**.
2. **Tier 1 — audio-only download.** If streaming stalls or ranges are
   unsupported, download `bestaudio` to temp, decode locally, delete temp.
3. **Tier 2 — smallest muxed.** Only when no audio-only format exists
   (rare): smallest format containing an audio stream.
4. **Local files:** decoded in place, never copied.
5. **Subtitle fast-path (opt-in):** when a video ships manual captions,
   offer "Use subtitles (instant)" vs "Transcribe audio (accurate)".
   Instant, zero model load; accuracy tradeoff is explicit in the UI copy.

### Backend layout (`voice_typer/server/media_ingest/`, one concern per file)

`sources.py` (classify file/URL) · `url_resolver.py` (yt-dlp library mode,
format selector `bestaudio[protocol^=http]/bestaudio/best`, metadata) ·
`decoder.py` (PyAV to PCM chunk generator, local + remote) · `engine_loop.py`
(chunk windows to existing model + Silero VAD, cancellable, progress events) ·
`jobs.py` (single active job + cancel) · `storage.py` (History + optional
txt/srt/vtt/json export).

### IPC surface (both allowlists, §6.4)

`media_transcribe_start {source, options}` · `media_transcribe_cancel` ·
pushes `media_transcribe_progress` (percent, ETA) /
`media_transcribe_complete` / `media_transcribe_error` (user-readable
reason, never raw tracebacks).

### Packaging and updates (user-confirmed 2026-09-23)

- **Deno binary to offline pack.** ~40 MB, frozen, downloaded once with the
  pack. Rarely used (YouTube-only), almost never changes, never
  re-downloaded with app updates.
- **yt-dlp + solver scripts to the fast-moving side with their own silent
  mini-update channel** (few MB, no reinstall). YouTube breaks every few
  weeks; fixes ship in hours, not at the next full release. Deno is the
  player, solver scripts are the discs: YouTube changes are absorbed by
  script updates, the runtime stays put.
- **PO-token plugin is phase 2**, same runtime, same update channel.

## Consequences

Easier: one code path for all ~1000 sites; installer stays slim; YouTube
breakage heals via tiny silent updates (the SnapTube model); local-file and
non-YouTube flows never depend on the JS runtime.

Harder / risks: new deps (`yt-dlp`, `yt-dlp-ejs`, ISC/MIT attribution);
Nuitka must include yt-dlp package data (verify in CI build); Deno binary
per platform triple in the offline pack manifest; YouTube *will* break
periodically (hours-days) — surfaced as a clear "update the app" message,
never silent; DRM content is refused, not circumvented.

## Appendix: Edge-Case Catalog

| # | Case | Handling |
|---|------|----------|
| E1 | DRM / encrypted HLS (Widevine etc.) | Hard-fail, clear copy. Never circumvented. |
| E2 | Live / ongoing streams | v1: reject ("live streams unsupported"); capture-first-N-minutes is phase 2. |
| E3 | Age-gated / login-walled / private / deleted | Surface yt-dlp error with guidance. No cookie import in v1. |
| E4 | Geo-blocked | Surface region error verbatim. |
| E5 | Playlists / channels | v1: single item only ("paste one video link"). Expand-and-queue is phase 2. |
| E6 | Multi-audio-track containers | Decode default track; track picker is phase 2. |
| E7 | Video with no audio track | Fail fast: "no audio found". |
| E8 | Corrupt / truncated local file | PyAV error becomes a clean failure naming the file. |
| E9 | Mid-stream stall (Tier 0) | `read_timeout` fires, Range-resume retry, then Tier 1 fallback. |
| E10 | CDN URL expires mid-job | Re-resolve via yt-dlp, resume from last completed window. |
| E11 | Very long media (>~4 h) | Chunked windows keep memory flat; UI warns duration + ETA. |
| E12 | No JS runtime present | Non-YouTube unaffected; YouTube returns actionable error, never a hang. |
| E13 | Subtitle fast-path accuracy | Opt-in only; copy states captions are author-provided. |
| E14 | User cancels job | Token stops decode + engine; temps removed; partial transcript kept, marked partial. |
| E15 | Model not loaded | Lazy-load per job; "loading model" shown as its own progress phase. |

## Implementation record (2026-09-25)

v1 scope as built (verified against the tree, not the plan above):

- **URL ladder implemented**, not punted: resolve-only extraction
  (`url_resolver.py`, `skip_download`, `bestaudio[protocol^=http]`
  selector), Tier-0 PyAV range streaming with open/read timeouts +
  seek-resume (`decoder.py`), Tier-1/2 temp-download fallback
  (`downloader.py`), stall/expiry taxonomy with re-resolve (`E9/E10`),
  full edge taxonomy E1–E8/E11–E15, subtitle availability flag +
  opt-in UI + fetch path (`subtitles.py`, Media page checkbox).
  The `registry.py` / `allowlist.rs` comments saying "local-file jobs
  first, URLs in Phase 2" are stale and contradict this; URL jobs run
  end-to-end through `media_transcribe_start`.
- **JS runtime**: probed pack-first, then PATH deno, then Node ≥ 20
  (`runtime.py`); YouTube without any runtime fails loudly (E12),
  never hangs. Test battery green on 2026-09-25 (57 Python + 9
  renderer cases).

Open (not built; do not claim otherwise):

- **Deno in the offline pack**: `runtime.py` probes
  `<pack>/bin/deno[.exe]`, but no pack-assembly step ships it yet
  (no builder writes a Deno binary into the runtime-pack zip). Until
  then YouTube depends on a user-installed Deno/Node.
- **Mini-update surfacing**: `mini_update.check_refresh()` runs at
  startup and persists `update_available`, but nothing reads it back
  (no UI surface, no silent install — installs stay user-initiated
  by explicit decision). Wiring the Media page banner to it is the
  remaining half of the "fixes ship in hours" story.
- **Phase 2 per the Decision section**: PO-token plugin, playlists /
  channels, live capture, track picker.
