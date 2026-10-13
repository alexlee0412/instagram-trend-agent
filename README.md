# URL-based Social Trend Analysis

The primary interface is one URL-based CLI:

```bash
./analyze.sh "<social-content-url>"
```

Current support:

| Platform | Content type | Status |
| --- | --- | --- |
| Instagram | Reel | ✅ Supported |
| Instagram | Single-image post | ✅ Supported |
| Instagram | Video post | ✅ Supported |
| Instagram | Image carousel | ✅ Supported |
| Instagram | Mixed image/video carousel | ✅ Supported |
| TikTok | Public video | ✅ Supported |
| X / Twitter | Public video post | ✅ Supported |
| X / Twitter | Single-image post | ✅ Supported |
| X / Twitter | Multi-image post | ✅ Supported |
| X / Twitter | Mixed image/video post | ✅ Supported |
| X / Twitter | Text-only post | ⚠️ Identified; unsupported for visual analysis |
| Other URLs | — | ❌ Unsupported |

The router classifies and canonicalizes the URL before any acquisition call.
Supported Instagram content is acquired, converted into ordered visual
assets, and sent through the shared OpenAI multimodal trend identifier.
stdout contains exactly one JSON object; operational logs go to stderr.
Only `success` and `partial` analyses exit zero.

The result keeps the platform-level `content_type` (`reel`, `post`, or `video`)
separate from the material actually supplied to the model. The latter is
reported under `analysis_input.visual` with `source_type` (`video_frames`,
`single_image`, or `carousel`), ordering, asset count, and carousel
composition details when applicable. Unsupported or unidentified URLs return
`analysis_input: null`.

The analyzer also receives explicit visual context (`video_frames`,
`single_image`, or `carousel`) so it interprets chronology and slide order
correctly. The structured `trend` object includes `content_format`, an array
limited to reusable presentation, editing, sequencing, or interaction
structures such as `transition`, `photo collage`, or `list/ranking`. Topics,
subjects, aesthetics, events, audiences, and cultural meanings belong in the
other trend fields.

Instagram acquisition uses current yt-dlp first. Reels retain the public
`/embed/captioned/` fallback, and posts can use the equivalent public embed
representation when yt-dlp cannot expose usable media. No Instagram login,
browser profile, cookie export, or preparation step is required for normal
public content.

TikTok public videos use anonymous yt-dlp acquisition. Canonical video URLs
and recognized TikTok-owned short/share links are accepted; short links are
resolved and reclassified before acquisition. X video posts use yt-dlp first,
while X's public syndication representation supplies post metadata, original
images, ordered multi-image/mixed-media composition, and a direct public video
fallback. TikTok and X do not use cookies, browser profiles, Playwright, or
manual authentication. These anonymous public endpoints are best-effort and
can change independently of this project.

Setup:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
brew install ffmpeg
cp .env.example .env
```

Set `OPENAI_API_KEY` in `.env`; never paste it into a command or commit it.
`TREND_MODEL_PROVIDER=openai` is the default. `analyze.sh` automatically
uses `.venv/bin/python` when it exists, falling back to `python3` otherwise.

Examples:

```bash
./analyze.sh "https://www.instagram.com/reel/<REEL_ID>/"
./analyze.sh "https://www.instagram.com/p/<POST_ID>/"
./analyze.sh "https://www.tiktok.com/@creator/video/<VIDEO_ID>"
./analyze.sh "https://x.com/creator/status/<STATUS_ID>"
./analyze.sh "https://twitter.com/creator/status/<STATUS_ID>"
```

Current Instagram limitations:

- Only public, non-age-restricted content exposed anonymously by yt-dlp or
  Instagram's logged-out embed is supported.
- Instagram can change or remove the undocumented embed media payload. A
  production deployment should add a configurable managed acquisition
  provider behind the same loader interface.
- Some metadata is absent from anonymous source responses. Missing values are
  returned as `null`, never guessed.
- Video posts use five representative frames. Single images are passed
  directly. Carousels use up to six ordered items, with one representative
  frame for each selected video item.
- Video posts, a single-image post, and a mixed image/video carousel have
  been validated live. Image carousels use the same implemented media path.

Current TikTok/X limitations:

- Private, deleted, age/region-restricted, or login-gated content is not
  supported by the anonymous acquisition path.
- TikTok short links must still resolve to a canonical public `/@user/video/`
  URL. Expired share links fail rather than being guessed.
- X text-only posts return `analysis_status: "unsupported"` because the
  current shared analyzer requires visual input.
- X image/mixed-media support depends on the undocumented public syndication
  response used by X embeds. Extractor or payload changes may require an
  adapter update.

## Legacy proofs of concept

Two independent local proofs of concept:

1. **Discovery** — a Playwright-based collector that samples Instagram's recommendation feed without predefined keywords and structures what it observes.
2. **Trend Identification** — a standalone engine that takes a single Reel or post URL (from anywhere — the collector, a manual paste, a future n8n/Apify/Sheet source) and answers "what trend does this content appear to belong to?"

These are separate concepts, deliberately not automated together yet:

- **Discovery** determines *what content is surfacing*.
- **Trend Identification** determines *what trend that content represents*.
- **Future aggregation** (not built) would determine *whether that trend is actually spreading* — that requires many labeled Reels over time (creator diversity, post frequency, growth velocity), not a single Reel. Don't confuse trend identification with virality detection.

## 1. Discovery (implemented)

```text
Chromium / Instagram Reels
        ↓
