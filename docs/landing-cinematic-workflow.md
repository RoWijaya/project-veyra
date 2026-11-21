# Landing-page film

The home page presents the POS and operations workflow before the existing restaurant tour. A 660 × 587 browser capture shows the POS with an isolated example catalog. The hero and product section link to working account registration and the existing tour.

The home page uses the pinned GSAP/ScrollTrigger timeline from 900 px wide and 660 px high when motion is enabled. Scroll position scrubs the muted film. The source is assigned when the story approaches the viewport. Phone and reduced-motion views use the poster and stacked chapters. Data Saver leaves the poster in place.

## Current assets

- `core/static/core/landing/veyra-story.mp4` — user-generated Google Veo tour, 1920×1080, 9.96 s, 8,720,798 bytes. The repository copy matches the supplied original SHA-256. The original remains in Downloads.
- `core/static/core/landing/veyra-story-poster.webp` — still from the 5 s dining-aisle frame, 1920×1080, 104,284 bytes.

No WebM is shipped. A trial 720p browser encode visibly damaged the footage, so it was rejected. The original MP4 is already under 9 MB; mobile does not request it. FFmpeg/ffprobe were unavailable for a higher-quality, smaller re-encode. Revisit a 720p H.264/WebM encode with frequent keyframes when a proper transcoder is available, then visually review it before replacing the current asset.

## Playback behavior

- The `<video>` uses `preload="metadata"`, `muted`, `playsinline` and the WebP poster. Source URLs remain in `data-src` until the desktop story approaches; data-saver skips source assignment.
- `/media/landing-film/` streams the MP4 with HTTP byte-range support. The browser can seek to the scroll-selected time without first downloading the full file; the endpoint was verified in the browser with a seekable 0–9.963 s timeline.
- ScrollTrigger pins the scene and maps scroll progress to video time and three quiet chapter overlays. It does not call `play()`.
- Loading, ready, seeking, buffering and media-error states leave the poster as the fallback. Resize or media-query cleanup disconnects observers, aborts listeners, pauses the video, removes active source URLs and refreshes the media element.
- Mobile and reduced-motion use the static poster and all chapter copy. The page does not request video in those modes.

## Browser review

- Desktop forward and backward scrolling scrubbed through all three chapters; the browser reported the film paused and muted, with a seekable 0–9.963 s range and no observed seek stalls.
- A fresh 390×844 mobile load showed the poster and stacked copy with no video source assigned.
- See TESTING.md for browser evidence from the later product-wide audit.

The public home view discovers only files that exist. Add future approved formats under `core/static/core/landing/`; no provider key belongs in Veyra or the public page. The site serves finished assets only; generating more footage requires an authenticated provider account and its current usage terms.
