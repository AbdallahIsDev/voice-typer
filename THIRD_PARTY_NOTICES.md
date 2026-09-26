# Third-Party Notices

This project distributes or downloads the following third-party components
as part of the media-ingest feature (ADR-0023). Their licenses are
acknowledged here.

## yt-dlp

- **Homepage:** https://github.com/yt-dlp/yt-dlp
- **License:** Unlicense (public domain)
- **Use:** URL resolution and audio-only download fallback for remote media
  sources.

## yt-dlp-ejs

- **Homepage:** https://github.com/yt-dlp/ejs
- **License:** Unlicense
- **Bundled components in prebuilt wheels:** `meriyah` (ISC License),
  `astring` (MIT License)
- **Use:** External-JavaScript solver support for YouTube extraction.

## PyAV

- **Homepage:** https://github.com/PyAV-Org/PyAV
- **License:** BSD 3-Clause
- **Bundled components in binary wheels:** FFmpeg libraries, licensed under
  the GNU Lesser General Public License (LGPL) v2.1 or later. FFmpeg source
  code is available at https://ffmpeg.org/download.html
- **Use:** Container/codec decode of local files and remote audio streams
  to 16 kHz mono PCM.

## Deno

- **Homepage:** https://github.com/denoland/deno
- **License:** MIT
- **Use:** Optional external JavaScript runtime shipped with the offline
  runtime pack; executes yt-dlp's JavaScript solver scripts for YouTube
  extraction.