Playwright Collector
        ↓
Structured Reel Records + yt-dlp enrichment
        ↓
data/reels.xlsx
```

### Setup

```bash
python3 -m pip install -r requirements.txt
playwright install chromium
```

Copy `.env.example` to `.env` and fill in your Instagram credentials:

```bash
cp .env.example .env
```

### Run

```bash
python3 main.py --duration 300
```

This requires zero AI/model setup — it only needs Playwright + yt-dlp, both installed via `requirements.txt`.

## 2. Legacy Trend Identification module

The older module CLI experimentally accepts both Reel URLs
(`.../reel(s)/<ID>/`) and post URLs (`.../p/<ID>/` — single image,
carousel, or video). Both feed the same model boundary through a shared
"ordered visual assets" abstraction:

```text
Instagram Reel or Post URL
        ↓
validate_content_url()  →  "reel" or "post"
        ↓                              ↓
   Reel / video post            image or carousel post
        ↓                              ↓
yt-dlp (metadata + temp video)   yt-dlp (metadata) + direct
        ↓                        public media fetch
ffmpeg (representative frames)         ↓
        ↓                        original image(s), or one
        └──────────┬───────────  midpoint frame per video slide
                   ↓
        ordered visual assets (image paths)
                   ↓
        OpenAI multimodal trend identifier (gpt-5.6-terra)
                   ↓
        Structured Trend JSON
```

### Setup

```bash
brew install ffmpeg   # required for frame extraction — confirmed working
```

In `.env`, set:

```env
TREND_MODEL_PROVIDER=openai
OPENAI_API_KEY=your_key_here
```

The legacy module can still read `TREND_ANALYSIS_COOKIES_FILE`, but cookie
export is not part of the root CLI and was not used for the public-content
validation above.

### Run

```bash
# Preferred interface (all URL routing)
./analyze.sh "<social-content-url>"

# Legacy module interfaces
# Reel
python3 -m trend_analysis.analyze_reel "https://www.instagram.com/reels/<REEL_ID>/"

# Post (image, carousel, or video)
python3 -m trend_analysis.analyze_reel "https://www.instagram.com/p/<POST_ID>/"
```

Prints one JSON object to stdout (all logs go to stderr, so this is safe to pipe/parse — e.g. from n8n, see [`n8n/README.md`](n8n/README.md)). Exit code is `0` unless `analysis_status` is `"error"`.

### What's implemented right now

- The root `./analyze.sh` pipeline routes Instagram, TikTok, and X/Twitter to
  isolated acquisition adapters, then into shared visual preparation and live
  OpenAI analysis.
- Instagram `/p/` handling supports video, single-image, pure-image
  carousel, and mixed carousel representations. Live validation currently
  covers video, single image, and a mixed image/video carousel.
- The **OpenAI provider is implemented and live** (`trend_analysis/openai_provider.py`), using the Responses API with strict JSON-schema structured output. Model: `gpt-5.6-terra` by default (override via `OPENAI_TREND_MODEL`; verified against OpenAI's official docs, since model naming has moved past the `gpt-4o` era).
- Claude and Ollama remain unimplemented stubs in `trend_analysis/trend_identifier.py` — swapping to either means implementing that one function, nothing else changes.
- Carousel posts: resolvable slides (up to `MAX_VISUAL_ASSETS = 6`) are sent in original order, deterministically sampled (first + last + evenly spaced) when a carousel has more slides than that.

### Carousel behavior

Resolvable carousel media is retained in source order and capped at six
visual assets. Each image is passed directly; each selected video contributes
one representative frame. If an item cannot be acquired or converted, the
remaining assets are still analyzed and the result is marked `partial`.

### Not implemented (future layers)

Batch/multi-Reel aggregation, virality scoring, an embedding/vector database, clustering (CLIP/HDBSCAN/etc.), Google Sheets/Docs integration, the actual n8n workflow, scheduled runs, and automatic collector → analyzer chaining (`main.py` never calls the analyzer automatically).

## Security note

`.env` and the `browser_data/` directory contain credentials and session data. Never commit them — both are excluded via `.gitignore`. The legacy `python3 export_instagram_cookies.py` script writes a plaintext session-cookie file (default `instagram_cookies.txt`) for the optional `TREND_ANALYSIS_COOKIES_FILE` setting — also gitignored, also never commit it. Never paste `OPENAI_API_KEY` or cookie values into a terminal command, log, or chat — only `.env`.
